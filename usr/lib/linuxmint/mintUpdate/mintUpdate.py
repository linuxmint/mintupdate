#!/usr/bin/python3
# -*- coding: utf-8 -*-

# system imports
import os
import sys
import gi
import tempfile
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
import platform
import re
import aptkit.simpleclient
import checkAPT
from multiprocess import Process, Queue

gi.require_version('Gtk', '3.0')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, Gdk, Gio, GLib, GObject, Notify, Pango
from xapp.GSettingsWidgets import *

# local imports
import logger
from kernelwindow import KernelWindow
from Classes import Update, PRIORITY_UPDATES, UpdateTracker, _idle, _async


settings = Gio.Settings(schema_id="com.linuxmint.updates")
cinnamon_support = False
try:
    if settings.get_boolean("show-cinnamon-updates"):
        import cinnamon
        cinnamon_support = True
except Exception as e:
    if os.getenv("DEBUG"):
        print("No cinnamon update support:\n%s" % traceback.format_exc())

flatpak_support = False
try:
    if settings.get_boolean("show-flatpak-updates"):
        import flatpakUpdater
        flatpak_support = True
except Exception as e:
    if os.getenv("DEBUG"):
        print("No flatpak update support:\n%s" % traceback.format_exc())

CINNAMON_SUPPORT = cinnamon_support
FLATPAK_SUPPORT = flatpak_support

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

def name_search_func(model, column, key, iter):
    name = model.get_value(iter, column)
    return key.lower() not in name.lower() # False is a match


class APTCacheMonitor():
    """ Monitors package cache and dpkg status and runs the refresh thread() on change """

    def __init__(self, application):
        self.application = application
        self.cachetime = 0
        self.statustime = 0
        self.paused = False
        self.pkgcache = "/var/cache/apt/pkgcache.bin"
        self.dpkgstatus = "/var/lib/dpkg/status"

    @_async
    def start(self):
        self.application.refresh(False)
        self.update_cachetime()
        if os.path.isfile(self.pkgcache) and os.path.isfile(self.dpkgstatus):
            while True:
                if not self.paused and self.application.hidden:
                    try:
                        cachetime = os.path.getmtime(self.pkgcache)
                        statustime = os.path.getmtime(self.dpkgstatus)
                        if (cachetime != self.cachetime or statustime != self.statustime) and \
                            not self.application.dpkg_locked():
                            self.cachetime = cachetime
                            self.statustime = statustime
                            self.application.logger.write("Changes to the package cache detected; triggering refresh")
                            self.application.refresh(False)
                    except:
                        pass
                time.sleep(90)
        else:
            self.application.logger.write("Package cache location not found, disabling cache monitoring")

    def resume(self, update_cachetime=True):
        if self.paused:
            if update_cachetime:
                self.update_cachetime()
            self.paused = False

    def pause(self):
        self.paused = True

    def update_cachetime(self):
        if os.path.isfile(self.pkgcache) and os.path.isfile(self.dpkgstatus):
            self.cachetime = os.path.getmtime(self.pkgcache)
            self.statustime = os.path.getmtime(self.dpkgstatus)

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
        self.information_window_showing = False
        self.history_window_showing = False
        self.preferences_window_showing = False
        self.updates_inhibited = False
        self.reboot_required = False
        self.refreshing = False
        self.refreshing_apt = False
        self.refreshing_flatpak = False
        self.refreshing_cinnamon = False
        self.auto_refresh_is_alive = False
        self.hidden = False # whether the window is hidden or not
        self.packages = [] # packages selected for update
        self.flatpaks = [] # flatpaks selected for update
        self.spices = [] # spices selected for update
        self.inhibit_cookie = 0
        self.logger = logger.Logger()
        self.cache_monitor = None
        self.logger.write("Launching Update Manager")
        self.settings = Gio.Settings(schema_id="com.linuxmint.updates")

        self.is_lmde = False
        self.app_restart_required = False
        self.show_cinnamon_enabled = False
        self.settings.connect("changed", self._on_settings_changed)
        self._on_settings_changed(self.settings, None)

        #Set the Glade file
        gladefile = "/usr/share/linuxmint/mintupdate/main.ui"
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintupdate")
        self.builder.add_from_file(gladefile)

        #self.builder.connect_signals(self)
        for widget in self.builder.get_objects():
            if issubclass(type(widget), Gtk.Buildable):
                name = "ui_%s" % Gtk.Buildable.get_name(widget)
                if not "__" in name:
                    setattr(self, name, widget)

        self.context_id = self.ui_statusbar.get_context_id("mintUpdate")
        self.ui_window.connect("key-press-event",self.on_key_press_event)
        self.treeview = self.builder.get_object("treeview_update")

        try:
            self.ui_window.set_title(_("Update Manager"))

            self.ui_window.set_icon_name("mintupdate")

            # Add mintupdate style class for easier theming
            self.ui_window.get_style_context().add_class('mintupdate')

            accel_group = Gtk.AccelGroup()
            self.ui_window.add_accel_group(accel_group)

            self.textview_packages = self.ui_textview_packages.get_buffer()
            self.textview_description = self.ui_textview_description.get_buffer()
            self.textview_changes = self.ui_textview_changes.get_buffer()

            # Welcome page
            self.ui_button_welcome_finish.connect("clicked", self.on_welcome_page_finished)
            self.ui_button_welcome_help.connect("clicked", self.open_help)

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

            self.treeview.set_search_equal_func(name_search_func)
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
            self.ui_notebook_details.connect("switch-page", self.switch_page)
            self.ui_window.connect("delete_event", self.close_window)

            # Install Updates button
            self.ui_install_button.connect("clicked", self.install)
            key, mod = Gtk.accelerator_parse("<Control>I")
            self.ui_install_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

            # Clear button
            self.ui_clear_button.connect("clicked", self.clear)
            key, mod = Gtk.accelerator_parse("<Control><Shift>A")
            self.ui_clear_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

            # Select All button
            self.ui_select_all_button.connect("clicked", self.select_all)
            key, mod = Gtk.accelerator_parse("<Control>A")
            self.ui_select_all_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

            # Refresh button
            self.ui_refresh_button.connect("clicked", self.manual_refresh)
            key, mod = Gtk.accelerator_parse("<Control>R")
            self.ui_refresh_button.add_accelerator("clicked", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)

            # Self-update page:
            self.ui_self_update_button.connect("clicked", self.self_update)

            # Tray icon menu
            menu = Gtk.Menu()
            image = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU)
            menuItem3 = Gtk.ImageMenuItem(label=_("Refresh"), image=image)
            menuItem3.connect('activate', self.manual_refresh)
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

            # Main window menu
            fileMenu = Gtk.MenuItem.new_with_mnemonic(_("_File"))
            fileSubmenu = Gtk.Menu()
            fileMenu.set_submenu(fileSubmenu)
            image = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
            closeMenuItem = Gtk.ImageMenuItem(label=_("Close window"), image=image)
            closeMenuItem.connect("activate", self.hide_window)
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
            kernelMenuItem.connect("activate", self.on_kernel_menu_activated)
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
                if not os.path.exists("/usr/share/doc/debian-system-adjustments/copyright"):
                    viewSubmenu.append(kernelMenuItem)
                else:
                    self.is_lmde = True
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

            self.ui_menubar.append(fileMenu)
            self.ui_menubar.append(editMenu)
            self.ui_menubar.append(viewMenu)
            self.ui_menubar.append(helpMenu)

            self.ui_stack.set_visible_child_name("refresh_page")

            self.ui_window.resize(self.settings.get_int('window-width'), self.settings.get_int('window-height'))
            self.ui_paned.set_position(self.settings.get_int('window-pane-position'))

            self.ui_vbox.show_all()

            if len(sys.argv) > 1:
                showWindow = sys.argv[1]
                if showWindow == "show":
                    self.ui_window.present_with_time(Gtk.get_current_event_time())

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
                self.cache_monitor = APTCacheMonitor(self)
                self.cache_monitor.start()

            self.ui_notebook_details.set_current_page(0)

            self.refresh_schedule_enabled = self.settings.get_boolean("refresh-schedule-enabled")
            self.start_auto_refresh()

            Gtk.main()

        except Exception as e:
            print (e)
            print(sys.exc_info()[0])
            self.logger.write_error("Exception occurred in main thread: " + str(sys.exc_info()[0]))
            self.logger.close()

    def _on_settings_changed(self, settings, key, data=None):
        if key is None:
            self.show_flatpak_enabled = settings.get_boolean("show-flatpak-updates")
            self.show_cinnamon_enabled = settings.get_boolean("show-cinnamon-updates")
            return

        self.app_restart_required = settings.get_boolean("show-cinnamon-updates") != self.show_cinnamon_enabled or \
                                    settings.get_boolean("show-flatpak-updates") != self.show_flatpak_enabled


