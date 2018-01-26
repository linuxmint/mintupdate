#!/usr/bin/python3

import os
import sys
import gi
import tempfile
import threading
import time
import gettext
import io
import tarfile
import urllib.request
import proxygsettings
import subprocess
import pycurl
import datetime
import configparser
import traceback
import setproctitle

from kernelwindow import KernelWindow
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0') # Needed to get xid
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, Gdk, GdkPixbuf, GdkX11, Gio, Pango
from gi.repository import AppIndicator3 as AppIndicator

from Classes import Update

try:
    numMintUpdate = subprocess.check_output("ps -A | grep mintUpdate | wc -l", shell = True)
    if (numMintUpdate != "0"):
        os.system("killall mintUpdate")
except Exception as e:
    print (e)
    print(sys.exc_info()[0])

setproctitle.setproctitle("mintUpdate")

# i18n
gettext.install("mintupdate", "/usr/share/linuxmint/locale")

(TAB_UPDATES, TAB_UPTODATE, TAB_ERROR) = range(3)

(UPDATE_CHECKED, UPDATE_DISPLAY_NAME, UPDATE_LEVEL_PIX, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_SOURCE, UPDATE_LEVEL_STR, UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ) = range(14)

GIGABYTE = 1024 ** 3
MEGABYTE = 1024 ** 2
KILOBYTE = 1024

