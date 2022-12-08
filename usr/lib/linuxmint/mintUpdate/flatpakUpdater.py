#!/usr/bin/python3

import threading
import functools
import os
import re
from pathlib import Path

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

try:
    if not GLib.find_program_in_path("flatpak"):
        raise Exception
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak, Gio
    from mintcommon.installer import installer
    from mintcommon.installer import _flatpak
except Exception as e:
    print("No Flatpak support - are flatpak and gir1.2-flatpak-1.0 installed?")
    raise NotImplementedError

GET_UPDATES_TIMEOUT = 60
LOG_PATH = os.path.join(GLib.get_home_dir(), '.linuxmint', 'mintupdate', 'flatpak-updates.log')

class FlatpakUpdate():
    def __init__(self, op, installer, ref, installed_ref, remote_ref, pkginfo):
        self.op = op
        self.ref = ref

        # nullable
        self.installed_ref = installed_ref
        self.remote_ref = remote_ref
        self.pkginfo = pkginfo
        #

        self.ref_str = ref.get_name()
        self.metadata = op.get_metadata()
        self.size = op.get_download_size()
        self.link = installer.get_homepage_url(pkginfo) if pkginfo else None

        self.flatpak_type = "app" if ref.get_kind() == Flatpak.RefKind.APP else "runtime"

        # ideal:           old-version                     new-version
        # versions same:   old-version (commit)            new-version (commit)
        # no versions      commit                          commit

        # old version
        if installed_ref:
            iref_version = self.installed_ref.get_appdata_version()
            old_commit = installed_ref.get_commit()[:10]
        else:
            iref_version = ""
            old_commit = ""

        # new version
        if pkginfo:
            appstream_version = installer.get_version(pkginfo)

        new_commit = op.get_commit()[:10]

        if iref_version != "" and appstream_version != "":
            if iref_version != appstream_version:
                self.old_version = iref_version
                self.new_version = appstream_version
            else:
                self.old_version = "%s (%s)" % (iref_version, old_commit)
                self.new_version = "%s (%s)" % (appstream_version, new_commit)
        else:
            self.old_version = old_commit
            self.new_version = new_commit

        if pkginfo:
            self.name = installer.get_display_name(pkginfo)
        elif installed_ref:
            self.name = installed_ref.get_appdata_name()
        else:
            self.name = ref.get_name()

        if pkginfo:
            self.summary = installer.get_summary(pkginfo)
            description = installer.get_description(pkginfo)
            self.description = re.sub(r'\n+', '\n\n', description).rstrip()
        elif installed_ref:
            self.summary = installed_ref.get_appdata_summary()
            self.description = ""
        else:
            self.summary = ""
            self.description = ""

        if self.description == "" and self.flatpak_type == "runtime":
            self.summary = self.description = _("A Flatpak runtime package")

        self.real_source_name = self.ref_str
        self.source_packages = ["%s=%s" % (self.ref_str, self.new_version)]
        self.package_names = [self.ref_str]
        self.sub_updates = []

        if installed_ref:
            self.origin = installed_ref.get_origin().capitalize()
        elif remote_ref:
            self.origin = remote_ref.get_remote_name()
        else:
            self.origin = ""

    def add_package(self, update):
        self.sub_updates.append(update)
        self.package_names.append(update.ref_str)
        self.size += update.size
        # self.source_packages.append("%s=%s" % (update.ref_str, update.new_version))

