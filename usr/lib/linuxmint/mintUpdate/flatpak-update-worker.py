#!/usr/bin/python3

import functools
import os
import argparse
import json
import sys
import setproctitle
from pathlib import Path

import gi
gi.require_version('GLib', '2.0')
gi.require_version('Gtk', '3.0')
gi.require_version('Flatpak', '1.0')
from gi.repository import Gtk, GLib, Flatpak, Gio

from mintcommon.installer import installer
from mintcommon.installer import _flatpak

from Classes import FlatpakUpdate

setproctitle.setproctitle("flatpak-update-worker")

CHUNK_SIZE = 4096

LOG_PATH = os.path.join(GLib.get_home_dir(), '.linuxmint', 'mintupdate', 'flatpak-updates.log')

DEBUG_MODE = False
try:
    DEBUG_MODE = os.environ["DEBUG"]
except:
    pass

def debug(*args):
    if not DEBUG_MODE:
        return
    sanitized = [str(arg) for arg in args if arg is not None]
    argstr = " ".join(sanitized)
    print("flatpak-update-worker (DEBUG): %s" % argstr, file=sys.stderr, flush=True)

def warn(*args):
    sanitized = [str(arg) for arg in args if arg is not None]
    argstr = " ".join(sanitized)
    print("flatpak-update-worker (WARN): %s" % argstr, file=sys.stderr, flush=True)