def size_to_string(size):
    if (size >= GIGABYTE):
        return str(size // GIGABYTE) + _("GB")
    if (size >= (MEGABYTE)):
        return str(size // MEGABYTE) + _("MB")
    if (size >= KILOBYTE):
        return str(size // KILOBYTE) + _("KB")
    return str(size) + _("B")

class ChangelogRetriever(threading.Thread):
    def __init__(self, package_update, application):
        threading.Thread.__init__(self)
        self.source_package = package_update.real_source_name
        self.level = package_update.level
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
        self.application.builder.get_object("textview_changes").get_buffer().set_text(_("Downloading changelog..."))
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
        self.application.builder.get_object("textview_changes").get_buffer().set_text("")
        for change in changelog:
            self.application.builder.get_object("textview_changes").get_buffer().insert(self.application.builder.get_object("textview_changes").get_buffer().get_end_iter(), change)
            self.application.builder.get_object("textview_changes").get_buffer().insert(self.application.builder.get_object("textview_changes").get_buffer().get_end_iter(), "\n")
        Gdk.threads_leave()

class AutomaticRefreshThread(threading.Thread):
    def __init__(self, application):
        threading.Thread.__init__(self)
        self.application = application

    def run(self):
        # Initial refresh (with APT cache refresh)
        try:
            timer = (self.application.settings.get_int("refresh-minutes") * 60) + (self.application.settings.get_int("refresh-hours") * 60 * 60) + (self.application.settings.get_int("refresh-days") * 24 * 60 * 60)
            self.application.logger.write("Initial refresh will happen in " + str(self.application.settings.get_int("refresh-minutes")) + " minutes, " + str(self.application.settings.get_int("refresh-hours")) + " hours and " + str(self.application.settings.get_int("refresh-days")) + " days")
            timetosleep = int(timer)
            if (timetosleep == 0):
                time.sleep(60) # sleep 1 minute, don't mind the config we don't want an infinite loop to go nuts :)
            else:
                time.sleep(timetosleep)
                if (self.application.app_hidden == True):
                    self.application.logger.write("MintUpdate is in tray mode, performing initial refresh")
                    refresh = RefreshThread(self.application, root_mode=True)
                    refresh.start()
                else:
                    self.application.logger.write("The mintUpdate window is open, skipping initial refresh")
        except Exception as e:
            print (e)
            self.application.logger.write_error("Exception occurred during the initial refresh: " + str(sys.exc_info()[0]))

        # Autorefresh (also with APT cache refresh)
        try:
            while(True):
                timer = (self.application.settings.get_int("autorefresh-minutes") * 60) + (self.application.settings.get_int("autorefresh-hours") * 60 * 60) + (self.application.settings.get_int("autorefresh-days") * 24 * 60 * 60)
                self.application.logger.write("Auto-refresh will happen in " + str(self.application.settings.get_int("autorefresh-minutes")) + " minutes, " + str(self.application.settings.get_int("autorefresh-hours")) + " hours and " + str(self.application.settings.get_int("autorefresh-days")) + " days")
                timetosleep = int(timer)
                if (timetosleep == 0):
                    time.sleep(60) # sleep 1 minute, don't mind the config we don't want an infinite loop to go nuts :)
                else:
                    time.sleep(timetosleep)
                    if (self.application.app_hidden == True):
                        self.application.logger.write("MintUpdate is in tray mode, performing auto-refresh")
                        refresh = RefreshThread(self.application, root_mode=True)
                        refresh.start()
                    else:
                        self.application.logger.write("The mintUpdate window is open, skipping auto-refresh")
        except Exception as e:
            print (e)
            self.application.logger.write_error("Exception occurred in the auto-refresh thread.. so it's probably dead now: " + str(sys.exc_info()[0]))

class InstallThread(threading.Thread):

    def __init__(self, application):
        threading.Thread.__init__(self)
        self.application = application
        self.application.window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        self.application.window.set_sensitive(False)

    def run(self):
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
                                dialog = Gtk.MessageDialog(None, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.WARNING, Gtk.ButtonsType.OK_CANCEL, None)
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
                                    column1 = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                                    column1.set_sort_column_id(0)
                                    column1.set_resizable(True)
                                    treeview.append_column(column1)
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
                                    column1 = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                                    column1.set_sort_column_id(0)
                                    column1.set_resizable(True)
                                    treeview.append_column(column1)
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
                    cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                            "--non-interactive", "--parent-window-id", "%s" % self.application.window.get_window().get_xid(), "-o", "Synaptic::closeZvt=true"]
                    f = tempfile.NamedTemporaryFile()

                    for pkg in packages:
                        pkg_line = "%s\tinstall\n" % pkg
                        f.write(pkg_line.encode("utf-8"))

                    cmd.append("--set-selections-file")
                    cmd.append("%s" % f.name)
                    f.flush()
                    comnd = subprocess.Popen(' '.join(cmd), stdout=self.application.logger.log, stderr=self.application.logger.log, shell=True)
                    returnCode = comnd.wait()
                    self.application.logger.write("Return code:" + str(returnCode))
                    f.close()

                    latest_apt_update = ''
                    update_successful = False
                    with open("/var/log/apt/history.log") as apt_history:
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
                        if self.application.settings.get_boolean("hide-window-after-update"):
                            Gdk.threads_enter()
                            self.application.app_hidden = True
                            self.application.window.hide()
                            Gdk.threads_leave()

                        if "mintupdate" in packages or "mint-upgrade-info" in packages:
                            # Restart
                            try:
                                self.application.logger.write("Mintupdate was updated, restarting it...")
                                self.application.logger.close()
                            except:
                                pass #cause we might have closed it already

                            command = "/usr/lib/linuxmint/mintUpdate/mintUpdate.py show &"
                            os.system(command)

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

    def check_policy(self):
        # Check the presence of the Mint layer
        p1 = subprocess.Popen(['apt-cache', 'policy'], stdout=subprocess.PIPE)
        p = p1.communicate()[0]
        mint_layer_found = False
        output = p.decode("utf-8").split('\n')
        for line in output:
            line = line.strip()
            if line.startswith("700") and line.endswith("Packages") and "/upstream" in line:
                mint_layer_found = True
                break
        return mint_layer_found

    def run(self):

        if self.application.updates_inhibited:
            self.application.logger.write("Updates are inhibited, skipping refresh.")
            return False

        Gdk.threads_enter()
        vpaned_position = self.application.builder.get_object("paned1").get_position()
        for child in self.application.builder.get_object("hbox_infobar").get_children():
            child.destroy()

        context = self.application.builder.get_object("textview_description").get_style_context()
        insensitive_color = context.get_color(Gtk.StateFlags.INSENSITIVE)
        insensitive_color = "#{0:02x}{1:02x}{2:02x}".format(int(insensitive_color.red  * 255), int(insensitive_color.green * 255), int(insensitive_color.blue * 255))
        Gdk.threads_leave()
        try:
            if (self.root_mode):
                self.application.logger.write("Starting refresh (including refreshing the APT cache)")
            else:
                self.application.logger.write("Starting refresh")
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

            model = Gtk.TreeStore(str, str, GdkPixbuf.Pixbuf, str, str, str, str, int, str, str, str, str, str, object)
            # UPDATE_CHECKED, UPDATE_DISPLAY_NAME, UPDATE_LEVEL_PIX, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_SOURCE, UPDATE_LEVEL_STR,
            # UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ

            model.set_sort_column_id( UPDATE_SORT_STR, Gtk.SortType.ASCENDING )

            # Check to see if no other APT process is running
            if self.root_mode:
                p1 = subprocess.Popen(['ps', '-U', 'root', '-o', 'comm'], stdout=subprocess.PIPE)
                p = p1.communicate()[0]
                running = False
                pslist = p.split(b'\n')
                for process in pslist:
                    if process.strip() in ["dpkg", "apt-get","synaptic","update-manager", "adept", "adept-notifier"]:
                        running = True
                        break
                if (running == True):
                    Gdk.threads_enter()
                    self.application.set_status(_("Another application is using APT"), _("Another application is using APT"), "mintupdate-checking", not self.application.settings.get_boolean("hide-systray"))
                    self.application.logger.write_error("Another application is using APT")
                    if (not self.application.app_hidden):
                        self.application.window.get_window().set_cursor(None)
                    self.application.window.set_sensitive(True)
                    Gdk.threads_leave()
                    return False

            Gdk.threads_enter()
            self.application.set_status_message(_("Finding the list of updates..."))
            self.application.builder.get_object("paned1").set_position(vpaned_position)
            Gdk.threads_leave()
            if self.application.app_hidden:
                refresh_command = "/usr/lib/linuxmint/mintUpdate/checkAPT.py 2>/dev/null"
            else:
                refresh_command = "/usr/lib/linuxmint/mintUpdate/checkAPT.py --use-synaptic %s 2>/dev/null" % self.application.window.get_window().get_xid()
            if self.root_mode:
                refresh_command = "sudo %s" % refresh_command
            output =  subprocess.check_output(refresh_command, shell = True).decode("utf-8")

            if len(output) > 0 and not "CHECK_APT_ERROR" in output:
                if not self.check_policy():
                    Gdk.threads_enter()
                    label1 = _("Your APT cache is corrupted.")
                    label2 = _("Do not install or update anything, it could break your operating system!")
                    label3 = _("To switch to a different Linux Mint mirror and solve this problem, click OK.")
                    infobar = Gtk.InfoBar()
                    infobar.set_message_type(Gtk.MessageType.ERROR)
                    info_label = Gtk.Label()
                    infobar_message = "%s\n<small>%s</small>" % (_("Please switch to another Linux Mint mirror"), _("Your APT cache is corrupted."))
                    info_label.set_markup(infobar_message)
                    infobar.get_content_area().pack_start(info_label,False, False,0)
                    infobar.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
                    infobar.connect("response", self._on_infobar_response)
                    self.application.builder.get_object("hbox_infobar").pack_start(infobar, True, True,0)
                    infobar.show_all()
                    self.application.set_status(_("Could not refresh the list of updates"), "%s\n%s\n%s" % (label1, label2, label3), "mintupdate-error", True)
                    self.application.logger.write("Error: The APT policy is incorrect!")
                    self.application.stack.set_visible_child_name("status_error")
                    self.application.builder.get_object("label_error_details").set_markup("<b>%s\n%s\n%s</b>" % (label1, label2, label3))
                    self.application.builder.get_object("label_error_details").show()
                    if (not self.application.app_hidden):
                            self.application.window.get_window().set_cursor(None)
                    self.application.window.set_sensitive(True)
                    Gdk.threads_leave()
                    return False

            lines = output.split("---EOL---")

            # Look at the updates one by one
            num_visible = 0
            num_checked = 0
            download_size = 0

            if (len(lines) == None):
                Gdk.threads_enter()
                self.application.stack.set_visible_child_name("status_updated")
                self.application.set_status(_("Your system is up to date"), _("Your system is up to date"), "mintupdate-up-to-date", not self.application.settings.get_boolean("hide-systray"))
                self.application.logger.write("System is up to date")
                Gdk.threads_leave()
            else:
                for line in lines:
                    if line.startswith("CHECK_APT_ERROR"):
                        try:
                            error_msg = lines[1].replace("E:", "\n")
                        except:
                            error_msg = ""
                        Gdk.threads_enter()
                        self.application.set_status(_("Could not refresh the list of updates"), "%s\n\n%s" % (_("Could not refresh the list of updates"), error_msg), "mintupdate-error", True)
                        self.application.logger.write("Error in checkAPT.py, could not refresh the list of updates")
                        self.application.stack.set_visible_child_name("status_error")
                        self.application.builder.get_object("label_error_details").set_markup("<b>%s</b>" % error_msg)
                        self.application.builder.get_object("label_error_details").show()
                        if (not self.application.app_hidden):
                            self.application.window.get_window().set_cursor(None)
                        self.application.window.set_sensitive(True)
                        Gdk.threads_leave()
                        return False

                    if "###" in line:
                        update = Update(package=None, input_string=line, source_name=None)

                        level_is_visible = self.application.settings.get_boolean('level%s-is-visible' % str(update.level))
                        level_is_safe = self.application.settings.get_boolean('level%s-is-safe' % str(update.level))

                        if update.type == "kernel":
                            visible = (level_is_visible or self.application.settings.get_boolean('kernel-updates-are-visible'))
                            safe = (level_is_safe or self.application.settings.get_boolean('kernel-updates-are-safe'))
                        elif update.type == "security":
                            visible = (level_is_visible or self.application.settings.get_boolean('security-updates-are-visible'))
                            safe = (level_is_safe or self.application.settings.get_boolean('security-updates-are-safe'))
                        else:
                            visible = level_is_visible
                            safe = level_is_safe

                        if visible:
                            iter = model.insert_before(None, None)
                            if safe:
                                model.set_value(iter, UPDATE_CHECKED, "true")
                                num_checked = num_checked + 1
                                download_size = download_size + update.size
                            else:
                                model.set_value(iter, UPDATE_CHECKED, "false")

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

                            if (self.application.settings.get_boolean("show-descriptions")):
                                model.set_value(iter, UPDATE_DISPLAY_NAME, update.display_name + "\n<i><small><span foreground='%s'>%s</span></small></i>" % (insensitive_color, shortdesc))
                            else:
                                model.set_value(iter, UPDATE_DISPLAY_NAME, update.display_name)

                            theme = Gtk.IconTheme.get_default()
                            pixbuf = theme.load_icon("mintupdate-level" + str(update.level), 22, 0)

                            origin = update.origin
                            origin = origin.replace("linuxmint", "Linux Mint").replace("ubuntu", "Ubuntu").replace("LP-PPA-", "PPA ")

                            if update.type == "kernel":
                                tooltip = _("Kernel update")
                            elif update.type == "security":
                                tooltip = _("Security update")
                            elif update.type == "backport":
                                tooltip = _("Software backport. Be careful when upgrading. New versions of software can introduce regressions.")
                            elif update.type == "unstable":
                                tooltip = _("Unstable software. Only apply this update to help developers beta-test new software.")
                            else:
                                tooltip = _("Software update")

                            model.set_value(iter, UPDATE_LEVEL_PIX, pixbuf)
                            model.set_value(iter, UPDATE_OLD_VERSION, update.old_version)
                            model.set_value(iter, UPDATE_NEW_VERSION, update.new_version)
                            model.set_value(iter, UPDATE_SOURCE, "%s (%s)" % (origin, update.site))
                            model.set_value(iter, UPDATE_LEVEL_STR, str(update.level))
                            model.set_value(iter, UPDATE_SIZE, update.size)
                            model.set_value(iter, UPDATE_SIZE_STR, size_to_string(update.size))
                            model.set_value(iter, UPDATE_TYPE_PIX, "mintupdate-type-%s" % update.type)
                            model.set_value(iter, UPDATE_TYPE, update.type)
                            model.set_value(iter, UPDATE_TOOLTIP, tooltip)
                            model.set_value(iter, UPDATE_SORT_STR, "%s%s" % (str(update.level), update.display_name))
                            model.set_value(iter, UPDATE_OBJ, update)
                            num_visible = num_visible + 1

                Gdk.threads_enter()
                if (num_visible == 0):
                    self.application.stack.set_visible_child_name("status_updated")
                    self.application.set_status(_("Your system is up to date"), _("Your system is up to date"), "mintupdate-up-to-date", not self.application.settings.get_boolean("hide-systray"))
                    self.application.logger.write("System is up to date")
                else:
                    if (num_checked == 0):
                        self.statusString = _("No updates selected")
                    elif (num_checked >= 1):
                        self.statusString = gettext.ngettext("%(selected)d update selected (%(size)s)", "%(selected)d updates selected (%(size)s)", num_checked) % {'selected':num_checked, 'size':size_to_string(download_size)}

                    self.application.set_status(self.statusString, self.statusString, "mintupdate-updates-available", True)
                    self.application.logger.write("Found " + str(num_visible) + " software updates")

                    if (num_visible >= 1):
                        systrayString = gettext.ngettext("%d update available", "%d updates available", num_visible) % num_visible
                    self.application.statusIcon.set_tooltip_text(systrayString)

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

            try:
                sources_path = "/etc/apt/sources.list.d/official-package-repositories.list"
                if os.path.exists("/usr/bin/mintsources") and os.path.exists(sources_path):
                    mirror_url = None
                    infobar_message = None
                    infobar_message_type = Gtk.MessageType.QUESTION
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
                            infobar_message = "%s\n<small>%s</small>" % (_("Do you want to switch to a local mirror?"), _("Local mirrors are usually faster than packages.linuxmint.com"))
                    elif not self.application.app_hidden:
                        # Only perform up-to-date checks when refreshing from the UI (keep the load lower on servers)
                        mint_timestamp = self.get_url_last_modified("http://packages.linuxmint.com/db/version")
                        mirror_timestamp = self.get_url_last_modified("%s/db/version" % mirror_url)
                        if mirror_timestamp is None:
                            if mint_timestamp is None:
                                # Both default repo and mirror are unreachable, assume there's no Internet connection
                                pass
                            else:
                                infobar_message = "%s\n<small>%s</small>" % (_("Please switch to another mirror"), _("%s is unreachable") % mirror_url)
                                infobar_message_type = Gtk.MessageType.WARNING
                        elif mint_timestamp is not None:
                            mint_date = datetime.datetime.fromtimestamp(mint_timestamp)
                            now = datetime.datetime.now()
                            mint_age = (now - mint_date).days
                            if (mint_age > 2):
                                mirror_date = datetime.datetime.fromtimestamp(mirror_timestamp)
                                mirror_age = (mint_date - mirror_date).days
                                if (mirror_age > 2):
                                    infobar_message = "%s\n<small>%s</small>" % (_("Please switch to another mirror"), gettext.ngettext("The last update on %(mirror)s was %(days)d day ago", "The last update on %(mirror)s was %(days)d days ago", (now - mirror_date).days) % {'mirror': mirror_url, 'days':(now - mirror_date).days})
                                    infobar_message_type = Gtk.MessageType.WARNING
                    if infobar_message is not None:
                        infobar = Gtk.InfoBar()
                        infobar.set_message_type(infobar_message_type)
                        info_label = Gtk.Label()
                        info_label.set_markup(infobar_message)
                        infobar.get_content_area().pack_start(info_label,False, False,0)
                        infobar.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
                        infobar.connect("response", self._on_infobar_response)
                        self.application.builder.get_object("hbox_infobar").pack_start(infobar, True, True,0)
                        infobar.show_all()
            except Exception as e:
                print(sys.exc_info()[0])
                # best effort, just print out the error
                print("An exception occurred while checking if the repositories were up to date: %s" % sys.exc_info()[0])

            Gdk.threads_leave()

        except Exception as e:
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

    def _on_infobar_response(self, infobar, response_id):
        infobar.destroy()
        subprocess.Popen(["mintsources"])

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
        logdir = "/tmp/mintUpdate/"
        if not os.path.exists(logdir):
            os.system("mkdir -p " + logdir)
            os.system("chmod a+rwx " + logdir)
        self.log = tempfile.NamedTemporaryFile(mode = 'w', prefix = logdir, delete=False)
        try:
            os.system("chmod a+rw %s" % self.log.name)
        except Exception as e:
            print (e)
            print(sys.exc_info()[0])

    def write(self, line):
        try:
            self.log.writelines("%s ++ %s \n" % (datetime.datetime.now().strftime('%m.%d@%H:%M'), line))
            self.log.flush()
        except:
            pass # cause it might be closed already

    def write_error(self, line):
        try:
            self.log.writelines("%s -- %s \n" % (datetime.datetime.now().strftime('%m.%d@%H:%M'), line))
            self.log.flush()
        except:
            pass # cause it might be closed already

    def close(self):
        try:
            self.log.close()
        except:
            pass # cause it might be closed already


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
        self.updates_inhibited = False
        self.logger = Logger()
        self.logger.write("Launching mintUpdate")
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
        self.builder.add_from_file(gladefile)
        self.statusbar = self.builder.get_object("statusbar")
        self.context_id = self.statusbar.get_context_id("mintUpdate")
        self.window = self.builder.get_object("main_window")
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

            self.buffer = self.builder.get_object("textview_description").get_buffer()
            self.buffer.create_tag("smaller", scale=0.9, style=Pango.Style.ITALIC)

            # Configure page
            configure_page = self.builder.get_object("configure_page")
            self.stack.add_named(configure_page, "configure")
            self.builder.get_object("button_configure_finish").connect("clicked", self.on_configure_finished)
            self.builder.get_object("button_configure_finish").set_label(_("OK"))
            self.builder.get_object("button_configure_help").connect("clicked", self.on_configure_help)
            self.builder.get_object("button_configure_help").set_label(_("Help"))

            # Updates page
            updates_page = self.builder.get_object("updates_page")
            self.stack.add_named(updates_page, "updates_available")

            # the treeview
            cr = Gtk.CellRendererToggle()
            cr.connect("toggled", self.toggled)
            column1 = Gtk.TreeViewColumn(_("Upgrade"), cr)
            column1.set_cell_data_func(cr, self.celldatafunction_checkbox)
            column1.set_sort_column_id(UPDATE_CHECKED)
            column1.set_resizable(True)

            column2 = Gtk.TreeViewColumn(_("Name"), Gtk.CellRendererText(), markup=UPDATE_DISPLAY_NAME)
            column2.set_sort_column_id(UPDATE_DISPLAY_NAME)
            column2.set_resizable(True)

            column3 = Gtk.TreeViewColumn(_("Level"), Gtk.CellRendererPixbuf(), pixbuf=UPDATE_LEVEL_PIX)
            column3.set_sort_column_id(UPDATE_LEVEL_STR)
            column3.set_resizable(True)

            column4 = Gtk.TreeViewColumn(_("Old version"), Gtk.CellRendererText(), text=UPDATE_OLD_VERSION)
            column4.set_sort_column_id(UPDATE_OLD_VERSION)
            column4.set_resizable(True)

            column5 = Gtk.TreeViewColumn(_("New version"), Gtk.CellRendererText(), text=UPDATE_NEW_VERSION)
            column5.set_sort_column_id(UPDATE_NEW_VERSION)
            column5.set_resizable(True)

            column6 = Gtk.TreeViewColumn(_("Size"), Gtk.CellRendererText(), text=UPDATE_SIZE_STR)
            column6.set_sort_column_id(UPDATE_SIZE)
            column6.set_resizable(True)

            column7 = Gtk.TreeViewColumn(_("Type"), Gtk.CellRendererPixbuf(), icon_name=UPDATE_TYPE_PIX)
            column7.set_sort_column_id(UPDATE_TYPE)
            column7.set_resizable(True)

            column8 = Gtk.TreeViewColumn(_("Origin"), Gtk.CellRendererText(), text=UPDATE_SOURCE)
            column8.set_sort_column_id(UPDATE_SOURCE)
            column8.set_resizable(True)

            self.treeview.set_tooltip_column(UPDATE_TOOLTIP)

            self.treeview.append_column(column7)
            self.treeview.append_column(column3)
            self.treeview.append_column(column1)
            self.treeview.append_column(column2)
            self.treeview.append_column(column4)
            self.treeview.append_column(column5)
            self.treeview.append_column(column8)
            self.treeview.append_column(column6)

            self.treeview.set_headers_clickable(True)
            self.treeview.set_reorderable(False)
            self.treeview.show()

            self.treeview.connect("button-release-event", self.treeview_right_clicked)
            self.treeview.connect("row-activated", self.treeview_row_activated)

            selection = self.treeview.get_selection()
            selection.connect("changed", self.display_selected_package)
            self.builder.get_object("notebook_details").connect("switch-page", self.switch_page)
            self.window.connect("delete_event", self.close_window)
            self.builder.get_object("tool_apply").connect("clicked", self.install)
            self.builder.get_object("tool_clear").connect("clicked", self.clear)
            self.builder.get_object("tool_select_all").connect("clicked", self.select_all)
            self.builder.get_object("tool_refresh").connect("clicked", self.force_refresh)

            menu = Gtk.Menu()
            menuItem3 = Gtk.ImageMenuItem(Gtk.STOCK_REFRESH)
            menuItem3.set_use_stock(True)
            menuItem3.connect('activate', self.force_refresh)
            menu.append(menuItem3)
            menuItem2 = Gtk.ImageMenuItem(Gtk.STOCK_DIALOG_INFO)
            menuItem2.set_use_stock(True)
            menuItem2.connect('activate', self.open_information)
            menu.append(menuItem2)
            menuItem4 = Gtk.ImageMenuItem(Gtk.STOCK_PREFERENCES)
            menuItem4.set_use_stock(True)
            menuItem4.connect('activate', self.open_preferences)
            menu.append(menuItem4)
            menuItem = Gtk.ImageMenuItem(Gtk.STOCK_QUIT)
            menuItem.set_use_stock(True)
            menuItem.connect('activate', self.quit_from_systray)
            menu.append(menuItem)

            if os.getenv("XDG_CURRENT_DESKTOP") != "KDE":
                self.statusIcon.connect('activate', self.on_statusicon_clicked)
                self.statusIcon.connect('popup-menu', self.show_statusicon_menu, menu)

            # Set text for all visible widgets (because of i18n)
            self.builder.get_object("tool_apply").set_label(_("Install Updates"))
            self.builder.get_object("tool_refresh").set_label(_("Refresh"))
            self.builder.get_object("tool_select_all").set_label(_("Select All"))
            self.builder.get_object("tool_clear").set_label(_("Clear"))
            self.builder.get_object("label9").set_text(_("Description"))
            self.builder.get_object("label8").set_text(_("Changelog"))

            self.builder.get_object("label_success").set_markup("<b>" + _("Your system is up to date") + "</b>")
            self.builder.get_object("label_error").set_markup("<b>" + _("Could not refresh the list of updates") + "</b>")
            self.builder.get_object("image_success_status").set_pixel_size(96)
            self.builder.get_object("image_error_status").set_pixel_size(96)

            #l10n for update policy page
            self.builder.get_object("label_welcome1").set_markup("<span size='large'><b>%s</b></span>" % _("Welcome to the Update Manager"))
            self.builder.get_object("label_welcome2").set_markup("%s" % _("This tool provides your operating system with software and security updates."))
            self.builder.get_object("label_welcome3").set_markup("%s" % _("Please choose an update policy."))

            self.builder.get_object("label_policy1_1").set_markup("<b>%s</b>" % _("Just keep my computer safe"))
            self.builder.get_object("label_policy1_2").set_markup("<i>%s</i>" % _("Recommended for novice users."))
            self.builder.get_object("label_policy1_3").set_markup("%s\n%s" % (_("Select updates which do not impact important parts of the operating systems."), _("Show security and kernel updates so I can review them and apply them with caution.")))

            self.builder.get_object("label_policy2_1").set_markup("<b>%s</b>" % _("Let me review sensitive updates"))
            self.builder.get_object("label_policy2_2").set_markup("<i>%s</i>" % _("Recommended for most users."))
            self.builder.get_object("label_policy2_3").set_markup("%s\n%s" % (_("Select security updates and updates which do not impact important parts of the operating systems."), _("Show kernel updates and sensitive updates so I can review them and apply them with caution.")))

            self.builder.get_object("label_policy3_1").set_markup("<b>%s</b>" % _("Always update everything"))
            self.builder.get_object("label_policy3_2").set_markup("<i>%s</i>" % _("Recommended for experienced users."))
            self.builder.get_object("label_policy3_3").set_markup("%s\n%s" % (_("Always select all updates."), _("If a regression breaks something, I'll fix it.")))

            self.builder.get_object("label_policy_hint").set_markup(_("Security and kernel updates are always visible and updates with known issues (very rare) are always hidden. You can change this and fine-tune your policy in the preferences."))

            fileMenu = Gtk.MenuItem.new_with_mnemonic(_("_File"))
            fileSubmenu = Gtk.Menu()
            fileMenu.set_submenu(fileSubmenu)
            closeMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_CLOSE)
            closeMenuItem.set_use_stock(True)
            closeMenuItem.set_image(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU))
            closeMenuItem.set_label(_("Close"))
            closeMenuItem.connect("activate", self.hide_main_window)
            fileSubmenu.append(closeMenuItem)

            editMenu = Gtk.MenuItem.new_with_mnemonic(_("_Edit"))
            editSubmenu = Gtk.Menu()
            editMenu.set_submenu(editSubmenu)
            prefsMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_PREFERENCES)
            prefsMenuItem.set_use_stock(True)
            prefsMenuItem.set_label(_("Preferences"))
            prefsMenuItem.connect("activate", self.open_preferences)
            editSubmenu.append(prefsMenuItem)
            if os.path.exists("/usr/bin/software-sources") or os.path.exists("/usr/bin/software-properties-gtk") or os.path.exists("/usr/bin/software-properties-kde"):
                sourcesMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_PREFERENCES)
                sourcesMenuItem.set_use_stock(True)
                sourcesMenuItem.set_image(Gtk.Image.new_from_icon_name("software-properties", Gtk.IconSize.MENU))
                sourcesMenuItem.set_label(_("Software sources"))
                sourcesMenuItem.connect("activate", self.open_repositories)
                editSubmenu.append(sourcesMenuItem)
            configMenuItem = Gtk.ImageMenuItem()
            configMenuItem.set_image(Gtk.Image.new_from_icon_name("security-medium", Gtk.IconSize.MENU))
            configMenuItem.set_label(_("Update policy"))
            configMenuItem.connect("activate", self.show_configuration)
            editSubmenu.append(configMenuItem)

            editSubmenu.append(Gtk.SeparatorMenuItem())

            item = Gtk.ImageMenuItem(Gtk.STOCK_CLEAR)
            item.set_use_stock(True)
            item.set_label(_("Clear"))
            item.connect("activate", self.clear)
            key, mod = Gtk.accelerator_parse("<Control><Shift>A")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            item = Gtk.ImageMenuItem(Gtk.STOCK_SELECT_ALL)
            item.set_use_stock(True)
            item.set_label(_("Select All"))
            item.connect("activate", self.select_all)
            key, mod = Gtk.accelerator_parse("<Control>A")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            item = Gtk.ImageMenuItem(Gtk.STOCK_REFRESH)
            item.set_use_stock(True)
            item.set_label(_("Refresh"))
            item.connect("activate", self.force_refresh)
            key, mod = Gtk.accelerator_parse("<Control>R")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            item = Gtk.ImageMenuItem(Gtk.STOCK_APPLY)
            item.set_use_stock(True)
            item.set_label(_("Install"))
            item.connect("activate", self.install)
            key, mod = Gtk.accelerator_parse("<Control>I")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            editSubmenu.append(Gtk.SeparatorMenuItem())

            item = Gtk.MenuItem(_("Select level 1 updates"))
            item.connect("activate", self.select_level1)
            key, mod = Gtk.accelerator_parse("<Control>1")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            item = Gtk.MenuItem(_("Select level 2 updates"))
            item.connect("activate", self.select_level2)
            key, mod = Gtk.accelerator_parse("<Control>2")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            item = Gtk.MenuItem(_("Select level 3 updates"))
            item.connect("activate", self.select_level3)
            key, mod = Gtk.accelerator_parse("<Control>3")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            item = Gtk.MenuItem(_("Select level 4 updates"))
            item.connect("activate", self.select_level4)
            key, mod = Gtk.accelerator_parse("<Control>4")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            item = Gtk.MenuItem(_("Select security updates"))
            item.connect("activate", self.select_security_updates)
            key, mod = Gtk.accelerator_parse("<Control>S")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            item = Gtk.MenuItem(_("Select kernel updates"))
            item.connect("activate", self.select_kernel_updates)
            key, mod = Gtk.accelerator_parse("<Control>K")
            item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
            editSubmenu.append(item)

            rel_edition = 'unknown'
            rel_codename = 'unknown'
            if os.path.exists("/etc/linuxmint/info"):
                with open("/etc/linuxmint/info", "r") as info:
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
                    relUpgradeMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_PREFERENCES)
                    relUpgradeMenuItem.set_use_stock(True)
                    relUpgradeMenuItem.set_image(Gtk.Image.new_from_icon_name("mintupdate-release-upgrade", Gtk.IconSize.MENU))
                    relUpgradeMenuItem.set_label(_("Upgrade to %s") % rel_target)
                    relUpgradeMenuItem.connect("activate", self.open_rel_upgrade)
                    editSubmenu.append(relUpgradeMenuItem)

            viewMenu = Gtk.MenuItem.new_with_mnemonic(_("_View"))
            viewSubmenu = Gtk.Menu()
            viewMenu.set_submenu(viewSubmenu)
            historyMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_INDEX)
            historyMenuItem.set_use_stock(True)
            historyMenuItem.set_label(_("History of updates"))
            historyMenuItem.connect("activate", self.open_history)
            kernelMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_EXECUTE)
            kernelMenuItem.set_use_stock(True)
            kernelMenuItem.set_label(_("Linux kernels"))
            kernelMenuItem.connect("activate", self.open_kernels)
            infoMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_DIALOG_INFO)
            infoMenuItem.set_use_stock(True)
            infoMenuItem.set_label(_("Information"))
            infoMenuItem.connect("activate", self.open_information)
            visibleColumnsMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_DIALOG_INFO)
            visibleColumnsMenuItem.set_use_stock(True)
            visibleColumnsMenuItem.set_label(_("Visible columns"))
            visibleColumnsMenu = Gtk.Menu()
            visibleColumnsMenuItem.set_submenu(visibleColumnsMenu)

            typeColumnMenuItem = Gtk.CheckMenuItem(_("Type"))
            typeColumnMenuItem.set_active(self.settings.get_boolean("show-type-column"))
            column7.set_visible(self.settings.get_boolean("show-type-column"))
            typeColumnMenuItem.connect("toggled", self.setVisibleColumn, column7, "show-type-column")
            visibleColumnsMenu.append(typeColumnMenuItem)

            levelColumnMenuItem = Gtk.CheckMenuItem(_("Level"))
            levelColumnMenuItem.set_active(self.settings.get_boolean("show-level-column"))
            column3.set_visible(self.settings.get_boolean("show-level-column"))
            levelColumnMenuItem.connect("toggled", self.setVisibleColumn, column3, "show-level-column")
            visibleColumnsMenu.append(levelColumnMenuItem)

            packageColumnMenuItem = Gtk.CheckMenuItem(_("Package"))
            packageColumnMenuItem.set_active(self.settings.get_boolean("show-package-column"))
            column2.set_visible(self.settings.get_boolean("show-package-column"))
            packageColumnMenuItem.connect("toggled", self.setVisibleColumn, column2, "show-package-column")
            visibleColumnsMenu.append(packageColumnMenuItem)

            oldVersionColumnMenuItem = Gtk.CheckMenuItem(_("Old version"))
            oldVersionColumnMenuItem.set_active(self.settings.get_boolean("show-old-version-column"))
            column4.set_visible(self.settings.get_boolean("show-old-version-column"))
            oldVersionColumnMenuItem.connect("toggled", self.setVisibleColumn, column4, "show-old-version-column")
            visibleColumnsMenu.append(oldVersionColumnMenuItem)

            newVersionColumnMenuItem = Gtk.CheckMenuItem(_("New version"))
            newVersionColumnMenuItem.set_active(self.settings.get_boolean("show-new-version-column"))
            column5.set_visible(self.settings.get_boolean("show-new-version-column"))
            newVersionColumnMenuItem.connect("toggled", self.setVisibleColumn, column5, "show-new-version-column")
            visibleColumnsMenu.append(newVersionColumnMenuItem)

            sizeColumnMenuItem = Gtk.CheckMenuItem(_("Size"))
            sizeColumnMenuItem.set_active(self.settings.get_boolean("show-size-column"))
            column6.set_visible(self.settings.get_boolean("show-size-column"))
            sizeColumnMenuItem.connect("toggled", self.setVisibleColumn, column6, "show-size-column")
            visibleColumnsMenu.append(sizeColumnMenuItem)

            sizeColumnMenuItem = Gtk.CheckMenuItem(_("Origin"))
            sizeColumnMenuItem.set_active(self.settings.get_boolean("show-origin-column"))
            column8.set_visible(self.settings.get_boolean("show-origin-column"))
            sizeColumnMenuItem.connect("toggled", self.setVisibleColumn, column8, "show-origin-column")
            visibleColumnsMenu.append(sizeColumnMenuItem)

            viewSubmenu.append(visibleColumnsMenuItem)

            descriptionsMenuItem = Gtk.CheckMenuItem(_("Show descriptions"))
            descriptionsMenuItem.set_active(self.settings.get_boolean("show-descriptions"))
            descriptionsMenuItem.connect("toggled", self.setVisibleDescriptions)
            viewSubmenu.append(descriptionsMenuItem)

            viewSubmenu.append(historyMenuItem)

            try:
                # Only support kernel selection in Linux Mint (not LMDE)
                if (subprocess.check_output("lsb_release -is", shell = True).strip() == b"LinuxMint" and float(subprocess.check_output("lsb_release -rs", shell = True).strip()) >= 13):
                    viewSubmenu.append(kernelMenuItem)
            except Exception as e:
                print (e)
                print(sys.exc_info()[0])
            viewSubmenu.append(infoMenuItem)

            helpMenu = Gtk.MenuItem.new_with_mnemonic(_("_Help"))
            helpSubmenu = Gtk.Menu()
            helpMenu.set_submenu(helpSubmenu)
            if os.path.exists("/usr/share/help/C/linuxmint"):
                helpMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_HELP)
                helpMenuItem.set_use_stock(True)
                helpMenuItem.set_label(_("Contents"))
                helpMenuItem.connect("activate", self.open_help)
                key, mod = Gtk.accelerator_parse("F1")
                helpMenuItem.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
                helpSubmenu.append(helpMenuItem)
            aboutMenuItem = Gtk.ImageMenuItem(Gtk.STOCK_ABOUT)
            aboutMenuItem.set_use_stock(True)
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
                    self.window.show_all()
                    self.builder.get_object("paned1").set_position(self.settings.get_int('window-pane-position'))
                    self.app_hidden = False

            # Status pages
            status_updated_page = self.builder.get_object("status_updated")
            self.stack.add_named(status_updated_page, "status_updated")

            status_error_page = self.builder.get_object("status_error")
            self.stack.add_named(status_error_page, "status_error")

            self.stack.show_all()
            if self.settings.get_boolean("show-policy-configuration"):
                self.show_configuration()
            else:
                self.stack.set_visible_child_name("updates_available")
                refresh = RefreshThread(self)
                refresh.start()

            self.builder.get_object("notebook_details").set_current_page(0)

            self.window.resize(self.settings.get_int('window-width'), self.settings.get_int('window-height'))
            self.builder.get_object("paned1").set_position(self.settings.get_int('window-pane-position'))

            auto_refresh = AutomaticRefreshThread(self)
            auto_refresh.start()

            Gdk.threads_enter()
            Gtk.main()
            Gdk.threads_leave()

        except Exception as e:
            print (e)
            print(sys.exc_info()[0])
            self.logger.write_error("Exception occurred in main thread: " + str(sys.exc_info()[0]))
            self.logger.close()

