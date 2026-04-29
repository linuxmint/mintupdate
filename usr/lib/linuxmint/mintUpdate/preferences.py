#!/usr/bin/python3
# -*- coding: utf-8 -*-

import gettext
import json
import os
import subprocess
import tempfile

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
from xapp.GSettingsWidgets import *

_ = gettext.gettext

BLACKLIST_PKG_NAME = 0

with open("/usr/share/linuxmint/mintupdate/automation/index.json") as f:
    AUTOMATIONS = json.load(f)


class PreferencesWindow:
    def __init__(self, parent, show_automation=False):
        self.parent = parent
        self.settings = parent.settings

        gladefile = "/usr/share/linuxmint/mintupdate/preferences.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        self.window = builder.get_object("main_window")
        self.window.set_transient_for(parent.ui_window)
        self.window.set_title(_("Preferences"))
        self.window.set_icon_name("mintupdate")

        # Add mintupdate style class for easier theming
        parent.ui_window.get_style_context().add_class('mintupdate')

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

        self._build_options_page(builder)
        self._build_update_types(builder)
        self._build_blacklist(builder)
        self._build_automation(builder)

        self.window.show_all()

        if show_automation:
            stack.set_visible_child_name("page_auto")

    def _build_options_page(self, builder):
        box = builder.get_object("page_options")
        page = SettingsPage()
        box.pack_start(page, True, True, 0)
        section = page.add_section(_("Interface"))
        section.add_row(GSettingsSwitch(_("Hide the update manager after applying updates"),
                                        "com.linuxmint.updates", "hide-window-after-update"))
        section.add_row(GSettingsSwitch(_("Only show a tray icon when updates are available or in case of errors"),
                                        "com.linuxmint.updates", "hide-systray"))

        section = page.add_section(_("Auto-refresh"))
        switch = GSettingsSwitch(_("Refresh the list of updates automatically"),
                                 "com.linuxmint.updates", "refresh-schedule-enabled")
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
        label.set_alignment(0, 0.5)
        grid.attach(label, 0, 1, 1, 1)
        label = Gtk.Label(label=_("Then, refresh the list of updates every:"))
        label.set_justify(Gtk.Justification.LEFT)
        label.set_alignment(0, 0.5)
        grid.attach(label, 0, 2, 1, 1)

        for col, key in enumerate(("refresh-days", "refresh-hours", "refresh-minutes"), start=1):
            grid.attach(self._make_spin(key), col, 1, 1, 1)
        for col, key in enumerate(("autorefresh-days", "autorefresh-hours", "autorefresh-minutes"), start=1):
            grid.attach(self._make_spin(key), col, 2, 1, 1)

        label = Gtk.Label()
        label.set_markup("<i>%s</i>" % _("Note: The list only gets refreshed while the Update Manager window is closed (in system tray mode)."))
        grid.attach(label, 0, 3, 4, 1)
        section.add_reveal_row(grid, "com.linuxmint.updates", "refresh-schedule-enabled")

        section = SettingsSection(_("Notifications"))
        revealer = SettingsRevealer("com.linuxmint.updates", "refresh-schedule-enabled")
        revealer.add(section)
        section._revealer = revealer
        page.pack_start(revealer, False, False, 0)

        switch = GSettingsSwitch(_("Only show notifications for security and kernel updates"),
                                 "com.linuxmint.updates", "tracker-security-only")
        section.add_reveal_row(switch, "com.linuxmint.updates", "tracker-disable-notifications", [False])
        switch = GSettingsSpinButton(_("Show a notification if an update has been available for (in logged-in days):"),
                                     "com.linuxmint.updates", "tracker-max-days", mini=2, maxi=90, step=1, page=5)
        section.add_reveal_row(switch, "com.linuxmint.updates", "tracker-disable-notifications", [False])
        switch = GSettingsSpinButton(_("Show a notification if an update is older than (in days):"),
                                     "com.linuxmint.updates", "tracker-max-age", mini=2, maxi=90, step=1, page=5)
        section.add_reveal_row(switch, "com.linuxmint.updates", "tracker-disable-notifications", [False])
        switch = GSettingsSpinButton(_("Don't show notifications if an update was applied in the last (in days):"),
                                     "com.linuxmint.updates", "tracker-grace-period", mini=2, maxi=90, step=1, page=5)
        section.add_reveal_row(switch, "com.linuxmint.updates", "tracker-disable-notifications", [False])

    @staticmethod
    def _make_spin(key):
        ranges = {
            "refresh-days": (0, 99, 2),
            "refresh-hours": (0, 23, 5),
            "refresh-minutes": (0, 59, 10),
            "autorefresh-days": (0, 99, 2),
            "autorefresh-hours": (0, 23, 5),
            "autorefresh-minutes": (0, 59, 10),
        }
        mini, maxi, page_step = ranges[key]
        spin = GSettingsSpinButton("", "com.linuxmint.updates", key,
                                   mini=mini, maxi=maxi, step=1, page=page_step)
        spin.set_spacing(0)
        spin.set_margin_start(0)
        spin.set_margin_end(0)
        spin.set_border_width(0)
        return spin

    def _build_update_types(self, builder):
        box = builder.get_object("update_types_box")
        page = SettingsPage()
        box.pack_start(page, True, True, 0)

        if os.path.exists("/usr/bin/cinnamon") or os.path.exists("/usr/bin/flatpak"):
            section = page.add_section(_("Update types"), _("In addition to system packages, check for:"))
            if os.path.exists("/usr/bin/cinnamon"):
                section.add_row(GSettingsSwitch(_("Cinnamon spice updates"),
                                                "com.linuxmint.updates", "show-cinnamon-updates"))
            if os.path.exists("/usr/bin/flatpak"):
                section.add_row(GSettingsSwitch(_("Flatpak updates"),
                                                "com.linuxmint.updates", "show-flatpak-updates"))
            box.show_all()
        else:
            box.set_no_show_all(True)
            box.hide()

    def _build_blacklist(self, builder):
        treeview = builder.get_object("treeview_blacklist")
        column = Gtk.TreeViewColumn(_("Ignored Updates"), Gtk.CellRendererText(), text=BLACKLIST_PKG_NAME)
        column.set_sort_column_id(BLACKLIST_PKG_NAME)
        column.set_resizable(True)
        treeview.append_column(column)
        treeview.set_headers_clickable(True)
        treeview.set_reorderable(False)
        treeview.show()
        model = Gtk.TreeStore(str)
        model.set_sort_column_id(BLACKLIST_PKG_NAME, Gtk.SortType.ASCENDING)
        treeview.set_model(model)
        for ignored_pkg in self.settings.get_strv("blacklisted-packages"):
            iter = model.insert_before(None, None)
            model.set_value(iter, BLACKLIST_PKG_NAME, ignored_pkg)
        builder.get_object("button_add").connect("clicked", self._add_blacklisted_package, treeview)
        builder.get_object("button_remove").connect("clicked", self._remove_blacklisted_package, treeview)
        builder.get_object("button_add").set_always_show_image(True)
        builder.get_object("button_remove").set_always_show_image(True)

    def _build_automation(self, builder):
        box = builder.get_object("page_auto_inner")
        page = SettingsPage()
        box.pack_start(page, True, True, 0)

        section = page.add_section(_("Package Updates"), _("Performed as root on a daily basis"))
        autoupgrade_switch = Switch(_("Apply updates automatically"))
        autoupgrade_switch.content_widget.set_active(os.path.isfile(AUTOMATIONS["upgrade"][2]))
        autoupgrade_switch.content_widget.connect("notify::active", self._set_auto_upgrade)
        section.add_row(autoupgrade_switch)
        button = Gtk.Button(label=_("Export blacklist to /etc/mintupdate.blacklist"))
        button.set_margin_start(20)
        button.set_margin_end(20)
        button.set_border_width(5)
        button.set_tooltip_text(_("Click this button to make automatic updates use your current blacklist."))
        button.connect("clicked", self._export_blacklist)
        section.add_row(button)

        additional_options = []
        if os.path.exists("/usr/bin/cinnamon"):
            additional_options.append(GSettingsSwitch(_("Update Cinnamon spices automatically"),
                                                      "com.linuxmint.updates", "auto-update-cinnamon-spices"))
        if os.path.exists("/usr/bin/flatpak"):
            additional_options.append(GSettingsSwitch(_("Update Flatpaks automatically"),
                                                      "com.linuxmint.updates", "auto-update-flatpaks"))
        if additional_options:
            section = page.add_section(_("Other Updates"), _("Performed when you log in"))
            for switch in additional_options:
                section.add_row(switch)

        section = page.add_section(_("Automatic Maintenance"), _("Performed as root on a weekly basis"))
        autoremove_switch = Switch(_("Remove obsolete kernels and dependencies"))
        autoremove_switch.content_widget.set_active(os.path.isfile(AUTOMATIONS["autoremove"][2]))
        autoremove_switch.content_widget.connect("notify::active", self._set_auto_remove)
        section.add_row(autoremove_switch)
        section.add_note(_("This option always leaves at least one older kernel installed and never removes manually installed kernels."))

    def _export_blacklist(self, widget):
        filename = os.path.join(tempfile.gettempdir(), "mintUpdate/blacklist")
        blacklist = self.settings.get_strv("blacklisted-packages")
        with open(filename, "w") as f:
            f.write("\n".join(blacklist) + "\n")
        subprocess.run(["pkexec", "/usr/bin/mintupdate-automation", "blacklist", "enable"])

    def _set_auto_upgrade(self, widget, param):
        self._toggle_automation(widget, "upgrade")

    def _set_auto_remove(self, widget, param):
        self._toggle_automation(widget, "autoremove")

    @staticmethod
    def _toggle_automation(widget, automation_id):
        touchfile = AUTOMATIONS[automation_id][2]
        exists = os.path.isfile(touchfile)
        action = None
        if widget.get_active() and not exists:
            action = "enable"
        elif not widget.get_active() and exists:
            action = "disable"
        if action:
            subprocess.run(["pkexec", "/usr/bin/mintupdate-automation", automation_id, action])
        if widget.get_active() != os.path.isfile(touchfile):
            widget.set_active(not widget.get_active())

    def _save_blacklist(self, treeview):
        blacklist = []
        model = treeview.get_model()
        iter = model.get_iter_first()
        while iter is not None:
            blacklist.append(model.get_value(iter, BLACKLIST_PKG_NAME))
            iter = model.iter_next(iter)
        self.settings.set_strv("blacklisted-packages", blacklist)

    def _add_blacklisted_package(self, widget, treeview):
        dialog = Gtk.MessageDialog(self.window,
                                   Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                                   Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK, None)
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
                pkg = "%s=%s" % (name, version) if version else name
                model = treeview.get_model()
                iter = model.insert_before(None, None)
                model.set_value(iter, BLACKLIST_PKG_NAME, pkg)
        dialog.destroy()
        self._save_blacklist(treeview)

    def _remove_blacklisted_package(self, widget, treeview):
        selection = treeview.get_selection()
        (model, iter) = selection.get_selected()
        if iter is not None:
            model.remove(iter)
        self._save_blacklist(treeview)
