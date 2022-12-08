#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import sys
import gi
import tempfile
import threading
import time
import gettext
import io
import json
import locale
import tarfile
import urllib.request
import proxygsettings
import subprocess
import pycurl
import datetime
import configparser
import traceback
import setproctitle
try:
    import cinnamon
    CINNAMON_SUPPORT = True
except:
    CINNAMON_SUPPORT = False

try:
    import flatpakUpdater
    FLATPAK_SUPPORT = True
except Exception as e:
    FLATPAK_SUPPORT = False

from kernelwindow import KernelWindow
gi.require_version('Gtk', '3.0')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, Gdk, Gio, GLib, Notify, Pango

from Classes import Update, PRIORITY_UPDATES, UpdateTracker
from xapp.GSettingsWidgets import *

# import AUTOMATIONS dict
with open("/usr/share/linuxmint/mintupdate/automation/index.json") as f:
    AUTOMATIONS = json.load(f)

try:
    os.system("killall -q mintUpdate")
except Exception as e:
    print (e)
    print(sys.exc_info()[0])

setproctitle.setproctitle("mintUpdate")

# i18n
APP = 'mintupdate'
LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)
_ = gettext.gettext


Notify.init(_("Update Manager"))

(TAB_DESC, TAB_PACKAGES, TAB_CHANGELOG) = range(3)

(UPDATE_CHECKED, UPDATE_DISPLAY_NAME, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_SOURCE, UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ) = range(12)

BLACKLIST_PKG_NAME = 0

GIGABYTE = 1000 ** 3
MEGABYTE = 1000 ** 2
KILOBYTE = 1000