######### UTILITY FUNCTIONS #########
    def hide_window(self, widget, window):
        window.hide()

    def refresh(self):
        refresh = RefreshThread(self)
        refresh.start()

    def set_status_message(self, message):
        self.statusbar.push(self.context_id, message)

    def set_status(self, message, tooltip, icon, visible):
        self.set_status_message(message)
        self.statusIcon.set_from_icon_name(icon)
        self.statusIcon.set_tooltip_text(tooltip)
        self.statusIcon.set_visible(visible)

######### WINDOW/STATUSICON ##########

    def close_window(self, window, event):
        window.hide()
        self.save_window_size()
        self.app_hidden = True
        return True

    def save_window_size(self):
        self.settings.set_int('window-width', self.window.get_size()[0])
        self.settings.set_int('window-height', self.window.get_size()[1])
        self.settings.set_int('window-pane-position', self.builder.get_object("paned1").get_position())

######### MENU/TOOLBAR FUNCTIONS ################

    def hide_main_window(self, widget):
        self.window.hide()
        self.app_hidden = True

    def setVisibleColumn(self, checkmenuitem, column, key):
        self.settings.set_boolean(key, checkmenuitem.get_active())
        column.set_visible(checkmenuitem.get_active())

    def setVisibleDescriptions(self, checkmenuitem):
        self.settings.set_boolean("show-descriptions", checkmenuitem.get_active())
        refresh = RefreshThread(self)
        refresh.start()

    def clear(self, widget):
        model = self.treeview.get_model()
        iter = model.get_iter_first()
        while (iter != None):
            model.set_value(iter, 0, "false")
            iter = model.iter_next(iter)
        self.set_status_message(_("No updates selected"))

    def select_all(self, widget):
        self.select_updates()

    def select_level1(self, widget):
        self.select_updates(1)

    def select_level2(self, widget):
        self.select_updates(2)

    def select_level3(self, widget):
        self.select_updates(3)

    def select_level4(self, widget):
        self.select_updates(4)

    def select_security_updates(self, widget):
        self.select_updates(level=None, security=True, kernel=False)

    def select_kernel_updates(self, widget):
        self.select_updates(level=None, security=False, kernel=True)

    def select_updates(self, level=None, security=False, kernel=False):
        model = self.treeview.get_model()
        iter = model.get_iter_first()
        while (iter != None):
            update =  model.get_value(iter, UPDATE_OBJ)
            if level is not None:
                if level == update.level:
                    model.set_value(iter, UPDATE_CHECKED, "true")
            elif security:
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
            self.set_status_message(gettext.ngettext("%(selected)d update selected (%(size)s)", "%(selected)d updates selected (%(size)s)", num_selected) % {'selected':num_selected, 'size':size_to_string(download_size)})

    def force_refresh(self, widget):
        refresh = RefreshThread(self, root_mode=True)
        refresh.start()

    def install(self, widget):
        install = InstallThread(self)
        install.start()