######### EVENT HANDLERS #########

    def on_key_press_event(self, widget, event):
        ctrl = (event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl:
            if event.keyval == Gdk.KEY_s:
                self.select_updates(security=True)
            elif event.keyval == Gdk.KEY_k:
                self.select_updates(kernel=True)

######### UTILITY FUNCTIONS #########

    @_idle
    def set_status_message(self, message):
        self.ui_statusbar.push(self.context_id, message)

    @_idle
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

    @_idle
    def show_infobar(self, title, msg, msg_type, icon, callback):
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
        for child in self.ui_infobar.get_children():
            child.destroy()
        self.ui_infobar.pack_start(infobar, True, True, 0)

    @_idle
    def show_error(self, error_msg):
        self.ui_stack.set_visible_child_name("error_page")
        self.ui_label_error_details.set_text(error_msg)


    @_idle
    def show_updates_in_UI(self, num_visible, num_software, num_security, download_size, is_self_update, model_items):
        if num_visible > 0:
            self.logger.write("Found %d software updates" % num_visible)
            if is_self_update:
                self.ui_stack.set_visible_child_name("self_update_page")
                self.ui_statusbar.set_visible(False)
                status_string = ""
                details = []
                for update in updates:
                    details.append(f"{update.source_name} {update.new_version}")
                details = ", ".join(details)
                self.ui_label_self_update_details.set_text(details)
            else:
                status_string = gettext.ngettext("%(selected)d update selected (%(size)s)",
                                        "%(selected)d updates selected (%(size)s)", num_visible) % \
                                        {'selected':num_visible, 'size':size_to_string(download_size)}
                self.ui_clear_button.set_sensitive(True)
                self.ui_select_all_button.set_sensitive(True)
                self.ui_install_button.set_sensitive(True)
                self.ui_window.set_sensitive(True)
            systray_tooltip = gettext.ngettext("%d update available", "%d updates available", num_visible) % num_visible
            self.set_status(status_string, systray_tooltip, "mintupdate-updates-available-symbolic", True)
        else:
            self.logger.write("System is up to date")
            self.ui_stack.set_visible_child_name("success_page")
            self.set_status("", _("Your system is up to date"), "mintupdate-up-to-date-symbolic",
                                        not self.settings.get_boolean("hide-systray"))


        self.ui_notebook_details.set_current_page(0)

        tracker = UpdateTracker(self.settings, self.logger)
        model = Gtk.TreeStore(bool, str, str, str, str, GObject.TYPE_LONG, str, str, str, str, str, object)
        # UPDATE_CHECKED, UPDATE_DISPLAY_NAME, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_SOURCE,
        # UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ

        model.set_sort_column_id(UPDATE_SORT_STR, Gtk.SortType.ASCENDING)
        for item in model_items:
            self.add_update_to_model(model, tracker, item)

        if tracker.active:
            if tracker.notify():
                self.show_tracker_notification(num_software, num_security)
            tracker.record()

        self.treeview.set_model(model)
        self.treeview.set_search_column(UPDATE_DISPLAY_NAME)

    def show_tracker_notification(self, num_software, num_security):
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
        self.notification = Notify.Notification.new(_("Updates are available"), msg, "mintupdate-updates-available-symbolic")
        self.notification.set_urgency(2)
        self.notification.set_timeout(Notify.EXPIRES_NEVER)
        self.notification.add_action("show_updates", _("View updates"), self.on_notification_action, None)
        self.notification.add_action("enable_automatic_updates", _("Enable automatic updates"), self.on_notification_action, None)
        self.notification.show()

    def on_notification_action(self, notification, action_name, data):
        if action_name == "show_updates":
            os.system("/usr/lib/linuxmint/mintUpdate/mintUpdate.py show &")
        elif action_name == "enable_automatic_updates":
            self.open_preferences(None, show_automation=True)

######### WINDOW/STATUSICON ##########

    def close_window(self, window, event):
        self.save_window_size()
        self.hide_window(window)
        return True

    @_idle
    def hide_window(self, widget=None):
        self.ui_window.hide()
        self.hidden = True

    @_idle
    def show_window(self, time=Gtk.get_current_event_time()):
        self.ui_window.show()
        self.ui_window.present_with_time(time)
        self.hidden = False

    @_idle
    def set_window_busy(self, busy):
        if self.ui_window.get_window() is None:
            return

        if busy and not self.hidden:
            self.ui_window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
            self.ui_window.set_sensitive(False)
        else:
            self.ui_window.get_window().set_cursor(None)
            self.ui_window.set_sensitive(True)

    @_idle
    def set_refresh_mode(self, enabled):
        if enabled:
            self.ui_refresh_spinner.start()
            self.ui_stack.set_visible_child_name("refresh_page")
            self.ui_toolbar.set_sensitive(False)
            self.ui_menubar.set_sensitive(False)
            self.ui_clear_button.set_sensitive(False)
            self.ui_select_all_button.set_sensitive(False)
            self.ui_install_button.set_sensitive(False)
        else:
            self.ui_refresh_spinner.stop()
            # Make sure we're never stuck on the status_refreshing page:
            if self.ui_stack.get_visible_child_name() == "refresh_page":
                self.ui_stack.set_visible_child_name("updates_page")
            #self.ui_paned.set_position(self.ui_paned.get_position())
            self.ui_toolbar.set_sensitive(True)
            self.ui_menubar.set_sensitive(True)
        self.set_window_busy(enabled)


    def save_window_size(self):
        self.settings.set_int('window-width', self.ui_window.get_size()[0])
        self.settings.set_int('window-height', self.ui_window.get_size()[1])
        self.settings.set_int('window-pane-position', self.ui_paned.get_position())

######### MENU/TOOLBAR FUNCTIONS #########

    def update_installable_state(self):
        model = self.treeview.get_model()

        iter = model.get_iter_first()
        download_size = 0
        num_selected = 0
        while iter is not None:
            checked = model.get_value(iter, UPDATE_CHECKED)
            if checked:
                size = model.get_value(iter, UPDATE_SIZE)
                download_size = download_size + size
                num_selected = num_selected + 1
            iter = model.iter_next(iter)
        if num_selected == 0:
            self.ui_install_button.set_sensitive(False)
            self.set_status_message(_("No updates selected"))
        else:
            self.ui_install_button.set_sensitive(True)
            self.set_status_message(gettext.ngettext("%(selected)d update selected (%(size)s)", "%(selected)d updates selected (%(size)s)", num_selected) % {'selected':num_selected, 'size':size_to_string(download_size)})

    def setVisibleColumn(self, checkmenuitem, column, key):
        state = checkmenuitem.get_active()
        self.settings.set_boolean(key, state)
        column.set_visible(state)

    def setVisibleDescriptions(self, checkmenuitem):
        self.settings.set_boolean("show-descriptions", checkmenuitem.get_active())
        self.refresh(False)

    def clear(self, widget):
        model = self.treeview.get_model()
        if len(model):
            iter = model.get_iter_first()
            while iter is not None:
                model.set_value(iter, 0, False)
                iter = model.iter_next(iter)

        self.update_installable_state()

    def select_all(self, widget):
        self.select_updates()

    def select_updates(self, security=False, kernel=False):
        model = self.treeview.get_model()
        iter = model.get_iter_first()
        while iter is not None:
            update = model.get_value(iter, UPDATE_OBJ)
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

    def manual_refresh(self, widget):
        if self.dpkg_locked():
            self.show_dpkg_lock_msg(self.ui_window)
        else:
            self.refresh(True)

    def self_update(self, widget):
        self.select_all(widget)
        self.install(widget)

######### WELCOME PAGE FUNCTIONS #######

    def on_welcome_page_finished(self, button):
        self.settings.set_boolean("show-welcome-page", False)
        self.ui_toolbar.set_sensitive(True)
        self.ui_menubar.set_sensitive(True)
        self.updates_inhibited = False
        if self.cache_monitor is None:
            self.cache_monitor = APTCacheMonitor(self)
            self.cache_monitor.start()
        else:
            self.ui_stack.set_visible_child_name("updates_page")

    def show_welcome_page(self, widget=None):
        self.updates_inhibited = True
        self.ui_stack.set_visible_child_name("welcome_page")
        self.set_status("", _("Welcome to the Update Manager"), "mintupdate-updates-available-symbolic", True)
        self.ui_toolbar.set_sensitive(False)
        self.ui_menubar.set_sensitive(False)

######### TREEVIEW/SELECTION FUNCTIONS #######

    def treeview_row_activated(self, treeview, path, view_column):
        self.toggled(None, path)

    def toggled(self, renderer, path):
        model = self.treeview.get_model()
        iter = model.get_iter(path)
        if iter is not None:
            model.set_value(iter, UPDATE_CHECKED, (not model.get_value(iter, UPDATE_CHECKED)))

        self.update_installable_state()

    @_idle
    def set_textview_changes_text(self, text):
        self.textview_changes.set_text(text)

    def display_selected_update(self, selection):
        try:
            self.textview_packages.set_text("")
            self.textview_description.set_text("")
            self.textview_changes.set_text("")
            (model, iter) = selection.get_selected()
            if iter is not None:
                update = model.get_value(iter, UPDATE_OBJ)
                description = update.description.replace("\\n", "\n")
                desc_tab = self.ui_notebook_details.get_nth_page(TAB_DESC)

                if update.type == "cinnamon":
                    latest_change_str = _("Most recent change")
                    desc = "%s\n\n%s: %s" % (description, latest_change_str, update.commit_msg)

                    self.textview_description.set_text(desc)
                    self.ui_notebook_details.get_nth_page(TAB_PACKAGES).hide()
                    self.ui_notebook_details.get_nth_page(TAB_CHANGELOG).hide()
                    self.ui_notebook_details.set_current_page(TAB_DESC)
                elif update.type == "flatpak":
                    if update.link is not None and update.link != "":
                        website_label_str = _("Website: %s") % update.link
                        description = "%s\n\n%s" % (update.description, website_label_str)
                    else:
                        description = "%s" % update.description

                    self.textview_description.set_text(description)
                    self.ui_notebook_details.get_nth_page(TAB_PACKAGES).show()
                    self.ui_notebook_details.get_nth_page(TAB_CHANGELOG).hide()
                    self.ui_notebook_details.set_current_page(TAB_DESC)
                    self.display_package_list(update, is_flatpak=True)
                else:
                    self.textview_description.set_text(description)
                    self.ui_notebook_details.get_nth_page(TAB_PACKAGES).show()
                    self.ui_notebook_details.get_nth_page(TAB_CHANGELOG).show()
                    self.display_package_list(update)

                    if self.ui_notebook_details.get_current_page() == 2:
                        # Changelog tab
                        self.retrieve_changelog(update)
                        self.changelog_retriever_started = True
                    else:
                        self.changelog_retriever_started = False

        except Exception as e:
            print (e)
            print(sys.exc_info()[0])


    def get_ppa_info(self, origin):
        ppa_sources_file = "/etc/apt/sources.list"
        ppa_sources_dir = "/etc/apt/sources.list.d/"
        ppa_words = origin.lstrip("LP-PPA-").split("-")

        source = ppa_sources_file
        if os.path.exists(ppa_sources_dir):
            for filename in os.listdir(ppa_sources_dir):
                if filename.startswith(origin.lstrip("LP-PPA-")):
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

    def get_ppa_changelog(self, ppa_owner, ppa_name, source_package, version):
        max_tarball_size = 1000000
        print ("\nFetching changelog for PPA package %s/%s/%s ..." % (ppa_owner, ppa_name, source_package))
        if source_package.startswith("lib"):
            ppa_abbr = source_package[:4]
        else:
            ppa_abbr = source_package[0]
        deb_dsc_uri = "https://ppa.launchpadcontent.net/%s/%s/ubuntu/pool/main/%s/%s/%s_%s.dsc" % (ppa_owner, ppa_name, ppa_abbr, source_package, source_package, version)
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
        deb_file_uri = "https://ppa.launchpadcontent.net/%s/%s/ubuntu/pool/main/%s/%s/%s" % (ppa_owner, ppa_name, ppa_abbr, source_package, deb_filename)
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


    @_async
    def retrieve_changelog(self, update):
        source_package = update.real_source_name
        origin = update.origin
        is_kernel_update = update.type == "kernel"

        # get the proxy settings from gsettings
        ps = proxygsettings.get_proxy_settings()


        # Remove the epoch if present in the version
        version = update.new_version
        if ":" in version:
            version = version.split(":")[-1]

        self.set_textview_changes_text(_("Downloading changelog..."))

        if ps == {}:
            # use default urllib.request proxy mechanisms (possibly *_proxy environment vars)
            proxy = urllib.request.ProxyHandler()
        else:
            # use proxy settings retrieved from gsettings
            proxy = urllib.request.ProxyHandler(ps)

        opener = urllib.request.build_opener(proxy)
        urllib.request.install_opener(opener)

        changelog = [_("No changelog available")]

        prefix = source_package[0]
        if (source_package.startswith("lib")):
            prefix = source_package[0:4]

        changelog_sources = []
        if origin == "linuxmint":
            changelog_sources.append(f"http://packages.linuxmint.com/dev/{source_package}_{version}_amd64.changes")
            changelog_sources.append(f"http://packages.linuxmint.com/dev/{source_package}_{version}_i386.changes")
        elif origin == "ubuntu":
            if is_kernel_update:
                # Ubuntu HWE kernel versions end with '~' followed by the Ubuntu version (e.g. ~22.04.1). This suffix needs to be removed to get the correct changelog URL
                kernel_version = version.split("~")[0]
                changelog_sources.append(f"https://changelogs.ubuntu.com/changelogs/pool/main/l/linux/linux_{kernel_version}/changelog")
            else:
                for component in ["main", "multiverse", "universe", "restricted"]:
                    changelog_sources.append(f"https://changelogs.ubuntu.com/changelogs/pool/{component}/{prefix}/{source_package}/{source_package}_{version}/changelog")
        elif origin == "debian":
            if is_kernel_update:
                changelog_sources.append(f"https://metadata.ftp-master.debian.org/changelogs/main/l/linux/linux_{version}_changelog")
            else:
                for component in ["main", "contrib", "non-free", "non-free-firmware"]:
                    changelog_sources.append(f"https://metadata.ftp-master.debian.org/changelogs/{component}/{prefix}/{source_package}/{source_package}_{version}_changelog")
        elif origin.startswith("LP-PPA"):
            ppa_owner, ppa_name = self.get_ppa_info(origin)
            if ppa_owner and ppa_name:
                deb_changelog = self.get_ppa_changelog(ppa_owner, ppa_name, source_package, version)
                if not deb_changelog:
                    changelog_sources.append(f"https://launchpad.net/~{ppa_owner}/+archive/ubuntu/{ppa_name}/+files/{source_package}_{version}_source.changes")
                else:
                    changelog = f"{deb_changelog}\n"
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

        self.set_textview_changes_text("\n".join(changelog))


    @_async
    def start_auto_refresh(self):
        self.auto_refresh_is_alive = True
        minute = 60
        hour = 60 * minute
        day = 24 * hour
        initial_refresh = True
        settings_prefix = ""
        refresh_type = "initial"

        while self.refresh_schedule_enabled:
            try:
                schedule = {
                    "minutes": self.settings.get_int("%srefresh-minutes" % settings_prefix),
                    "hours": self.settings.get_int("%srefresh-hours" % settings_prefix),
                    "days": self.settings.get_int("%srefresh-days" % settings_prefix)
                }
                timetosleep = schedule["minutes"] * minute + schedule["hours"] * hour + schedule["days"] * day

                if not timetosleep:
                    time.sleep(60) # sleep 1 minute, don't mind the config we don't want an infinite loop to go nuts :)
                else:
                    now = int(time.time())
                    if not initial_refresh:
                        refresh_last_run = self.settings.get_int("refresh-last-run")
                        if not refresh_last_run or refresh_last_run > now:
                            refresh_last_run = now
                            self.settings.set_int("refresh-last-run", now)
                        time_since_last_refresh = now - refresh_last_run
                        if time_since_last_refresh > 0:
                            timetosleep = timetosleep - time_since_last_refresh
                        # always wait at least 1 minute to be on the safe side
                        if timetosleep < 60:
                            timetosleep = 60

                    schedule["days"] = int(timetosleep / day)
                    schedule["hours"] = int((timetosleep - schedule["days"] * day) / hour)
                    schedule["minutes"] = int((timetosleep - schedule["days"] * day - schedule["hours"] * hour) / minute)
                    self.logger.write("%s refresh will happen in %d day(s), %d hour(s) and %d minute(s)" %
                        (refresh_type.capitalize(), schedule["days"], schedule["hours"], schedule["minutes"]))
                    time.sleep(timetosleep)
                    if not self.refresh_schedule_enabled:
                        self.logger.write(f"Auto-refresh disabled in preferences; cancelling {refresh_type} refresh")
                        self.uninhibit_pm()
                        return
                    if self.hidden:
                        self.logger.write(f"Update Manager is in tray mode; performing {refresh_type} refresh")
                        self.refresh(True)
                        while self.refreshing:
                            time.sleep(5)
                    else:
                        if initial_refresh:
                            self.logger.write(f"Update Manager window is open; skipping {refresh_type} refresh")
                        else:
                            self.logger.write(f"Update Manager window is open; delaying {refresh_type} refresh by 60s")
                            time.sleep(60)
            except Exception as e:
                print (e)
                self.logger.write_error("Exception occurred during %s refresh: %s" % (refresh_type, str(sys.exc_info()[0])))

            if initial_refresh:
                initial_refresh = False
                settings_prefix = "auto"
                refresh_type = "auto"
        else:
            self.logger.write("Auto-refresh disabled in preferences, automatic refresh thread stopped")
        self.auto_refresh_is_alive = False


    def switch_page(self, notebook, page, page_num):
        selection = self.treeview.get_selection()
        (model, iter) = selection.get_selected()
        if iter and page_num == 2 and not self.changelog_retriever_started:
            # Changelog tab
            update = model.get_value(iter, UPDATE_OBJ)
            self.retrieve_changelog(update)
            self.changelog_retriever_started = True

    def display_package_list(self, update, is_flatpak=False):
        prefix = "\n    • "
        count = len(update.package_names)
        if is_flatpak:
            size_label = _("Total size: <")
            if update.installing:
                self.textview_packages.set_text("%s %s" % (size_label, size_to_string(update.size)))
                return
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
            if iter is not None:
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
        self.refresh(False)

######### SYSTRAY #########

    def tray_activate(self, time=0):
        try:
            focused = self.ui_window.get_window().get_state() & Gdk.WindowState.FOCUSED
        except:
            focused = self.ui_window.is_active() and self.ui_window.get_visible()

        if focused:
            self.save_window_size()
            self.hide_window()
        else:
            self.show_window(time)

    def on_statusicon_activated(self, icon, button, time):
        if button == Gdk.BUTTON_PRIMARY:
            self.tray_activate(time)

    def quit(self, widget, data = None):
        if self.ui_window:
            self.hide_window()
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
            self.logger.remove_callback()
            self.information_window_showing = False
            window.destroy()
        @_idle
        def update_log(line):
            textbuffer.insert(textbuffer.get_end_iter(), line)
        gladefile = "/usr/share/linuxmint/mintupdate/information.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_title(_("Information"))
        window.set_icon_name("mintupdate")

        # Add mintupdate style class for easier theming
        self.ui_window.get_style_context().add_class('mintupdate')

        textbuffer = builder.get_object("log_textview").get_buffer()
        window.connect("destroy", destroy_window)
        builder.get_object("close_button").connect("clicked", destroy_window)
        builder.get_object("processid_label").set_text(str(os.getpid()))
        textbuffer.set_text(self.logger.read())
        builder.get_object("log_filename").set_text(str(self.logger.log.name))
        self.logger.set_callback(update_log)
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

        # Add mintupdate style class for easier theming
        self.ui_window.get_style_context().add_class('mintupdate')

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
        treeview.set_search_equal_func(name_search_func)
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
            logfile = os.path.join(GLib.get_user_state_dir(), 'cinnamon', 'harvester.log')
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
        treeview.set_search_column(COL_NAME)

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
        os.system("xdg-open https://linuxmint-user-guide.readthedocs.io/en/latest/mintupdate.html &")

    def open_rel_upgrade(self, widget):
        os.system("/usr/bin/mint-release-upgrade &")

    def open_about(self, widget):
        dlg = Gtk.AboutDialog()
        dlg.set_transient_for(self.ui_window)
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
        dlg.set_website("https://www.github.com/linuxmint/mintupdate")
        def close(w, res):
            if res == Gtk.ResponseType.CANCEL or res == Gtk.ResponseType.DELETE_EVENT:
                w.destroy()
        dlg.connect("response", close)
        dlg.show()

    def open_repositories(self, widget):
        self.ui_window.set_sensitive(False)
        self.run_mintsources()

    @_async
    def run_mintsources(self):
        proc = subprocess.Popen(["pkexec", "mintsources"])
        proc.wait()
        self.refresh(False)

    def open_timeshift(self, widget):
        subprocess.Popen(["pkexec", "timeshift-gtk"])

    def open_shortcuts(self, widget):
        gladefile = "/usr/share/linuxmint/mintupdate/shortcuts.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        window = builder.get_object("shortcuts")
        window.connect("destroy", Gtk.Widget.destroyed, window)

        if self.ui_window != window.get_transient_for():
            window.set_transient_for(self.ui_window)

        window.show_all()
        window.present_with_time(Gtk.get_current_event_time())

######### PREFERENCES SCREEN #########

    def open_preferences(self, widget, show_automation=False):
        if self.preferences_window_showing:
            return
        self.preferences_window_showing = True
        self.ui_window.set_sensitive(False)
        gladefile = "/usr/share/linuxmint/mintupdate/preferences.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        window = builder.get_object("main_window")
        window.set_transient_for(self.ui_window)
        window.set_title(_("Preferences"))
        window.set_icon_name("mintupdate")

        # Add mintupdate style class for easier theming
        self.ui_window.get_style_context().add_class('mintupdate')

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
        stack.add_titled(builder.get_object("page_blacklist"), "page_blacklist", _("Packages"))
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
        label.set_markup("<i>%s</i>" % _("Note: The list only gets refreshed while the Update Manager window is closed (in system tray mode)."))
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

        box = builder.get_object("update_types_box")
        page = SettingsPage()
        box.pack_start(page, True, True, 0)

        # if False:
        if os.path.exists("/usr/bin/cinnamon") or os.path.exists("/usr/bin/flatpak"):
            section = page.add_section(_("Update types"), _("In addition to system packages, check for:"))

            if os.path.exists("/usr/bin/cinnamon"):
                section.add_row(GSettingsSwitch(_("Cinnamon spice updates"), "com.linuxmint.updates", "show-cinnamon-updates"))
            if os.path.exists("/usr/bin/flatpak"):
                section.add_row(GSettingsSwitch(_("Flatpak updates"), "com.linuxmint.updates", "show-flatpak-updates"))
            box.show_all()
        else:
            box.set_no_show_all(True)
            box.hide()

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
        button.set_tooltip_text(_("Click this button to make automatic updates use your current blacklist."))
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
        if self.refresh_schedule_enabled and not self.auto_refresh_is_alive:
            self.start_auto_refresh()

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
        if iter is not None:
            pkg = model.get_value(iter, BLACKLIST_PKG_NAME)
            model.remove(iter)
        self.save_blacklist(treeview_blacklist)

    def close_preferences(self, widget, window):
        self.ui_window.set_sensitive(True)
        self.preferences_window_showing = False
        window.destroy()

        if self.app_restart_required:
            self.restart_app()
        else:
            self.refresh(False)

    def restart_app(self):
        self.logger.write("Restarting update manager...")
        os.system("/usr/lib/linuxmint/mintUpdate/mintUpdate.py show &")

    def inhibit_pm(self, reason):
        if self.inhibit_cookie > 0:
            return

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        except GLib.Error as e:
            self.logger.write("Couldn't get session bus to inhibit power management: %s" % e.message)
            return

        name, path, iface, args, unused = self.get_inhibitor_info(reason)

        try:
            ret = bus.call_sync(
                name,
                path,
                iface,
                "Inhibit",
                args,
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None
            )
        except GLib.Error as e:
            self.logger.write("Could not inhibit power management: %s" % e.message)
            return

        self.logger.write("Inhibited power management")
        self.inhibit_cookie = ret.unpack()[0]

    def uninhibit_pm(self):
        if self.inhibit_cookie > 0:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            except GLib.Error as e:
                self.logger.write("Couldn't get session bus to uninhibit power management: %s" % e.message)
                return

            name, path, iface, unused_args, uninhibit_method = self.get_inhibitor_info("none")

            try:
                bus.call_sync(
                    name,
                    path,
                    iface,
                    uninhibit_method,
                    GLib.Variant("(u)", (self.inhibit_cookie,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None
                )
            except GLib.Error as e:
                self.logger.write("Could not uninhibit power management: %s" % e.message)
                return

            self.logger.write("Resumed power management")
            self.inhibit_cookie = 0

    def get_inhibitor_info(self, reason):
        session = os.environ.get("XDG_CURRENT_DESKTOP")

        if session == "XFCE":
            name = "org.freedesktop.PowerManagement"
            path = "/org/freedesktop/PowerManagement/Inhibit"
            iface = "org.freedesktop.PowerManagement.Inhibit"
            args = GLib.Variant("(ss)", ("mintupdate", reason))
            uninhibit_method = "UnInhibit"
        else:
            # https://github.com/linuxmint/cinnamon-session/blob/master/cinnamon-session/csm-inhibitor.h#L51-L58
            #       LOGOUT | SUSPEND
            flags =      1 | 4

            xid = 0
            if os.environ.get("XDG_SESSION_TYPE", "x11") == "x11":
                try:
                    xid = self.ui_window.get_window().get_xid()
                except:
                    pass

            name = "org.gnome.SessionManager"
            path = "/org/gnome/SessionManager"
            iface = "org.gnome.SessionManager"
            args = GLib.Variant("(susu)", ("mintupdate", xid, reason, flags))
            uninhibit_method = "Uninhibit"

        return name, path, iface, args, uninhibit_method

######### KERNEL FEATURES #########

    def on_kernel_menu_activated(self, widget):
        self.ui_window.set_sensitive(False)
        self.cache_monitor.pause()
        KernelWindow(self.on_kernel_window_closed)

    def on_kernel_window_closed(self, needs_refresh):
        self.ui_window.set_sensitive(True)
        self.cache_monitor.resume()
        if needs_refresh:
            self.refresh(False)

######### REFRESH THREAD ##########

    def add_update_to_model(self, model, tracker, item):
        update, title, description, source, icon, sort_key, tooltip = item

        iter = model.insert_before(None, None)
        model.row_changed(model.get_path(iter), iter)

        model.set_value(iter, UPDATE_CHECKED, True)
        if self.settings.get_boolean("show-descriptions"):
            model.set_value(iter, UPDATE_DISPLAY_NAME, "<b>%s</b>\n%s" % (GLib.markup_escape_text(title),
                                                                          GLib.markup_escape_text(description)))
        else:
            model.set_value(iter, UPDATE_DISPLAY_NAME, "<b>%s</b>" % GLib.markup_escape_text(title))

        model.set_value(iter, UPDATE_OLD_VERSION, update.old_version)
        model.set_value(iter, UPDATE_NEW_VERSION, update.new_version)
        model.set_value(iter, UPDATE_SOURCE, source)
        model.set_value(iter, UPDATE_SIZE, update.size)
        model.set_value(iter, UPDATE_SIZE_STR, size_to_string(update.size))
        model.set_value(iter, UPDATE_TYPE_PIX, icon)
        model.set_value(iter, UPDATE_TYPE, update.type)
        model.set_value(iter, UPDATE_TOOLTIP, tooltip)
        model.set_value(iter, UPDATE_SORT_STR, sort_key)
        model.set_value(iter, UPDATE_OBJ, update)

        if tracker.active and update.type != "unstable":
            tracker.update(update)

    def check_policy(self):
        """ Check the presence of the Mint layer """
        p = subprocess.run(['apt-cache', 'policy'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"LC_ALL": "C"})
        output = p.stdout.decode()
        if p.stderr:
            error_msg = p.stderr.decode().strip()
            self.logger.write_error("APT policy error:\n%s" % error_msg)
            return False
        mint_layer_found = False
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("700") and line.endswith("Packages") and "/upstream" in line:
                mint_layer_found = True
                break
        return mint_layer_found

    def mirror_check(self):
        """ Mirror-related notifications """
        infobar_message = None
        infobar_message_type = Gtk.MessageType.WARNING
        infobar_callback = self._on_infobar_mintsources_response
        try:
            if os.path.exists("/usr/bin/mintsources") and os.path.exists("/etc/apt/sources.list.d/official-package-repositories.list"):
                mirror_url = None
                with open("/etc/apt/sources.list.d/official-package-repositories.list", 'r') as sources_file:
                    for line in sources_file:
                        line = line.strip()
                        if line.startswith("deb ") and "main upstream import" in line:
                            mirror_url = line.split()[1]
                            if mirror_url.endswith("/"):
                                mirror_url = mirror_url[:-1]
                            break
                if mirror_url is None or not mirror_url.startswith("http"):
                    # The Mint mirror being used either cannot be found or is not an HTTP(s) mirror
                    pass
                elif mirror_url == "http://packages.linuxmint.com":
                    if not self.settings.get_boolean("default-repo-is-ok"):
                        infobar_title = _("Do you want to switch to a local mirror?")
                        infobar_message = _("Local mirrors are usually faster than packages.linuxmint.com.")
                        infobar_message_type = Gtk.MessageType.QUESTION
                elif not self.hidden:
                    # Only perform up-to-date checks when refreshing from the UI (keep the load lower on servers)
                    mint_timestamp = self.get_url_last_modified("http://packages.linuxmint.com/db/version")
                    mirror_timestamp = self.get_url_last_modified("%s/db/version" % mirror_url)
                    if mirror_timestamp is None:
                        if mint_timestamp is None:
                            # Both default repo and mirror are unreachable; so, assume there's no Internet connection
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
            self.show_infobar(infobar_title,
                            infobar_message,
                            infobar_message_type,
                            None,
                            infobar_callback)

    def _on_infobar_mintsources_response(self, infobar, response_id):
        infobar.destroy()
        if response_id == Gtk.ResponseType.NO:
            self.settings.set_boolean("default-repo-is-ok", True)
        else:
            subprocess.Popen(["pkexec", "mintsources"])

    def get_url_last_modified(self, url):
        try:
            c = pycurl.Curl()
            c.setopt(pycurl.URL, url)
            c.setopt(pycurl.CONNECTTIMEOUT, 10)
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
                            if not pkgFound:
                                newPkg = cache[o.name]
                                changes.append(newPkg)
                                foundSomething = True
                    except Exception as e:
                        print (e)
                        pass # don't know why we get these..
        if (foundSomething):
            changes = self.checkDependencies(changes, cache)
        return changes

    def refresh_cleanup(self):
        # cleanup when finished refreshing
        self.refreshing = False
        self.uninhibit_pm()
        self.cache_monitor.resume()
        self.set_refresh_mode(False)

    @_idle
    def refresh(self, refresh_cache):
        if self.refreshing:
            return False

        if self.updates_inhibited:
            self.logger.write("Updates are inhibited, skipping refresh")
            self.show_window()
            return False

        # Switch to status_refreshing page
        self.refreshing = True
        self.set_refresh_mode(True)
        self.set_status(_("Checking for updates"), _("Checking for updates"), "mintupdate-checking-symbolic", not self.settings.get_boolean("hide-systray"))
        self.inhibit_pm("Checking for updates")
        self.cache_monitor.pause()

        if self.reboot_required:
            self.show_infobar(_("Reboot required"),
                _("You have installed updates that require a reboot to take effect. Please reboot your system as soon as possible."), Gtk.MessageType.WARNING, "system-reboot-symbolic", None)

        if refresh_cache:
            # Note: All cache refresh happen asynchronously
            # refresh_updates() waits for them to finish
            self.logger.write("Refreshing cache")

            # APT
            self.settings.set_int("refresh-last-run", int(time.time()))
            self.refreshing_apt = True
            if self.hidden:
                self.refresh_apt_cache_externally()
            else:
                client = aptkit.simpleclient.SimpleAPTClient(self.ui_window)
                client.set_finished_callback(self.on_cache_updated)
                client.update_cache()

            # Cinnamon
            if CINNAMON_SUPPORT:
                self.refreshing_cinnamon = True
                self.refresh_cinnamon_cache()

            # Flatpak
            if FLATPAK_SUPPORT:
                self.refreshing_flatpak = True
                self.refresh_flatpak_cache()

        self.refresh_updates()

    @_async
    def refresh_apt_cache_externally(self):
        try:
            refresh_command = ["sudo", "/usr/bin/mint-refresh-cache"]
            subprocess.run(refresh_command)
        except:
            print("Exception while calling mint-refresh-cache")
        finally:
            self.refreshing_apt = False

    @_async
    def refresh_cinnamon_cache(self):
        self.logger.write("Refreshing cache for Cinnamon updates")
        for spice_type in cinnamon.updates.SPICE_TYPES:
            try:
                self.cinnamon_updater.refresh_cache_for_type(spice_type)
            except:
                self.logger.write_error("Something went wrong fetching Cinnamon %ss: %s" % (spice_type, str(sys.exc_info()[0])))
                print("-- Exception occurred fetching Cinnamon %ss:\n%s" % (spice_type, traceback.format_exc()))
        self.refreshing_cinnamon = False

    @_async
    def refresh_flatpak_cache(self):
        self.logger.write("Refreshing cache for Flatpak updates")
        self.flatpak_updater.refresh()
        self.refreshing_flatpak = False

    def on_cache_updated(self, transaction=None, exit_state=None):
        self.refreshing_apt = False

    # called in a different process
    def check_apt_in_external_process(self, queue):
        # in the queue we put: error_message (None if successful), list_of_updates (None if error)
        try:
            check = checkAPT.APTCheck()
            check.find_changes()
            check.apply_l10n_descriptions()
            check.load_aliases()
            check.apply_aliases()
            check.clean_descriptions()
            updates = check.get_updates()
            queue.put([None, updates])
        except Exception as error:
            error_msg = str(error).replace("E:", "\n").strip()
            queue.put([error_msg, None])
            print(sys.exc_info()[0])
            print("Error in checkAPT: %s" % error)
            traceback.print_exc()

    @_async
    def refresh_updates(self):
        # Wait for all the caches to be refreshed
        while (self.refreshing_apt or self.refreshing_flatpak or self.refreshing_cinnamon):
            time.sleep(1)

        # Check presence of Mint layer
        if os.getenv("MINTUPDATE_TEST") == "layer-error" or (not self.check_policy()):
            error_msg = "%s\n%s\n%s" % (_("Your APT configuration is corrupt."),
            _("Do not install or update anything - doing so could break your operating system!"),
            _("To switch to a different Linux Mint mirror and solve this problem, click OK."))
            self.show_error(error_msg)
            self.set_status(_("Could not refresh the list of updates"),
                    "%s%s%s" % (_("Could not refresh the list of updates"), "\n\n" if error_msg else "", error_msg),
                    "mintupdate-error-symbolic", True)
            self.show_infobar(_("Please switch to another Linux Mint mirror"),
                _("Your APT configuration is corrupt."), Gtk.MessageType.ERROR, None,
                self._on_infobar_mintsources_response)
            self.refresh_cleanup()
            return

        self.logger.write("Checking for updates)")

        try:
            error = None
            updates = None
            if os.getenv("MINTUPDATE_TEST") is None:
                output = subprocess.run("/usr/lib/linuxmint/mintUpdate/checkAPT.py", stdout=subprocess.PIPE).stdout.decode("utf-8")
                # call checkAPT in a different process
                queue = Queue()
                process = Process(target=self.check_apt_in_external_process, args=(queue,))
                process.start()
                error, updates = queue.get()
                process.join()
            # TODO rewrite tests to deal with classes vs text lines
            # else:
            #     if os.path.exists("/usr/share/linuxmint/mintupdate/tests/%s.test" % os.getenv("MINTUPDATE_TEST")):
            #         output = subprocess.run("sleep 1; cat /usr/share/linuxmint/mintupdate/tests/%s.test" % os.getenv("MINTUPDATE_TEST"), shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
            #     else:
            #         output = subprocess.run("/usr/lib/linuxmint/mintUpdate/checkAPT.py", stdout=subprocess.PIPE).stdout.decode("utf-8")

            if error is not None:
                self.logger.write_error("Error in checkAPT.py, could not refresh the list of updates")
                if "apt.cache.FetchFailedException" in error and " changed its " in error:
                    error += "\n\n%s" % _("Run 'apt update' in a terminal window to address this")
                self.show_error(error)
                self.set_status(_("Could not refresh the list of updates"),
                "%s%s%s" % (_("Could not refresh the list of updates"), "\n\n" if error else "", error),
                "mintupdate-error-symbolic", True)
                self.refresh_cleanup()
                return
            else:
                self.show_updates(updates)

        except:
            print("-- Exception occurred in the refresh thread:\n%s" % traceback.format_exc())
            self.logger.write_error("Exception occurred in the refresh thread: %s" % str(sys.exc_info()[0]))
            self.set_status(_("Could not refresh the list of updates"),
                                        _("Could not refresh the list of updates"), "mintupdate-error-symbolic", True)

    def show_updates(self, updates):
        try:
            model_items = []

            # Look at the updates one by one
            num_visible = 0
            num_security = 0
            num_software = 0
            download_size = 0
            is_self_update = False

            if len(updates) > 0:
                for update in updates:
                    # Check if self-update is needed
                    if update.source_name in PRIORITY_UPDATES:
                        is_self_update = True

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

                    origin = update.origin.replace("linuxmint", "Linux Mint").replace("ubuntu", "Ubuntu").replace("LP-PPA-", "PPA ").replace("debian", "Debian")

                    if update.type == "security":
                        sort_key = 1
                        tooltip = _("Security update")
                        num_security += 1
                    elif update.type == "kernel":
                        sort_key = 2
                        tooltip = _("Kernel update")
                        num_security += 1
                    elif update.type == "unstable":
                        sort_key = 7
                        tooltip = _("Unstable software. Only apply this update to help developers beta-test new software.")
                    else:
                        if origin in ["Ubuntu", "Debian", "Linux Mint", "Canonical"]:
                            sort_key = 3
                            tooltip = _("Software update")
                        else:
                            sort_key = 4
                            tooltip = "%s\n%s" % (_("3rd-party update"), origin)
                            update.type = "3rd-party"
                        num_software += 1

                    title = update.display_name
                    description = shortdesc
                    source = f"{origin} / {update.archive}"
                    icon = f"mintupdate-type-{update.type}-symbolic"
                    model_items.append((update, title, description, source, icon, f"{sort_key}{update.display_name}", tooltip))

                    num_visible += 1
                    download_size += update.size

            if FLATPAK_SUPPORT and self.flatpak_updater and not is_self_update:
                blacklist = self.settings.get_strv("blacklisted-packages")

                self.flatpak_updater.fetch_updates()
                if self.flatpak_updater.error is None:
                    for update in self.flatpak_updater.updates:
                        update.type = "flatpak"
                        if update.ref_name in blacklist or update.source_packages[0] in blacklist:
                            continue
                        if update.flatpak_type == "app":
                            tooltip = _("Flatpak application")
                        else:
                            tooltip = _("Flatpak runtime")

                        title = update.name
                        description = update.summary
                        source = update.origin
                        icon = "mintupdate-type-flatpak-symbolic"
                        model_items.append((update, title, description, source, icon, f"5{update.ref_name}", tooltip))

                        num_software += 1
                        num_visible += 1
                        download_size += update.size

            if CINNAMON_SUPPORT and not is_self_update:
                blacklist = self.settings.get_strv("blacklisted-packages")

                for update in self.cinnamon_updater.get_updates():
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
                    elif update.spice_type == "action":
                        # The constant cinnamon.SPICE_TYPE_ACTION is new in Cinnamon 6.0
                        # use the value "action" instead here so this code can be
                        # backported.
                        tooltip = _("Nemo action")
                    elif update.spice_type == cinnamon.SPICE_TYPE_THEME:
                        tooltip = _("Cinnamon theme")
                    else:
                        tooltip = _("Cinnamon extension")

                    title = update.uuid
                    description = update.name
                    source = "Linux Mint / cinnamon"
                    icon = "cinnamon-symbolic"
                    model_items.append((update, title, description, source, icon, f"6{update.uuid}", tooltip))

                    num_software += 1
                    num_visible += 1
                    download_size += update.size

            # Updates found, update status message
            self.show_updates_in_UI(num_visible, num_software, num_security, download_size, is_self_update, model_items)

            if FLATPAK_SUPPORT and self.flatpak_updater.error is not None and not is_self_update:
                self.logger.write("Could not check for flatpak updates: %s" % self.flatpak_updater.error)
                msg = _("Error checking for flatpak updates: %s") % self.flatpak_updater.error
                self.set_status_message(msg)

            # Check whether to display the mirror infobar
            self.mirror_check()

            self.logger.write("Refresh finished")

        except:
            print("-- Exception occurred while showing updates:\n%s" % traceback.format_exc())
            self.logger.write_error("Exception occurred while showing updates: %s" % str(sys.exc_info()[0]))
            self.set_status(_("Could not refresh the list of updates"),
                                        _("Could not refresh the list of updates"), "mintupdate-error-symbolic", True)

        finally:
            self.refresh_cleanup()


############## INSTALLATION #########

    def show_cinnamon_error(self, title, message):
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self._show_cinnamon_error_cb, title, message)

    def _show_cinnamon_error_cb(self, title, message):
        dialog = Gtk.MessageDialog(transient_for=self.ui_window,
                                   destroy_with_parent=True,
                                   modal=True,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.OK,
                                   use_markup=True,
                                   text=f"<big><b>{title}</b></big>")
        dialog.set_title(_("Update Manager"))

        message_label = Gtk.Label(label=message, lines=20, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
        message_label.set_max_width_chars(60)
        message_label.show()
        dialog.get_message_area().pack_start(message_label, False, False, 0)

        dialog.connect("response", lambda x, y: dialog.destroy())

        dialog.show_all()
        dialog.present()

        return GLib.SOURCE_REMOVE

    def on_apt_install_finished(self, transaction=None, exit_state=None):
        needs_refresh = False
        if exit_state == aptkit.enums.EXIT_SUCCESS:
            self.logger.write("Install finished successfully")
            # override the monitor since there's a forced refresh later already
            self.cache_monitor.update_cachetime()

            if self.settings.get_boolean("hide-window-after-update"):
                self.hide_window()

            if [pkg for pkg in PRIORITY_UPDATES if pkg in self.packages]:
                # Restart
                self.uninhibit_pm()
                self.logger.write("Mintupdate was updated, restarting it...")
                self.logger.close()
                self.restart_app()
                return

            # Refresh
            needs_refresh = True
        else:
            self.logger.write_error("APT install failed")
            self.set_status(_("Could not install the security updates"), _("Could not install the security updates"), "mintupdate-error-symbolic", True)

        self.finish_install(needs_refresh)

    @_async
    def finish_install(self, refresh_needed):
        try:
            # Install flatpaks
            if len(self.flatpaks) > 0:
                self.flatpak_updater.prepare_start_updates(self.flatpaks)
                self.flatpak_updater.perform_updates()
                if self.flatpak_updater.error is not None:
                    self.logger.write_error("Flatpak update failed %s" % self.flatpak_updater.error)
                refresh_needed = True

            # Install spices
            if len(self.spices) > 0:
                self.set_status(_("Updating Cinnamon Spices"), _("Updating Cinnamon Spices"), "mintupdate-installing-symbolic", True)
                need_cinnamon_restart = False
                try:
                    for update in self.spices:
                        self.cinnamon_updater.upgrade(update)
                        try:
                            if self.cinnamon_updater.spice_is_enabled(update):
                                need_cinnamon_restart = True
                        except:
                            need_cinnamon_restart = True
                except Exception as e:
                    self.logger.write_error("Cinnamon spice install failed %s" % str(e))
                    error_message = str(e)
                    error_title = _("Could not update Cinnamon Spices")
                    self.show_cinnamon_error(error_title, error_message)
                    refresh_needed = True
                if need_cinnamon_restart and not self.reboot_required and os.getenv("XDG_CURRENT_DESKTOP") in ["Cinnamon", "X-Cinnamon"]:
                    subprocess.run(["cinnamon-dbus-command", "RestartCinnamon", "0"])
                refresh_needed = True
        except Exception as e:
            print (e)
            self.logger.write_error("Exception occurred in the install thread: " + str(sys.exc_info()[0]))

        self.uninhibit_pm()
        self.cache_monitor.resume(False)
        self.set_window_busy(False)
        if refresh_needed:
            self.refresh(False)

    def install(self, widget):
        if self.dpkg_locked():
            self.show_dpkg_lock_msg(self.ui_window)
        else:
            # Find list of packages to install
            install_needed = False
            self.packages = []
            self.spices = []
            self.flatpaks = []
            model = self.treeview.get_model()
            iter = model.get_iter_first()
            while iter is not None:
                if model.get_value(iter, UPDATE_CHECKED):
                    install_needed = True
                    update = model.get_value(iter, UPDATE_OBJ)
                    if update.type == "cinnamon":
                        self.spices.append(update)
                        self.logger.write("Will install spice " + str(update.uuid))
                        iter = model.iter_next(iter)
                        continue
                    elif update.type == "flatpak":
                        self.flatpaks.append(update)
                        self.logger.write("Will install flatpak " + str(update.ref_name))
                        iter = model.iter_next(iter)
                        continue
                    if update.type == "kernel":
                        for pkg in update.package_names:
                            if "-image-" in pkg:
                                try:
                                    if self.is_lmde:
                                        # In Mint, platform.release() returns the kernel version. In LMDE it returns the kernel
                                        # abi version.  So for LMDE, parse platform.version() instead.
                                        version_string = platform.version()
                                        kernel_version = re.search(r"(\d+\.\d+\.\d+)", version_string).group(1)
                                    else:
                                        kernel_version = platform.release().split("-")[0]

                                    if update.old_version.startswith(kernel_version):
                                        self.reboot_required = True
                                except Exception as e:
                                    print("Warning: Could not assess the current kernel version: %s" % str(e))
                                    self.reboot_required = True
                                break
                    if update.type == "security" and \
                       [True for pkg in update.package_names if "nvidia" in pkg]:
                       self.reboot_required = True
                    for package in update.package_names:
                        self.packages.append(package)
                        self.logger.write("Will install " + str(package))
                iter = model.iter_next(iter)
            self.settings.set_int("install-last-run", int(time.time()))
            if install_needed:
                self.set_window_busy(True)
                self.cache_monitor.pause()
                self.inhibit_pm("Installing updates")
                self.logger.write("Install requested by user")
                if len(self.packages) > 0:
                    self.set_status(_("Installing updates"), _("Installing updates"), "mintupdate-installing-symbolic", True)
                    self.logger.write("Ready to launch aptkit")
                    client = aptkit.simpleclient.SimpleAPTClient(self.ui_window)
                    client.set_finished_callback(self.on_apt_install_finished)
                    client.install_packages(self.packages)
                else:
                    self.finish_install(False)

if __name__ == "__main__":
    MintUpdate()