class FlatpakUpdater():
    def __init__(self):
        self.installer = installer.Installer()
        self.fp_sys = _flatpak.get_fp_sys()

        self.task = None
        self.task_ready_event = threading.Event()
        self.perform_updates_finished_event = threading.Event()

        self.no_pull = True

        self.updates = []

    def refresh(self):
        self.installer = installer.Installer(installer.PKG_TYPE_FLATPAK, temp=True)

        self.fp_sys.cleanup_local_refs_sync(None)
        self.fp_sys.prune_local_repo(None)

        self.installer.init_sync()
        self.installer.force_new_cache_sync()

        self.no_pull = False

    def fetch_updates(self):
        print("Flatpak: generating updates")
        _flatpak._initialize_appstream_thread()
        self.installer.generate_uncached_pkginfos()

        self.updates = []
        self.task_ready_event.clear()

        thread = threading.Thread(target=self._fetch_update_thread)
        thread.start()

        self.task_ready_event.wait(GET_UPDATES_TIMEOUT)
        print("Flatpak: done generating updates")

    def _fetch_update_thread(self):
        self.installer.select_flatpak_updates(None,
                                              self._fetch_task_ready, self._fetch_updates_error, 
                                              None, None,
                                              use_mainloop=False)

    def _fetch_task_ready(self, task):
        self.task = task
        self.error = task.error_message
        print("fetch task ready")

        if self.error == None:
            self._process_fetch_task(task)
        self.task_ready_event.set()

    def _fetch_updates_error(self, task):
        print("fetch error", task.error_message)
        self.error = task.error_message
        self.task_ready_event.set()

    def _process_fetch_task(self, task):
        trans = task.transaction
        ops = trans.get_operations()

        def cmp_ref_str(a, b):
            ref_a = Flatpak.Ref.parse(a.get_ref())
            ref_b = Flatpak.Ref.parse(b.get_ref())
            a_refstr_len = len(ref_a.get_name().split("."))
            b_refstr_len = len(ref_b.get_name().split("."))

            return a_refstr_len < b_refstr_len

        ops.sort(key=functools.cmp_to_key(cmp_ref_str))
        ops.sort(key=lambda op: Flatpak.Ref.parse(op.get_ref()).get_name())

        for op in ops:
            if op.get_operation_type() == Flatpak.TransactionOperationType.UPDATE:
                ref = Flatpak.Ref.parse(op.get_ref())
                print("Update: ", op.get_ref())
                try:
                    installed_ref = self.fp_sys.get_installed_ref(ref.get_kind(),
                                                                  ref.get_name(),
                                                                  ref.get_arch(),
                                                                  ref.get_branch(),
                                                                  None)
                    installed_ref.load_appdata()
                except GLib.Error as e:
                    if e.code == Flatpak.Error.NOT_INSTALLED:
                        installed_ref = None

                pkginfo = self.installer.find_pkginfo(ref.get_name(), installer.PKG_TYPE_FLATPAK, remote=op.get_remote())
                try:
                    update = FlatpakUpdate(op, self.installer, ref, installed_ref, None, pkginfo)

                    if self.is_base_package(update) or (not self.add_to_parent_update(update)):
                        self.updates.append(update)
                except Exception as e:
                    print("Problem creating FlatpakUpdate for %s: %s" % (ref.format_ref(), e))

            elif op.get_operation_type() == Flatpak.TransactionOperationType.INSTALL:
                ref = Flatpak.Ref.parse(op.get_ref())
                print("Install: ", op.get_ref())
                try:
                    remote_ref = self.fp_sys.fetch_remote_ref_sync(op.get_remote(),
                                                                   ref.get_kind(),
                                                                   ref.get_name(),
                                                                   ref.get_arch(),
                                                                   ref.get_branch(),
                                                                   None)
                except GLib.Error as e:
                    remote_ref = None

                pkginfo = self.installer.find_pkginfo(ref.get_name(), installer.PKG_TYPE_FLATPAK, remote=op.get_remote())
                try:
                    update = FlatpakUpdate(op, self.installer, ref, None, remote_ref, pkginfo)

                    if self.is_base_package(update) or (not self.add_to_parent_update(update)):
                        self.updates.append(update)
                except Exception as e:
                    print("Problem creating FlatpakUpdate for %s: %s" % (ref.format_ref(), e))
        task.cancel()

    def add_to_parent_update(self, update):
        for maybe_parent in self.updates:
            if update.ref_str.startswith(maybe_parent.ref_str):
                maybe_parent.add_package(update)
                return True
            # if not self.is_base_package(maybe_parent):
            #     continue
            built_extensions = []
            try:
                kf = maybe_parent.metadata
                try:
                    built_extensions = kf.get_string_list("Build", "built-extensions")
                except:
                    # runtimes, sdks don't have built-extensions, so we must parse the group names...
                    groups, n_groups = kf.get_groups()

                    for group in groups:
                        ref_str = group.replace("Extension ", "")
                        built_extensions.append(ref_str)
            except Exception as e:
                return False
            for extension in built_extensions:
                if update.ref_str.startswith(extension):
                    maybe_parent.add_package(update)
                    return True

    def is_base_package(self, update):
        name = update.ref_str
        if name.startswith("app"):
            return True
        try:
            kf = update.metadata
            runtime_ref_id = "runtime/%s" % kf.get_string("Runtime", "runtime")
            runtime_ref = Flatpak.Ref.parse(runtime_ref_id)
            if name == runtime_ref.get_name():
                return True
        except Exception as e:
            return False

    def prepare_start_updates(self, updates):
        print("Flatpak: creating real update task")
        self.error = None
        self.task_ready_event.clear()

        refs = []

        for update in updates:
            refs.append(update.op.get_ref())

        thread = threading.Thread(target=self._start_update_thread, args=(refs,))
        thread.start()

        self.task_ready_event.wait()

        print("Flatpak: done creating real update task")

    def _start_update_thread(self, refs):
        self.installer.select_flatpak_updates(refs,
                                              self._start_task_ready, self._start_updates_error,
                                              self._execute_finished, None)

    def _start_task_ready(self, task):
        self.task = task
        self.task_ready_event.set()

    def _start_updates_error(self, task):
        print("start updates error", task.error_message)
        self.error = task.error_message
        self.task_ready_event.set()

    def confirm_start(self):
        if self.task.confirm():
            return True
        else:
            self.task.cancel()

    def perform_updates(self):
        self.perform_updates_finished_event.clear()

        thread = threading.Thread(target=self._perform_updates_thread)
        thread.start()

        self.perform_updates_finished_event.wait()

    def _perform_updates_thread(self):
        self.installer.execute_task(self.task)

    def _execute_finished(self, task):
        self.error = task.error_message
        self.write_to_log(task)

        self.perform_updates_finished_event.set()

    def write_to_log(self, task):
        try:
            entries = task.get_transaction_log()
        except:
            return

        directory = Path(LOG_PATH).parent

        try:
            os.makedirs(directory, exist_ok=True)
            with open(LOG_PATH, "a") as f:
                for entry in entries:
                    f.write("%s\n" % entry)
        except Exception as e:
            print("Can't write to flatpak update log:", e)

if __name__ == "__main__":
    updater = FlatpakUpdater()
    ml = GLib.MainLoop()
    try:
        ml.run()
    except KeyboardInterrupt:
        ml.quit()