######### CONFIGURE PAGE FUNCTIONS #######

    def on_configure_finished(self, button):
        self.settings.set_boolean("show-policy-configuration", False)

        if (self.builder.get_object("radiobutton_policy_1").get_active()):
            self.settings.set_boolean("level3-is-visible", False)
            self.settings.set_boolean("level4-is-visible", False)
            self.settings.set_boolean("level3-is-safe", False)
            self.settings.set_boolean("level4-is-safe", False)
            self.settings.set_boolean("security-updates-are-safe", False)
            self.settings.set_boolean("kernel-updates-are-safe", False)
        elif (self.builder.get_object("radiobutton_policy_2").get_active()):
            self.settings.set_boolean("level3-is-visible", True)
            self.settings.set_boolean("level4-is-visible", True)
            self.settings.set_boolean("level3-is-safe", False)
            self.settings.set_boolean("level4-is-safe", False)
            self.settings.set_boolean("security-updates-are-safe", True)
            self.settings.set_boolean("kernel-updates-are-safe", False)
        elif (self.builder.get_object("radiobutton_policy_3").get_active()):
            self.settings.set_boolean("level3-is-visible", True)
            self.settings.set_boolean("level4-is-visible", True)
            self.settings.set_boolean("level3-is-safe", True)
            self.settings.set_boolean("level4-is-safe", True)
            self.settings.set_boolean("security-updates-are-safe", True)
            self.settings.set_boolean("kernel-updates-are-safe", True)

        # Common to all policies
        self.settings.set_boolean("level1-is-visible", True)
        self.settings.set_boolean("level2-is-visible", True)
        self.settings.set_boolean("level5-is-visible", False)
        self.settings.set_boolean("level1-is-safe", True)
        self.settings.set_boolean("level2-is-safe", True)
        self.settings.set_boolean("level5-is-safe", False)
        self.settings.set_boolean("security-updates-are-visible", True)
        self.settings.set_boolean("kernel-updates-are-visible", True)

        self.builder.get_object("toolbar1").set_visible(True)
        self.builder.get_object("toolbar1").set_sensitive(True)
        self.builder.get_object("menubar1").set_visible(True)
        self.builder.get_object("menubar1").set_sensitive(True)
        self.updates_inhibited = False
        refresh = RefreshThread(self)
        refresh.start()

    def on_configure_help(self, button):
        os.system("yelp help:mintupdate/index &")

    def show_configuration(self, widget=None):
        self.updates_inhibited = True
        policy = "radiobutton_policy_1"
        if (self.settings.get_boolean("security-updates-are-safe")):
            policy = "radiobutton_policy_2"
        if (self.settings.get_boolean("level4-is-safe")):
            policy = "radiobutton_policy_3"
        self.builder.get_object(policy).set_active(True)
        self.stack.set_visible_child_name("configure")
        self.set_status(_("Please choose an update policy."), _("Please choose an update policy."), "mintupdate-updates-available", True)
        self.set_status_message("")
        self.builder.get_object("toolbar1").set_sensitive(False)
        self.builder.get_object("toolbar1").set_visible(False)
        self.builder.get_object("menubar1").set_sensitive(False)
        self.builder.get_object("menubar1").set_visible(False)

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
            self.set_status_message(gettext.ngettext("%(selected)d update selected (%(size)s)", "%(selected)d updates selected (%(size)s)", num_selected) % {'selected':num_selected, 'size':size_to_string(download_size)})

    def display_selected_package(self, selection):
        try:
            self.builder.get_object("textview_description").get_buffer().set_text("")
            self.builder.get_object("textview_changes").get_buffer().set_text("")
            (model, iter) = selection.get_selected()
            if (iter != None):
                package_update = model.get_value(iter, UPDATE_OBJ)
                self.display_package_description(package_update)
                if self.builder.get_object("notebook_details").get_current_page() == 0:
                    # Description tab
                    self.changelog_retriever_started = False
                else:
                    # Changelog tab
                    retriever = ChangelogRetriever(package_update, self)
                    retriever.start()
                    self.changelog_retriever_started = True
        except Exception as e:
            print (e)
            print(sys.exc_info()[0])

    def switch_page(self, notebook, page, page_num):
        selection = self.treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            if (page_num == 1 and not self.changelog_retriever_started):
                # Changelog tab
                package_update = model.get_value(iter, UPDATE_OBJ)
                retriever = ChangelogRetriever(package_update, self)
                retriever.start()
                self.changelog_retriever_started = True

    def display_package_description(self, package_update):
        description = package_update.description
        description = description.split("\\n")
        for line in description:
            self.buffer.insert(self.buffer.get_end_iter(), line)
            self.buffer.insert(self.buffer.get_end_iter(), "\n")

        if (len(package_update.package_names) > 1 or package_update.package_names[0] != package_update.display_name):
            smaller_description = "\n%s %s" % (_("Packages included in this update:"), " ".join(sorted(package_update.package_names)))
            self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), smaller_description, "smaller")

    def treeview_right_clicked(self, widget, event):
        if event.button == 3:
            (model, iter) = widget.get_selection().get_selected()
            if (iter != None):
                package_update = model.get_value(iter, UPDATE_OBJ)
                menu = Gtk.Menu()
                menuItem = Gtk.MenuItem.new_with_mnemonic(_("Ignore updates for this package"))
                menuItem.connect("activate", self.add_to_ignore_list, package_update.source_name)
                menu.append(menuItem)
                menu.attach_to_widget (widget, None)
                menu.show_all()
                menu.popup(None, None, None, None, event.button, event.time)

    def add_to_ignore_list(self, widget, pkg):
        blacklist = self.settings.get_strv("blacklisted-packages")
        blacklist.append(pkg)
        self.settings.set_strv("blacklisted-packages", blacklist)
        refresh = RefreshThread(self)
        refresh.start()


