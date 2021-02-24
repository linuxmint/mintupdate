#!/usr/bin/python3

import codecs
import fnmatch
import gettext
import os
import re
import sys
import traceback
import html

import apt
from gi.repository import Gio

from Classes import (CONFIGURED_KERNEL_TYPE, KERNEL_PKG_NAMES,
                     PRIORITY_UPDATES, SUPPORTED_KERNEL_TYPES, Alias, KernelVersion, Update)

gettext.install("mintupdate", "/usr/share/locale")

meta_names = []

class APTCheck():

    def __init__(self):
        self.settings = Gio.Settings(schema_id="com.linuxmint.updates")
        self.cache = apt.Cache()
        self.priority_updates_available = False

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
            if (pkg.is_installed and pkg.marked_upgrade and pkg.candidate.version != pkg.installed.version):
                self.add_update(pkg)

        # Kernel updates
        lts_meta_name = "linux" + CONFIGURED_KERNEL_TYPE
        _metas = [s for s in self.cache.keys() if s.startswith(lts_meta_name)]
        if CONFIGURED_KERNEL_TYPE == "-generic":
            _metas.append("linux-virtual")
        global meta_names
        for meta in _metas:
            shortname = meta.split(":")[0]
            if shortname not in meta_names:
                meta_names.append(shortname)
        try:
            # Get the uname version
            active_kernel = KernelVersion(os.uname().release)

            # Override installed kernel if not of the configured type
            try:
                active_kernel_type = "-" + active_kernel.version.split("-")[-1]
            except:
                active_kernel_type = CONFIGURED_KERNEL_TYPE
            if  active_kernel_type != CONFIGURED_KERNEL_TYPE:
                active_kernel.series = ("0","0","0")

            # Uncomment for testing:
            # active_kernel = KernelVersion("4.18.0-0-generic")

            # Check if any meta is installed..
            meta_candidate_same_series = None
            meta_candidate_higher_series = None
            for meta_name in meta_names:
                if meta_name in self.cache:
                    meta = self.cache[meta_name]
                    meta_kernel = KernelVersion(meta.candidate.version)
                    if (active_kernel.series > meta_kernel.series):
                        # Meta is lower than the installed kernel series, ignore
                        continue
                    else:
                        if meta.is_installed:
                            # Meta is already installed, return
                            return
                        # never install linux-virtual, we only support it being
                        # installed already
                        if meta_name == "linux-virtual":
                            continue
                        # Meta is not installed, make it a candidate if higher
                        # than any current candidate
                        if active_kernel.series == meta_kernel.series:
                            # same series
                            if (not meta_candidate_same_series or meta_kernel.version_id >
                                KernelVersion(meta_candidate_same_series.candidate.version).version_id
                                ):
                                meta_candidate_same_series = meta
                        else:
                            # higher series
                            if (not meta_candidate_higher_series or meta_kernel.version_id >
                                KernelVersion(meta_candidate_higher_series.candidate.version).version_id
                                ):
                                meta_candidate_higher_series = meta

            # If we're here, no meta was installed
            if meta_candidate_same_series:
                # but a candidate of the same series was found, add to updates and return
                self.add_update(meta_candidate_same_series, kernel_update=True)
                return

            # If we're here, no matching meta was found
            if meta_candidate_higher_series:
                # but we found a higher meta candidate, add it to the list of updates
                # unless the installed kernel series is lower than the LTS series
                # for some reason, in the latter case force the LTS meta
                if meta_candidate_higher_series.name != lts_meta_name:
                    if lts_meta_name in self.cache:
                        lts_meta = self.cache[lts_meta_name]
                        lts_meta_kernel = KernelVersion(lts_meta.candidate.version)
                        if active_kernel.series < lts_meta_kernel.series:
                            meta_candidate_higher_series = lts_meta
                self.add_update(meta_candidate_higher_series, kernel_update=True)
                return

            # We've gone past all the metas, so we should recommend the latest
            # kernel on the series we're in
            max_kernel = active_kernel
            for pkgname in self.cache.keys():
                match = re.match(r'^(?:linux-image-)(\d.+?)%s$' % active_kernel_type, pkgname)
                if match:
                    kernel = KernelVersion(match.group(1))
                    if kernel.series == max_kernel.series and kernel.version_id > max_kernel.version_id:
                        max_kernel = kernel
            if max_kernel.version_id != active_kernel.version_id:
                for pkgname in KERNEL_PKG_NAMES:
                    pkgname = pkgname.replace('VERSION', max_kernel.version).replace("-KERNELTYPE", active_kernel_type)
                    if pkgname in self.cache:
                        pkg = self.cache[pkgname]
                        if not pkg.is_installed:
                            self.add_update(pkg, kernel_update=True)
                            return

        except:
            traceback.print_exc()

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
            if not dependency.target_versions or not dependency.rawtype == "Depends":
                return None
            deppkg = dependency.target_versions[0]
            if deppkg.source_name in ("linux", "linux-signed"):
                return deppkg.source_version
            if deppkg.source_name.startswith("linux-meta"):
                return self.get_kernel_version_from_meta_package(deppkg)
        return None

    def add_update(self, package, kernel_update=False):
        source_version = package.candidate.version
        # Change version of kernel meta packages to that of the actual kernel
        # for grouping with related updates
        if package.candidate.source_name.startswith("linux-meta"):
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
                if kernel_update and package.is_installed and not \
                   "-" in update.old_version and "-" in package.installed.version:
                    update.old_version = package.installed.version
            else:
                update = Update(package, source_name=source_name)
                self.updates[source_name] = update
            if kernel_update:
                update.type = "kernel"
            update.new_version = source_version

    def serialize_updates(self):
        # Print updates
        for source_name in self.updates.keys():
            update = self.updates[source_name]
            update.serialize()

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
        if os.path.exists("/var/lib/apt/lists"):
            try:
                super_buffer = []
                for file in os.listdir("/var/lib/apt/lists"):
                    if ("i18n_Translation") in file and not file.endswith("Translation-en"):
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
                                            dlines = description.split("\n")
                                            value = ""
                                            num = 0
                                            newline = False
                                            for dline in dlines:
                                                dline = dline.strip()
                                                if len(dline) > 0:
                                                    if dline == ".":
                                                        value = "%s\n" % (value)
                                                        newline = True
                                                    else:
                                                        if (newline):
                                                            value = "%s%s" % (value, self.capitalize(dline))
                                                        else:
                                                            value = "%s %s" % (value, dline)
                                                        newline = False
                                                    num += 1
                                            value = value.replace("  ", " ").strip()
                                            # Capitalize the first letter
                                            value = value[:1].upper() + value[1:]
                                            # Add missing punctuation
                                            if len(value) > 0 and value[-1] not in [".", "!", "?"]:
                                                value = "%s." % value
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
        check = APTCheck()
        check.find_changes()
        check.apply_l10n_descriptions()
        check.load_aliases()
        check.apply_aliases()
        check.clean_descriptions()
        check.serialize_updates()
        if os.getuid() == 0 and os.path.exists("/usr/bin/mintinstall-update-pkgcache"):
            # Spawn the cache update asynchronously
            # We're using os.system with & here to make sure it's async and detached
            # from the caller (which will die before the child process is finished)
            # stdout/stderr is also directed to /dev/null so it doesn't interfere
            # or block the output from checkAPT
            os.system("/usr/bin/mintinstall-update-pkgcache > /dev/null 2>&1 &")
    except Exception as error:
        print("CHECK_APT_ERROR---EOL---")
        print(sys.exc_info()[0])
        print("Error: %s" % error)
        traceback.print_exc()
        sys.exit(1)