def size_to_string(size):
    if (size >= GIGABYTE):
        return "%d %s" % (size // GIGABYTE,  _("GB"))
    if (size >= (MEGABYTE)):
        return "%d %s" % (size // MEGABYTE,  _("MB"))
    if (size >= KILOBYTE):
        return "%d %s" % (size // KILOBYTE,  _("KB"))
    return "%d %s" % (size,  _("B"))

class CacheWatcher(threading.Thread):
    """ Monitors package cache and dpkg status and runs RefreshThread() on change """

    def __init__(self, application, refresh_frequency=90):
        threading.Thread.__init__(self)
        self.application = application
        self.cachetime = 0
        self.statustime = 0
        self.paused = False
        self.refresh_frequency = refresh_frequency
        self.pkgcache = "/var/cache/apt/pkgcache.bin"
        self.dpkgstatus = "/var/lib/dpkg/status"

    def run(self):
        self.refresh_cache()
        if os.path.isfile(self.pkgcache) and os.path.isfile(self.dpkgstatus):
            self.update_cachetime()
            self.loop()
        else:
            self.application.logger.write("Package cache location not found, disabling cache monitoring")

    def loop(self):
        while True:
            if not self.paused and self.application.window.get_sensitive():
                try:
                    cachetime = os.path.getmtime(self.pkgcache)
                    statustime = os.path.getmtime(self.dpkgstatus)
                    if (not cachetime == self.cachetime or not statustime == self.statustime) and \
                        not self.application.dpkg_locked():
                        self.cachetime = cachetime
                        self.statustime = statustime
                        self.refresh_cache()
                except:
                    pass
            time.sleep(self.refresh_frequency)

    def resume(self, update_cachetime=True):
        if not self.paused:
            return
        if update_cachetime:
            self.update_cachetime()
        self.paused = False

    def pause(self):
        self.paused = True

    def update_cachetime(self):
        if os.path.isfile(self.pkgcache) and os.path.isfile(self.dpkgstatus):
            self.cachetime = os.path.getmtime(self.pkgcache)
            self.statustime = os.path.getmtime(self.dpkgstatus)

    def refresh_cache(self):
        self.application.logger.write("Changes to the package cache detected, triggering refresh")
        self.application.refresh()

class ChangelogRetriever(threading.Thread):
    def __init__(self, update, application):
        threading.Thread.__init__(self)
        self.source_package = update.real_source_name
        self.version = update.new_version
        self.origin = update.origin
        self.application = application
        # get the proxy settings from gsettings
        self.ps = proxygsettings.get_proxy_settings()


        # Remove the epoch if present in the version
        if ":" in self.version:
            self.version = self.version.split(":")[-1]

    def get_ppa_info(self):
        ppa_sources_file = "/etc/apt/sources.list"
        ppa_sources_dir = "/etc/apt/sources.list.d/"
        ppa_words = self.origin.lstrip("LP-PPA-").split("-")

        source = ppa_sources_file
        if os.path.exists(ppa_sources_dir):
            for filename in os.listdir(ppa_sources_dir):
                if filename.startswith(self.origin.lstrip("LP-PPA-")):
                    source = os.path.join(ppa_sources_dir, filename)
                    break
        if not os.path.exists(source):
            return None, None
        try:
            with open(source) as f:
                for line in f:
                    if (not line.startswith("#") and all(word in line for word in ppa_words)):
                        ppa_info = line.split("://")[1]
                        break
                else:
                    return None, None
        except EnvironmentError as e:
            print ("Error encountered while trying to get PPA owner and name: %s" % e)
            return None, None
        ppa_url, ppa_owner, ppa_name, ppa_x = ppa_info.split("/", 3)
        return ppa_owner, ppa_name

    def get_ppa_changelog(self, ppa_owner, ppa_name):
        max_tarball_size = 1000000
        print ("\nFetching changelog for PPA package %s/%s/%s ..." % (ppa_owner, ppa_name, self.source_package))
        if self.source_package.startswith("lib"):
            ppa_abbr = self.source_package[:4]
        else:
            ppa_abbr = self.source_package[0]
        deb_dsc_uri = "https://ppa.launchpadcontent.net/%s/%s/ubuntu/pool/main/%s/%s/%s_%s.dsc" % (ppa_owner, ppa_name, ppa_abbr, self.source_package, self.source_package, self.version)
        try:
            deb_dsc = urllib.request.urlopen(deb_dsc_uri, None, 10).read().decode("utf-8")
        except Exception as e:
            print ("Could not open Launchpad URL %s - %s" % (deb_dsc_uri, e))
            return
        for line in deb_dsc.split("\n"):
            if "debian.tar" not in line:
                continue
            tarball_line = line.strip().split(" ", 2)
            if len(tarball_line) == 3:
                deb_checksum, deb_size, deb_filename = tarball_line
                break
        else:
            deb_filename = None
        if not deb_filename or not deb_size or not deb_size.isdigit():
            print ("Unsupported debian .dsc file format. Skipping this package.")
            return
        if (int(deb_size) > max_tarball_size):
            print ("Tarball size %s B exceeds maximum download size %d B. Skipping download." % (deb_size, max_tarball_size))
            return
        deb_file_uri = "https://ppa.launchpadcontent.net/%s/%s/ubuntu/pool/main/%s/%s/%s" % (ppa_owner, ppa_name, ppa_abbr, self.source_package, deb_filename)
        try:
            deb_file = urllib.request.urlopen(deb_file_uri, None, 10).read().decode("utf-8")
        except Exception as e:
            print ("Could not download tarball from %s - %s" % (deb_file_uri, e))
            return
        if deb_filename.endswith(".xz"):
            cmd = ["xz", "--decompress"]
            try:
                xz = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                xz.stdin.write(deb_file)
                xz.stdin.close()
                deb_file = xz.stdout.read()
                xz.stdout.close()
            except EnvironmentError as e:
                print ("Error encountered while decompressing xz file: %s" % e)
                return
        deb_file = io.BytesIO(deb_file)
        try:
            with tarfile.open(fileobj = deb_file) as f:
                deb_changelog = f.extractfile("debian/changelog").read()
        except tarfile.TarError as e:
            print ("Error encountered while reading tarball: %s" % e)
            return

        return deb_changelog

    def run(self):
        Gdk.threads_enter()
        self.application.textview_changes.set_text(_("Downloading changelog..."))
        Gdk.threads_leave()

        if self.ps == {}:
            # use default urllib.request proxy mechanisms (possibly *_proxy environment vars)
            proxy = urllib.request.ProxyHandler()
        else:
            # use proxy settings retrieved from gsettings
            proxy = urllib.request.ProxyHandler(self.ps)

        opener = urllib.request.build_opener(proxy)
        urllib.request.install_opener(opener)

        changelog = [_("No changelog available")]

        changelog_sources = []
        if self.origin == "linuxmint":
            changelog_sources.append("http://packages.linuxmint.com/dev/" + self.source_package + "_" + self.version + "_amd64.changes")
            changelog_sources.append("http://packages.linuxmint.com/dev/" + self.source_package + "_" + self.version + "_i386.changes")
        elif self.origin == "ubuntu":
            if (self.source_package.startswith("lib")):
                changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/main/%s/%s/%s_%s/changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/multiverse/%s/%s/%s_%s/changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/universe/%s/%s/%s_%s/changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/restricted/%s/%s/%s_%s/changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
            else:
                changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/main/%s/%s/%s_%s/changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/multiverse/%s/%s/%s_%s/changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/universe/%s/%s/%s_%s/changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/restricted/%s/%s/%s_%s/changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
        elif self.origin == "debian":
            if (self.source_package.startswith("lib")):
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/main/%s/%s/%s_%s_changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/contrib/%s/%s/%s_%s_changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/non-free/%s/%s/%s_%s_changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
            else:
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/main/%s/%s/%s_%s_changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/contrib/%s/%s/%s_%s_changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
                changelog_sources.append("http://metadata.ftp-master.debian.org/changelogs/non-free/%s/%s/%s_%s_changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
        elif self.origin.startswith("LP-PPA"):
            ppa_owner, ppa_name = self.get_ppa_info()
            if ppa_owner and ppa_name:
                deb_changelog = self.get_ppa_changelog(ppa_owner, ppa_name)
                if not deb_changelog:
                    changelog_sources.append("https://launchpad.net/~%s/+archive/ubuntu/%s/+files/%s_%s_source.changes" % (ppa_owner, ppa_name, self.source_package, self.version))
                else:
                    changelog = "%s\n" % deb_changelog
            else:
                print ("PPA owner or name could not be determined")

        for changelog_source in changelog_sources:
            try:
                print("Trying to fetch the changelog from: %s" % changelog_source)
                url = urllib.request.urlopen(changelog_source, None, 10)
                source = url.read().decode("utf-8")
                url.close()

                changelog = ""
                if "linuxmint.com" in changelog_source:
                    changes = source.split("\n")
                    for change in changes:
                        stripped_change = change.strip()
                        if stripped_change == ".":
                            change = ""
                        if change == "" or stripped_change.startswith("*") or stripped_change.startswith("["):
                            changelog = changelog + change + "\n"
                elif "launchpad.net" in changelog_source:
                    changes = source.split("Changes:")[1].split("Checksums")[0].split("\n")
                    for change in changes:
                        stripped_change = change.strip()
                        if stripped_change != "":
                            if stripped_change == ".":
                                stripped_change = ""
                            changelog = changelog + stripped_change + "\n"
                else:
                    changelog = source
                changelog = changelog.split("\n")
                break
            except:
                pass

        Gdk.threads_enter()
        self.application.textview_changes.set_text("\n".join(changelog))
        Gdk.threads_leave()

class AutomaticRefreshThread(threading.Thread):

    def __init__(self, application):
        threading.Thread.__init__(self)
        self.application = application

    def run(self):
        minute = 60
        hour = 60 * minute
        day = 24 * hour
        initial_refresh = True
        settings_prefix = ""
        refresh_type = "initial"

        while self.application.refresh_schedule_enabled:
            try:
                schedule = {
                    "minutes": self.application.settings.get_int("%srefresh-minutes" % settings_prefix),
                    "hours": self.application.settings.get_int("%srefresh-hours" % settings_prefix),
                    "days": self.application.settings.get_int("%srefresh-days" % settings_prefix)
                }
                timetosleep = schedule["minutes"] * minute + schedule["hours"] * hour + schedule["days"] * day

                if not timetosleep:
                    time.sleep(60) # sleep 1 minute, don't mind the config we don't want an infinite loop to go nuts :)
                else:
                    now = int(time.time())
                    if not initial_refresh:
                        refresh_last_run = self.application.settings.get_int("refresh-last-run")
                        if not refresh_last_run or refresh_last_run > now:
                            refresh_last_run = now
                            self.application.settings.set_int("refresh-last-run", now)
                        time_since_last_refresh = now - refresh_last_run
                        if time_since_last_refresh > 0:
                            timetosleep = timetosleep - time_since_last_refresh
                        # always wait at least 1 minute to be on the safe side
                        if timetosleep < 60:
                            timetosleep = 60

                    schedule["days"] = int(timetosleep / day)
                    schedule["hours"] = int((timetosleep - schedule["days"] * day) / hour)
                    schedule["minutes"] = int((timetosleep - schedule["days"] * day - schedule["hours"] * hour) / minute)
                    self.application.logger.write("%s refresh will happen in %d day(s), %d hour(s) and %d minute(s)" %
                        (refresh_type.capitalize(), schedule["days"], schedule["hours"], schedule["minutes"]))
                    time.sleep(timetosleep)
                    if not self.application.refresh_schedule_enabled:
                        self.application.logger.write("Auto-refresh disabled in preferences, cancelling %s refresh" % refresh_type)
                        return
                    if self.application.app_hidden():
                        self.application.logger.write("Update Manager is in tray mode, performing %s refresh" % refresh_type)
                        refresh = RefreshThread(self.application, root_mode=True)
                        refresh.start()
                        while refresh.is_alive():
                            time.sleep(5)
                    else:
                        if initial_refresh:
                            self.application.logger.write("Update Manager window is open, skipping %s refresh" % refresh_type)
                        else:
                            self.application.logger.write("Update Manager window is open, delaying %s refresh by 60s" % refresh_type)
                            time.sleep(60)
            except Exception as e:
                print (e)
                self.application.logger.write_error("Exception occurred during %s refresh: %s" % (refresh_type, str(sys.exc_info()[0])))

            if initial_refresh:
                initial_refresh = False
                settings_prefix = "auto"
                refresh_type = "recurring"
        else:
            self.application.logger.write("Auto-refresh disabled in preferences, AutomaticRefreshThread stopped")



class InstallThread(threading.Thread):

    def __init__(self, application):
        threading.Thread.__init__(self)
        self.application = application
        self.application.window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        self.application.window.set_sensitive(False)
        self.reboot_required = self.application.reboot_required

    def __del__(self):
        self.application.cache_watcher.resume(False)
        Gdk.threads_enter()
        self.application.window.get_window().set_cursor(None)
        self.application.window.set_sensitive(True)
        Gdk.threads_leave()

    def run(self):
        self.application.cache_watcher.pause()
        try:
            self.application.logger.write("Install requested by user")
            Gdk.threads_enter()
            aptInstallNeeded = False
            packages = []
            cinnamon_spices = []
            flatpaks = []
            model = self.application.treeview.get_model()
            Gdk.threads_leave()

            iter = model.get_iter_first()
            while (iter != None):
                checked = model.get_value(iter, UPDATE_CHECKED)
                if (checked):
                    update = model.get_value(iter, UPDATE_OBJ)
                    if update.type == "cinnamon":
                        cinnamon_spices.append(update)
                        iter = model.iter_next(iter)
                        continue
                    elif update.type == "flatpak":
                        flatpaks.append(update)
                        iter = model.iter_next(iter)
                        continue

                    aptInstallNeeded = True
                    if update.type == "kernel" and \
                       [True for pkg in update.package_names if "-image-" in pkg]:
                        self.reboot_required = True
                    if update.type == "security" and \
                       [True for pkg in update.package_names if "nvidia" in pkg]:
                       self.reboot_required = True
                    for package in update.package_names:
                        packages.append(package)
                        self.application.logger.write("Will install " + str(package))
                iter = model.iter_next(iter)

            needs_refresh = False
            proceed = True
            update_flatpaks = False

            if aptInstallNeeded:
                try:
                    pkgs = ' '.join(str(pkg) for pkg in packages)
                    warnings = subprocess.check_output("/usr/lib/linuxmint/mintUpdate/checkWarnings.py %s" % pkgs, shell = True).decode("utf-8")
                    #print ("/usr/lib/linuxmint/mintUpdate/checkWarnings.py %s" % pkgs)
                    warnings = warnings.split("###")
                    if len(warnings) == 2:
                        installations = warnings[0].split()
                        removals = warnings[1].split()
                        if len(installations) > 0 or len(removals) > 0:
                            Gdk.threads_enter()
                            try:
                                dialog = Gtk.MessageDialog(self.application.window, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.WARNING, Gtk.ButtonsType.OK_CANCEL, None)
                                dialog.set_title("")
                                dialog.set_markup("<b>" + _("This upgrade will trigger additional changes") + "</b>")
                                #dialog.format_secondary_markup("<i>" + _("All available upgrades for this package will be ignored.") + "</i>")
                                dialog.set_icon_name("mintupdate")
                                dialog.set_default_size(320, 400)
                                dialog.set_resizable(True)

                                if len(removals) > 0:
                                    # Removals
                                    label = Gtk.Label()
                                    label.set_text(_("The following packages will be removed:"))
                                    label.set_alignment(0, 0.5)
                                    label.set_padding(20, 0)
                                    scrolledWindow = Gtk.ScrolledWindow()
                                    scrolledWindow.set_shadow_type(Gtk.ShadowType.IN)
                                    scrolledWindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                                    treeview = Gtk.TreeView()
                                    column = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                                    column.set_sort_column_id(0)
                                    column.set_resizable(True)
                                    treeview.append_column(column)
                                    treeview.set_headers_clickable(False)
                                    treeview.set_reorderable(False)
                                    treeview.set_headers_visible(False)
                                    model = Gtk.TreeStore(str)
                                    removals.sort()
                                    for pkg in removals:
                                        iter = model.insert_before(None, None)
                                        model.set_value(iter, 0, pkg)
                                    treeview.set_model(model)
                                    treeview.show()
                                    scrolledWindow.add(treeview)
                                    dialog.get_content_area().pack_start(label, False, False, 0)
                                    dialog.get_content_area().pack_start(scrolledWindow, True, True, 0)
                                    dialog.get_content_area().set_border_width(6)

                                if len(installations) > 0:
                                    # Installations
                                    label = Gtk.Label()
                                    label.set_text(_("The following packages will be installed:"))
                                    label.set_alignment(0, 0.5)
                                    label.set_padding(20, 0)
                                    scrolledWindow = Gtk.ScrolledWindow()
                                    scrolledWindow.set_shadow_type(Gtk.ShadowType.IN)
                                    scrolledWindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                                    treeview = Gtk.TreeView()
                                    column = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                                    column.set_sort_column_id(0)
                                    column.set_resizable(True)
                                    treeview.append_column(column)
                                    treeview.set_headers_clickable(False)
                                    treeview.set_reorderable(False)
                                    treeview.set_headers_visible(False)
                                    model = Gtk.TreeStore(str)
                                    installations.sort()
                                    for pkg in installations:
                                        iter = model.insert_before(None, None)
                                        model.set_value(iter, 0, pkg)
                                    treeview.set_model(model)
                                    treeview.show()
                                    scrolledWindow.add(treeview)
                                    dialog.get_content_area().pack_start(label, False, False, 0)
                                    dialog.get_content_area().pack_start(scrolledWindow, True, True, 0)

                                dialog.show_all()
                                if dialog.run() == Gtk.ResponseType.OK:
                                    proceed = True
                                else:
                                    proceed = False
                                dialog.destroy()
                            except Exception as e:
                                print (e)
                                print(sys.exc_info()[0])
                            Gdk.threads_leave()
                        else:
                            proceed = True
                except Exception as e:
                    print (e)
                    print(sys.exc_info()[0])

            if len(flatpaks) > 0:
                self.application.flatpak_updater.prepare_start_updates(flatpaks)

                if self.application.flatpak_updater.confirm_start():
                    update_flatpaks = True
                else:
                    proceed = False

            if aptInstallNeeded and proceed:
                Gdk.threads_enter()
                self.application.set_status(_("Installing updates"), _("Installing updates"), "mintupdate-installing-symbolic", True)
                Gdk.threads_leave()
                self.application.logger.write("Ready to launch synaptic")
                f = tempfile.NamedTemporaryFile()

                cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                        "--non-interactive", "--parent-window-id", "%s" % self.application.window.get_window().get_xid(), \
                        "-o", "Synaptic::closeZvt=true", "--set-selections-file", "%s" % f.name]

                for pkg in packages:
                    pkg_line = "%s\tinstall\n" % pkg
                    f.write(pkg_line.encode("utf-8"))
                f.flush()

                subprocess.run(["sudo","/usr/lib/linuxmint/mintUpdate/synaptic-workaround.py","enable"])
                try:
                    result = subprocess.run(cmd, stdout=self.application.logger.log, stderr=self.application.logger.log, check=True)
                    returnCode = result.returncode
                except subprocess.CalledProcessError as e:
                    returnCode = e.returncode
                subprocess.run(["sudo","/usr/lib/linuxmint/mintUpdate/synaptic-workaround.py","disable"])
                self.application.logger.write("Return code:" + str(returnCode))
                f.close()

                latest_apt_update = ''
                update_successful = False
                with open("/var/log/apt/history.log", encoding="utf-8") as apt_history:
                    for line in reversed(list(apt_history)):
                        if "Start-Date" in line:
                            break
                        else:
                            latest_apt_update += line
                    if f.name in latest_apt_update and "End-Date" in latest_apt_update:
                        update_successful = True
                        self.application.logger.write("Install finished")
                    else:
                        self.application.logger.write("Install failed")

                if update_successful:
                    # override CacheWatcher since there's a forced refresh later already
                    self.application.cache_watcher.update_cachetime()

                    if self.reboot_required:
                        self.application.reboot_required = True
                    elif self.application.settings.get_boolean("hide-window-after-update"):
                        Gdk.threads_enter()
                        self.application.window.hide()
                        Gdk.threads_leave()

                    if [pkg for pkg in PRIORITY_UPDATES if pkg in packages]:
                        # Restart
                        self.application.logger.write("Mintupdate was updated, restarting it...")
                        self.application.logger.close()
                        os.system("/usr/lib/linuxmint/mintUpdate/mintUpdate.py show &")
                        return

                    # Refresh
                    needs_refresh = True
                else:
                    Gdk.threads_enter()
                    self.application.set_status(_("Could not install the security updates"), _("Could not install the security updates"), "mintupdate-error-symbolic", True)
                    Gdk.threads_leave()

            if update_flatpaks and proceed:
                self.application.flatpak_updater.perform_updates()
                if self.application.flatpak_updater.error != None:
                    Gdk.threads_enter()
                    self.application.set_status_message(self.application.flatpak_updater.error)
                    Gdk.threads_leave()
                needs_refresh = True

            if proceed and len(cinnamon_spices) > 0:
                Gdk.threads_enter()
                spices_install_window = Gtk.Window(title=_("Updating Cinnamon Spices"),
                                           default_width=400,
                                           default_height=100,
                                           deletable=False,
                                           skip_taskbar_hint=True,
                                           skip_pager_hint=True,
                                           resizable=False,
                                           modal=True,
                                           window_position=Gtk.WindowPosition.CENTER_ON_PARENT,
                                           transient_for=self.application.window)
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=10,
                              margin=10,
                              valign=Gtk.Align.CENTER)
                spinner = Gtk.Spinner(active=True, height_request=32)
                box.pack_start(spinner, False, False, 0)
                label = Gtk.Label()
                box.pack_start(label, False, False, 0)
                spices_install_window.add(box)
                spices_install_window.show_all()
                Gdk.threads_leave()

                need_cinnamon_restart = False

                for update in cinnamon_spices:
                    Gdk.threads_enter()
                    label.set_text("%s (%s)" % (update.name, update.uuid))
                    Gdk.threads_leave()
                    self.application.cinnamon_updater.upgrade(update)

                    try:
                        if self.application.cinnamon_updater.spice_is_enabled(update):
                            need_cinnamon_restart = True
                            break
                    except:
                        need_cinnamon_restart = True

                if (not self.reboot_required) \
                        and os.getenv("XDG_CURRENT_DESKTOP") in ["Cinnamon", "X-Cinnamon"] \
                        and need_cinnamon_restart:
                    Gdk.threads_enter()
                    label.set_text(_("Restarting Cinnamon"))
                    spinner.hide()
                    Gdk.threads_leave()

                    # Keep the dialog from looking funky before it freezes during the restart
                    time.sleep(.25)
                    subprocess.run(["cinnamon-dbus-command", "RestartCinnamon", "0"])

                    # We want to be back from the restart before refreshing or else it looks bad. Restarting can
                    # take a bit longer than the restart command before it's properly 'running' again.
                    time.sleep(2)

                Gdk.threads_enter()
                spices_install_window.destroy()
                Gdk.threads_leave()

                needs_refresh = True

            if needs_refresh:
                self.application.refresh()

        except Exception as e:
            print (e)
            self.application.logger.write_error("Exception occurred in the install thread: " + str(sys.exc_info()[0]))
            Gdk.threads_enter()
            self.application.set_status(_("Could not install the security updates"), _("Could not install the security updates"), "mintupdate-error-symbolic", True)
            self.application.logger.write_error("Could not install security updates")
            Gdk.threads_leave()

class RefreshThread(threading.Thread):

    def __init__(self, application, root_mode=False):
        threading.Thread.__init__(self)
        self.root_mode = root_mode
        self.application = application
        self.running = False

    def cleanup(self):
        # cleanup when finished refreshing
        self.application.refreshing = False
        if not self.running:
            return
        self.application.cache_watcher.resume()
        Gdk.threads_enter()
        self.application.status_refreshing_spinner.stop()
        # Make sure we're never stuck on the status_refreshing page:
        if self.application.stack.get_visible_child_name() == "status_refreshing":
            self.application.stack.set_visible_child_name("updates_available")
        # Reset cursor
        if not self.application.app_hidden():
            self.application.window.get_window().set_cursor(None)
        self.application.paned.set_position(self.vpaned_position)
        self.application.toolbar.set_sensitive(True)
        self.application.menubar.set_sensitive(True)
        Gdk.threads_leave()

    def show_window(self):
        Gdk.threads_enter()
        self.application.window.present_with_time(Gtk.get_current_event_time())
        Gdk.threads_leave()

    def on_notification_action(self, notification, action_name, data):
        if action_name == "show_updates":
            os.system("/usr/lib/linuxmint/mintUpdate/mintUpdate.py show &")
        elif action_name == "enable_automatic_updates":
            self.application.open_preferences(None, show_automation=True)

    def run(self):
        if self.application.refreshing:
            return False

        if self.application.updates_inhibited:
            self.application.logger.write("Updates are inhibited, skipping refresh")
            self.show_window()
            return False

        self.application.refreshing = True
        self.running = True

        if self.root_mode:
            while self.application.dpkg_locked():
                self.application.logger.write("Package management system locked by another process, retrying in 60s")
                time.sleep(60)

        self.application.cache_watcher.pause()

        Gdk.threads_enter()
        self.vpaned_position = self.application.paned.get_position()
        for child in self.application.infobar.get_children():
            child.destroy()
        if self.application.reboot_required:
            self.application.show_infobar(_("Reboot required"),
                _("You have installed updates that require a reboot to take effect, please reboot your system as soon as possible."), icon="system-reboot-symbolic")
        Gdk.threads_leave()

        try:
            if self.root_mode:
                self.application.logger.write("Starting refresh (retrieving lists of updates from remote servers)")
            else:
                self.application.logger.write("Starting refresh (local only)")
            Gdk.threads_enter()
            # Switch to status_refreshing page
            self.application.status_refreshing_spinner.start()
            self.application.stack.set_visible_child_name("status_refreshing")
            if not self.application.app_hidden():
                self.application.window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
            self.application.toolbar.set_sensitive(False)
            self.application.menubar.set_sensitive(False)
            self.application.builder.get_object("tool_clear").set_sensitive(False)
            self.application.builder.get_object("tool_select_all").set_sensitive(False)
            self.application.builder.get_object("tool_apply").set_sensitive(False)

            # Starts the blinking
            self.application.set_status(_("Checking for package updates"), _("Checking for updates"), "mintupdate-checking-symbolic", not self.application.settings.get_boolean("hide-systray"))
            Gdk.threads_leave()

            model = Gtk.TreeStore(bool, str, str, str, str, int, str, str, str, str, str, object)
            # UPDATE_CHECKED, UPDATE_DISPLAY_NAME, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_SOURCE,
            # UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ

            model.set_sort_column_id(UPDATE_SORT_STR, Gtk.SortType.ASCENDING)

            # Refresh the APT cache
            if self.root_mode:
                refresh_command = ["sudo", "/usr/bin/mint-refresh-cache"]
                if not self.application.app_hidden():
                    refresh_command.extend(["--use-synaptic",
                                            str(self.application.window.get_window().get_xid())])
                subprocess.run(refresh_command)
                self.application.settings.set_int("refresh-last-run", int(time.time()))

            if CINNAMON_SUPPORT:
                if self.root_mode:
                    self.application.logger.write("Refreshing available Cinnamon updates from the server")
                    self.application.set_status_message(_("Checking for Cinnamon spices"))
                    for spice_type in cinnamon.updates.SPICE_TYPES:
                        try:
                            self.application.cinnamon_updater.refresh_cache_for_type(spice_type)
                        except:
                            self.application.logger.write_error("Something went wrong fetching Cinnamon %ss: %s" % (spice_type, str(sys.exc_info()[0])))
                            print("-- Exception occurred fetching Cinnamon %ss:\n%s" % (spice_type, traceback.format_exc()))

            if FLATPAK_SUPPORT:
                # if self.root_mode:
                self.application.logger.write("Refreshing available Flatpak updates")
                self.application.set_status_message(_("Checking for Flatpak updates"))
                self.application.flatpak_updater.refresh()

            self.application.set_status_message(_("Processing updates"))

            if os.getenv("MINTUPDATE_TEST") == None:
                output = subprocess.run("/usr/lib/linuxmint/mintUpdate/checkAPT.py", stdout=subprocess.PIPE).stdout.decode("utf-8")
            else:
                if os.path.exists("/usr/share/linuxmint/mintupdate/tests/%s.test" % os.getenv("MINTUPDATE_TEST")):
                    output = subprocess.run("sleep 1; cat /usr/share/linuxmint/mintupdate/tests/%s.test" % os.getenv("MINTUPDATE_TEST"), shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
                else:
                    output = subprocess.run("/usr/lib/linuxmint/mintUpdate/checkAPT.py", stdout=subprocess.PIPE).stdout.decode("utf-8")

            error_found = False

            # Return on error
            if "CHECK_APT_ERROR" in output:
                error_found = True
                self.application.logger.write_error("Error in checkAPT.py, could not refresh the list of updates")
                try:
                    error_msg = output.split("Error: ")[1].replace("E:", "\n").strip()
                    if "apt.cache.FetchFailedException" in output and " changed its " in error_msg:
                        error_msg += "\n\n%s" % _("Run 'apt update' in a terminal window to address this")
                except:
                    error_msg = ""

            # Check presence of Mint layer
            (mint_layer_found, error_msg) = self.check_policy()
            if os.getenv("MINTUPDATE_TEST") == "layer-error" or (not mint_layer_found):
                error_found = True
                self.application.logger.write_error("Error: The APT policy is incorrect!")

                label1 = _("Your APT configuration is corrupt.")
                label2 = _("Do not install or update anything, it could break your operating system!")
                label3 = _("To switch to a different Linux Mint mirror and solve this problem, click OK.")

                msg = _("Your APT configuration is corrupt.")
                if error_msg:
                    error_msg = "\n\n%s\n%s" % (_("APT error:"), error_msg)
                else:
                    error_msg = ""
                self.application.show_infobar(_("Please switch to another Linux Mint mirror"),
                    msg, Gtk.MessageType.ERROR,
                    callback=self._on_infobar_mintsources_response)
                self.application.set_status(_("Could not refresh the list of updates"),
                    "%s\n%s" % (label1, label2), "mintupdate-error-symbolic", True)
                self.application.builder.get_object("label_error_details").set_markup("<b>%s\n%s\n%s%s</b>" % (label1, label2, label3, error_msg))

            if error_found:
                Gdk.threads_enter()
                self.application.set_status(_("Could not refresh the list of updates"),
                    "%s%s%s" % (_("Could not refresh the list of updates"), "\n\n" if error_msg else "", error_msg),
                    "mintupdate-error-symbolic", True)
                self.application.stack.set_visible_child_name("status_error")
                self.application.builder.get_object("label_error_details").set_text(error_msg)
                self.application.builder.get_object("label_error_details").show()
                Gdk.threads_leave()
                self.cleanup()
                return False

            # Look at the updates one by one
            num_visible = 0
            num_security = 0
            num_software = 0
            download_size = 0
            is_self_update = False
            tracker = UpdateTracker(self.application.settings, self.application.logger)
            lines = output.split("---EOL---")
            if len(lines):
                for line in lines:
                    if not "###" in line:
                        continue

                    # Create update object
                    update = Update(package=None, input_string=line, source_name=None)

                    if tracker.active and update.type != "unstable":
                        tracker.update(update)

                    # Check if self-update is needed
                    if update.source_name in PRIORITY_UPDATES:
                        is_self_update = True

                    iter = model.insert_before(None, None)
                    model.row_changed(model.get_path(iter), iter)

                    model.set_value(iter, UPDATE_CHECKED, True)
                    download_size += update.size

                    shortdesc = update.short_description
                    if len(shortdesc) > 100:
                        try:
                            shortdesc = shortdesc[:100]
                            # Remove the last word.. in case we chomped
                            # a word containing an &#234; character..
                            # if we ended up with &.. without the code and ; sign
                            # pango would fail to set the markup
                            words = shortdesc.split()
                            shortdesc = " ".join(words[:-1]) + "..."
                        except:
                            pass

                    if self.application.settings.get_boolean("show-descriptions"):
                        model.set_value(iter, UPDATE_DISPLAY_NAME,
                                        "<b>%s</b>\n%s" % (GLib.markup_escape_text(update.display_name), GLib.markup_escape_text(shortdesc)))
                    else:
                        model.set_value(iter, UPDATE_DISPLAY_NAME,
                                        "<b>%s</b>" % GLib.markup_escape_text(update.display_name))

                    origin = update.origin
                    origin = origin.replace("linuxmint", "Linux Mint").replace("ubuntu", "Ubuntu").replace("LP-PPA-", "PPA ").replace("debian", "Debian")

                    type_sort_key = 0 # Used to sort by type
                    if update.type == "kernel":
                        tooltip = _("Kernel update")
                        type_sort_key = 2
                        num_security += 1
                    elif update.type == "security":
                        tooltip = _("Security update")
                        type_sort_key = 1
                        num_security += 1
                    elif update.type == "unstable":
                        tooltip = _("Unstable software. Only apply this update to help developers beta-test new software.")
                        type_sort_key = 7
                    else:
                        num_software += 1
                        if origin in ["Ubuntu", "Debian", "Linux Mint", "Canonical"]:
                            tooltip = _("Software update")
                            type_sort_key = 3
                        else:
                            update.type = "3rd-party"
                            tooltip = "%s\n%s" % (_("3rd-party update"), origin)
                            type_sort_key = 4

                    model.set_value(iter, UPDATE_OLD_VERSION, update.old_version)
                    model.set_value(iter, UPDATE_NEW_VERSION, update.new_version)
                    model.set_value(iter, UPDATE_SOURCE, "%s / %s" % (origin, update.archive))
                    model.set_value(iter, UPDATE_SIZE, update.size)
                    model.set_value(iter, UPDATE_SIZE_STR, size_to_string(update.size))
                    model.set_value(iter, UPDATE_TYPE_PIX, "mintupdate-type-%s-symbolic" % update.type)
                    model.set_value(iter, UPDATE_TYPE, update.type)
                    model.set_value(iter, UPDATE_TOOLTIP, tooltip)
                    model.set_value(iter, UPDATE_SORT_STR, "%s%s" % (str(type_sort_key), update.display_name))
                    model.set_value(iter, UPDATE_OBJ, update)
                    num_visible += 1

            if CINNAMON_SUPPORT and not is_self_update:
                type_sort_key = 6
                blacklist = self.application.settings.get_strv("blacklisted-packages")

                for update in self.application.cinnamon_updater.get_updates():
                    update.real_source_name = update.uuid
                    update.source_packages = ["%s=%s" % (update.uuid, update.new_version)]
                    update.package_names = []
                    update.type = "cinnamon"
                    if update.uuid in blacklist or update.source_packages[0] in blacklist:
                        continue
                    if update.spice_type == cinnamon.SPICE_TYPE_APPLET:
                        tooltip = _("Cinnamon applet")
                    elif update.spice_type == cinnamon.SPICE_TYPE_DESKLET:
                        tooltip = _("Cinnamon desklet")
                    elif update.spice_type == cinnamon.SPICE_TYPE_THEME:
                        tooltip = _("Cinnamon theme")
                    else:
                        tooltip = _("Cinnamon extension")

                    if tracker.active:
                        tracker.update(update)

                    iter = model.insert_before(None, None)
                    model.row_changed(model.get_path(iter), iter)

                    model.set_value(iter, UPDATE_CHECKED, True)

                    if self.application.settings.get_boolean("show-descriptions"):
                        model.set_value(iter, UPDATE_DISPLAY_NAME, "<b>%s</b>\n%s" % (GLib.markup_escape_text(update.uuid),
                                                                                      GLib.markup_escape_text(update.name)))
                    else:
                        model.set_value(iter, UPDATE_DISPLAY_NAME, "<b>%s</b>" % GLib.markup_escape_text(update.uuid))

                    model.set_value(iter, UPDATE_OLD_VERSION, update.old_version)
                    model.set_value(iter, UPDATE_NEW_VERSION, update.new_version)
                    model.set_value(iter, UPDATE_SOURCE, "Linux Mint / cinnamon")
                    model.set_value(iter, UPDATE_SIZE, update.size)
                    model.set_value(iter, UPDATE_SIZE_STR, size_to_string(update.size))
                    model.set_value(iter, UPDATE_TYPE_PIX, "cinnamon-symbolic")
                    model.set_value(iter, UPDATE_TYPE, "cinnamon")
                    model.set_value(iter, UPDATE_TOOLTIP, tooltip)
                    model.set_value(iter, UPDATE_SORT_STR, "%s%s" % (str(type_sort_key), update.uuid))
                    model.set_value(iter, UPDATE_OBJ, update)
                    num_software += 1
                    num_visible += 1
                    download_size += update.size

            if FLATPAK_SUPPORT and self.application.flatpak_updater and not is_self_update:
                type_sort_key = 5
                blacklist = self.application.settings.get_strv("blacklisted-packages")

                self.application.flatpak_updater.fetch_updates()
                if self.application.flatpak_updater.error == None:
                    for update in self.application.flatpak_updater.updates:
                        update.type = "flatpak"
                        if update.ref_str in blacklist or update.source_packages[0] in blacklist:
                            continue
                        if update.flatpak_type == "app":
                            tooltip = _("Flatpak application")
                        else:
                            tooltip = _("Flatpak runtime")

                        if tracker.active:
                            tracker.update(update)

                        iter = model.insert_before(None, None)
                        model.row_changed(model.get_path(iter), iter)

                        model.set_value(iter, UPDATE_CHECKED, True)

                        if self.application.settings.get_boolean("show-descriptions"):
                            model.set_value(iter, UPDATE_DISPLAY_NAME, "<b>%s</b>\n%s" % (GLib.markup_escape_text(update.name),
                                                                                          GLib.markup_escape_text(update.summary)))
                        else:
                            model.set_value(iter, UPDATE_DISPLAY_NAME, "<b>%s</b>" % GLib.markup_escape_text(update.name))

                        model.set_value(iter, UPDATE_OLD_VERSION, update.old_version)
                        model.set_value(iter, UPDATE_NEW_VERSION, update.new_version)
                        model.set_value(iter, UPDATE_SOURCE, update.origin)
                        model.set_value(iter, UPDATE_SIZE, update.size)
                        model.set_value(iter, UPDATE_SIZE_STR, size_to_string(update.size))
                        model.set_value(iter, UPDATE_TYPE_PIX, "mintupdate-type-flatpak-symbolic")
                        model.set_value(iter, UPDATE_TYPE, "flatpak")
                        model.set_value(iter, UPDATE_TOOLTIP, tooltip)
                        model.set_value(iter, UPDATE_SORT_STR, "%s%s" % (str(type_sort_key), update.ref_str))
                        model.set_value(iter, UPDATE_OBJ, update)
                        num_software += 1
                        num_visible += 1
                        download_size += update.size

            if tracker.active:
                if tracker.notify():
                    Gdk.threads_enter()
                    notification_title = _("Updates are available")
                    security_msg = gettext.ngettext("%d security update", "%d security updates", num_security) % num_security
                    software_msg = gettext.ngettext("%d software update", "%d software updates", num_software) % num_software
                    msg = ""
                    if num_security > 0:
                        msg = "%s\n" % security_msg
                    if num_software > 0:
                        msg = "%s%s\n" % (msg, software_msg)
                    msg = "%s\n%s" % (msg, _("Apply them to keep your operating system safe and up to date."))

                    # We use self.notification (instead of just a variable) to keep a memory pointer
                    # on the notification. Without doing this, the callbacks are never executed by Gtk/Notify.
                    self.notification = Notify.Notification.new(notification_title, msg, "mintupdate-updates-available-symbolic")
                    self.notification.set_urgency(2)
                    self.notification.set_timeout(Notify.EXPIRES_NEVER)
                    self.notification.add_action("show_updates", _("View updates"), self.on_notification_action, None)
                    self.notification.add_action("enable_automatic_updates", _("Enable automatic updates"), self.on_notification_action, None)
                    self.notification.show()
                    Gdk.threads_leave()
                tracker.record()

            Gdk.threads_enter()
            # Updates found, update status message
            if num_visible > 0:
                self.application.logger.write("Found %d software updates" % num_visible)
                if is_self_update:
                    self.application.stack.set_visible_child_name("status_self-update")
                    self.application.statusbar.set_visible(False)
                    status_string = ""
                else:
                    status_string = gettext.ngettext("%(selected)d update selected (%(size)s)",
                                            "%(selected)d updates selected (%(size)s)", num_visible) % \
                                            {'selected':num_visible, 'size':size_to_string(download_size)}
                    self.application.builder.get_object("tool_clear").set_sensitive(True)
                    self.application.builder.get_object("tool_select_all").set_sensitive(True)
                    self.application.builder.get_object("tool_apply").set_sensitive(True)
                systray_tooltip = gettext.ngettext("%d update available", "%d updates available", num_visible) % num_visible
                self.application.set_status(status_string, systray_tooltip, "mintupdate-updates-available-symbolic", True)
            else:
                self.application.logger.write("System is up to date")
                self.application.stack.set_visible_child_name("status_updated")
                self.application.set_status("", _("Your system is up to date"), "mintupdate-up-to-date-symbolic",
                                            not self.application.settings.get_boolean("hide-systray"))

            if FLATPAK_SUPPORT and self.application.flatpak_updater.error != None and not is_self_update:
                self.application.logger.write("Could not check for flatpak updates: %s" % self.application.flatpak_updater.error)
                msg = _("Error checking for flatpak updates: %s") % self.application.flatpak_updater.error
                self.application.set_status_message(msg)

            self.application.builder.get_object("notebook_details").set_current_page(0)
            self.application.treeview.set_model(model)
            del model
            Gdk.threads_leave()

            # Check whether to display the mirror infobar
            self.mirror_check()

            self.application.logger.write("Refresh finished")
        except:
            print("-- Exception occurred in the refresh thread:\n%s" % traceback.format_exc())
            self.application.logger.write_error("Exception occurred in the refresh thread: %s" % str(sys.exc_info()[0]))
            Gdk.threads_enter()
            self.application.set_status(_("Could not refresh the list of updates"),
                                        _("Could not refresh the list of updates"), "mintupdate-error-symbolic", True)
            Gdk.threads_leave()

        finally:
            self.cleanup()

    def check_policy(self):
        """ Check the presence of the Mint layer """
        p = subprocess.run(['apt-cache', 'policy'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"LC_ALL": "C"})
        output = p.stdout.decode()
        if p.stderr:
            error_msg = p.stderr.decode().strip()
            self.application.logger.write_error("APT policy error:\n%s" % error_msg)
        else:
            error_msg = None
        mint_layer_found = False
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("700") and line.endswith("Packages") and "/upstream" in line:
                mint_layer_found = True
                break
        return (mint_layer_found, error_msg)

    def mirror_check(self):
        """ Mirror-related notifications """
        infobar_message = None
        infobar_message_type = Gtk.MessageType.WARNING
        infobar_callback = self._on_infobar_mintsources_response
        try:
            if os.path.exists("/usr/bin/mintsources") and os.path.exists("/etc/apt/sources.list.d/official-package-repositories.list"):
                mirror_url = None

                codename = subprocess.check_output("lsb_release -cs", shell = True).strip().decode("UTF-8")
                with open("/etc/apt/sources.list.d/official-package-repositories.list", 'r') as sources_file:
                    for line in sources_file:
                        line = line.strip()
                        if line.startswith("deb ") and "%s main upstream import" % codename in line:
                            mirror_url = line.split()[1]
                            if mirror_url.endswith("/"):
                                mirror_url = mirror_url[:-1]
                            break
                if mirror_url is None or not mirror_url.startswith("http"):
                    # The Mint mirror being used either cannot be found or is not an HTTP(s) mirror
                    pass
                elif mirror_url == "http://packages.linuxmint.com":
                    if not self.application.settings.get_boolean("default-repo-is-ok"):
                        infobar_title = _("Do you want to switch to a local mirror?")
                        infobar_message = _("Local mirrors are usually faster than packages.linuxmint.com.")
                        infobar_message_type = Gtk.MessageType.QUESTION
                elif not self.application.app_hidden():
                    # Only perform up-to-date checks when refreshing from the UI (keep the load lower on servers)
                    mint_timestamp = self.get_url_last_modified("http://packages.linuxmint.com/db/version")
                    mirror_timestamp = self.get_url_last_modified("%s/db/version" % mirror_url)
                    if mirror_timestamp is None:
                        if mint_timestamp is None:
                            # Both default repo and mirror are unreachable, assume there's no Internet connection
                            pass
                        else:
                            infobar_title = _("Please switch to another mirror")
                            infobar_message = _("%s is unreachable.") % mirror_url
                    elif mint_timestamp is not None:
                        mint_date = datetime.datetime.fromtimestamp(mint_timestamp)
                        now = datetime.datetime.now()
                        mint_age = (now - mint_date).days
                        if (mint_age > 2):
                            mirror_date = datetime.datetime.fromtimestamp(mirror_timestamp)
                            mirror_age = (mint_date - mirror_date).days
                            if (mirror_age > 2):
                                infobar_title = _("Please switch to another mirror")
                                infobar_message = gettext.ngettext("The last update on %(mirror)s was %(days)d day ago.",
                                                            "The last update on %(mirror)s was %(days)d days ago.",
                                                            (now - mirror_date).days) % \
                                                            {'mirror': mirror_url, 'days': (now - mirror_date).days}
        except:
            print(sys.exc_info()[0])
            # best effort, just print out the error
            print("An exception occurred while checking if the repositories were up to date: %s" % sys.exc_info()[0])

        if infobar_message:
            Gdk.threads_enter()
            self.application.show_infobar(infobar_title,
                                            infobar_message,
                                            infobar_message_type,
                                            callback=infobar_callback)
            Gdk.threads_leave()

    def _on_infobar_mintsources_response(self, infobar, response_id):
        infobar.destroy()
        if response_id == Gtk.ResponseType.NO:
            self.application.settings.set_boolean("default-repo-is-ok", True)
        else:
            subprocess.Popen(["pkexec", "mintsources"])

    def get_url_last_modified(self, url):
        try:
            c = pycurl.Curl()
            c.setopt(pycurl.URL, url)
            c.setopt(pycurl.CONNECTTIMEOUT, 5)
            c.setopt(pycurl.TIMEOUT, 30)
            c.setopt(pycurl.FOLLOWLOCATION, 1)
            c.setopt(pycurl.NOBODY, 1)
            c.setopt(pycurl.OPT_FILETIME, 1)
            c.perform()
            filetime = c.getinfo(pycurl.INFO_FILETIME)
            if filetime < 0:
                return None
            else:
                return filetime
        except Exception as e:
            print (e)
            return None

    def checkDependencies(self, changes, cache):
        foundSomething = False
        for pkg in changes:
            for dep in pkg.candidateDependencies:
                for o in dep.or_dependencies:
                    try:
                        if cache[o.name].isUpgradable:
                            pkgFound = False
                            for pkg2 in changes:
                                if o.name == pkg2.name:
                                    pkgFound = True
                            if pkgFound == False:
                                newPkg = cache[o.name]
                                changes.append(newPkg)
                                foundSomething = True
                    except Exception as e:
                        print (e)
                        pass # don't know why we get these..
        if (foundSomething):
            changes = self.checkDependencies(changes, cache)
        return changes

class Logger():

    def __init__(self):
        self.logdir = os.path.join(tempfile.gettempdir(), "mintUpdate/")
        self._create_log()
        self.hook = None

    def _create_log(self):
        if not os.path.exists(self.logdir):
            os.umask(0)
            os.makedirs(self.logdir)
        self.log = tempfile.NamedTemporaryFile(mode="w", prefix=self.logdir, delete=False)
        try:
            os.chmod(self.log.name, 0o666)
        except:
            traceback.print_exc()

    def _log_ready(self):
        if self.log.closed:
            return False
        if not os.path.exists(self.log.name):
            self.log.close()
            self._create_log()
        return True

    def _write(self, line):
        if self._log_ready():
            self.log.write(line)
            self.log.flush()
        if self.hook:
            self.hook(line)

    def write(self, line):
        self._write("%s ++ %s\n" % (datetime.datetime.now().strftime('%m.%d@%H:%M'), line))

    def write_error(self, line):
        self._write("%s -- %s\n" % (datetime.datetime.now().strftime('%m.%d@%H:%M'), line))

    def read(self):
        if not os.path.exists(self.log.name):
            self._create_log()
            return ""
        else:
            with open(self.log.name) as f:
                return f.read()

    def close(self):
        self.log.close()

    def set_hook(self, callback):
        self.hook = callback

    def remove_hook(self):
        self.hook = None

class XAppStatusIcon():

    def __init__(self, menu):
        self.icon = XApp.StatusIcon()
        self.icon.set_secondary_menu(menu)

    def set_from_icon_name(self, name):
        self.icon.set_icon_name(name)

    def set_tooltip_text(self, text):
        self.icon.set_tooltip_text(text)

    def set_visible(self, visible):
        self.icon.set_visible(visible)

class MintUpdate():

    def __init__(self):
        Gdk.threads_init()
        self.information_window_showing = False
        self.history_window_showing = False
        self.preferences_window_showing = False
        self.updates_inhibited = False
        self.reboot_required = False
        self.refreshing = False
        self.logger = Logger()
        self.logger.write("Launching Update Manager")
        self.settings = Gio.Settings(schema_id="com.linuxmint.updates")

        #Set the Glade file
        gladefile = "/usr/share/linuxmint/mintupdate/main.ui"
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintupdate")
        self.builder.add_from_file(gladefile)
        self.statusbar = self.builder.get_object("statusbar")
        self.context_id = self.statusbar.get_context_id("mintUpdate")
        self.window = self.builder.get_object("main_window")
        self.window.connect("key-press-event",self.on_key_press_event)
        self.treeview = self.builder.get_object("treeview_update")
        self.stack = Gtk.Stack()
        self.builder.get_object("stack_container").pack_start(self.stack, True, True, 0)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(175)

        try:
            self.window.set_title(_("Update Manager"))

            self.window.set_icon_name("mintupdate")

            accel_group = Gtk.AccelGroup()
            self.window.add_accel_group(accel_group)

            self.toolbar = self.builder.get_object("toolbar1")
            self.menubar = self.builder.get_object("menubar1")

            self.notebook_details = self.builder.get_object("notebook_details")
            self.textview_packages = self.builder.get_object("textview_packages").get_buffer()

            self.textview_description = self.builder.get_object("textview_description").get_buffer()
            self.textview_changes = self.builder.get_object("textview_changes").get_buffer()

            self.paned = self.builder.get_object("paned1")

            # Welcome page
            welcome_page = self.builder.get_object("welcome_page")
            self.stack.add_named(welcome_page, "welcome")
            self.builder.get_object("button_welcome_finish").connect("clicked", self.on_welcome_page_finished)
            self.builder.get_object("button_welcome_help").connect("clicked", self.show_help)

            # Updates page
            updates_page = self.builder.get_object("updates_page")
            self.stack.add_named(updates_page, "updates_available")

            # the infobar container
            self.infobar = self.builder.get_object("hbox_infobar")

            # the treeview
            cr = Gtk.CellRendererToggle()
            cr.connect("toggled", self.toggled)
            cr.set_property("activatable", True)

            column_upgrade = Gtk.TreeViewColumn(_("Upgrade"), cr)
            column_upgrade.add_attribute(cr, "active", UPDATE_CHECKED)
            column_upgrade.set_sort_column_id(UPDATE_CHECKED)
            column_upgrade.set_resizable(True)

            column_name = Gtk.TreeViewColumn(_("Name"), Gtk.CellRendererText(), markup=UPDATE_DISPLAY_NAME)
            column_name.set_sort_column_id(UPDATE_DISPLAY_NAME)
            column_name.set_resizable(True)

            column_old_version = Gtk.TreeViewColumn(_("Old Version"), Gtk.CellRendererText(), text=UPDATE_OLD_VERSION)
            column_old_version.set_sort_column_id(UPDATE_OLD_VERSION)
            column_old_version.set_resizable(True)

            column_new_version = Gtk.TreeViewColumn(_("New Version"), Gtk.CellRendererText(), text=UPDATE_NEW_VERSION)
            column_new_version.set_sort_column_id(UPDATE_NEW_VERSION)
            column_new_version.set_resizable(True)

            column_size = Gtk.TreeViewColumn(_("Size"), Gtk.CellRendererText(), text=UPDATE_SIZE_STR)
            column_size.set_sort_column_id(UPDATE_SIZE)
            column_size.set_resizable(True)

            column_type = Gtk.TreeViewColumn(_("Type"), Gtk.CellRendererPixbuf(), icon_name=UPDATE_TYPE_PIX)
            column_type.set_sort_column_id(UPDATE_TYPE)
            column_type.set_resizable(True)

            column_origin = Gtk.TreeViewColumn(_("Origin"), Gtk.CellRendererText(), text=UPDATE_SOURCE)
            column_origin.set_sort_column_id(UPDATE_SOURCE)
            column_origin.set_resizable(True)

            self.treeview.set_tooltip_column(UPDATE_TOOLTIP)

            self.treeview.append_column(column_type)
            self.treeview.append_column(column_upgrade)
            self.treeview.append_column(column_name)
            self.treeview.append_column(column_old_version)
            self.treeview.append_column(column_new_version)
            self.treeview.append_column(column_origin)
            self.treeview.append_column(column_size)

            self.treeview.set_headers_clickable(True)
            self.treeview.set_reorderable(False)
            self.treeview.show()

            self.treeview.connect("button-release-event", self.treeview_right_clicked)
            self.treeview.connect("row-activated", self.treeview_row_activated)

            selection = self.treeview.get_selection()
            selection.connect("changed", self.display_selected_update)
            self.builder.get_object("notebook_details").connect("switch-page", self.switch_page)
            self.window.connect("delete_event", self.close_window)

            # Install Updates button
            self.install_button = self.builder.get_object("tool_apply")
            self.install_button.connect("clicked", self.install)
            key, mod = Gtk.accelerator_parse("<Control>I")
            self.install_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

            # Clear button
            clear_button = self.builder.get_object("tool_clear")
            clear_button.connect("clicked", self.clear)
            key, mod = Gtk.accelerator_parse("<Control><Shift>A")
            clear_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

            # Select All button
            select_all_button = self.builder.get_object("tool_select_all")
            select_all_button.connect("clicked", self.select_all)
            key, mod = Gtk.accelerator_parse("<Control>A")
            select_all_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

            # Refresh button
            refresh_button = self.builder.get_object("tool_refresh")
            refresh_button.connect("clicked", self.force_refresh)
            key, mod = Gtk.accelerator_parse("<Control>R")
            refresh_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

            # Self-update page:
            self.builder.get_object("confirm-self-update").connect("clicked", self.self_update)

            # Refreshing page spinner:
            self.status_refreshing_spinner = self.builder.get_object("status_refreshing_spinner")

            # Tray icon menu
            menu = Gtk.Menu()
            image = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU)
            menuItem3 = Gtk.ImageMenuItem(label=_("Refresh"), image=image)
            menuItem3.connect('activate', self.force_refresh)
            menu.append(menuItem3)
            image = Gtk.Image.new_from_icon_name("dialog-information-symbolic", Gtk.IconSize.MENU)
            menuItem2 = Gtk.ImageMenuItem(label=_("Information"), image=image)
            menuItem2.connect('activate', self.open_information)
            menu.append(menuItem2)
            image = Gtk.Image.new_from_icon_name("preferences-other-symbolic", Gtk.IconSize.MENU)
            menuItem4 = Gtk.ImageMenuItem(label=_("Preferences"), image=image)
            menuItem4.connect('activate', self.open_preferences)
            menu.append(menuItem4)
            image = Gtk.Image.new_from_icon_name("application-exit-symbolic", Gtk.IconSize.MENU)
            menuItem = Gtk.ImageMenuItem(label=_("Quit"), image=image)
            menuItem.connect('activate', self.quit)
            menu.append(menuItem)
            menu.show_all()

            self.statusIcon = XAppStatusIcon(menu)
            self.statusIcon.icon.connect('activate', self.on_statusicon_activated)

            self.set_status("", _("Checking for updates"), "mintupdate-checking-symbolic", not self.settings.get_boolean("hide-systray"))

            # Main window menu
            fileMenu = Gtk.MenuItem.new_with_mnemonic(_("_File"))
            fileSubmenu = Gtk.Menu()
            fileMenu.set_submenu(fileSubmenu)
            image = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
            closeMenuItem = Gtk.ImageMenuItem(label=_("Close window"), image=image)
            closeMenuItem.connect("activate", self.hide_main_window)
            key, mod = Gtk.accelerator_parse("<Control>W")
            closeMenuItem.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            fileSubmenu.append(closeMenuItem)
            fileSubmenu.append(Gtk.SeparatorMenuItem())
            image = Gtk.Image.new_from_icon_name("application-exit-symbolic", Gtk.IconSize.MENU)
            quitMenuItem = Gtk.ImageMenuItem(label=_("Quit"), image=image)
            quitMenuItem.connect('activate', self.quit)
            key, mod = Gtk.accelerator_parse("<Control>Q")
            quitMenuItem.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            fileSubmenu.append(quitMenuItem)

            editMenu = Gtk.MenuItem.new_with_mnemonic(_("_Edit"))
            editSubmenu = Gtk.Menu()
            editMenu.set_submenu(editSubmenu)
            image = Gtk.Image.new_from_icon_name("preferences-other-symbolic", Gtk.IconSize.MENU)
            prefsMenuItem = Gtk.ImageMenuItem(label=_("Preferences"), image=image)
            prefsMenuItem.connect("activate", self.open_preferences)
            editSubmenu.append(prefsMenuItem)
            if os.path.exists("/usr/bin/timeshift-gtk"):
                image = Gtk.Image.new_from_icon_name("document-open-recent-symbolic", Gtk.IconSize.MENU)
                sourcesMenuItem = Gtk.ImageMenuItem(label=_("System Snapshots"), image=image)
                sourcesMenuItem.connect("activate", self.open_timeshift)
                editSubmenu.append(sourcesMenuItem)
            if os.path.exists("/usr/bin/mintsources"):
                image = Gtk.Image.new_from_icon_name("system-software-install-symbolic", Gtk.IconSize.MENU)
                sourcesMenuItem = Gtk.ImageMenuItem(label=_("Software Sources"), image=image)
                sourcesMenuItem.connect("activate", self.open_repositories)
                editSubmenu.append(sourcesMenuItem)

            rel_edition = 'unknown'
            rel_codename = 'unknown'
            if os.path.exists("/etc/linuxmint/info"):
                with open("/etc/linuxmint/info", encoding="utf-8") as info:
                    for line in info:
                        line = line.strip()
                        if "EDITION=" in line:
                            rel_edition = line.split('=')[1].replace('"', '').split()[0]
                        if "CODENAME=" in line:
                            rel_codename = line.split('=')[1].replace('"', '').split()[0]

            rel_path = "/usr/share/mint-upgrade-info/%s/info" % rel_codename
            if os.path.exists(rel_path):
                config = configparser.ConfigParser()
                config.read(rel_path)
                if rel_edition.lower() in config['general']['editions']:
                    rel_target = config['general']['target_name']
                    image = Gtk.Image.new_from_icon_name("mintupdate-type-package-symbolic", Gtk.IconSize.MENU)
                    relUpgradeMenuItem = Gtk.ImageMenuItem(label=_("Upgrade to %s") % rel_target, image=image)
                    relUpgradeMenuItem.connect("activate", self.open_rel_upgrade)
                    editSubmenu.append(relUpgradeMenuItem)

            viewMenu = Gtk.MenuItem.new_with_mnemonic(_("_View"))
            viewSubmenu = Gtk.Menu()
            viewMenu.set_submenu(viewSubmenu)
            image = Gtk.Image.new_from_icon_name("document-open-recent-symbolic", Gtk.IconSize.MENU)
            historyMenuItem = Gtk.ImageMenuItem(label=_("History of Updates"), image=image )
            historyMenuItem.connect("activate", self.open_history)
            image = Gtk.Image.new_from_icon_name("system-run-symbolic", Gtk.IconSize.MENU)
            kernelMenuItem = Gtk.ImageMenuItem(label=_("Linux Kernels"), image=image)
            kernelMenuItem.connect("activate", self.open_kernels)
            image = Gtk.Image.new_from_icon_name("dialog-information-symbolic", Gtk.IconSize.MENU)
            infoMenuItem = Gtk.ImageMenuItem(label=_("Information"), image=image)
            infoMenuItem.connect("activate", self.open_information)
            image = Gtk.Image.new_from_icon_name("dialog-information-symbolic", Gtk.IconSize.MENU)
            visibleColumnsMenuItem = Gtk.ImageMenuItem(label=_("Visible Columns"), image=image)
            visibleColumnsMenu = Gtk.Menu()
            visibleColumnsMenuItem.set_submenu(visibleColumnsMenu)

            typeColumnMenuItem = Gtk.CheckMenuItem(label=_("Type"))
            typeColumnMenuItem.set_active(self.settings.get_boolean("show-type-column"))
            column_type.set_visible(self.settings.get_boolean("show-type-column"))
            typeColumnMenuItem.connect("toggled", self.setVisibleColumn, column_type, "show-type-column")
            visibleColumnsMenu.append(typeColumnMenuItem)

            packageColumnMenuItem = Gtk.CheckMenuItem(label=_("Package"))
            packageColumnMenuItem.set_active(self.settings.get_boolean("show-package-column"))
            column_name.set_visible(self.settings.get_boolean("show-package-column"))
            packageColumnMenuItem.connect("toggled", self.setVisibleColumn, column_name, "show-package-column")
            visibleColumnsMenu.append(packageColumnMenuItem)

            oldVersionColumnMenuItem = Gtk.CheckMenuItem(label=_("Old Version"))
            oldVersionColumnMenuItem.set_active(self.settings.get_boolean("show-old-version-column"))
            column_old_version.set_visible(self.settings.get_boolean("show-old-version-column"))
            oldVersionColumnMenuItem.connect("toggled", self.setVisibleColumn, column_old_version, "show-old-version-column")
            visibleColumnsMenu.append(oldVersionColumnMenuItem)

            newVersionColumnMenuItem = Gtk.CheckMenuItem(label=_("New Version"))
            newVersionColumnMenuItem.set_active(self.settings.get_boolean("show-new-version-column"))
            column_new_version.set_visible(self.settings.get_boolean("show-new-version-column"))
            newVersionColumnMenuItem.connect("toggled", self.setVisibleColumn, column_new_version, "show-new-version-column")
            visibleColumnsMenu.append(newVersionColumnMenuItem)

            sizeColumnMenuItem = Gtk.CheckMenuItem(label=_("Origin"))
            sizeColumnMenuItem.set_active(self.settings.get_boolean("show-origin-column"))
            column_origin.set_visible(self.settings.get_boolean("show-origin-column"))
            sizeColumnMenuItem.connect("toggled", self.setVisibleColumn, column_origin, "show-origin-column")
            visibleColumnsMenu.append(sizeColumnMenuItem)

            sizeColumnMenuItem = Gtk.CheckMenuItem(label=_("Size"))
            sizeColumnMenuItem.set_active(self.settings.get_boolean("show-size-column"))
            column_size.set_visible(self.settings.get_boolean("show-size-column"))
            sizeColumnMenuItem.connect("toggled", self.setVisibleColumn, column_size, "show-size-column")
            visibleColumnsMenu.append(sizeColumnMenuItem)

            viewSubmenu.append(visibleColumnsMenuItem)

            descriptionsMenuItem = Gtk.CheckMenuItem(label=_("Show Descriptions"))
            descriptionsMenuItem.set_active(self.settings.get_boolean("show-descriptions"))
            descriptionsMenuItem.connect("toggled", self.setVisibleDescriptions)
            viewSubmenu.append(descriptionsMenuItem)

            viewSubmenu.append(historyMenuItem)

            try:
                # Only support kernel selection in Linux Mint (not LMDE)
                release_info = subprocess.run(["lsb_release", "-irs"], stdout=subprocess.PIPE).stdout.decode().split("\n")
                if release_info[0].lower() == "linuxmint" and float(release_info[1]) >= 13:
                    viewSubmenu.append(kernelMenuItem)
            except Exception as e:
                print (e)
                print(sys.exc_info()[0])
            viewSubmenu.append(infoMenuItem)
            helpMenu = Gtk.MenuItem.new_with_mnemonic(_("_Help"))
            helpSubmenu = Gtk.Menu()
            helpMenu.set_submenu(helpSubmenu)

            image = Gtk.Image.new_from_icon_name("security-high-symbolic", Gtk.IconSize.MENU)
            helpMenuItem = Gtk.ImageMenuItem(label=_("Welcome Screen"), image=image)
            helpMenuItem.connect("activate", self.show_welcome_page)
            helpSubmenu.append(helpMenuItem)
            if (Gtk.check_version(3,20,0) is None):
                image = Gtk.Image.new_from_icon_name("preferences-desktop-keyboard-shortcuts-symbolic", Gtk.IconSize.MENU)
                shortcutsMenuItem = Gtk.ImageMenuItem(label=_("Keyboard Shortcuts"), image=image)
                shortcutsMenuItem.connect("activate", self.open_shortcuts)
                helpSubmenu.append(shortcutsMenuItem)
            image = Gtk.Image.new_from_icon_name("help-contents-symbolic", Gtk.IconSize.MENU)
            helpMenuItem = Gtk.ImageMenuItem(label=_("Contents"), image=image)
            helpMenuItem.connect("activate", self.open_help)
            key, mod = Gtk.accelerator_parse("F1")
            helpMenuItem.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            helpSubmenu.append(helpMenuItem)
            image = Gtk.Image.new_from_icon_name("help-about-symbolic", Gtk.IconSize.MENU)
            aboutMenuItem = Gtk.ImageMenuItem(label=_("About"), image=image)
            aboutMenuItem.connect("activate", self.open_about)
            helpSubmenu.append(aboutMenuItem)

            self.menubar.append(fileMenu)
            self.menubar.append(editMenu)
            self.menubar.append(viewMenu)
            self.menubar.append(helpMenu)

            # Status pages
            self.stack.add_named(self.builder.get_object("status_updated"), "status_updated")
            self.stack.add_named(self.builder.get_object("status_error"), "status_error")
            self.stack.add_named(self.builder.get_object("status_self-update"), "status_self-update")
            self.stack.add_named(self.builder.get_object("status_refreshing"), "status_refreshing")
            self.stack.set_visible_child_name("status_refreshing")
            self.stack.show_all()

            vbox = self.builder.get_object("vbox_main")
            vbox.show_all()

            if len(sys.argv) > 1:
                showWindow = sys.argv[1]
                if showWindow == "show":
                    self.window.present_with_time(Gtk.get_current_event_time())

            if CINNAMON_SUPPORT:
                self.cinnamon_updater = cinnamon.UpdateManager()
            else:
                self.cinnamon_updater = None

            global FLATPAK_SUPPORT
            if FLATPAK_SUPPORT:
                try:
                    self.flatpak_updater = flatpakUpdater.FlatpakUpdater()
                except Exception as e:
                    print("Error creating FlatpakUpdater:", str(e))
                    self.flatpak_updater = None
                    FLATPAK_SUPPORT = False

            if self.settings.get_boolean("show-welcome-page"):
                self.show_welcome_page()
            else:
                self.cache_watcher = CacheWatcher(self)
                self.cache_watcher.start()

            self.builder.get_object("notebook_details").set_current_page(0)

            self.window.resize(self.settings.get_int('window-width'), self.settings.get_int('window-height'))
            self.paned.set_position(self.settings.get_int('window-pane-position'))

            self.refresh_schedule_enabled = self.settings.get_boolean("refresh-schedule-enabled")
            self.auto_refresh = AutomaticRefreshThread(self)
            self.auto_refresh.start()

            Gdk.threads_enter()
            Gtk.main()
            Gdk.threads_leave()

        except Exception as e:
            print (e)
            print(sys.exc_info()[0])
            self.logger.write_error("Exception occurred in main thread: " + str(sys.exc_info()[0]))
            self.logger.close()

######### EVENT HANDLERS #########

    def on_key_press_event(self, widget, event):
        ctrl = (event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl:
            if event.keyval == Gdk.KEY_s:
                self.select_updates(security=True)
            elif event.keyval == Gdk.KEY_k:
                self.select_updates(kernel=True)

######### UTILITY FUNCTIONS #########

    def refresh(self, root_mode=False):
        refresh = RefreshThread(self, root_mode=root_mode)
        refresh.start()

    def set_status_message(self, message):
        self.statusbar.push(self.context_id, message)

    def set_status(self, message, tooltip, icon, visible):
        self.set_status_message(message)
        self.statusIcon.set_from_icon_name(icon)
        self.statusIcon.set_tooltip_text(tooltip)
        self.statusIcon.set_visible(visible)

    @staticmethod
    def dpkg_locked():
        """ Returns True if a process has a handle on /var/lib/dpkg/lock (no check for write lock) """
        try:
            subprocess.run(["sudo", "/usr/lib/linuxmint/mintUpdate/dpkg_lock_check.sh"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    @staticmethod
    def show_dpkg_lock_msg(parent):
        dialog = Gtk.MessageDialog(parent, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        Gtk.MessageType.ERROR, Gtk.ButtonsType.OK, _("Cannot Proceed"))
        dialog.format_secondary_markup(_("Another process is currently using the package management system. Please wait for it to finish and then try again."))
        dialog.set_title(_("Update Manager"))
        dialog.run()
        dialog.destroy()

    def show_infobar(self, title, msg, msg_type=Gtk.MessageType.WARNING, icon=None, callback=None):
        infobar = Gtk.InfoBar()
        infobar.set_margin_bottom(2)
        infobar.set_message_type(msg_type)
        if not icon:
            if msg_type == Gtk.MessageType.WARNING:
                icon = "dialog-warning-symbolic"
            elif msg_type == Gtk.MessageType.ERROR:
                icon = "dialog-error-symbolic"
            elif msg_type == Gtk.MessageType.QUESTION:
                icon = "dialog-information-symbolic"
        if icon:
            img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
        else:
            img = Gtk.Image.new_from_icon_name("dialog-warning-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
        infobar.get_content_area().pack_start(img, False, False, 0)

        info_label = Gtk.Label()
        info_label.set_line_wrap(True)
        info_label.set_markup("<b>%s</b>\n%s" % (title, msg))
        infobar.get_content_area().pack_start(info_label, False, False, 0)
        if callback:
            if msg_type == Gtk.MessageType.QUESTION:
                infobar.add_button(_("Yes"), Gtk.ResponseType.YES)
                infobar.add_button(_("No"), Gtk.ResponseType.NO)
            else:
                infobar.add_button(_("OK"), Gtk.ResponseType.OK)
            infobar.connect("response", callback)
        infobar.show_all()
        self.infobar.pack_start(infobar, True, True, 0)

######### WINDOW/STATUSICON ##########

    def close_window(self, window, event):
        self.save_window_size()
        self.hide_main_window(window)
        return True

    def save_window_size(self):
        self.settings.set_int('window-width', self.window.get_size()[0])
        self.settings.set_int('window-height', self.window.get_size()[1])
        self.settings.set_int('window-pane-position', self.paned.get_position())

######### MENU/TOOLBAR FUNCTIONS #########

    def hide_main_window(self, widget):
        self.window.hide()

    def update_installable_state(self):
        model = self.treeview.get_model()

        iter = model.get_iter_first()
        download_size = 0
        num_selected = 0
        while (iter != None):
            checked = model.get_value(iter, UPDATE_CHECKED)
            if (checked):
                size = model.get_value(iter, UPDATE_SIZE)
                download_size = download_size + size
                num_selected = num_selected + 1
            iter = model.iter_next(iter)
        if num_selected == 0:
            self.install_button.set_sensitive(False)
            self.set_status_message(_("No updates selected"))
        else:
            self.install_button.set_sensitive(True)
            self.set_status_message(gettext.ngettext("%(selected)d update selected (%(size)s)", "%(selected)d updates selected (%(size)s)", num_selected) % {'selected':num_selected, 'size':size_to_string(download_size)})

    def setVisibleColumn(self, checkmenuitem, column, key):
        state = checkmenuitem.get_active()
        self.settings.set_boolean(key, state)
        column.set_visible(state)

    def setVisibleDescriptions(self, checkmenuitem):
        self.settings.set_boolean("show-descriptions", checkmenuitem.get_active())
        self.refresh()

    def clear(self, widget):
        model = self.treeview.get_model()
        if len(model):
            iter = model.get_iter_first()
            while (iter != None):
                model.set_value(iter, 0, False)
                iter = model.iter_next(iter)

        self.update_installable_state()

    def select_all(self, widget):
        self.select_updates()

    def select_updates(self, security=False, kernel=False):
        model = self.treeview.get_model()
        iter = model.get_iter_first()
        while (iter != None):
            update =  model.get_value(iter, UPDATE_OBJ)
            if security:
                if update.type == "security":
                    model.set_value(iter, UPDATE_CHECKED, True)
            elif kernel:
                if update.type == "kernel":
                    model.set_value(iter, UPDATE_CHECKED, True)
            else:
                model.set_value(iter, UPDATE_CHECKED, True)
            iter = model.iter_next(iter)

        self.update_installable_state()

    def force_refresh(self, widget):
        if self.dpkg_locked():
            self.show_dpkg_lock_msg(self.window)
        else:
            self.refresh(root_mode=True)

    def install(self, widget):
        if self.dpkg_locked():
            self.show_dpkg_lock_msg(self.window)
        else:
            install = InstallThread(self)
            install.start()
            self.settings.set_int("install-last-run", int(time.time()))

    def self_update(self, widget):
        self.select_all(widget)
        self.install(widget)

######### WELCOME PAGE FUNCTIONS #######

    def on_welcome_page_finished(self, button):
        self.settings.set_boolean("show-welcome-page", False)
        self.toolbar.set_sensitive(True)
        self.menubar.set_sensitive(True)
        self.updates_inhibited = False
        self.cache_watcher = CacheWatcher(self)
        self.cache_watcher.start()

    def show_help(self, button):
        os.system("yelp help:mintupdate/index &")

    def show_welcome_page(self, widget=None):
        self.updates_inhibited = True
        self.stack.set_visible_child_name("welcome")
        self.set_status(_("Welcome to the Update Manager"), _("Welcome to the Update Manager"), "mintupdate-updates-available-symbolic", True)
        self.set_status_message("")
        self.toolbar.set_sensitive(False)
        self.menubar.set_sensitive(False)

######### TREEVIEW/SELECTION FUNCTIONS #######

    def treeview_row_activated(self, treeview, path, view_column):
        self.toggled(None, path)

    def toggled(self, renderer, path):
        model = self.treeview.get_model()
        iter = model.get_iter(path)
        if (iter != None):
            model.set_value(iter, UPDATE_CHECKED, (not model.get_value(iter, UPDATE_CHECKED)))

        self.update_installable_state()

    def display_selected_update(self, selection):
        try:
            self.textview_packages.set_text("")
            self.textview_description.set_text("")
            self.textview_changes.set_text("")
            (model, iter) = selection.get_selected()
            if (iter != None):
                update = model.get_value(iter, UPDATE_OBJ)
                description = update.description.replace("\\n", "\n")
                desc_tab = self.notebook_details.get_nth_page(TAB_DESC)

                if update.type == "cinnamon":
                    latest_change_str = _("Most recent change")
                    desc = "%s\n\n%s: %s" % (description, latest_change_str, update.commit_msg)

                    self.textview_description.set_text(desc)

                    self.notebook_details.get_nth_page(TAB_PACKAGES).hide()
                    self.notebook_details.get_nth_page(TAB_CHANGELOG).hide()

                    self.notebook_details.set_current_page(TAB_DESC)
                    self.notebook_details.set_tab_label_text(desc_tab, _("Information"))
                elif update.type == "flatpak":
                    if update.link is not None:
                        website_label_str = _("Website: %s") % update.link
                        description = "%s\n\n%s" % (update.description, website_label_str)
                    else:
                        description = "%s" % update.description

                    self.textview_description.set_text(description)

                    self.notebook_details.get_nth_page(TAB_PACKAGES).show()
                    self.notebook_details.get_nth_page(TAB_CHANGELOG).hide()

                    self.notebook_details.set_current_page(TAB_DESC)
                    self.notebook_details.set_tab_label_text(desc_tab, _("Information"))
                    self.display_package_list(update, is_flatpak=True)
                else:
                    self.textview_description.set_text(description)
                    self.notebook_details.get_nth_page(TAB_PACKAGES).show()
                    self.notebook_details.get_nth_page(TAB_CHANGELOG).show()
                    self.notebook_details.set_tab_label_text(desc_tab, _("Description"))
                    self.display_package_list(update)

                    if self.notebook_details.get_current_page() == 2:
                        # Changelog tab
                        retriever = ChangelogRetriever(update, self)
                        retriever.start()
                        self.changelog_retriever_started = True
                    else:
                        self.changelog_retriever_started = False

        except Exception as e:
            print (e)
            print(sys.exc_info()[0])

    def switch_page(self, notebook, page, page_num):
        selection = self.treeview.get_selection()
        (model, iter) = selection.get_selected()
        if iter and page_num == 2 and not self.changelog_retriever_started:
            # Changelog tab
            update = model.get_value(iter, UPDATE_OBJ)
            retriever = ChangelogRetriever(update, self)
            retriever.start()
            self.changelog_retriever_started = True

    def display_package_list(self, update, is_flatpak=False):
        prefix = "\n    • "
        count = len(update.package_names)
        if is_flatpak:
            size_label = _("Total size: <")
        else:
            size_label = _("Total size:")

        packages = "%s%s%s\n%s %s\n\n" % \
            (gettext.ngettext("This update affects the following installed package:",
                      "This update affects the following installed packages:",
                      count),
             prefix,
             prefix.join(sorted(update.package_names)),
             size_label, size_to_string(update.size))
        self.textview_packages.set_text(packages)

    def treeview_right_clicked(self, widget, event):
        if event.button == 3:
            (model, iter) = widget.get_selection().get_selected()
            if (iter != None):
                update = model.get_value(iter, UPDATE_OBJ)
                menu = Gtk.Menu()
                menuItem = Gtk.MenuItem.new_with_mnemonic(_("Ignore the current update for this package"))
                menuItem.connect("activate", self.add_to_ignore_list, update.source_packages, True)
                menu.append(menuItem)
                menuItem = Gtk.MenuItem.new_with_mnemonic(_("Ignore all future updates for this package"))
                menuItem.connect("activate", self.add_to_ignore_list, update.source_packages, False)
                menu.append(menuItem)
                menu.attach_to_widget (widget, None)
                menu.show_all()
                menu.popup(None, None, None, None, event.button, event.time)

    def add_to_ignore_list(self, widget, source_packages, versioned):
        blacklist = self.settings.get_strv("blacklisted-packages")
        for source_package in source_packages:
            if not versioned:
                source_package = source_package.split("=")[0]
            blacklist.append(source_package)
        self.settings.set_strv("blacklisted-packages", blacklist)
        self.refresh()

######### SYSTRAY #########

    def app_hidden(self):
        return not self.window.get_visible()

    def tray_activate(self, time=0):
        try:
            focused = self.window.get_window().get_state() & Gdk.WindowState.FOCUSED
        except:
            focused = self.window.is_active() and self.window.get_visible()

        if focused:
            self.save_window_size()
            self.window.hide()
        else:
            self.window.show()
            self.window.present_with_time(time)

    def on_statusicon_activated(self, icon, button, time):
        if button == Gdk.BUTTON_PRIMARY:
            self.tray_activate(time)

    def quit(self, widget, data = None):
        if self.window:
            self.window.hide()
        try:
            self.logger.write("Exiting - requested by user")
            self.logger.close()
            self.save_window_size()
        except:
            pass # cause log might already been closed
        # Whatever works best heh :)
        os.system("kill -9 %s &" % os.getpid())

######### INFORMATION SCREEN #########

    def open_information(self, widget):
        if self.information_window_showing:
            return
        def destroy_window(widget):
            self.logger.remove_hook()
            self.information_window_showing = False
            window.destroy()
        def update_log(line):
            textbuffer.insert(textbuffer.get_end_iter(), line)
        gladefile = "/usr/share/linuxmint/mintupdate/information.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_title(_("Information"))
        window.set_icon_name("mintupdate")
        textbuffer = builder.get_object("log_textview").get_buffer()
        window.connect("destroy", destroy_window)
        builder.get_object("close_button").connect("clicked", destroy_window)
        builder.get_object("processid_label").set_text(str(os.getpid()))
        textbuffer.set_text(self.logger.read())
        builder.get_object("log_filename").set_text(str(self.logger.log.name))
        self.logger.set_hook(update_log)
        self.information_window_showing = True

######### HISTORY SCREEN #########

    def open_history(self, widget):
        if self.history_window_showing:
            return
        gladefile = "/usr/share/linuxmint/mintupdate/history.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_icon_name("mintupdate")
        window.set_title(_("History of Updates"))

        (COL_DATE, COL_TYPE, COL_NAME, COL_OLD_VER, COL_NEW_VER) = range(5)
        model = Gtk.TreeStore(str, str, str, str, str)

        treeview = builder.get_object("treeview_history")
        column_date = Gtk.TreeViewColumn(_("Date"), Gtk.CellRendererText(), text=COL_DATE)
        column_date.set_sort_column_id(COL_DATE)
        column_date.set_resizable(True)
        column_type = Gtk.TreeViewColumn(_("Type"), Gtk.CellRendererText(), text=COL_TYPE)
        column_type.set_sort_column_id(COL_TYPE)
        column_type.set_resizable(True)
        column_package = Gtk.TreeViewColumn(_("Update"), Gtk.CellRendererText(), text=COL_NAME)
        column_package.set_sort_column_id(COL_NAME)
        column_package.set_resizable(True)
        self.column_old_version = Gtk.TreeViewColumn(_("Old Version"), Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END), text=COL_OLD_VER)
        self.column_old_version.set_sort_column_id(COL_OLD_VER)
        self.column_old_version.set_resizable(True)
        self.column_new_version = Gtk.TreeViewColumn(_("New Version"), Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END), text=COL_NEW_VER)
        self.column_new_version.set_sort_column_id(COL_NEW_VER)
        self.column_new_version.set_resizable(True)
        treeview.append_column(column_date)
        treeview.append_column(column_type)
        treeview.append_column(column_package)
        treeview.append_column(self.column_old_version)
        treeview.append_column(self.column_new_version)
        treeview.set_headers_clickable(True)
        treeview.set_reorderable(False)
        treeview.set_search_column(0)
        treeview.set_enable_search(True)
        treeview.show()

        updates = []
        apt_updates = []
        cinnamon_updates = []
        flatpak_updates = []

        if os.path.isfile("/var/log/dpkg.log"):
            apt_updates = subprocess.run('zgrep " upgrade " -sh /var/log/dpkg.log*',
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True)\
                .stdout.decode().split("\n")

            for pkg in apt_updates:
                values = pkg.split(" ")
                if len(values) == 6:
                    (date, time, action, package, oldVersion, newVersion) = values
                    if action != "upgrade" or oldVersion == newVersion:
                        continue
                    if ":" in package:
                        package = package.split(":")[0]

                    iter = model.insert_before(None, None)
                    model.set_value(iter, COL_NAME, package)
                    model.row_changed(model.get_path(iter), iter)
                    model.set_value(iter, COL_DATE, "%s - %s" % (date, time))
                    model.set_value(iter, COL_OLD_VER, oldVersion)
                    model.set_value(iter, COL_NEW_VER, newVersion)
                    model.set_value(iter, COL_TYPE, _("package"))

        if CINNAMON_SUPPORT:
            logfile = '%s/.cinnamon/harvester.log' % os.path.expanduser("~")
            if os.path.isfile(logfile):
                cinnamon_updates += subprocess.run('grep " upgrade " -sh %s' % logfile,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True)\
                    .stdout.decode().split("\n")

                for pkg in cinnamon_updates:
                    values = pkg.split(" ")
                    if len(values) == 7:
                        (date, time, spice_type, action, package, oldVersion, newVersion) = values
                        if action != "upgrade" or oldVersion == newVersion:
                            continue
                        if ":" in package:
                            package = package.split(":")[0]

                        iter = model.insert_before(None, None)
                        model.set_value(iter, COL_NAME, package)
                        model.row_changed(model.get_path(iter), iter)
                        model.set_value(iter, COL_DATE, "%s - %s" % (date, time))
                        model.set_value(iter, COL_OLD_VER, oldVersion)
                        model.set_value(iter, COL_NEW_VER, newVersion)
                        model.set_value(iter, COL_TYPE, spice_type)

        if FLATPAK_SUPPORT:
            logfile = flatpakUpdater.LOG_PATH
            if os.path.isfile(logfile):
                with open(logfile, "r") as f:
                    for entry in f:
                        values = entry.strip("\n").split("::")
                        if len(values) == 7:
                            (date, time, fp_type, action, name, old_version, new_version) = values

                            iter = model.insert_before(None, None)
                            model.set_value(iter, COL_NAME, name)
                            model.row_changed(model.get_path(iter), iter)
                            model.set_value(iter, COL_DATE, "%s - %s" % (date, time))
                            model.set_value(iter, COL_OLD_VER, old_version)
                            model.set_value(iter, COL_NEW_VER, new_version)
                            model.set_value(iter, COL_TYPE, "flatpak-runtime" if fp_type == "runtime" else "flatpak-app")


        updates = apt_updates + cinnamon_updates + flatpak_updates

        model.set_sort_column_id(COL_DATE, Gtk.SortType.DESCENDING)
        treeview.set_model(model)

        def on_query_tooltip(widget, x, y, keyboard, tooltip):
            if not widget.get_tooltip_context(x, y, keyboard):
                return False
            else:
                on_row, wx, wy, model, path, iter = widget.get_tooltip_context(x, y, keyboard)
                bx, by = widget.convert_widget_to_bin_window_coords(x, y)
                result = widget.get_path_at_pos(bx, by)

                if result is not None:
                    path, column, cx, cy = result
                    if column == self.column_old_version:
                        text = model[iter][COL_OLD_VER]
                    elif column == self.column_new_version:
                        text = model[iter][COL_NEW_VER]
                    else:
                        return False
                    
                    tooltip.set_text(text)
                    return True

        treeview.connect("query-tooltip", on_query_tooltip)

        def destroy_window(widget):
            self.history_window_showing = False
            window.destroy()
        window.connect("destroy", destroy_window)
        builder.get_object("button_close").connect("clicked", destroy_window)
        self.history_window_showing = True

######### HELP/ABOUT/SHORTCUTS/SOURCES SCREEN #########

    def open_help(self, widget):
        os.system("yelp help:mintupdate/index &")

    def open_rel_upgrade(self, widget):
        os.system("/usr/bin/mint-release-upgrade &")

    def open_about(self, widget):
        dlg = Gtk.AboutDialog()
        dlg.set_transient_for(self.window)
        dlg.set_title(_("About"))
        dlg.set_program_name("mintUpdate")
        dlg.set_comments(_("Update Manager"))
        try:
            h = open('/usr/share/common-licenses/GPL', encoding="utf-8")
            s = h.readlines()
            gpl = ""
            for line in s:
                gpl += line
            h.close()
            dlg.set_license(gpl)
        except Exception as e:
            print (e)
            print(sys.exc_info()[0])

        dlg.set_version("__DEB_VERSION__")
        dlg.set_icon_name("mintupdate")
        dlg.set_logo_icon_name("mintupdate")
        dlg.set_website("http://www.github.com/linuxmint/mintupdate")
        def close(w, res):
            if res == Gtk.ResponseType.CANCEL or res == Gtk.ResponseType.DELETE_EVENT:
                w.destroy()
        dlg.connect("response", close)
        dlg.show()

    def open_repositories(self, widget):
        subprocess.Popen(["pkexec", "mintsources"])

    def open_timeshift(self, widget):
        subprocess.Popen(["pkexec", "timeshift-gtk"])

    def open_shortcuts(self, widget):
        gladefile = "/usr/share/linuxmint/mintupdate/shortcuts.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        window = builder.get_object("shortcuts")
        window.connect("destroy", Gtk.Widget.destroyed, window)

        if self.window != window.get_transient_for():
            window.set_transient_for(self.window)

        window.show_all()
        window.present_with_time(Gtk.get_current_event_time())

######### PREFERENCES SCREEN #########

    def open_preferences(self, widget, show_automation=False):
        if self.preferences_window_showing:
            return
        self.preferences_window_showing = True
        self.window.set_sensitive(False)
        gladefile = "/usr/share/linuxmint/mintupdate/preferences.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_transient_for(self.window)
        window.set_title(_("Preferences"))
        window.set_icon_name("mintupdate")
        window.connect("destroy", self.close_preferences, window)

        switch_container = builder.get_object("switch_container")
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack.set_transition_duration(150)
        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.set_stack(stack)
        switch_container.pack_start(stack_switcher, True, True, 0)
        stack_switcher.set_halign(Gtk.Align.CENTER)

        page_holder = builder.get_object("page_container")
        page_holder.add(stack)

        stack.add_titled(builder.get_object("page_options"), "page_options", _("Options"))
        stack.add_titled(builder.get_object("page_blacklist"), "page_blacklist", _("Blacklist"))
        stack.add_titled(builder.get_object("page_auto"), "page_auto", _("Automation"))

        # Options
        box = builder.get_object("page_options")
        page = SettingsPage()
        box.pack_start(page, True, True, 0)
        section = page.add_section(_("Interface"))
        section.add_row(GSettingsSwitch(_("Hide the update manager after applying updates"), "com.linuxmint.updates", "hide-window-after-update"))
        section.add_row(GSettingsSwitch(_("Only show a tray icon when updates are available or in case of errors"), "com.linuxmint.updates", "hide-systray"))

        section = page.add_section(_("Auto-refresh"))
        switch = GSettingsSwitch(_("Refresh the list of updates automatically"), "com.linuxmint.updates", "refresh-schedule-enabled")
        switch.content_widget.connect("notify::active", self.auto_refresh_toggled)
        section.add_row(switch)

        grid = Gtk.Grid()
        grid.set_row_spacing(12)
        grid.set_column_spacing(12)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        grid.set_margin_start(32)
        grid.set_margin_end(32)

        grid.attach(Gtk.Label(label=_("days")), 1, 0, 1, 1)
        grid.attach(Gtk.Label(label=_("hours")), 2, 0, 1, 1)
        grid.attach(Gtk.Label(label=_("minutes")), 3, 0, 1, 1)
        label = Gtk.Label(label=_("First, refresh the list of updates after:"))
        label.set_justify(Gtk.Justification.LEFT)
        label.set_alignment(0,0.5)
        grid.attach(label, 0, 1, 1, 1)
        label = Gtk.Label(label=_("Then, refresh the list of updates every:"))
        label.set_justify(Gtk.Justification.LEFT)
        label.set_alignment(0,0.5)
        grid.attach(label, 0, 2, 1, 1)

        spin_button = GSettingsSpinButton("", "com.linuxmint.updates", "refresh-days", mini=0, maxi=99, step=1, page=2)
        spin_button.set_spacing(0)
        spin_button.set_margin_start(0)
        spin_button.set_margin_end(0)
        spin_button.set_border_width(0)
        grid.attach(spin_button, 1, 1, 1, 1)
        spin_button = GSettingsSpinButton("", "com.linuxmint.updates", "refresh-hours", mini=0, maxi=23, step=1, page=5)
        spin_button.set_spacing(0)
        spin_button.set_margin_start(0)
        spin_button.set_margin_end(0)
        spin_button.set_border_width(0)
        grid.attach(spin_button, 2, 1, 1, 1)
        spin_button = GSettingsSpinButton("", "com.linuxmint.updates", "refresh-minutes", mini=0, maxi=59, step=1, page=10)
        spin_button.set_spacing(0)
        spin_button.set_margin_start(0)
        spin_button.set_margin_end(0)
        spin_button.set_border_width(0)
        grid.attach(spin_button, 3, 1, 1, 1)
        spin_button = GSettingsSpinButton("", "com.linuxmint.updates", "autorefresh-days", mini=0, maxi=99, step=1, page=2)
        spin_button.set_spacing(0)
        spin_button.set_margin_start(0)
        spin_button.set_margin_end(0)
        spin_button.set_border_width(0)
        grid.attach(spin_button, 1, 2, 1, 1)
        spin_button = GSettingsSpinButton("", "com.linuxmint.updates", "autorefresh-hours", mini=0, maxi=23, step=1, page=5)
        spin_button.set_spacing(0)
        spin_button.set_margin_start(0)
        spin_button.set_margin_end(0)
        spin_button.set_border_width(0)
        grid.attach(spin_button, 2, 2, 1, 1)
        spin_button = GSettingsSpinButton("", "com.linuxmint.updates", "autorefresh-minutes", mini=0, maxi=59, step=1, page=10)
        spin_button.set_spacing(0)
        spin_button.set_margin_start(0)
        spin_button.set_margin_end(0)
        spin_button.set_border_width(0)
        grid.attach(spin_button, 3, 2, 1, 1)

        label = Gtk.Label()
        label.set_markup("<i>%s</i>" % _("Note: The list only gets refreshed while the Update Manager window is closed (system tray mode)."))
        grid.attach(label, 0, 3, 4, 1)
        section.add_reveal_row(grid, "com.linuxmint.updates", "refresh-schedule-enabled")

        section = SettingsSection(_("Notifications"))
        revealer = SettingsRevealer("com.linuxmint.updates", "refresh-schedule-enabled")
        revealer.add(section)
        section._revealer = revealer
        page.pack_start(revealer, False, False, 0)

        switch = GSettingsSwitch(_("Only show notifications for security and kernel updates"), "com.linuxmint.updates", "tracker-security-only")
        section.add_reveal_row(switch, "com.linuxmint.updates", "tracker-disable-notifications", [False])
        switch = GSettingsSpinButton(_("Show a notification if an update has been available for (in logged-in days):"), "com.linuxmint.updates", "tracker-max-days", mini=2, maxi=90, step=1, page=5)
        section.add_reveal_row(switch, "com.linuxmint.updates", "tracker-disable-notifications", [False])
        switch = GSettingsSpinButton(_("Show a notification if an update is older than (in days):"), "com.linuxmint.updates", "tracker-max-age", mini=2, maxi=90, step=1, page=5)
        section.add_reveal_row(switch, "com.linuxmint.updates", "tracker-disable-notifications", [False])
        switch = GSettingsSpinButton(_("Don't show notifications if an update was applied in the last (in days):"), "com.linuxmint.updates", "tracker-grace-period", mini=2, maxi=90, step=1, page=5)
        section.add_reveal_row(switch, "com.linuxmint.updates", "tracker-disable-notifications", [False])

        # Blacklist
        treeview_blacklist = builder.get_object("treeview_blacklist")
        column = Gtk.TreeViewColumn(_("Ignored Updates"), Gtk.CellRendererText(), text=BLACKLIST_PKG_NAME)
        column.set_sort_column_id(BLACKLIST_PKG_NAME)
        column.set_resizable(True)
        treeview_blacklist.append_column(column)
        treeview_blacklist.set_headers_clickable(True)
        treeview_blacklist.set_reorderable(False)
        treeview_blacklist.show()
        model = Gtk.TreeStore(str) # BLACKLIST_PKG_NAME
        model.set_sort_column_id(BLACKLIST_PKG_NAME, Gtk.SortType.ASCENDING )
        treeview_blacklist.set_model(model)
        blacklist = self.settings.get_strv("blacklisted-packages")
        for ignored_pkg in blacklist:
            iter = model.insert_before(None, None)
            model.set_value(iter, BLACKLIST_PKG_NAME, ignored_pkg)
        builder.get_object("button_add").connect("clicked", self.add_blacklisted_package, treeview_blacklist, window)
        builder.get_object("button_remove").connect("clicked", self.remove_blacklisted_package, treeview_blacklist)
        builder.get_object("button_add").set_always_show_image(True)
        builder.get_object("button_remove").set_always_show_image(True)

        # Automation
        box = builder.get_object("page_auto_inner")
        page = SettingsPage()
        box.pack_start(page, True, True, 0)
        section = page.add_section(_("Package Updates"), _("Performed as root on a daily basis"))
        autoupgrade_switch = Switch(_("Apply updates automatically"))
        autoupgrade_switch.content_widget.set_active(os.path.isfile(AUTOMATIONS["upgrade"][2]))
        autoupgrade_switch.content_widget.connect("notify::active", self.set_auto_upgrade)
        section.add_row(autoupgrade_switch)
        button = Gtk.Button(label=_("Export blacklist to /etc/mintupdate.blacklist"))
        button.set_margin_start(20)
        button.set_margin_end(20)
        button.set_border_width(5)
        button.set_tooltip_text(_("Click this button for automatic updates to use your current blacklist."))
        button.connect("clicked", self.export_blacklist)
        section.add_row(button)
        additional_options = []
        if os.path.exists("/usr/bin/cinnamon"):
            switch = GSettingsSwitch(_("Update Cinnamon spices automatically"), "com.linuxmint.updates", "auto-update-cinnamon-spices")
            additional_options.append(switch)
        if os.path.exists("/usr/bin/flatpak"):
            switch = GSettingsSwitch(_("Update Flatpaks automatically"), "com.linuxmint.updates", "auto-update-flatpaks")
            additional_options.append(switch)
        if len(additional_options) > 0:
            section = page.add_section(_("Other Updates"), _("Performed when you log in"))
            for switch in additional_options:
                section.add_row(switch)
        section = page.add_section(_("Automatic Maintenance"), _("Performed as root on a weekly basis"))
        autoremove_switch = Switch(_("Remove obsolete kernels and dependencies"))
        autoremove_switch.content_widget.set_active(os.path.isfile(AUTOMATIONS["autoremove"][2]))
        autoremove_switch.content_widget.connect("notify::active", self.set_auto_remove)
        section.add_row(autoremove_switch)
        section.add_note(_("This option always leaves at least one older kernel installed and never removes manually installed kernels."))

        window.show_all()

        if show_automation:
            stack.set_visible_child_name("page_auto")

    def export_blacklist(self, widget):
        filename = os.path.join(tempfile.gettempdir(), "mintUpdate/blacklist")
        blacklist = self.settings.get_strv("blacklisted-packages")
        with open(filename, "w") as f:
            f.write("\n".join(blacklist) + "\n")
        subprocess.run(["pkexec", "/usr/bin/mintupdate-automation", "blacklist", "enable"])

    def auto_refresh_toggled(self, widget, param):
        self.refresh_schedule_enabled = widget.get_active()
        if self.refresh_schedule_enabled and not self.auto_refresh.is_alive():
            self.auto_refresh = AutomaticRefreshThread(self)
            self.auto_refresh.start()

    def set_auto_upgrade(self, widget, param):
        exists = os.path.isfile(AUTOMATIONS["upgrade"][2])
        action = None
        if widget.get_active() and not exists:
            action = "enable"
        elif not widget.get_active() and exists:
            action = "disable"
        if action:
            subprocess.run(["pkexec", "/usr/bin/mintupdate-automation", "upgrade", action])
        if widget.get_active() != os.path.isfile(AUTOMATIONS["upgrade"][2]):
            widget.set_active(not widget.get_active())

    def set_auto_remove(self, widget, param):
        exists = os.path.isfile(AUTOMATIONS["autoremove"][2])
        action = None
        if widget.get_active() and not exists:
            action = "enable"
        elif not widget.get_active() and exists:
            action = "disable"
        if action:
            subprocess.run(["pkexec", "/usr/bin/mintupdate-automation", "autoremove", action])
        if widget.get_active() != os.path.isfile(AUTOMATIONS["autoremove"][2]):
            widget.set_active(not widget.get_active())

    def save_blacklist(self, treeview_blacklist):
        blacklist = []
        model = treeview_blacklist.get_model()
        iter = model.get_iter_first()
        while iter is not None:
            pkg = model.get_value(iter, BLACKLIST_PKG_NAME)
            iter = model.iter_next(iter)
            blacklist.append(pkg)
        self.settings.set_strv("blacklisted-packages", blacklist)

    def add_blacklisted_package(self, widget, treeview_blacklist, window):
        dialog = Gtk.MessageDialog(window, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK, None)
        dialog.set_markup(_("Please specify the source package name of the update to ignore (wildcards are supported) and optionally the version:"))
        dialog.set_title(_("Ignore an Update"))
        dialog.set_icon_name("mintupdate")
        grid = Gtk.Grid()
        grid.set_column_spacing(5)
        grid.set_row_spacing(5)
        grid.set_halign(Gtk.Align.CENTER)
        name_entry = Gtk.Entry()
        version_entry = Gtk.Entry()
        grid.attach(Gtk.Label(label=_("Name:")), 0, 0, 1, 1)
        grid.attach(name_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label=_("Version:")), 0, 1, 1, 1)
        grid.attach(version_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label=_("(optional)")), 2, 1, 1, 1)
        dialog.get_content_area().add(grid)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            name = name_entry.get_text().strip()
            version = version_entry.get_text().strip()
            if name:
                if version:
                    pkg = "%s=%s" % (name, version)
                else:
                    pkg = name
                model = treeview_blacklist.get_model()
                iter = model.insert_before(None, None)
                model.set_value(iter, BLACKLIST_PKG_NAME, pkg)
        dialog.destroy()
        self.save_blacklist(treeview_blacklist)

    def remove_blacklisted_package(self, widget, treeview_blacklist):
        selection = treeview_blacklist.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            pkg = model.get_value(iter, BLACKLIST_PKG_NAME)
            model.remove(iter)
        self.save_blacklist(treeview_blacklist)

    def close_preferences(self, widget, window):
        self.window.set_sensitive(True)
        self.preferences_window_showing = False
        window.destroy()
        self.refresh()

######### KERNEL FEATURES #########

    def open_kernels(self, widget):
        kernel_window = KernelWindow(self)

if __name__ == "__main__":
    MintUpdate()