######### SYSTRAY ####################

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
        gladefile = "/usr/share/linuxmint/mintupdate/information.ui"
        builder = Gtk.Builder()
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_title(_("Information") + " - " + _("Update Manager"))
        window.set_icon_name("mintupdate")
        builder.get_object("close_button").connect("clicked", self.hide_window, window)
        builder.get_object("label4").set_text(_("Process ID:"))
        builder.get_object("label5").set_text(_("Log file:"))
        builder.get_object("processid_label").set_text(str(os.getpid()))
        builder.get_object("log_filename").set_text(str(self.logger.log.name))
        txtbuffer = Gtk.TextBuffer()
        txtbuffer.set_text(subprocess.check_output("cat " + self.logger.log.name, shell = True).decode("utf-8"))
        builder.get_object("log_textview").set_buffer(txtbuffer)

######### HISTORY SCREEN #########

    def open_history(self, widget):
        gladefile = "/usr/share/linuxmint/mintupdate/history.ui"
        builder = Gtk.Builder()
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_icon_name("mintupdate")
        window.set_title(_("History of updates") + " - " + _("Update Manager"))

        treeview = builder.get_object("treeview_history")
        column1 = Gtk.TreeViewColumn(_("Date"), Gtk.CellRendererText(), text=1)
        column1.set_sort_column_id(1)
        column1.set_resizable(True)
        column2 = Gtk.TreeViewColumn(_("Package"), Gtk.CellRendererText(), text=0)
        column2.set_sort_column_id(0)
        column2.set_resizable(True)
        column3 = Gtk.TreeViewColumn(_("Old version"), Gtk.CellRendererText(), text=2)
        column3.set_sort_column_id(2)
        column3.set_resizable(True)
        column4 = Gtk.TreeViewColumn(_("New version"), Gtk.CellRendererText(), text=3)
        column4.set_sort_column_id(3)
        column4.set_resizable(True)
        treeview.append_column(column1)
        treeview.append_column(column2)
        treeview.append_column(column3)
        treeview.append_column(column4)
        treeview.set_headers_clickable(True)
        treeview.set_reorderable(False)
        treeview.set_search_column(0)
        treeview.set_enable_search(True)
        treeview.show()

        model = Gtk.TreeStore(str, str, str, str) # (packageName, date, oldVersion, newVersion)
        if (os.path.exists("/var/log/dpkg.log")):
            updates = subprocess.check_output("cat /var/log/dpkg.log /var/log/dpkg.log.? 2>/dev/null | egrep \"upgrade\"", shell = True).decode("utf-8")
            updates = updates.split("\n")
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

        model.set_sort_column_id( 1, Gtk.SortType.DESCENDING )
        treeview.set_model(model)
        del model
        builder.get_object("button_close").connect("clicked", self.hide_window, window)

