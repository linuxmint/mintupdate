#!/usr/bin/python3

import codecs
import fnmatch
import gettext
import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import html
import locale

import apt
from gi.repository import Gio

import aptkit.simpleclient
import aptkit.enums

from multiprocess import Process, Queue

from Classes import (CONFIG_PATH, PRIORITY_UPDATES, SUPPORTED_KERNEL_TYPES,
                     Alias, Update)
from util import _idle

gettext.install("mintupdate", "/usr/share/locale")

meta_names = []

# packages which description is incorrect in Ubuntu (usually those which were replaced by snap dependencies)
NON_TRANSLATED_PKGS = ["firefox", "thunderbird"]

class AptUpdater():

    def __init__(self, ui_window=None):
        self.settings = Gio.Settings(schema_id="com.linuxmint.updates")
        self.ui_window = ui_window
        self.cache = None
        self.priority_updates_available = False
        self.updates = {}
        self.update_list = []
        self.error = None
        self.install_error = None
        self.install_cancelled = False

    def load_cache(self):
        self.cache = apt.Cache()

    def refresh(self, interactive=False):
        # Refresh the on-disk APT cache. Blocks until done. Returns None on
        # success, or a short string describing what went wrong (transaction
        # non-success exit_state, daemon error, cancellation, sync exception,
        # or non-zero exit from mint-refresh-cache) — caller logs it.

        if interactive:
            self._aptkit_done = threading.Event()
            self._refresh_error = None
            self._refresh_via_aptkit()
            if not self._aptkit_done.wait(timeout=180):
                self._refresh_error = "Timed out waiting for aptkit cache refresh"
            return self._refresh_error
        else:
            return self._refresh_via_mint_refresh_cache()

    @_idle
    def _refresh_via_aptkit(self):
        try:
            aptkit_client = aptkit.simpleclient.SimpleAPTClient(self.ui_window)

            def on_finished(transaction, exit_state):
                if exit_state != aptkit.enums.EXIT_SUCCESS:
                    self._refresh_error = f"aptkit transaction finished with exit_state={exit_state}"
                self._aptkit_done.set()

            def on_error(error_code, error_details):
                self._refresh_error = f"aptkit error code={error_code} details={error_details}"
                self._aptkit_done.set()

            def on_cancelled():
                self._refresh_error = "aptkit transaction cancelled"
                self._aptkit_done.set()

            aptkit_client.set_progress_callback(None)
            aptkit_client.set_finished_callback(on_finished)
            aptkit_client.set_error_callback(on_error)
            aptkit_client.set_cancelled_callback(on_cancelled)

            aptkit_client.update_cache()
        except Exception as e:
            self._refresh_error = f"aptkit setup raised: {e}"
            self._aptkit_done.set()

    @staticmethod
    def _refresh_via_mint_refresh_cache():
        try:
            result = subprocess.run(["sudo", "/usr/bin/mint-refresh-cache"])
            if result.returncode != 0:
                return f"mint-refresh-cache exited with code {result.returncode}"
            return None
        except Exception as e:
            return f"mint-refresh-cache raised: {e}"

    def install_packages(self, packages):
        # Install the given list of package names via aptkit. Blocks until the
        # transaction finishes, is cancelled, or errors. After return, callers
        # should check self.install_cancelled and self.install_error.
        self.install_error = None
        self.install_cancelled = False
        self._aptkit_done = threading.Event()
        self._start_install(packages)
        # Generous bound (4h) so legitimately long upgrades aren't aborted, but
        # we never hang forever if aptkit dies without firing a callback.
        if not self._aptkit_done.wait(timeout=4 * 60 * 60):
            self.install_error = "Timed out waiting for aptkit install"

    @_idle
    def _start_install(self, packages):
        try:
            client = aptkit.simpleclient.SimpleAPTClient(self.ui_window)

            def on_finished(transaction, exit_state):
                if exit_state != aptkit.enums.EXIT_SUCCESS:
                    self.install_error = f"aptkit transaction finished with exit_state={exit_state}"
                self._aptkit_done.set()

            def on_error(error_code, error_details):
                self.install_error = f"aptkit error code={error_code} details={error_details}"
                self._aptkit_done.set()

            def on_cancelled():
                self.install_cancelled = True
                self._aptkit_done.set()

            client.set_finished_callback(on_finished)
            client.set_error_callback(on_error)
            client.set_cancelled_callback(on_cancelled)

            client.install_packages(packages)
        except Exception as e:
            self.install_error = f"aptkit setup raised: {e}"
            self._aptkit_done.set()

    def fetch_updates(self):
        # Compute the list of available updates. Blocks until done, or until
        # the child process dies / times out — in which case self.error is set.
        self.update_list = []
        self.error = None
        result_queue = Queue()
        process = Process(target=self._fetch_updates_in_process, args=(result_queue,))
        process.start()
        try:
            self.error, self.update_list = result_queue.get(timeout=60)
        except queue.Empty:
            self.error = "Timed out waiting for fetch_updates result"
            if process.is_alive():
                process.terminate()
        process.join(timeout=5)
        if self.error is None and process.exitcode not in (0, None):
            self.error = f"fetch_updates child exited with code {process.exitcode}"

    def _fetch_updates_in_process(self, queue):
        try:
            self.load_cache()
            self.find_changes()
            self.apply_l10n_descriptions()
            self.load_aliases()
            self.apply_aliases()
            self.clean_descriptions()
            queue.put([None, self.get_updates()])
        except Exception as error:
            print(sys.exc_info()[0])
            print("Error in fetch_updates: %s" % error)
            traceback.print_exc()
            queue.put([str(error).replace("E:", "\n").strip(), []])

    def fetch_test_updates(self, test_name):
        print("SIMULATING TEST MODE:", test_name)
        self.updates = {}
        self.error = None

        if test_name == "error":
            self.error = "Testing - this is a simulated error."
        elif test_name == "up-to-date":
            pass
        elif test_name == "self-update":
            self.load_cache()
            self._add_dummy_update("mintupdate", False)
        elif test_name == "updates":
            self.load_cache()
            self._add_dummy_update("python3", False)
            self._add_dummy_update("mint-meta-core", False)
            self._add_dummy_update("linux-generic", True)
            self._add_dummy_update("xreader", False)
        elif test_name == "tracker-max-age":
            self.load_cache()
            self._add_dummy_update("dnsmasq", False)
            self._add_dummy_update("linux-generic", True)

            updates_json = {
                "mint-meta-common": { "type": "package",  "since": "2020.12.03", "days": 99 },
                "linux-meta":       { "type": "security", "since": "2020.12.03", "days": 99 }
            }
            root_json = {
                "updates": updates_json,
                "version": 1,
                "checked": "2020.12.04",
                "notified": "2020.12.03"
            }
            os.makedirs(CONFIG_PATH, exist_ok=True)
            with open(os.path.join(CONFIG_PATH, "updates.json"), "w") as f:
                json.dump(root_json, f)

        self.update_list = list(self.updates.values())

    def _add_dummy_update(self, package_name, kernel_update):
        pkg = self.cache[package_name]
        self.add_update(pkg, kernel_update, "99.0.0")

    def load_aliases(self):
        self.aliases = {}
        with open("/usr/lib/linuxmint/mintUpdate/aliases") as alias_file:
            for line in alias_file:
                if not line.startswith('#'):
                    splitted = line.split("#####")
                    if len(splitted) == 4:
                        (alias_packages, alias_name, alias_short_description, alias_description) = splitted
                        alias_object = Alias(alias_name, alias_short_description, alias_description)
                        for alias_package in alias_packages.split(','):
                            alias_package = alias_package.strip()
                            self.aliases[alias_package] = alias_object

    def find_changes(self):
        self.cache.upgrade(True) # dist-upgrade
        changes = self.cache.get_changes()

        self.updates = {}

        # Package updates
        for pkg in changes:
            if pkg.candidate is None:
                # Installed package has no candidate version (e.g. orphaned
                # or removed from the repositories). Skip it so the rest of
                # the update list can still be refreshed.
                print("Skipping package without candidate version: %s" % pkg.name)
                continue
            if (pkg.is_installed and pkg.candidate.version != pkg.installed.version):
                if (pkg.marked_upgrade or pkg.marked_downgrade):
                    self.add_update(pkg)

    def is_blacklisted(self, source_name, version):
        for blacklist in self.settings.get_strv("blacklisted-packages"):
            if "=" in blacklist:
                (bl_pkg, bl_ver) = blacklist.split("=", 1)
            else:
                bl_pkg = blacklist
                bl_ver = None
            if fnmatch.fnmatch(source_name, bl_pkg) and (not bl_ver or bl_ver == version):
                return True
        return False

    def get_kernel_version_from_meta_package(self, pkg):
        for dependency in pkg.dependencies:
            if not dependency.target_versions or dependency.rawtype != "Depends":
                return None
            deppkg = dependency.target_versions[0]
            if deppkg.source_name in ("linux", "linux-signed") or deppkg.source_name.startswith("linux-hwe"):
                return deppkg.source_version
            if deppkg.source_name.startswith("linux-meta"):
                return self.get_kernel_version_from_meta_package(deppkg)
        return None

    def add_update(self, package, kernel_update=False, test_version=None):
        if test_version is not None:
            source_version = test_version
        else:
            source_version = package.candidate.version
        # Change version of kernel meta packages to that of the actual kernel
        # for grouping with related updates
        if package.candidate.source_name.startswith("linux-meta"):
            shortname = package.name.split(":")[0]
            if shortname not in meta_names:
                meta_names.append(shortname)
            _source_version = self.get_kernel_version_from_meta_package(package.candidate)
            if _source_version:
                source_version = _source_version

        # Change source name of kernel packages for grouping with related updates
        if (package.candidate.source_name == "linux" or
            package.candidate.source_name.startswith("linux-hwe") or
            package.candidate.source_name.startswith("linux-meta") or
            package.candidate.source_name.startswith("linux-signed") or
            [True for flavor in SUPPORTED_KERNEL_TYPES if package.candidate.source_name.startswith("linux%s" % flavor)]
           ):
            kernel_update = True
            source_name = "linux-%s" % source_version
        else:
            source_name = package.candidate.source_name

        # ignore packages blacklisted by the user
        if self.is_blacklisted(package.candidate.source_name, package.candidate.version):
            return

        if source_name in PRIORITY_UPDATES:
            if not self.priority_updates_available and len(self.updates) > 0:
                self.updates.clear()
            self.priority_updates_available = True
        if source_name in PRIORITY_UPDATES or not self.priority_updates_available:
            if source_name in self.updates:
                update = self.updates[source_name]
                update.add_package(package)
                # Adjust update.old_version for kernel updates to try and
                # match the kernel, not the meta
                if kernel_update and package.is_installed and \
                        "-" not in update.old_version and "-" in package.installed.version:
                    update.old_version = package.installed.version
            else:
                update = Update(package, source_name=source_name)
                self.updates[source_name] = update
            if kernel_update:
                update.type = "kernel"
            update.new_version = source_version

    def get_updates(self):
        update_list = []
        for source_name in sorted(self.updates.keys()):
            update = self.updates[source_name]
            update_list.append(update)
        return update_list

    def apply_aliases(self):
        for source_name in self.updates.keys():
            update = self.updates[source_name]
            if source_name in self.aliases:
                alias = self.aliases[source_name]
                update.display_name = alias.name
                update.short_description = alias.short_description
                update.description = alias.description
            elif (update.type == "kernel" and
                  source_name not in ['linux-libc-dev', 'linux-kernel-generic'] and
                  (len(update.package_names) >= 3 or update.package_names[0] in meta_names)
                 ):
                update.display_name = _("Linux kernel %s") % update.new_version
                update.short_description = _("The Linux kernel.")
                update.description = _("The Linux Kernel is responsible for hardware and drivers support. Note that this update will not remove your existing kernel. You will still be able to boot with the current kernel by choosing the advanced options in your boot menu. Please be cautious though.. kernel regressions can affect your ability to connect to the Internet or to log in graphically. DKMS modules are compiled for the most recent kernels installed on your computer. If you are using proprietary drivers and you want to use an older kernel, you will need to remove the new one first.")

    def apply_l10n_descriptions(self):
        lang, encoding = locale.getlocale()
        if "_" in lang:
            lang = lang.split("_")[0]
        if lang in [None, "C", "en"]:
            return
        print("found lang", lang)
        if os.path.exists("/var/lib/apt/lists"):
            try:
                super_buffer = []
                for file in os.listdir("/var/lib/apt/lists"):
                    if file.endswith(f"_i18n_Translation-{lang}"):
                        fd = codecs.open(os.path.join("/var/lib/apt/lists", file), "r", "utf-8")
                        super_buffer += fd.readlines()

                i = 0
                while i < len(super_buffer):
                    line = super_buffer[i].strip()
                    if line.startswith("Package: "):
                        try:
                            pkgname = line.replace("Package: ", "")
                            if pkgname in self.updates:
                                update = self.updates[pkgname]
                                j = 2 # skip md5 line after package name line
                                while True:
                                    if (i+j >= len(super_buffer)):
                                        break
                                    line = super_buffer[i+j].strip()
                                    if line.startswith("Package: "):
                                        break
                                    if j==2:
                                        try:
                                            # clean short description
                                            value = line
                                            try:
                                                value = html.unescape(value)
                                            except:
                                                print ("Unable to unescape '%s'" % value)
                                            # Remove "Description-xx: " prefix
                                            value = re.sub(r'Description-(\S+): ', r'', value)
                                            # Only take the first line and trim it
                                            value = value.split("\n")[0].strip()
                                            value = value.split("\\n")[0].strip()
                                            # Capitalize the first letter
                                            value = value[:1].upper() + value[1:]
                                            # Add missing punctuation
                                            if len(value) > 0 and value[-1] not in [".", "!", "?"]:
                                                value = "%s." % value
                                            if update.source_name not in NON_TRANSLATED_PKGS:
                                                update.short_description = value
                                                update.description = ""
                                        except Exception as e:
                                            print(e)
                                            print(sys.exc_info()[0])
                                    else:
                                        description = "\n" + line
                                        try:
                                            try:
                                                description = html.unescape(description)
                                            except:
                                                print ("Unable to unescape '%s'" % description)
                                            if update.source_name not in NON_TRANSLATED_PKGS:
                                                update.description += description
                                        except Exception as e:
                                            print (e)
                                            print(sys.exc_info()[0])
                                    j += 1

                        except Exception as e:
                            print (e)
                            print(sys.exc_info()[0])
                    i += 1
                del super_buffer
            except Exception as e:
                print (e)
                print("Could not fetch l10n descriptions..")
                print(sys.exc_info()[0])

    def clean_descriptions(self):
        for source_name in self.updates.keys():
            update = self.updates[source_name]
            if "\n" in update.short_description:
                update.short_description = update.short_description.split("\n")[0]
            if update.short_description.endswith("."):
                update.short_description = update.short_description[:-1]
            update.short_description = self.capitalize(update.short_description)
            if "& " in update.short_description:
                update.short_description = update.short_description.replace('&', '&amp;')
            if "& " in update.description:
                update.description = update.description.replace('&', '&amp;')

    def capitalize(self, string):
        if len(string) > 1:
            return (string[0].upper() + string[1:])
        else:
            return (string)

if __name__ == "__main__":
    try:
        check = AptUpdater()
        check.refresh()
        check.fetch_updates()
        if check.error is not None:
            print("Error: %s" % check.error)
            sys.exit(1)
        for update in check.update_list:
            print(update.display_name, update.new_version, update.short_description)
    except Exception as error:
        print(error)
        print(sys.exc_info()[0])
        traceback.print_exc()
        sys.exit(1)
