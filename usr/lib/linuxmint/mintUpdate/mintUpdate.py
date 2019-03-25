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
import tarfile
import urllib.request
import proxygsettings
import subprocess
import pycurl
from datetime import datetime
import configparser
import traceback
import setproctitle

from kernelwindow import KernelWindow
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0') # Needed to get xid
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, Gdk, GdkPixbuf, GdkX11, Gio, Pango, GLib
from gi.repository import AppIndicator3 as AppIndicator

from Classes import Update, PRIORITY_UPDATES, get_release_dates

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
gettext.install("mintupdate", "/usr/share/locale", names="ngettext")

(TAB_UPDATES, TAB_UPTODATE, TAB_ERROR) = range(3)

(UPDATE_CHECKED, UPDATE_DISPLAY_NAME, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_SOURCE, UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ) = range(12)

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
        self.pkgcache = None
        self.statustime = 0
        self.dpkgstatus = None
        self.paused = False
        self.refresh_frequency = refresh_frequency

    def run(self):
        if not self.pkgcache:
            basedir = self.get_apt_config("Dir")
            cachedir = self.get_apt_config("Dir::Cache")
            cachefile = self.get_apt_config("Dir::Cache::pkgcache")
            self.pkgcache = os.path.join(basedir, cachedir, cachefile)
            statedir = self.get_apt_config("Dir::State")
            statefile = self.get_apt_config("Dir::State::status")
            self.dpkgstatus = os.path.join(basedir, statedir, statefile)

        if not os.path.isfile(self.pkgcache) or not os.path.isfile(self.dpkgstatus):
            self.application.logger.write("Package cache location not found, disabling cache monitoring")
            self.pkgcache = None

        self.do_refresh()

        if self.pkgcache:
            self.update_cachetime()
            self.loop()

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
        if not self.paused or not self.pkgcache:
            return
        if update_cachetime:
            self.update_cachetime()
        self.paused = False

    def pause(self):
        self.paused = True

    def update_cachetime(self):
        self.cachetime = os.path.getmtime(self.pkgcache)
        self.statustime = os.path.getmtime(self.dpkgstatus)

    def refresh_cache(self):
        self.application.logger.write("Changes to the package cache detected, triggering refresh")
        self.do_refresh()

    def do_refresh(self):
        self.application.refresh()

    @staticmethod
    def get_apt_config(config_option):
        output = subprocess.run(["apt-config", "shell", "val", config_option],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        try:
            output = output.decode().partition("val='")[2].partition("'")[0]
        except:
            output = ""
        return output

class ChangelogRetriever(threading.Thread):
    def __init__(self, package_update, application):
        threading.Thread.__init__(self)
        self.source_package = package_update.real_source_name
        self.version = package_update.new_version
        self.origin = package_update.origin
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
                        ppa_info = line.split("ppa.launchpad.net/")[1]
                        break
                else:
                    return None, None
        except EnvironmentError as e:
            print ("Error encountered while trying to get PPA owner and name: %s" % e)
            return None, None
        ppa_owner, ppa_name, ppa_x = ppa_info.split("/", 2)
        return ppa_owner, ppa_name

    def get_ppa_changelog(self, ppa_owner, ppa_name):
        max_tarball_size = 1000000
        print ("\nFetching changelog for PPA package %s/%s/%s ..." % (ppa_owner, ppa_name, self.source_package))
        if self.source_package.startswith("lib"):
            ppa_abbr = self.source_package[:4]
        else:
            ppa_abbr = self.source_package[0]
        deb_dsc_uri = "http://ppa.launchpad.net/%s/%s/ubuntu/pool/main/%s/%s/%s_%s.dsc" % (ppa_owner, ppa_name, ppa_abbr, self.source_package, self.source_package, self.version)
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
        deb_file_uri = "http://ppa.launchpad.net/%s/%s/ubuntu/pool/main/%s/%s/%s" % (ppa_owner, ppa_name, ppa_abbr, self.source_package, deb_filename)
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
                    "minutes": self.application.settings.get_int(f"{settings_prefix}refresh-minutes"),
                    "hours": self.application.settings.get_int(f"{settings_prefix}refresh-hours"),
                    "days": self.application.settings.get_int(f"{settings_prefix}refresh-days")
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
                        self.application.logger.write(f"Auto-refresh disabled in preferences, cancelling {refresh_type} refresh")
                        return
                    if self.application.app_hidden:
                        self.application.logger.write(f"Update Manager is in tray mode, performing {refresh_type} refresh")
                        refresh = RefreshThread(self.application, root_mode=True)
                        refresh.start()
                        while refresh.is_alive():
                            time.sleep(5)
                    else:
                        if initial_refresh:
                            self.application.logger.write(f"Update Manager window is open, skipping {refresh_type} refresh")
                        else:
                            self.application.logger.write(f"Update Manager window is open, delaying {refresh_type} refresh by 60s")
                            time.sleep(60)
            except Exception as e:
                print (e)
                self.application.logger.write_error("Exception occurred during %s refresh: %s" % (refresh_type, str(sys.exc_info()[0])))

            if initial_refresh:
                initial_refresh = False
                settings_prefix = "auto"
                refresh_type = "recurring"
        else:
            self.application.logger.write(f"Auto-refresh disabled in preferences, AutomaticRefreshThread stopped")

class InstallThread(threading.Thread):

    def __init__(self, application):
        threading.Thread.__init__(self)
        self.application = application
        self.application.window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        self.application.window.set_sensitive(False)
        self.reboot_required = self.application.reboot_required

    def __del__(self):
        self.application.cache_watcher.resume(False)

    def run(self):
        self.application.cache_watcher.pause()
        try:
            self.application.logger.write("Install requested by user")
            Gdk.threads_enter()
            installNeeded = False
            packages = []
            model = self.application.treeview.get_model()
            Gdk.threads_leave()

            iter = model.get_iter_first()
            while (iter != None):
                checked = model.get_value(iter, UPDATE_CHECKED)
                if (checked == "true"):
                    installNeeded = True
                    package_update = model.get_value(iter, UPDATE_OBJ)
                    if package_update.type == "kernel" and \
                       [True for pkg in package_update.package_names if "-image-" in pkg]:
                        self.reboot_required = True
                    for package in package_update.package_names:
                        packages.append(package)
                        self.application.logger.write("Will install " + str(package))
                iter = model.iter_next(iter)

            if (installNeeded == True):

                proceed = True
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
                                    dialog.vbox.pack_start(label, False, False, 0)
                                    dialog.vbox.pack_start(scrolledWindow, True, True, 0)
                                    dialog.vbox.set_border_width(6)

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
                                    dialog.vbox.pack_start(label, False, False, 0)
                                    dialog.vbox.pack_start(scrolledWindow, True, True, 0)

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

                if proceed:
                    Gdk.threads_enter()
                    self.application.set_status(_("Installing updates"), _("Installing updates"), "mintupdate-installing", True)
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
                            self.application.app_hidden = True
                            self.application.window.hide()
                            Gdk.threads_leave()

                        if [pkg for pkg in PRIORITY_UPDATES if pkg in packages]:
                            # Restart
                            self.application.logger.write("Mintupdate was updated, restarting it...")
                            self.application.logger.close()
                            os.system("/usr/lib/linuxmint/mintUpdate/mintUpdate.py show &")
                            return

                        # Refresh
                        Gdk.threads_enter()
                        self.application.set_status(_("Checking for updates"), _("Checking for updates"), "mintupdate-checking", not self.application.settings.get_boolean("hide-systray"))
                        self.application.window.get_window().set_cursor(None)
                        self.application.window.set_sensitive(True)
                        Gdk.threads_leave()
                        refresh = RefreshThread(self.application)
                        refresh.start()
                    else:
                        Gdk.threads_enter()
                        self.application.set_status(_("Could not install the security updates"), _("Could not install the security updates"), "mintupdate-error", True)
                        self.application.window.get_window().set_cursor(None)
                        self.application.window.set_sensitive(True)
                        Gdk.threads_leave()

                else:
                    # Stop the blinking but don't refresh
                    Gdk.threads_enter()
                    self.application.window.get_window().set_cursor(None)
                    self.application.window.set_sensitive(True)
                    Gdk.threads_leave()
            else:
                # Stop the blinking but don't refresh
                Gdk.threads_enter()
                self.application.window.get_window().set_cursor(None)
                self.application.window.set_sensitive(True)
                Gdk.threads_leave()

        except Exception as e:
            print (e)
            self.application.logger.write_error("Exception occurred in the install thread: " + str(sys.exc_info()[0]))
            Gdk.threads_enter()
            self.application.set_status(_("Could not install the security updates"), _("Could not install the security updates"), "mintupdate-error", True)
            self.application.logger.write_error("Could not install security updates")
            self.application.window.get_window().set_cursor(None)
            self.application.window.set_sensitive(True)
            Gdk.threads_leave()

class RefreshThread(threading.Thread):

    def __init__(self, application, root_mode=False):
        threading.Thread.__init__(self)
        self.root_mode = root_mode
        self.application = application

    def __del__(self):
        self.application.cache_watcher.resume()

    def check_policy(self):
        # Check the presence of the Mint layer
        p = subprocess.run(['apt-cache', 'policy'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = p.stdout.decode()
        if p.stderr:
            error_msg = p.stderr.decode().strip()
            self.application.logger.write_error(f"APT policy error:\n{error_msg}")
        else:
            error_msg = ""
        mint_layer_found = False
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("700") and line.endswith("Packages") and "/upstream" in line:
                mint_layer_found = True
                break
        return (mint_layer_found, error_msg)

    @staticmethod
    def get_eol_status():
        """ Checks if distribution has reached end of life (EOL)

        Returns:
        * is_eol: True if EOL
        * show_eol_warning: True if early_warning_days > EOL - now
        * eol_date: datetime object of EOL date
        """
        early_warning_days = 90
        is_eol = False
        eol_date = None
        show_eol_warning = False
        try:
            release_dates = get_release_dates()
            if release_dates and os.path.isfile("/etc/os-release"):
                with open("/etc/os-release", encoding="utf-8") as f:
                    release_data = f.readlines()
                base_release = next((x.split("=",1)[1].strip() for x in release_data if (x.startswith("UBUNTU_CODENAME=")) or x.startswith("DEBIAN_CODENAME=")), None)
                if base_release in release_dates.keys():
                    now = datetime.now()
                    eol_date = release_dates[base_release][1]
                    is_eol =  now > eol_date
                    show_eol_warning =  (eol_date - now).days <= early_warning_days
        except:
            pass
        return (is_eol, show_eol_warning, eol_date)

    def run(self):

        if self.application.updates_inhibited:
            self.application.logger.write("Updates are inhibited, skipping refresh")
            return False

        if self.root_mode:
            while self.application.dpkg_locked():
                self.application.logger.write("Package management system locked by another process, retrying in 60s")
                time.sleep(60)

        self.application.cache_watcher.pause()

        Gdk.threads_enter()
        vpaned_position = self.application.builder.get_object("paned1").get_position()
        for child in self.application.infobar.get_children():
            child.destroy()
        if self.application.reboot_required:
            self.application.show_infobar(_("Reboot required"),
                _("You have installed updates that require a reboot to take effect, please reboot your system as soon as possible."))
        Gdk.threads_leave()

        try:
            if (self.root_mode):
                self.application.logger.write("Starting refresh (retrieving lists of updates from remote servers)")
            else:
                self.application.logger.write("Starting refresh (local only)")
            Gdk.threads_enter()
            self.application.set_status_message(_("Starting refresh..."))
            self.application.stack.set_visible_child_name("updates_available")
            if (not self.application.app_hidden):
                self.application.window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
            self.application.window.set_sensitive(False)

            # Starts the blinking
            self.application.statusIcon.set_from_icon_name("mintupdate-checking")
            self.application.statusIcon.set_tooltip_text(_("Checking for updates"))
            self.application.statusIcon.set_visible(not self.application.settings.get_boolean("hide-systray"))
            self.application.builder.get_object("paned1").set_position(vpaned_position)
            Gdk.threads_leave()

            model = Gtk.TreeStore(str, str, str, str, str, int, str, str, str, str, str, object)
            # UPDATE_CHECKED, UPDATE_DISPLAY_NAME, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_SOURCE,
            # UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ

            model.set_sort_column_id(UPDATE_SORT_STR, Gtk.SortType.ASCENDING)

            Gdk.threads_enter()
            self.application.set_status_message(_("Finding the list of updates..."))
            self.application.builder.get_object("paned1").set_position(vpaned_position)
            Gdk.threads_leave()

            # Refresh the APT cache
            if self.root_mode:
                refresh_command = ["sudo", "/usr/bin/mint-refresh-cache"]
                if not self.application.app_hidden:
                    refresh_command.extend(["--use-synaptic",
                                            str(self.application.window.get_window().get_xid())])
                subprocess.run(refresh_command)
                self.application.settings.set_int("refresh-last-run", int(time.time()))

            output = subprocess.run("/usr/lib/linuxmint/mintUpdate/checkAPT.py",
                                    stdout=subprocess.PIPE).stdout.decode("utf-8")

            if len(output) > 0 and not "CHECK_APT_ERROR" in output:
                (mint_layer_found, error_msg) = self.check_policy()
                if not mint_layer_found:
                    Gdk.threads_enter()
                    label1 = _("Your APT configuration is corrupt.")
                    label2 = _("Do not install or update anything, it could break your operating system!")
                    label3 = _("To switch to a different Linux Mint mirror and solve this problem, click OK.")
                    msg = _("Your APT configuration is corrupt.")
                    error_label = _("APT error:")
                    if error_msg:
                        error_msg = f"\n\n{error_label}\n{error_msg}"
                    else:
                        error_label = ""
                    self.application.show_infobar(_("Please switch to another Linux Mint mirror"),
                        msg, Gtk.MessageType.ERROR,
                        callback=self._on_infobar_mintsources_response)
                    self.application.set_status(_("Could not refresh the list of updates"), f"{label1}\n{label2}", "mintupdate-error", True)
                    self.application.logger.write_error("Error: The APT policy is incorrect!")
                    self.application.stack.set_visible_child_name("status_error")
                    self.application.builder.get_object("label_error_details").set_markup(f"<b>{label1}\n{label2}\n{label3}{error_msg}</b>")
                    self.application.builder.get_object("label_error_details").show()
                    if not self.application.app_hidden:
                        self.application.window.get_window().set_cursor(None)
                    self.application.window.set_sensitive(True)
                    Gdk.threads_leave()
                    return False

            lines = output.split("---EOL---")

            # Look at the updates one by one
            num_visible = 0
            num_checked = 0
            download_size = 0

            if "CHECK_APT_ERROR" in output:
                try:
                    error_msg = output.split("Error: ")[1].replace("E:", "\n").strip()
                    if "apt.cache.FetchFailedException" in output and " changed its " in error_msg:
                        error_msg += "\n\n%s" % _("Run 'apt update' in a terminal window to address this")
                except:
                    error_msg = ""
                Gdk.threads_enter()
                self.application.set_status(_("Could not refresh the list of updates"),
                    "%s%s%s" % (_("Could not refresh the list of updates"), "\n\n" if error_msg else "", error_msg),
                    "mintupdate-error", True)
                self.application.logger.write_error("Error in checkAPT.py, could not refresh the list of updates")
                self.application.stack.set_visible_child_name("status_error")
                self.application.builder.get_object("label_error_details").set_text(error_msg)
                self.application.builder.get_object("label_error_details").show()
                if (not self.application.app_hidden):
                    self.application.window.get_window().set_cursor(None)
                self.application.window.set_sensitive(True)
                Gdk.threads_leave()
                return False
            elif len(lines):
                is_self_update = False
                for line in lines:
                    if "###" in line:
                        update = Update(package=None, input_string=line, source_name=None)

                        # Check if self-update is needed
                        if update.source_name in PRIORITY_UPDATES:
                            is_self_update = True
                            self.application.stack.set_visible_child_name("status_self-update")

                        iter = model.insert_before(None, None)

                        model.set_value(iter, UPDATE_CHECKED, "true")
                        num_checked = num_checked + 1
                        download_size = download_size + update.size

                        model.row_changed(model.get_path(iter), iter)

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
                            model.set_value(iter, UPDATE_DISPLAY_NAME, f"<b>{GLib.markup_escape_text(update.display_name)}</b>\n{GLib.markup_escape_text(shortdesc)}")
                        else:
                            model.set_value(iter, UPDATE_DISPLAY_NAME, f"<b>{GLib.markup_escape_text(update.display_name)}</b>")

                        origin = update.origin
                        origin = origin.replace("linuxmint", "Linux Mint").replace("ubuntu", "Ubuntu").replace("LP-PPA-", "PPA ").replace("debian", "Debian")

                        type_sort_key = 0 # Used to sort by type
                        if update.type == "kernel":
                            tooltip = _("Kernel update")
                            type_sort_key = 2
                        elif update.type == "security":
                            tooltip = _("Security update")
                            type_sort_key = 1
                        elif update.type == "unstable":
                            tooltip = _("Unstable software. Only apply this update to help developers beta-test new software.")
                            type_sort_key = 5
                        else:
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
                        model.set_value(iter, UPDATE_SORT_STR, "%d%s" % (type_sort_key, update.display_name))
                        model.set_value(iter, UPDATE_OBJ, update)
                        num_visible = num_visible + 1

                Gdk.threads_enter()
                if num_visible:
                    if is_self_update:
                        self.application.builder.get_object("toolbar1").set_sensitive(False)
                        self.application.statusbar.set_visible(False)
                        statusString = _("Update Manager needs to be updated")
                    elif num_checked == 0:
                        statusString = _("No updates selected")
                    elif num_checked >= 1:
                        statusString = ngettext("%(selected)d update selected (%(size)s)", "%(selected)d updates selected (%(size)s)", num_checked) % {'selected':num_checked, 'size':size_to_string(download_size)}

                    self.application.set_status(statusString, statusString, "mintupdate-updates-available", True)
                    self.application.logger.write("Found " + str(num_visible) + " software updates")

                    if num_visible >= 1:
                        systrayString = ngettext("%d update available", "%d updates available", num_visible) % num_visible
                    self.application.statusIcon.set_tooltip_text(systrayString)

                Gdk.threads_leave()

            is_end_of_life, show_eol_warning, eol_date = self.get_eol_status()

            if not len(lines) or not num_visible:
                if is_end_of_life:
                    NO_UPDATES_MSG = _("Your distribution has reached end of life and is no longer supported")
                    log_msg = "System is end of life, no updates available"
                    tray_icon = "mintupdate-error"
                    status_icon = "emblem-important-symbolic"
                else:
                    NO_UPDATES_MSG = _("Your system is up to date")
                    tray_icon = "mintupdate-up-to-date"
                    status_icon = "object-select-symbolic"
                    log_msg = "System is up to date"
                Gdk.threads_enter()
                self.application.builder.get_object("label_success").set_text(NO_UPDATES_MSG)
                self.application.builder.get_object("image_success_status").set_from_icon_name(status_icon, 96)
                self.application.stack.set_visible_child_name("status_updated")
                self.application.set_status(NO_UPDATES_MSG, NO_UPDATES_MSG, tray_icon, not self.application.settings.get_boolean("hide-systray"))
                self.application.logger.write(log_msg)
                Gdk.threads_leave()

            Gdk.threads_enter()
            self.application.logger.write("Refresh finished")

            # Stop the blinking
            self.application.builder.get_object("notebook_details").set_current_page(0)
            if (not self.application.app_hidden):
                self.application.window.get_window().set_cursor(None)
            self.application.treeview.set_model(model)
            del model
            self.application.window.set_sensitive(True)
            self.application.builder.get_object("paned1").set_position(vpaned_position)

            if show_eol_warning and self.application.settings.get_boolean("warn-about-distribution-eol"):
                try:
                    release_name = subprocess.run(["lsb_release", "-d"], stdout=subprocess.PIPE).stdout.decode().split(":", 1)[1].strip()
                except:
                    release_name = _("distribution")

                infobar_title = _("DISTRIBUTION END OF LIFE WARNING")
                infobar_message = "%s\n\n%s %s\n\n%s" % (
                    _(f"Your {release_name} is only supported until {eol_date.strftime('%x')}."),
                    _("Your system will remain functional after that date, but the official software repositories will become unavailable along with any updates including security updates."),
                    _("You should perform an upgrade to or a clean install of a newer version of your distribution before that happens."),
                    _("For more information visit <a href='https://linuxmint.com'>linuxmint.com</a>."))
                self.application.show_infobar(infobar_title,
                                              infobar_message,
                                              Gtk.MessageType.WARNING,
                                              callback=self._on_infobar_eol_response)

            if self.application.settings.get_boolean("warn-about-timeshift") and not self.checkTimeshiftConfiguration():
                self.application.show_infobar(_("Please set up System Snapshots"),
                    _("If something breaks, snapshots will allow you to restore your system to the previous working condition."),
                                              Gtk.MessageType.WARNING,
                                              callback=self._on_infobar_timeshift_response)

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
                    if mirror_url is None:
                        # Unable to find the Mint mirror being used..
                        pass
                    elif mirror_url == "http://packages.linuxmint.com":
                        if not self.application.settings.get_boolean("default-repo-is-ok"):
                            infobar_title = _("Do you want to switch to a local mirror?")
                            infobar_message = _("Local mirrors are usually faster than packages.linuxmint.com.")
                            infobar_message_type = Gtk.MessageType.QUESTION
                    elif not self.application.app_hidden:
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
                            mint_date = datetime.fromtimestamp(mint_timestamp)
                            now = datetime.now()
                            mint_age = (now - mint_date).days
                            if (mint_age > 2):
                                mirror_date = datetime.fromtimestamp(mirror_timestamp)
                                mirror_age = (mint_date - mirror_date).days
                                if (mirror_age > 2):
                                    infobar_title = _("Please switch to another mirror")
                                    infobar_message = ngettext("The last update on %(mirror)s was %(days)d day ago.",
                                                                "The last update on %(mirror)s was %(days)d days ago.",
                                                                (now - mirror_date).days) % \
                                                                {'mirror': mirror_url, 'days': (now - mirror_date).days}
            except:
                print(sys.exc_info()[0])
                # best effort, just print out the error
                print("An exception occurred while checking if the repositories were up to date: %s" % sys.exc_info()[0])

            if infobar_message is not None:
                self.application.show_infobar(infobar_title,
                                              infobar_message,
                                              infobar_message_type,
                                              callback=infobar_callback)

            Gdk.threads_leave()

        except:
            traceback.print_exc()
            print("-- Exception occurred in the refresh thread: " + str(sys.exc_info()[0]))
            self.application.logger.write_error("Exception occurred in the refresh thread: " + str(sys.exc_info()[0]))
            Gdk.threads_enter()
            self.application.set_status(_("Could not refresh the list of updates"), _("Could not refresh the list of updates"), "mintupdate-error", True)
            if (not self.application.app_hidden):
                self.application.window.get_window().set_cursor(None)
            self.application.window.set_sensitive(True)
            self.application.builder.get_object("paned1").set_position(vpaned_position)
            Gdk.threads_leave()

    def _on_infobar_mintsources_response(self, infobar, response_id):
        infobar.destroy()
        if response_id == Gtk.ResponseType.NO:
            self.application.settings.set_boolean("default-repo-is-ok", True)
        else:
            subprocess.Popen(["pkexec", "mintsources"])

    def _on_infobar_timeshift_response(self, infobar, response_id):
        infobar.destroy()
        subprocess.Popen(["pkexec", "timeshift-gtk"])

    def _on_infobar_eol_response(self, infobar, response_id):
        infobar.destroy()
        self.application.settings.set_boolean("warn-about-distribution-eol", False)

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

    def checkTimeshiftConfiguration(self):
        if os.path.isfile("/etc/timeshift.json"):
            try:
                data = json.load(open("/etc/timeshift.json", encoding="utf-8"))
                if 'backup_device_uuid' in data and data['backup_device_uuid']:
                    return True
            except Exception as e:
                print("Error while checking Timeshift configuration: ", e)
        return False

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
        self._write(f"{datetime.now().strftime('%m.%d@%H:%M')} ++ {line}\n")

    def write_error(self, line):
        self._write(f"{datetime.now().strftime('%m.%d@%H:%M')} -- {line}\n")

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

class StatusIcon():

    def __init__(self, app):
        self.app = app
        self.icon = AppIndicator.Indicator.new("mintUpdate", "mintupdate", AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.icon.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.icon.set_title(_("Update Manager"))

        self.menu = Gtk.Menu()
        item = Gtk.MenuItem()
        item.set_label(_("Update Manager"))
        item.connect("activate", self.app.on_statusicon_clicked)
        self.menu.append(item)

        item = Gtk.MenuItem()
        item.set_label(_("Exit"))
        item.connect("activate", self.cb_exit, '')
        self.menu.append(item)

        self.menu.show_all()
        self.icon.set_menu(self.menu)

    def cb_exit(self, w, data):
        self.app.quit_from_systray(None, None)

    def set_from_icon_name(self, name):
        self.icon.set_icon(name)

    def set_tooltip_text(self, text):
        pass # appindicator doesn't support that

    def set_visible(self, visible):
        if visible:
            self.icon.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        else:
            self.icon.set_status(AppIndicator.IndicatorStatus.PASSIVE)

class MintUpdate():

    def __init__(self):
        Gdk.threads_init()
        self.app_hidden = True
        self.information_window_showing = False
        self.history_window_showing = False
        self.preferences_window_showing = False
        self.updates_inhibited = False
        self.reboot_required = False
        self.logger = Logger()
        self.logger.write("Launching Update Manager")
        self.settings = Gio.Settings("com.linuxmint.updates")
        if os.getenv("XDG_CURRENT_DESKTOP") == "KDE":
            self.statusIcon = StatusIcon(self)
        else:
            self.statusIcon = Gtk.StatusIcon()
        self.statusIcon.set_from_icon_name("mintupdate-checking")
        self.statusIcon.set_tooltip_text (_("Checking for updates"))
        self.statusIcon.set_visible(not self.settings.get_boolean("hide-systray"))

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

            vbox = self.builder.get_object("vbox_main")
            self.window.set_icon_name("mintupdate")

            accel_group = Gtk.AccelGroup()
            self.window.add_accel_group(accel_group)

            self.notebook_details = self.builder.get_object("notebook_details")
            self.textview_packages = self.builder.get_object("textview_packages").get_buffer()
            self.textview_description = self.builder.get_object("textview_description").get_buffer()
            self.textview_changes = self.builder.get_object("textview_changes").get_buffer()

            # Welcome page
            welcome_page = self.builder.get_object("welcome_page")
            self.stack.add_named(welcome_page, "welcome")
            self.builder.get_object("button_welcome_finish").connect("clicked", self.on_welcome_page_finished)
            self.builder.get_object("button_welcome_help").connect("clicked", self.show_help)

            # Updates page
            updates_page = self.builder.get_object("updates_page")
            self.stack.add_named(updates_page, "updates_available")
            self.stack.set_visible_child_name("updates_available")

            # the infobar container
            self.infobar = self.builder.get_object("hbox_infobar")

            # the treeview
            cr = Gtk.CellRendererToggle()
            cr.connect("toggled", self.toggled)
            column_upgrade = Gtk.TreeViewColumn(_("Upgrade"), cr)
            column_upgrade.set_cell_data_func(cr, self.celldatafunction_checkbox)
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
            selection.connect("changed", self.display_selected_package)
            self.builder.get_object("notebook_details").connect("switch-page", self.switch_page)
            self.window.connect("delete_event", self.close_window)

            # Install Updates button
            install_button = self.builder.get_object("tool_apply")
            install_button.connect("clicked", self.install)
            key, mod = Gtk.accelerator_parse("<Control>I")
            install_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

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

            menu = Gtk.Menu()
            menuItem3 = Gtk.ImageMenuItem.new_with_label(_("Refresh"))
            image = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU)
            menuItem3.set_image(image)
            menuItem3.connect('activate', self.force_refresh)
            menu.append(menuItem3)
            menuItem2 = Gtk.ImageMenuItem.new_with_label(_("Information"))
            image = Gtk.Image.new_from_icon_name("dialog-information-symbolic", Gtk.IconSize.MENU)
            menuItem2.set_image(image)
            menuItem2.connect('activate', self.open_information)
            menu.append(menuItem2)
            menuItem4 = Gtk.ImageMenuItem.new_with_label(_("Preferences"))
            image = Gtk.Image.new_from_icon_name("preferences-other-symbolic", Gtk.IconSize.MENU)
            menuItem4.set_image(image)
            menuItem4.connect('activate', self.open_preferences)
            menu.append(menuItem4)
            menuItem = Gtk.ImageMenuItem.new_with_label(_("Quit"))
            image = Gtk.Image.new_from_icon_name("application-exit-symbolic", Gtk.IconSize.MENU)
            menuItem.set_image(image)
            menuItem.connect('activate', self.quit_from_systray)
            menu.append(menuItem)

            if os.getenv("XDG_CURRENT_DESKTOP") != "KDE":
                self.statusIcon.connect('activate', self.on_statusicon_clicked)
                self.statusIcon.connect('popup-menu', self.show_statusicon_menu, menu)

            fileMenu = Gtk.MenuItem.new_with_mnemonic(_("_File"))
            fileSubmenu = Gtk.Menu()
            fileMenu.set_submenu(fileSubmenu)
            closeMenuItem = Gtk.ImageMenuItem()
            closeMenuItem.set_image(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU))
            closeMenuItem.set_label(_("Close"))
            closeMenuItem.connect("activate", self.hide_main_window)
            key, mod = Gtk.accelerator_parse("<Control>W")
            closeMenuItem.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            fileSubmenu.append(closeMenuItem)

            editMenu = Gtk.MenuItem.new_with_mnemonic(_("_Edit"))
            editSubmenu = Gtk.Menu()
            editMenu.set_submenu(editSubmenu)
            prefsMenuItem = Gtk.ImageMenuItem()
            prefsMenuItem.set_image(Gtk.Image.new_from_icon_name("preferences-other-symbolic", Gtk.IconSize.MENU))
            prefsMenuItem.set_label(_("Preferences"))
            prefsMenuItem.connect("activate", self.open_preferences)
            editSubmenu.append(prefsMenuItem)
            if os.path.exists("/usr/bin/timeshift-gtk"):
                sourcesMenuItem = Gtk.ImageMenuItem()
                sourcesMenuItem.set_image(Gtk.Image.new_from_icon_name("document-open-recent-symbolic", Gtk.IconSize.MENU))
                sourcesMenuItem.set_label(_("System Snapshots"))
                sourcesMenuItem.connect("activate", self.open_timeshift)
                editSubmenu.append(sourcesMenuItem)
            if os.path.exists("/usr/bin/mintsources"):
                sourcesMenuItem = Gtk.ImageMenuItem()
                sourcesMenuItem.set_image(Gtk.Image.new_from_icon_name("system-software-install-symbolic", Gtk.IconSize.MENU))
                sourcesMenuItem.set_label(_("Software Sources"))
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
                    relUpgradeMenuItem = Gtk.ImageMenuItem()
                    relUpgradeMenuItem.set_image(Gtk.Image.new_from_icon_name("mintupdate-type-package-symbolic", Gtk.IconSize.MENU))
                    relUpgradeMenuItem.set_label(_("Upgrade to %s") % rel_target)
                    relUpgradeMenuItem.connect("activate", self.open_rel_upgrade)
                    editSubmenu.append(relUpgradeMenuItem)

            viewMenu = Gtk.MenuItem.new_with_mnemonic(_("_View"))
            viewSubmenu = Gtk.Menu()
            viewMenu.set_submenu(viewSubmenu)
            historyMenuItem = Gtk.ImageMenuItem()
            historyMenuItem.set_image(Gtk.Image.new_from_icon_name("document-open-recent-symbolic", Gtk.IconSize.MENU))
            historyMenuItem.set_label(_("History of Updates"))
            historyMenuItem.connect("activate", self.open_history)
            kernelMenuItem = Gtk.ImageMenuItem()
            kernelMenuItem.set_image(Gtk.Image.new_from_icon_name("system-run-symbolic", Gtk.IconSize.MENU))
            kernelMenuItem.set_label(_("Linux Kernels"))
            kernelMenuItem.connect("activate", self.open_kernels)
            infoMenuItem = Gtk.ImageMenuItem()
            infoMenuItem.set_image(Gtk.Image.new_from_icon_name("dialog-information-symbolic", Gtk.IconSize.MENU))
            infoMenuItem.set_label(_("Information"))
            infoMenuItem.connect("activate", self.open_information)
            visibleColumnsMenuItem = Gtk.ImageMenuItem()
            visibleColumnsMenuItem.set_image(Gtk.Image.new_from_icon_name("dialog-information-symbolic", Gtk.IconSize.MENU))
            visibleColumnsMenuItem.set_label(_("Visible Columns"))
            visibleColumnsMenu = Gtk.Menu()
            visibleColumnsMenuItem.set_submenu(visibleColumnsMenu)

            typeColumnMenuItem = Gtk.CheckMenuItem(_("Type"))
            typeColumnMenuItem.set_active(self.settings.get_boolean("show-type-column"))
            column_type.set_visible(self.settings.get_boolean("show-type-column"))
            typeColumnMenuItem.connect("toggled", self.setVisibleColumn, column_type, "show-type-column")
            visibleColumnsMenu.append(typeColumnMenuItem)

            packageColumnMenuItem = Gtk.CheckMenuItem(_("Package"))
            packageColumnMenuItem.set_active(self.settings.get_boolean("show-package-column"))
            column_name.set_visible(self.settings.get_boolean("show-package-column"))
            packageColumnMenuItem.connect("toggled", self.setVisibleColumn, column_name, "show-package-column")
            visibleColumnsMenu.append(packageColumnMenuItem)

            oldVersionColumnMenuItem = Gtk.CheckMenuItem(_("Old Version"))
            oldVersionColumnMenuItem.set_active(self.settings.get_boolean("show-old-version-column"))
            column_old_version.set_visible(self.settings.get_boolean("show-old-version-column"))
            oldVersionColumnMenuItem.connect("toggled", self.setVisibleColumn, column_old_version, "show-old-version-column")
            visibleColumnsMenu.append(oldVersionColumnMenuItem)

            newVersionColumnMenuItem = Gtk.CheckMenuItem(_("New Version"))
            newVersionColumnMenuItem.set_active(self.settings.get_boolean("show-new-version-column"))
            column_new_version.set_visible(self.settings.get_boolean("show-new-version-column"))
            newVersionColumnMenuItem.connect("toggled", self.setVisibleColumn, column_new_version, "show-new-version-column")
            visibleColumnsMenu.append(newVersionColumnMenuItem)

            sizeColumnMenuItem = Gtk.CheckMenuItem(_("Origin"))
            sizeColumnMenuItem.set_active(self.settings.get_boolean("show-origin-column"))
            column_origin.set_visible(self.settings.get_boolean("show-origin-column"))
            sizeColumnMenuItem.connect("toggled", self.setVisibleColumn, column_origin, "show-origin-column")
            visibleColumnsMenu.append(sizeColumnMenuItem)

            sizeColumnMenuItem = Gtk.CheckMenuItem(_("Size"))
            sizeColumnMenuItem.set_active(self.settings.get_boolean("show-size-column"))
            column_size.set_visible(self.settings.get_boolean("show-size-column"))
            sizeColumnMenuItem.connect("toggled", self.setVisibleColumn, column_size, "show-size-column")
            visibleColumnsMenu.append(sizeColumnMenuItem)

            viewSubmenu.append(visibleColumnsMenuItem)

            descriptionsMenuItem = Gtk.CheckMenuItem(_("Show Descriptions"))
            descriptionsMenuItem.set_active(self.settings.get_boolean("show-descriptions"))
            descriptionsMenuItem.connect("toggled", self.setVisibleDescriptions)
            viewSubmenu.append(descriptionsMenuItem)

            viewSubmenu.append(historyMenuItem)

            try:
                # Only support kernel selection in Linux Mint (not LMDE)
                release_info = subprocess.run(["lsb_release", "-irs"], stdout=subprocess.PIPE).stdout.decode().split("\n")
                if release_info[0] == "LinuxMint" and float(release_info[1]) >= 13:
                    viewSubmenu.append(kernelMenuItem)
            except Exception as e:
                print (e)
                print(sys.exc_info()[0])
            viewSubmenu.append(infoMenuItem)
            helpMenu = Gtk.MenuItem.new_with_mnemonic(_("_Help"))
            helpSubmenu = Gtk.Menu()
            helpMenu.set_submenu(helpSubmenu)

            helpMenuItem = Gtk.ImageMenuItem()
            helpMenuItem.set_image(Gtk.Image.new_from_icon_name("security-high-symbolic", Gtk.IconSize.MENU))
            helpMenuItem.set_label(_("Welcome Screen"))
            helpMenuItem.connect("activate", self.show_welcome_page)
            helpSubmenu.append(helpMenuItem)
            if (Gtk.check_version(3,20,0) is None):
                shortcutsMenuItem = Gtk.ImageMenuItem()
                shortcutsMenuItem.set_label(_("Keyboard Shortcuts"))
                shortcutsMenuItem.set_image(Gtk.Image.new_from_icon_name("preferences-desktop-keyboard-shortcuts-symbolic", Gtk.IconSize.MENU))
                shortcutsMenuItem.connect("activate", self.open_shortcuts)
                helpSubmenu.append(shortcutsMenuItem)
            helpMenuItem = Gtk.ImageMenuItem()
            helpMenuItem.set_image(Gtk.Image.new_from_icon_name("help-contents-symbolic", Gtk.IconSize.MENU))
            helpMenuItem.set_label(_("Contents"))
            helpMenuItem.connect("activate", self.open_help)
            key, mod = Gtk.accelerator_parse("F1")
            helpMenuItem.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            helpSubmenu.append(helpMenuItem)
            aboutMenuItem = Gtk.ImageMenuItem()
            aboutMenuItem.set_image(Gtk.Image.new_from_icon_name("help-about-symbolic", Gtk.IconSize.MENU))
            aboutMenuItem.set_label(_("About"))
            aboutMenuItem.connect("activate", self.open_about)
            helpSubmenu.append(aboutMenuItem)

            self.builder.get_object("menubar1").append(fileMenu)
            self.builder.get_object("menubar1").append(editMenu)
            self.builder.get_object("menubar1").append(viewMenu)
            self.builder.get_object("menubar1").append(helpMenu)

            if len(sys.argv) > 1:
                showWindow = sys.argv[1]
                if (showWindow == "show"):
                    self.window.set_sensitive(False)
                    self.window.show_all()
                    self.builder.get_object("paned1").set_position(self.settings.get_int('window-pane-position'))
                    self.app_hidden = False

            # Status pages
            self.stack.add_named(self.builder.get_object("status_updated"), "status_updated")
            self.stack.add_named(self.builder.get_object("status_error"), "status_error")
            self.stack.add_named(self.builder.get_object("status_self-update"), "status_self-update")

            self.stack.show_all()
            if self.settings.get_boolean("show-welcome-page"):
                self.window.set_sensitive(True)
                self.show_welcome_page()
            else:
                self.stack.set_visible_child_name("updates_available")
                self.cache_watcher = CacheWatcher(self)
                self.cache_watcher.start()

            self.builder.get_object("notebook_details").set_current_page(0)

            self.window.resize(self.settings.get_int('window-width'), self.settings.get_int('window-height'))
            self.builder.get_object("paned1").set_position(self.settings.get_int('window-pane-position'))

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
        info_label.set_markup(f"<b>{title}</b>\n{msg}")
        infobar.get_content_area().pack_start(info_label, False, False, 0)
        if callback:
            if msg_type == Gtk.MessageType.QUESTION:
                infobar.add_button(_("Yes"), Gtk.ResponseType.YES)
                infobar.add_button(_("No"), Gtk.ResponseType.NO)
            else:
                infobar.add_button(_("OK"), Gtk.ResponseType.OK)
            infobar.connect("response", callback)
        infobar.show_all()
        self.infobar.pack_start(infobar, True, True, 2)

######### WINDOW/STATUSICON ##########

    def close_window(self, window, event):
        self.save_window_size()
        self.hide_main_window(window)
        return True

    def save_window_size(self):
        self.settings.set_int('window-width', self.window.get_size()[0])
        self.settings.set_int('window-height', self.window.get_size()[1])
        self.settings.set_int('window-pane-position', self.builder.get_object("paned1").get_position())

######### MENU/TOOLBAR FUNCTIONS #########

    def hide_main_window(self, widget):
        self.window.hide()
        self.app_hidden = True

    def setVisibleColumn(self, checkmenuitem, column, key):
        state = checkmenuitem.get_active()
        self.settings.set_boolean(key, state)
        column.set_visible(state)

    def setVisibleDescriptions(self, checkmenuitem):
        self.settings.set_boolean("show-descriptions", checkmenuitem.get_active())
        self.refresh()

    def clear(self, widget):
        model = self.treeview.get_model()
        iter = model.get_iter_first()
        while (iter != None):
            model.set_value(iter, 0, "false")
            iter = model.iter_next(iter)
        self.set_status_message(_("No updates selected"))

    def select_all(self, widget):
        self.select_updates()

    def select_updates(self, security=False, kernel=False):
        model = self.treeview.get_model()
        iter = model.get_iter_first()
        while (iter != None):
            update =  model.get_value(iter, UPDATE_OBJ)
            if security:
                if update.type == "security":
                    model.set_value(iter, UPDATE_CHECKED, "true")
            elif kernel:
                if update.type == "kernel":
                    model.set_value(iter, UPDATE_CHECKED, "true")
            else:
                model.set_value(iter, UPDATE_CHECKED, "true")
            iter = model.iter_next(iter)
        iter = model.get_iter_first()
        download_size = 0
        num_selected = 0
        while (iter != None):
            checked = model.get_value(iter, UPDATE_CHECKED)
            if (checked == "true"):
                size = model.get_value(iter, UPDATE_SIZE)
                download_size = download_size + size
                num_selected = num_selected + 1
            iter = model.iter_next(iter)
        if num_selected == 0:
            self.set_status_message(_("No updates selected"))
        else:
            self.set_status_message(ngettext("%(selected)d update selected (%(size)s)", "%(selected)d updates selected (%(size)s)", num_selected) % {'selected':num_selected, 'size':size_to_string(download_size)})

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

    def self_update(self, widget):
        self.select_all(widget)
        self.install(widget)

######### WELCOME PAGE FUNCTIONS #######

    def on_welcome_page_finished(self, button):
        self.settings.set_boolean("show-welcome-page", False)
        self.builder.get_object("toolbar1").set_sensitive(True)
        self.builder.get_object("menubar1").set_sensitive(True)
        self.updates_inhibited = False
        self.cache_watcher = CacheWatcher(self)
        self.cache_watcher.start()

    def show_help(self, button):
        os.system("yelp help:mintupdate/index &")

    def show_welcome_page(self, widget=None):
        self.updates_inhibited = True
        self.stack.set_visible_child_name("welcome")
        self.set_status(_("Welcome to the Update Manager"), _("Welcome to the Update Manager"), "mintupdate-updates-available", True)
        self.set_status_message("")
        self.builder.get_object("toolbar1").set_sensitive(False)
        self.builder.get_object("menubar1").set_sensitive(False)

######### TREEVIEW/SELECTION FUNCTIONS #######

    def celldatafunction_checkbox(self, column, cell, model, iter, data):
        cell.set_property("activatable", True)
        checked = model.get_value(iter, UPDATE_CHECKED)
        if (checked == "true"):
            cell.set_property("active", True)
        else:
            cell.set_property("active", False)

    def treeview_row_activated(self, treeview, path, view_column):
        self.toggled(None, path)

    def toggled(self, renderer, path):
        model = self.treeview.get_model()
        iter = model.get_iter(path)
        if (iter != None):
            checked = model.get_value(iter, UPDATE_CHECKED)
            if (checked == "true"):
                model.set_value(iter, UPDATE_CHECKED, "false")
            else:
                model.set_value(iter, UPDATE_CHECKED, "true")

        iter = model.get_iter_first()
        download_size = 0
        num_selected = 0
        while (iter != None):
            checked = model.get_value(iter, UPDATE_CHECKED)
            if (checked == "true"):
                size = model.get_value(iter, UPDATE_SIZE)
                download_size = download_size + size
                num_selected = num_selected + 1
            iter = model.iter_next(iter)
        if num_selected == 0:
            self.set_status_message(_("No updates selected"))
        else:
            self.set_status_message(ngettext("%(selected)d update selected (%(size)s)", "%(selected)d updates selected (%(size)s)", num_selected) % {'selected':num_selected, 'size':size_to_string(download_size)})

    def display_selected_package(self, selection):
        try:
            self.textview_packages.set_text("")
            self.textview_description.set_text("")
            self.textview_changes.set_text("")
            (model, iter) = selection.get_selected()
            if (iter != None):
                package_update = model.get_value(iter, UPDATE_OBJ)
                self.display_package_list(package_update)
                self.display_package_description(package_update)
                if self.notebook_details.get_current_page() == 2:
                    # Changelog tab
                    retriever = ChangelogRetriever(package_update, self)
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
            package_update = model.get_value(iter, UPDATE_OBJ)
            retriever = ChangelogRetriever(package_update, self)
            retriever.start()
            self.changelog_retriever_started = True

    def display_package_list(self, package_update):
        prefix = "\n    • "
        count = len(package_update.package_names)
        packages = "%s%s%s\n%s %s\n\n" % \
            (ngettext("This update affects the following installed package:",
                      "This update affects the following installed packages:",
                      count),
             prefix,
             prefix.join(sorted(package_update.package_names)),
             _("Total size:"), size_to_string(package_update.size))
        self.textview_packages.set_text(packages)

    def display_package_description(self, package_update):
        description = package_update.description.replace("\\n", "\n")
        self.textview_description.set_text(description)

    def treeview_right_clicked(self, widget, event):
        if event.button == 3:
            (model, iter) = widget.get_selection().get_selected()
            if (iter != None):
                package_update = model.get_value(iter, UPDATE_OBJ)
                menu = Gtk.Menu()
                menuItem = Gtk.MenuItem.new_with_mnemonic(_("Ignore the current update for this package"))
                menuItem.connect("activate", self.add_to_ignore_list,
                                    f"{package_update.real_source_name}={package_update.new_version}")
                menu.append(menuItem)
                menuItem = Gtk.MenuItem.new_with_mnemonic(_("Ignore all future updates for this package"))
                menuItem.connect("activate", self.add_to_ignore_list, package_update.real_source_name)
                menu.append(menuItem)
                menu.attach_to_widget (widget, None)
                menu.show_all()
                menu.popup(None, None, None, None, event.button, event.time)

    def add_to_ignore_list(self, widget, pkg):
        blacklist = self.settings.get_strv("blacklisted-packages")
        blacklist.append(pkg)
        self.settings.set_strv("blacklisted-packages", blacklist)
        self.refresh()

######### SYSTRAY #########

    def show_statusicon_menu(self, icon, button, time, menu):
        menu.show_all()
        menu.popup(None, None, None, None, button, time)

    def on_statusicon_clicked(self, widget):
        if (self.app_hidden):
            self.window.show_all()
        else:
            self.window.hide()
            self.save_window_size()
        self.app_hidden = not self.app_hidden

    def quit_from_systray(self, widget, data = None):
        if data:
            data.set_visible(False)
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

        treeview = builder.get_object("treeview_history")
        column_date = Gtk.TreeViewColumn(_("Date"), Gtk.CellRendererText(), text=1)
        column_date.set_sort_column_id(1)
        column_date.set_resizable(True)
        column_package = Gtk.TreeViewColumn(_("Package"), Gtk.CellRendererText(), text=0)
        column_package.set_sort_column_id(0)
        column_package.set_resizable(True)
        column_old_version = Gtk.TreeViewColumn(_("Old Version"), Gtk.CellRendererText(), text=2)
        column_old_version.set_sort_column_id(2)
        column_old_version.set_resizable(True)
        column_new_version = Gtk.TreeViewColumn(_("New Version"), Gtk.CellRendererText(), text=3)
        column_new_version.set_sort_column_id(3)
        column_new_version.set_resizable(True)
        treeview.append_column(column_date)
        treeview.append_column(column_package)
        treeview.append_column(column_old_version)
        treeview.append_column(column_new_version)
        treeview.set_headers_clickable(True)
        treeview.set_reorderable(False)
        treeview.set_search_column(0)
        treeview.set_enable_search(True)
        treeview.show()

        model = Gtk.TreeStore(str, str, str, str) # (packageName, date, oldVersion, newVersion)
        if os.path.isfile("/var/log/dpkg.log"):
            updates = subprocess.run('zgrep " upgrade " -sh /var/log/dpkg.log*',
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True)\
                .stdout.decode().split("\n")
            updates.sort(reverse=True)
            for pkg in updates:
                values = pkg.split(" ")
                if len(values) == 6:
                    (date, time, action, package, oldVersion, newVersion) = values
                    if action != "upgrade" or oldVersion == newVersion:
                        continue
                    if ":" in package:
                        package = package.split(":")[0]

                    iter = model.insert_before(None, None)
                    model.set_value(iter, 0, package)
                    model.row_changed(model.get_path(iter), iter)
                    model.set_value(iter, 1, "%s - %s" % (date, time))
                    model.set_value(iter, 2, oldVersion)
                    model.set_value(iter, 3, newVersion)

        # model.set_sort_column_id( 1, Gtk.SortType.DESCENDING )
        treeview.set_model(model)
        del model
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
        window.present()

######### PREFERENCES SCREEN #########

    def open_preferences(self, widget):
        if self.preferences_window_showing:
            return
        self.window.set_sensitive(False)
        gladefile = "/usr/share/linuxmint/mintupdate/preferences.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_title(_("Preferences"))
        window.set_icon_name("mintupdate")

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

        builder.get_object("checkbutton_hide_window_after_update").set_active(self.settings.get_boolean("hide-window-after-update"))
        builder.get_object("checkbutton_hide_systray").set_active(self.settings.get_boolean("hide-systray"))
        builder.get_object("checkbutton_default_repo_is_ok").set_active(self.settings.get_boolean("default-repo-is-ok"))
        builder.get_object("checkbutton_warning_timeshift").set_active(self.settings.get_boolean("warn-about-timeshift"))
        builder.get_object("auto_upgrade_checkbox").set_active(os.path.isfile(AUTOMATIONS["upgrade"][0]))
        builder.get_object("auto_autoremove_checkbox").set_active(os.path.isfile(AUTOMATIONS["autoremove"][0]))

        def set_GtkSpinButton(name, value, range_min=0, range_max=1, increment_step=1, increment_page=10):
            obj = builder.get_object(name)
            obj.set_range(range_min, range_max)
            obj.set_increments(increment_step, increment_page)
            obj.set_value(value)

        set_GtkSpinButton("refresh_days", self.settings.get_int("refresh-days"), range_max=99, increment_page=2)
        set_GtkSpinButton("refresh_hours", self.settings.get_int("refresh-hours"), range_max=23, increment_page=5)
        set_GtkSpinButton("refresh_minutes", self.settings.get_int("refresh-minutes"), range_max=59, increment_page=10)
        set_GtkSpinButton("autorefresh_days", self.settings.get_int("autorefresh-days"), range_max=99, increment_page=2)
        set_GtkSpinButton("autorefresh_hours", self.settings.get_int("autorefresh-hours"), range_max=23, increment_page=5)
        set_GtkSpinButton("autorefresh_minutes", self.settings.get_int("autorefresh-minutes"), range_max=59, increment_page=10)

        builder.get_object("checkbutton_refresh_schedule_enabled").set_active(self.refresh_schedule_enabled)
        builder.get_object("checkbutton_refresh_schedule_enabled").connect("toggled", self.on_refresh_schedule_toggled, builder)

        treeview_blacklist = builder.get_object("treeview_blacklist")
        column = Gtk.TreeViewColumn(_("Ignored Updates"), Gtk.CellRendererText(), text=0)
        column.set_sort_column_id(0)
        column.set_resizable(True)
        treeview_blacklist.append_column(column)
        treeview_blacklist.set_headers_clickable(True)
        treeview_blacklist.set_reorderable(False)
        treeview_blacklist.show()
        model = Gtk.TreeStore(str)
        model.set_sort_column_id( 0, Gtk.SortType.ASCENDING )
        treeview_blacklist.set_model(model)
        blacklist = self.settings.get_strv("blacklisted-packages")
        for ignored_pkg in blacklist:
            iter = model.insert_before(None, None)
            model.set_value(iter, 0, ignored_pkg)

        window.connect("destroy", self.close_preferences, window)
        builder.get_object("pref_button_cancel").connect("clicked", self.close_preferences, window)
        builder.get_object("pref_button_apply").connect("clicked", self.save_preferences, builder)
        builder.get_object("button_add").connect("clicked", self.add_blacklisted_package, treeview_blacklist, window)
        builder.get_object("button_remove").connect("clicked", self.remove_blacklisted_package, treeview_blacklist)
        builder.get_object("button_add").set_always_show_image(True)
        builder.get_object("button_remove").set_always_show_image(True)
        builder.get_object("export_blacklist_button").connect("clicked", self.export_blacklist)
        self.preferences_window_showing = True

        window.show_all()
        builder.get_object("refresh_grid").set_visible(self.refresh_schedule_enabled)

    def on_refresh_schedule_toggled(self, widget, builder):
        builder.get_object("refresh_grid").set_visible(widget.get_active())

    def export_blacklist(self, widget):
        filename = os.path.join(tempfile.gettempdir(), "mintUpdate/blacklist")
        blacklist = self.settings.get_strv("blacklisted-packages")
        with open(filename, "w") as f:
            f.write("\n".join(blacklist) + "\n")
        subprocess.run(["pkexec", "/usr/bin/mintupdate-automation", "blacklist", "enable"])

    @staticmethod
    def set_automation(automation_id, builder):
        active = builder.get_object("auto_%s_checkbox" % automation_id).get_active()
        exists = os.path.isfile(AUTOMATIONS[automation_id][0])
        action = None
        if active and not exists:
            action = "enable"
        elif not active and exists:
            action = "disable"
        if action:
            subprocess.run(["pkexec", "/usr/bin/mintupdate-automation", automation_id, action])

    def save_preferences(self, widget, builder):
        self.settings.set_boolean('hide-window-after-update', builder.get_object("checkbutton_hide_window_after_update").get_active())
        self.settings.set_boolean('hide-systray', builder.get_object("checkbutton_hide_systray").get_active())
        self.settings.set_boolean('default-repo-is-ok', builder.get_object("checkbutton_default_repo_is_ok").get_active())
        self.settings.set_boolean('warn-about-timeshift', builder.get_object("checkbutton_warning_timeshift").get_active())
        self.settings.set_int('refresh-days', int(builder.get_object("refresh_days").get_value()))
        self.settings.set_int('refresh-hours', int(builder.get_object("refresh_hours").get_value()))
        self.settings.set_int('refresh-minutes', int(builder.get_object("refresh_minutes").get_value()))
        self.settings.set_int('autorefresh-days', int(builder.get_object("autorefresh_days").get_value()))
        self.settings.set_int('autorefresh-hours', int(builder.get_object("autorefresh_hours").get_value()))
        self.settings.set_int('autorefresh-minutes', int(builder.get_object("autorefresh_minutes").get_value()))
        blacklist = []
        treeview_blacklist = builder.get_object("treeview_blacklist")
        model = treeview_blacklist.get_model()
        iter = model.get_iter_first()
        while iter is not None:
            pkg = model.get_value(iter, UPDATE_CHECKED)
            iter = model.iter_next(iter)
            blacklist.append(pkg)
        self.settings.set_strv("blacklisted-packages", blacklist)

        self.set_automation("upgrade", builder)
        self.set_automation("autoremove", builder)

        self.refresh_schedule_enabled = builder.get_object("checkbutton_refresh_schedule_enabled").get_active()
        self.settings.set_boolean('refresh-schedule-enabled', self.refresh_schedule_enabled)
        if self.refresh_schedule_enabled and not self.auto_refresh.is_alive():
            self.auto_refresh = AutomaticRefreshThread(self)
            self.auto_refresh.start()

        self.close_preferences(widget, builder.get_object("main_window"))
        self.refresh()

    def add_blacklisted_package(self, widget, treeview_blacklist, window):
        dialog = Gtk.MessageDialog(window, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK, None)
        dialog.set_markup(_("Please specify the source package name of the update to ignore (wildcards are supported) and optionally the version:"))
        dialog.set_title(_("Ignore an Update"))
        dialog.set_icon_name("mintupdate")
        grid = Gtk.Grid()
        grid.set_column_spacing(5)
        grid.set_halign(Gtk.Align.CENTER)
        name_entry = Gtk.Entry()
        version_entry = Gtk.Entry()
        grid.attach(Gtk.Label(_("Name:")), 0, 0, 1, 1)
        grid.attach(name_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(_("Version:")), 0, 1, 1, 1)
        grid.attach(version_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label(_("(optional)")), 2, 1, 1, 1)
        dialog.get_content_area().add(grid)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            name = name_entry.get_text().strip()
            version = version_entry.get_text().strip()
            if name:
                if version:
                    pkg = f"{name}={version}"
                else:
                    pkg = name
                model = treeview_blacklist.get_model()
                iter = model.insert_before(None, None)
                model.set_value(iter, 0, pkg)
        dialog.destroy()

    def remove_blacklisted_package(self, widget, treeview_blacklist):
        selection = treeview_blacklist.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            pkg = model.get_value(iter, UPDATE_CHECKED)
            model.remove(iter)

    def close_preferences(self, widget, window):
        self.window.set_sensitive(True)
        self.preferences_window_showing = False
        window.destroy()

######### KERNEL FEATURES #########

    def open_kernels(self, widget):
        kernel_window = KernelWindow(self)

if __name__ == "__main__":
    MintUpdate()