######### HELP/ABOUT/SOURCES SCREEN #########

    def open_help(self, widget):
        os.system("yelp help:mintupdate/index &")

    def open_rel_upgrade(self, widget):
        os.system("/usr/bin/mint-release-upgrade &")

    def open_about(self, widget):
        dlg = Gtk.AboutDialog()
        dlg.set_title(_("About") + " - " + _("Update Manager"))
        dlg.set_program_name("mintUpdate")
        dlg.set_comments(_("Update Manager"))
        try:
            h = open('/usr/share/common-licenses/GPL','r')
            s = h.readlines()
            gpl = ""
            for line in s:
                gpl += line
            h.close()
            dlg.set_license(gpl)
        except Exception as e:
            print (e)
            print(sys.exc_info()[0])

        try:
            version = subprocess.getoutput("/usr/lib/linuxmint/common/version.py mintupdate")
            dlg.set_version(version)
        except Exception as e:
            print (e)
            print(sys.exc_info()[0])

        dlg.set_icon_name("mintupdate")
        dlg.set_logo_icon_name("mintupdate")
        dlg.set_website("http://www.github.com/linuxmint/mintupdate")
        def close(w, res):
            if res == Gtk.ResponseType.CANCEL or res == Gtk.ResponseType.DELETE_EVENT:
                w.hide()
        dlg.connect("response", close)
        dlg.show()

    def open_repositories(self, widget):
        if os.path.exists("/usr/bin/software-sources"):
            os.system("/usr/bin/software-sources &")
        elif os.path.exists("/usr/bin/software-properties-gtk"):
            os.system("/usr/bin/software-properties-gtk &")
        elif os.path.exists("/usr/bin/software-properties-kde"):
            os.system("/usr/bin/software-properties-kde &")