class FlatpakUpdateWorker():
    def __init__(self):
        self.installer = installer.Installer(installer.PKG_TYPE_FLATPAK)
        self.fp_sys = _flatpak.get_fp_sys()
        self.task = None

        self.cancellable = Gio.Cancellable()

        if not self.check_for_any_installed():
            self.send_to_updater("no-installed")
            self.cancellable.cancel()
            self.quit()

        self.stdin = Gio.UnixInputStream.new(sys.stdin.fileno(), True)

        self.updates = []

    def check_for_any_installed(self):
        try:
            installed = self.fp_sys.list_installed_refs(self.cancellable)
        except GLib.Error as e:
            warn("not able to list installed refs: (%d) %s" % (e.code, e.message))
            installed = []

        if len(installed) == 0:
            debug("no flatpaks installed, exiting without refreshing")
            return False

        debug("%d flatpaks installed, continuing" % len(installed))
        return True

    def refresh(self, init=True):
        if self.cancellable.is_cancelled():
            return

        self.fp_sys.cleanup_local_refs_sync(None)
        self.fp_sys.prune_local_repo(None)

        if init:
            self.installer.init_sync()

        self.installer.force_new_cache_sync()

    def fetch_updates(self):
        if self.cancellable.is_cancelled():
            return

        if not self.installer.init_sync():
            warn("cache not valid, refreshing")
            self.refresh(False)
        else:
            debug("cache valid")

        self.installer.generate_uncached_pkginfos()

        debug("generating updates")
        _flatpak._initialize_appstream_thread()

        self.updates = []
        self.installer.select_flatpak_updates(None,
                                              self._fetch_task_ready, self._fetch_updates_error, 
                                              None, None,
                                              use_mainloop=False)

    def _fetch_task_ready(self, task):
        debug("task object:", task, "transaction:", task.transaction)

        self.task = task
        self.error = task.error_message

        if self.error == None and task.transaction is not None:
            self._process_fetch_task(task)

            out = json.dumps(self.updates, default=lambda o: o.to_json(), indent=4)
            self.send_to_updater(out)
        else:
            self.send_to_updater(self.error)

        self.quit()
        debug("done generating updates", self.error)

    def _fetch_updates_error(self, task):
        warn("fetch error", task.error_message)

    def _process_fetch_task(self, task):
        trans = task.transaction
        ops = trans.get_operations()

        def cmp_ref_name(a, b):
            ref_a = Flatpak.Ref.parse(a.get_ref())
            ref_b = Flatpak.Ref.parse(b.get_ref())
            a_refstr_len = len(ref_a.get_name().split("."))
            b_refstr_len = len(ref_b.get_name().split("."))

            return a_refstr_len < b_refstr_len

        ops.sort(key=functools.cmp_to_key(cmp_ref_name))
        ops.sort(key=lambda op: Flatpak.Ref.parse(op.get_ref()).get_name())

        for op in ops:
            if op.get_operation_type() == Flatpak.TransactionOperationType.UPDATE:
                ref = Flatpak.Ref.parse(op.get_ref())
                debug("Update: ", op.get_ref())
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
                    warn("Problem creating FlatpakUpdate for %s: %s" % (ref.format_ref(), e))

            elif op.get_operation_type() == Flatpak.TransactionOperationType.INSTALL:
                ref = Flatpak.Ref.parse(op.get_ref())
                debug("Install: ", op.get_ref())
                try:
                    remote_ref = self.fp_sys.fetch_remote_ref_sync(op.get_remote(),
                                                                   ref.get_kind(),
                                                                   ref.get_name(),
                                                                   ref.get_arch(),
                                                                   ref.get_branch(),
                                                                   None)
                except GLib.Error as e:
                    debug("Can't add ref to install: %s" % e.message)
                    remote_ref = None

                pkginfo = self.installer.find_pkginfo(ref.get_name(), installer.PKG_TYPE_FLATPAK, remote=op.get_remote())
                try:
                    update = FlatpakUpdate(op, self.installer, ref, None, remote_ref, pkginfo)

                    if self.is_base_package(update) or (not self.add_to_parent_update(update)):
                        self.updates.append(update)
                except Exception as e:
                    warn("Problem creating FlatpakUpdate for %s: %s" % (ref.format_ref(), e))
        task.cancel()

    def add_to_parent_update(self, update):
        for maybe_parent in self.updates:
            if update.ref_name.startswith(maybe_parent.ref_name):
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
                        ref_name = group.replace("Extension ", "")
                        built_extensions.append(ref_name)
            except Exception:
                return False
            for extension in built_extensions:
                if update.ref_name.startswith(extension):
                    maybe_parent.add_package(update)
                    return True

    def is_base_package(self, update):
        name = update.ref_name
        if name.startswith("app"):
            return True
        try:
            kf = update.metadata
            runtime_ref_id = "runtime/%s" % kf.get_string("Runtime", "runtime")
            runtime_ref = Flatpak.Ref.parse(runtime_ref_id)
            if name == runtime_ref.get_name():
                return True
        except Exception:
            return False

    def prepare_start_updates(self, updates):
        if self.cancellable.is_cancelled():
            return

        debug("creating real update task")
        self.error = None

        self.installer.select_flatpak_updates(updates,
                                              self._start_task_ready, self._start_updates_error,
                                              self._execute_finished, None)

    def _start_task_ready(self, task):
        self.task = task
        self.send_to_updater("ready")

        self.stdin.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, self.message_from_updater)

    def _start_updates_error(self, task):
        warn("start updates error", task.error_message)
        self.send_to_updater(task.error_message)

    def confirm_start(self):
        if self.task.confirm():
            self.send_to_updater("yes")
        else:
            self.send_to_updater("no")
            self.quit()

    def start_updates(self):
        self.installer.execute_task(self.task)

    def _execute_finished(self, task):
        self.error = task.error_message
        self.write_to_log(task)

        self.send_to_updater("done")
        self.quit()

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
            warn("Can't write to flatpak update log:", e)

    def send_to_updater(self, msg):
        print(msg, flush=True)

    def message_from_updater(self, pipe, res):
        if self.cancellable is None or self.cancellable.is_cancelled():
            return

        try:
            bytes_read = pipe.read_bytes_finish(res)
        except GLib.Error as e:
            if e.code != Gio.IOErrorEnum.CANCELLED:
                warn("Error reading from updater: %s" % e.message)
            return

        if bytes_read:
            message = bytes_read.get_data().decode().strip("\n")
            debug("receiving from updater: '%s'" % message)

            if message == "confirm":
                self.confirm_start()
            elif message == "start":
                self.start_updates()

        pipe.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, self.cancellable, self.message_from_updater)

    def quit(self):
        GLib.timeout_add(0, self.quit_on_ml)

    def quit_on_ml(self):
        if self.task:
            self.task.cancel()

        self.cancellable.cancel()
        Gtk.main_quit()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Flatpak worker for mintupdate")
    parser.add_argument("-d", "--debug", help="Print debugging information.",
                        action="store_true")
    parser.add_argument("-r", "--refresh", help="Refresh local flatpak cache and appstream info.",
                        action="store_true")
    parser.add_argument("-f", "--fetch-updates", help="Get a json list of update info.",
                        action="store_true")
    parser.add_argument("-u", "--update-packages", help="Updates packages - one or more flatpak ref strings must be supplied. "
                                                        "This process will remain running for communication.",
                        action="store_true")
    
    parser.add_argument('refs', metavar='ref', type=str, nargs='*', help='refs to update')
    args = parser.parse_args()

    updater = FlatpakUpdateWorker()

    if args.refresh:
        try:
            updater.refresh()
            exit(0)
        except Exception as e:
            print(e)
            exit(1)
    elif args.fetch_updates:
        updater.fetch_updates()
    elif args.update_packages:
        if len(args.refs) == 0:
            print("Expected one or more space-separated flatpak refs")
            exit(1)
        updater.prepare_start_updates(args.refs)
    else:
        print("nothing to do")
        exit(0)

    try:
        Gtk.main()
    except KeyboardInterrupt:
        Gtk.main_quit()

    exit(0)