######### PREFERENCES SCREEN #########

    def open_preferences(self, widget):
        gladefile = "/usr/share/linuxmint/mintupdate/preferences.ui"
        builder = Gtk.Builder()
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_title(_("Preferences") + " - " + _("Update Manager"))
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
        stack.add_titled(builder.get_object("page_levels"), "page_levels", _("Levels"))
        stack.add_titled(builder.get_object("page_refresh"), "page_refresh", _("Auto-refresh"))
        stack.add_titled(builder.get_object("page_blacklist"), "page_blacklist", _("Blacklist"))

        #l10n
        builder.get_object("label39").set_markup("<b>" + _("Impact level") + "</b>")
        builder.get_object("label_recommandation").set_markup("<b>" + _("Description") + "</b>")
        builder.get_object("label41").set_markup("<b>" + _("Selected") + "</b>")
        builder.get_object("label42").set_markup("<b>" + _("Visible") + "</b>")

        builder.get_object("label_level_1").set_text(_("Minimal"))
        builder.get_object("label_level_2").set_text(_("Normal"))
        builder.get_object("label_level_3").set_text(_("Large"))
        builder.get_object("label_level_4").set_text(_("Sensitive"))
        builder.get_object("label_level_5").set_text(_("Dangerous"))

        builder.get_object("label_impact_1").set_text(_("No impact on the system or other applications."))
        builder.get_object("label_impact_2").set_text(_("Default level. Usually low impact on the system."))
        builder.get_object("label_impact_3").set_text(_("Apply with caution. Impact on multiple applications."))
        builder.get_object("label_impact_4").set_text(_("Apply one by one. Impact on sensitive parts of the system."))
        builder.get_object("label_impact_5").set_text(_("Very rare. Not recommended. Known to create issues."))

        builder.get_object("label_refresh").set_text(_("First, refresh the list of updates after:"))
        builder.get_object("label_autorefresh").set_text(_("Then, refresh the list of updates every:"))
        builder.get_object("label82").set_text("<i>" + _("Note: The list only gets refreshed while the update manager window is closed (system tray mode).") + "</i>")
        builder.get_object("label82").set_use_markup(True)
        builder.get_object("checkbutton_dist_upgrade").set_label(_("Include updates which require the installation of new packages or the removal of installed packages"))
        builder.get_object("checkbutton_hide_window_after_update").set_label(_("Hide the update manager after applying updates"))
        builder.get_object("checkbutton_hide_systray").set_label(_("Only show a tray icon when updates are available or in case of errors"))
        builder.get_object("checkbutton_default_repo_is_ok").set_label(_("Don't suggest to switch to a local mirror"))
        builder.get_object("checkbutton_security_visible").set_label(_("Always show security updates"))
        builder.get_object("checkbutton_security_safe").set_label(_("Always select security updates"))
        builder.get_object("checkbutton_kernel_visible").set_label(_("Always show kernel updates"))
        builder.get_object("checkbutton_kernel_safe").set_label(_("Always select kernel updates"))
        builder.get_object("label_minutes").set_text(_("minutes"))
        builder.get_object("label_hours").set_text(_("hours"))
        builder.get_object("label_days").set_text(_("days"))
        builder.get_object("pref_button_cancel").set_label(_("Cancel"))
        builder.get_object("pref_button_apply").set_label(_("Apply"))

        builder.get_object("visible1").set_active(self.settings.get_boolean("level1-is-visible"))
        builder.get_object("visible2").set_active(self.settings.get_boolean("level2-is-visible"))
        builder.get_object("visible3").set_active(self.settings.get_boolean("level3-is-visible"))
        builder.get_object("visible4").set_active(self.settings.get_boolean("level4-is-visible"))
        builder.get_object("visible5").set_active(self.settings.get_boolean("level5-is-visible"))
        builder.get_object("safe1").set_active(self.settings.get_boolean("level1-is-safe"))
        builder.get_object("safe2").set_active(self.settings.get_boolean("level2-is-safe"))
        builder.get_object("safe3").set_active(self.settings.get_boolean("level3-is-safe"))
        builder.get_object("safe4").set_active(self.settings.get_boolean("level4-is-safe"))
        builder.get_object("safe5").set_active(self.settings.get_boolean("level5-is-safe"))
        builder.get_object("checkbutton_security_visible").set_active(self.settings.get_boolean("security-updates-are-visible"))
        builder.get_object("checkbutton_security_safe").set_active(self.settings.get_boolean("security-updates-are-safe"))
        builder.get_object("checkbutton_kernel_visible").set_active(self.settings.get_boolean("kernel-updates-are-visible"))
        builder.get_object("checkbutton_kernel_safe").set_active(self.settings.get_boolean("kernel-updates-are-safe"))
        builder.get_object("checkbutton_dist_upgrade").set_active(self.settings.get_boolean("dist-upgrade"))
        builder.get_object("checkbutton_hide_window_after_update").set_active(self.settings.get_boolean("hide-window-after-update"))
        builder.get_object("checkbutton_hide_systray").set_active(self.settings.get_boolean("hide-systray"))
        builder.get_object("checkbutton_default_repo_is_ok").set_active(self.settings.get_boolean("default-repo-is-ok"))

        builder.get_object("refresh_days").set_range(0, 365)
        builder.get_object("refresh_days").set_increments(1, 10)
        builder.get_object("refresh_days").set_value(self.settings.get_int("refresh-days"))
        builder.get_object("refresh_hours").set_range(0, 59)
        builder.get_object("refresh_hours").set_increments(1, 5)
        builder.get_object("refresh_hours").set_value(self.settings.get_int("refresh-hours"))
        builder.get_object("refresh_minutes").set_range(0, 59)
        builder.get_object("refresh_minutes").set_increments(1, 5)
        builder.get_object("refresh_minutes").set_value(self.settings.get_int("refresh-minutes"))
        builder.get_object("autorefresh_days").set_range(0, 365)
        builder.get_object("autorefresh_days").set_increments(1, 10)
        builder.get_object("autorefresh_days").set_value(self.settings.get_int("autorefresh-days"))
        builder.get_object("autorefresh_hours").set_range(0, 59)
        builder.get_object("autorefresh_hours").set_increments(1, 5)
        builder.get_object("autorefresh_hours").set_value(self.settings.get_int("autorefresh-hours"))
        builder.get_object("autorefresh_minutes").set_range(0, 59)
        builder.get_object("autorefresh_minutes").set_increments(1, 5)
        builder.get_object("autorefresh_minutes").set_value(self.settings.get_int("autorefresh-minutes"))

        treeview_blacklist = builder.get_object("treeview_blacklist")
        column1 = Gtk.TreeViewColumn(_("Ignored updates"), Gtk.CellRendererText(), text=0)
        column1.set_sort_column_id(0)
        column1.set_resizable(True)
        treeview_blacklist.append_column(column1)
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

        builder.get_object("pref_button_cancel").connect("clicked", self.hide_window, window)
        builder.get_object("pref_button_apply").connect("clicked", self.save_preferences, builder)
        builder.get_object("button_add").connect("clicked", self.add_blacklisted_package, treeview_blacklist)
        builder.get_object("button_remove").connect("clicked", self.remove_blacklisted_package, treeview_blacklist)
        builder.get_object("button_add").set_always_show_image(True)
        builder.get_object("button_remove").set_always_show_image(True)

        window.show_all()

    def save_preferences(self, widget, builder):
        self.settings.set_boolean('hide-window-after-update', builder.get_object("checkbutton_hide_window_after_update").get_active())
        self.settings.set_boolean('hide-systray', builder.get_object("checkbutton_hide_systray").get_active())
        self.settings.set_boolean('default-repo-is-ok', builder.get_object("checkbutton_default_repo_is_ok").get_active())
        self.settings.set_boolean('level1-is-visible', builder.get_object("visible1").get_active())
        self.settings.set_boolean('level2-is-visible', builder.get_object("visible2").get_active())
        self.settings.set_boolean('level3-is-visible', builder.get_object("visible3").get_active())
        self.settings.set_boolean('level4-is-visible', builder.get_object("visible4").get_active())
        self.settings.set_boolean('level5-is-visible', builder.get_object("visible5").get_active())
        self.settings.set_boolean('level1-is-safe', builder.get_object("safe1").get_active())
        self.settings.set_boolean('level2-is-safe', builder.get_object("safe2").get_active())
        self.settings.set_boolean('level3-is-safe', builder.get_object("safe3").get_active())
        self.settings.set_boolean('level4-is-safe', builder.get_object("safe4").get_active())
        self.settings.set_boolean('level5-is-safe', builder.get_object("safe5").get_active())
        self.settings.set_boolean('security-updates-are-visible', builder.get_object("checkbutton_security_visible").get_active())
        self.settings.set_boolean('security-updates-are-safe', builder.get_object("checkbutton_security_safe").get_active())
        self.settings.set_boolean('kernel-updates-are-visible', builder.get_object("checkbutton_kernel_visible").get_active())
        self.settings.set_boolean('kernel-updates-are-safe', builder.get_object("checkbutton_kernel_safe").get_active())
        self.settings.set_int('refresh-days', int(builder.get_object("refresh_days").get_value()))
        self.settings.set_int('refresh-hours', int(builder.get_object("refresh_hours").get_value()))
        self.settings.set_int('refresh-minutes', int(builder.get_object("refresh_minutes").get_value()))
        self.settings.set_int('autorefresh-days', int(builder.get_object("autorefresh_days").get_value()))
        self.settings.set_int('autorefresh-hours', int(builder.get_object("autorefresh_hours").get_value()))
        self.settings.set_int('autorefresh-minutes', int(builder.get_object("autorefresh_minutes").get_value()))
        self.settings.set_boolean('dist-upgrade', builder.get_object("checkbutton_dist_upgrade").get_active())
        blacklist = []
        treeview_blacklist = builder.get_object("treeview_blacklist")
        model = treeview_blacklist.get_model()
        iter = model.get_iter_first()
        while iter is not None:
            pkg = model.get_value(iter, UPDATE_CHECKED)
            iter = model.iter_next(iter)
            blacklist.append(pkg)
        self.settings.set_strv("blacklisted-packages", blacklist)
        builder.get_object("main_window").hide()
        self.refresh()

    def add_blacklisted_package(self, widget, treeview_blacklist):
        dialog = Gtk.MessageDialog(None, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK, None)
        dialog.set_markup("<b>" + _("Please specify the name of the update to ignore:") + "</b>")
        dialog.set_title(_("Ignore an update"))
        dialog.set_icon_name("mintupdate")
        entry = Gtk.Entry()
        hbox = Gtk.HBox()
        hbox.pack_start(Gtk.Label(_("Name:")), False, 5, 5)
        hbox.pack_end(entry, True, True, 0)
        dialog.vbox.pack_end(hbox, True, True, 0)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            name = entry.get_text()
            pkg = name.strip()
            if pkg != '':
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

###### KERNEL FEATURES #####################################

    def open_kernels(self, widget):
        kernel_window = KernelWindow(self)

if __name__ == "__main__":
    MintUpdate()
