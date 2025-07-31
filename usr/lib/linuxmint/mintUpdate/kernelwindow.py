#!/usr/bin/python3

# System imports
import apt
import aptkit.simpleclient
import gettext
import locale
import os
import subprocess
from datetime import datetime

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, Gio

# Local imports
from apt.utils import get_maintenance_end_date

from Classes import CONFIGURED_KERNEL_TYPE, KERNEL_PKG_NAMES, \
                     SUPPORTED_KERNEL_TYPES, get_release_dates, _idle, _async

# i18n
APP = 'mintupdate'
LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)
_ = gettext.gettext


def list_header_func(row, before, user_data):
    if before and not row.get_header():
        row.set_header(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

class Kernel():
    def __init__(self, version, kernel_type, origin, installed):
        self.version = version
        self.type = kernel_type
        self.origin = origin
        self.installed = installed

class MarkKernelRow(Gtk.ListBoxRow):
    def __init__(self, kernel, kernel_list, version_id=None, supported=None):
        Gtk.ListBoxRow.__init__(self)
        self.kernel_list = kernel_list
        self.kernel = kernel
        if kernel.installed:
            action = _("Remove")
        else:
            action = _("Install")
        button = Gtk.CheckButton(label=f"{action} {kernel.version}{kernel.type}")
        button.connect("toggled", self.on_checked)
        Gtk.ToggleButton.set_active(button, not version_id or (not supported and ACTIVE_KERNEL_VERSION > version_id))
        self.add(button)

    def on_checked(self, widget):
        if widget.get_active():
            self.kernel_list.append(self.kernel)
        else:
            self.kernel_list.remove(self.kernel)

class KernelRow(Gtk.ListBoxRow):
    def __init__(self, version, pkg_version, kernel_type, text, installed, used, title,
                 installable, origin, support_status, kernel_window):
        Gtk.ListBoxRow.__init__(self)

        self.kernel_window = kernel_window

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.set_margin_top(8)
        hbox.set_margin_bottom(8)
        hbox.set_margin_start(20)
        hbox.set_margin_end(20)
        vbox.pack_start(hbox, True, True, 0)
        version_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.pack_start(version_box, False, False, 0)
        version_label = Gtk.Label()
        version_label.set_markup("%s" % text)
        version_box.pack_start(version_label, False, False, 0)
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        info_box.set_spacing(6)
        hbox.pack_end(info_box, False, False, 0)

        if title != "":
            label = Gtk.Label()
            label.set_margin_end(6)
            label.set_margin_start(6)
            label.props.xalign = 0.5
            label.set_markup("<i>%s</i>" % title)
            hbox.set_center_widget(label)

        if support_status:
            status_label = Gtk.Label()
            status_label.set_margin_end(0)
            status_label.set_markup(support_status)
            status_label.set_halign(Gtk.Align.END)
            hbox.pack_end(status_label, True, True, 0)

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(150)
        vbox.pack_start(self.revealer, True, True, 0)
        hidden_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hidden_box.set_margin_end(20)
        hidden_box.set_margin_start(20)
        hidden_box.set_margin_bottom(6)
        self.revealer.add(hidden_box)

        if origin == "1":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.set_margin_bottom(6)
            hidden_box.pack_start(box, True, True, 0)
            link = Gtk.Label()
            link.set_markup("<a href='https://launchpad.net/ubuntu/+source/linux/+bugs?field.searchtext=%s'>Bug reports</a>" % version)
            link.set_line_wrap(True)
            box.pack_start(link, False, False, 2)
            link = Gtk.Label()
            changelog_version = pkg_version
            if "~" in pkg_version:
                changelog_version = pkg_version.split("~")[0]
            link.set_markup("<a href='https://changelogs.ubuntu.com/changelogs/pool/main/l/linux/linux_%s/changelog'>Changelog</a>" % changelog_version)
            link.set_line_wrap(True)
            box.pack_start(link, False, False, 2)
            link = Gtk.Label()
            link.set_markup("<a href='https://ubuntu.com/security/cves?package=linux'>CVE Tracker</a>")
            link.set_line_wrap(True)
            box.pack_start(link, False, False, 2)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        button = Gtk.Button()
        kernel = Kernel(version, kernel_type, origin, installed)
        button.connect("clicked", self.install_kernel, kernel)
        queuebutton = Gtk.Button()
        queuebutton.connect("clicked", self.queue_kernel, kernel)
        if installed:
            button.set_label(_("Remove"))
            queuebutton.set_label(_("Queue Removal"))
            if used:
                button.set_tooltip_text(_("This kernel cannot be removed because it is currently in use."))
                button.set_sensitive(False)
        else:
            button.set_label(_("Install"))
            queuebutton.set_label(_("Queue Installation"))
            if not installable:
                button.set_tooltip_text(_("This kernel is not installable."))
                button.set_sensitive(False)
        queuebutton.set_tooltip_text(button.get_tooltip_text())
        queuebutton.set_sensitive(button.get_sensitive())

        button_box.pack_end(button, False, False, 0)
        button_box.pack_end(queuebutton, False, False, 5)
        hidden_box.pack_start(button_box, False, False, 0)

    def show_hide_children(self, widget):
        if self.revealer.get_child_revealed():
            self.revealer.set_reveal_child(False)
        else:
            self.revealer.set_reveal_child(True)

    def install_kernel(self, widget, kernel):
        if kernel.installed:
            message = _("Are you sure you want to remove the %s kernel?") % kernel.version
        else:
            message = _("Are you sure you want to install the %s kernel?") % kernel.version
        d = Gtk.MessageDialog(parent=self.kernel_window.ui_window, modal=True, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.YES_NO)
        d.set_markup(message)
        d.set_default_response(Gtk.ResponseType.NO)
        r = d.run()
        d.hide()
        d.destroy()
        if r == Gtk.ResponseType.YES:
            self.kernel_window.install_kernels([kernel])

    def queue_kernel(self, widget, kernel):
        widget.set_sensitive(False)
        if kernel not in self.kernel_window.queued_kernels:
            self.kernel_window.ui_button_do_queue.set_sensitive(True)
            self.kernel_window.queued_kernels_listbox.append(MarkKernelRow(kernel, self.kernel_window.queued_kernels))

class KernelWindow():

    def __init__(self, callback=None):
        self.callback = callback
        self.settings = Gio.Settings(schema_id="com.linuxmint.updates")
        self.refreshing_kernels = False
        self.cache = None
        self.remove_kernels_listbox = []
        self.queued_kernels_listbox = []
        self.queued_kernels = []

        # Set up UI and signals
        gladefile = "/usr/share/linuxmint/mintupdate/kernels.ui"
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintupdate")
        self.builder.add_from_file(gladefile)
        for widget in self.builder.get_objects():
            if issubclass(type(widget), Gtk.Buildable):
                name = "ui_%s" % Gtk.Buildable.get_name(widget)
                if not "__" in name:
                    setattr(self, name, widget)

        self.ui_continue_button.connect("clicked", self.on_continue_clicked)
        self.ui_help_button.connect("clicked", self.show_help)
        self.ui_check_button.connect("toggled", self.on_info_checkbox_toggled)
        self.ui_close_button.connect("clicked", self.destroy_window)
        self.ui_window.connect("destroy", self.destroy_window)
        self.ui_confirmation_dialog.connect("destroy", self.on_cancel_clicked)
        self.ui_confirmation_dialog.connect("delete-event", self.on_cancel_clicked)
        self.ui_confirm_button.connect("clicked", self.on_confirm_clicked)
        self.ui_cancel_button.connect("clicked", self.on_cancel_clicked)
        self.ui_button_massremove.connect("clicked", self.show_confirmation_dialog, _("Remove Kernels"), self.remove_kernels_listbox)
        self.ui_button_do_queue.connect("clicked", self.show_confirmation_dialog, _("Perform Queued Actions"), self.queued_kernels_listbox)

        self.ui_confirmation_listbox.set_sort_func(self.confirmation_listbox_sort)

        # Get distro release dates for support duration calculation
        self.release_dates = get_release_dates()
        self.allow_kernel_type_selection = False
        self.initially_configured_kernel_type = CONFIGURED_KERNEL_TYPE
        if not self.allow_kernel_type_selection and \
           self.settings.get_boolean("allow-kernel-type-selection"):
            self.allow_kernel_type_selection = True
        if self.allow_kernel_type_selection:
            # Set up the kernel type selection dropdown
            for index, kernel_type in enumerate(SUPPORTED_KERNEL_TYPES):
                self.ui_kernel_type_combo.append_text(kernel_type[1:])
                if kernel_type[1:] == CONFIGURED_KERNEL_TYPE[1:]:
                    self.ui_kernel_type_combo.set_active(index)
            self.ui_kernel_type_combo.connect("changed", self.on_kernel_type_combo_changed)

        self.ui_window.show_all()
        self.ui_kernel_type_label.set_visible(self.allow_kernel_type_selection)
        self.ui_kernel_type_combo.set_visible(self.allow_kernel_type_selection)

        if self.settings.get_boolean("hide-kernel-update-warning"):
            self.refresh_kernels_list()
        else:
            self.ui_stack.set_visible_child_name("intro_page")

    # Refresh window on kernel type selection change
    def on_kernel_type_combo_changed(self, widget):
        global CONFIGURED_KERNEL_TYPE
        CONFIGURED_KERNEL_TYPE = "-" + widget.get_active_text()
        self.settings.set_string("selected-kernel-type", CONFIGURED_KERNEL_TYPE)
        self.refresh_kernels_list()

    def refresh_kernels_list(self):
        if self.refreshing_kernels:
            return
        self.refreshing_kernels = True
        self.ui_spinner.start()
        self.ui_stack.set_visible_child_name("refresh_page")
        self.ui_window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        self.remove_kernels_listbox.clear()
        for child in self.ui_kernel_stack.get_children():
            child.destroy()
        self.refresh_kernels_async()

    @_async
    def refresh_kernels_async(self):
        kernels = subprocess.run(["/usr/lib/linuxmint/mintUpdate/checkKernels.py", CONFIGURED_KERNEL_TYPE],
        stdout=subprocess.PIPE).stdout.decode()
        self.cache = apt.Cache()
        self.refresh_kernels_list_done(kernels)

    @_idle
    def refresh_kernels_list_done(self, kernels):
        self.refreshing_kernels = False
        now = datetime.now()
        hwe_support_duration = {}
        kernels = kernels.split("\n")
        kernels.sort()
        kernel_list_prelim = []
        pages_needed = []
        pages_needed_sort = []
        self.marked_kernels = []
        # ACTIVE_KERNEL_VERSION is used by the MarkKernelRow class
        global ACTIVE_KERNEL_VERSION
        ACTIVE_KERNEL_VERSION = "0"
        for kernel in kernels:
            values = kernel.split('###')
            if len(values) == 11:
                (version_id, version, pkg_version, installed, used, installable, origin, archive, support_duration, kernel_type) = values[1:]
                installed = (installed == "1")
                used = (used == "1")
                title = ""
                if used:
                    title = _("Active")
                    ACTIVE_KERNEL_VERSION = version_id
                elif installed:
                    title = _("Installed")

                installable = (installable == "1")
                if kernel_type == CONFIGURED_KERNEL_TYPE:
                    label = version
                else:
                    label = version + kernel_type
                    self.allow_kernel_type_selection = True
                page_label = ".".join(label.replace("-",".").split(".")[:2])

                support_duration = int(support_duration)

                release = archive.split("-", 1)[0]
                if support_duration and origin == "1":
                    if release not in hwe_support_duration:
                        hwe_support_duration[release] = []
                    if not [x for x in hwe_support_duration[release] if x[0] == page_label]:
                        hwe_support_duration[release].append([page_label, support_duration])

                kernel_list_prelim.append([version_id, version, pkg_version, kernel_type, page_label, label, installed, used, title,
                    installable, origin, release, support_duration])
                if page_label not in pages_needed:
                    pages_needed.append(page_label)
                    pages_needed_sort.append([version_id, page_label])

        # get kernel support duration
        kernel_support_info = {}
        for release in hwe_support_duration:
            if release not in self.release_dates.keys():
                continue
            kernel_support_info[release] = []
            kernel_count = len(hwe_support_duration[release])
            time_since_release = (now.year - self.release_dates[release][0].year) * 12 + (now.month - self.release_dates[release][0].month)
            for point_release, kernel in enumerate(hwe_support_duration[release]):
                (page_label, support_duration) = kernel
                if support_duration == -1:
                    # here's some magic to determine hwe support duration based on the release cycle
                    # described here: https://wiki.ubuntu.com/Kernel/Support#A18.04.x_Ubuntu_Kernel_Support
                    if point_release >= 4:
                        # Regularly the 4th point release is the next LTS kernel. However, this sequence breaks when
                        # out-of-turn HWE kernels like 4.11 are introduced, so we have to work around that:
                        if kernel_count > 5 and point_release < kernel_count - 1:
                            support_duration = kernel_support_info[release][3][1]
                        # the 4th point release is LTS and scheduled 28 months after original release:
                        elif time_since_release >= 28:
                            support_duration = (self.release_dates[release][1].year - self.release_dates[release][0].year) * 12 + \
                                (self.release_dates[release][1].month - self.release_dates[release][0].month)
                    if point_release >= 1 and support_duration == -1:
                        # out of turn HWE kernels can be detected quite well at the time of release,
                        # but later on there's no way to know which one was the one that was out of turn
                        max_expected_point_release = (time_since_release - 3) // 6 + 1
                        if point_release > max_expected_point_release:
                            # out of turn HWE kernel
                            support_duration = 10 + max_expected_point_release * 6
                        else:
                            # treat as regular HWE kernel
                            support_duration = 10 + point_release * 6

                support_end_str = ""
                (support_end_year, support_end_month) = get_maintenance_end_date(self.release_dates[release][0], support_duration)
                is_end_of_life = (now.year > support_end_year or (now.year == support_end_year and now.month > support_end_month))
                if not is_end_of_life:
                    support_end_str = "%s %s" % (locale.nl_langinfo(getattr(locale,"MON_%d" %support_end_month)), support_end_year)

                kernel_support_info[release].append([page_label, support_duration, support_end_str, is_end_of_life])

        kernel_list_prelim.sort(reverse=True)
        kernel_list = []
        supported_kernels = {}

        self.installed_kernels = []
        for kernel in kernel_list_prelim:
            (version_id, version, pkg_version, kernel_type, page_label, label, installed, used, title, installable, origin, release, support_duration) = kernel
            support_status = ""
            newest_supported_in_series = False
            if support_duration and origin == "1":
                if release in kernel_support_info.keys():
                    support_info = [x for x in kernel_support_info[release] if x[0] == page_label]
                else:
                    support_info = None
                if support_info:
                    (page_label, support_duration, support_end_str, is_end_of_life) = support_info[0]
                    if support_end_str:
                        if kernel_type not in supported_kernels.keys():
                            supported_kernels[kernel_type] = []
                        if page_label not in supported_kernels[kernel_type]:
                            supported_kernels[kernel_type].append(page_label)
                            support_status = '%s %s' % (_("Supported until"), support_end_str)
                            newest_supported_in_series = True
                        else:
                            support_status = _("Superseded")
                    elif is_end_of_life:
                        support_status = _("End of Life")
            else:
                support_status = _("Unsupported")
            if installed:
                self.installed_kernels.append((kernel_type, version))
                if not used:
                    self.ui_button_massremove.set_sensitive(True)
                    self.remove_kernels_listbox.append(MarkKernelRow(Kernel(version, kernel_type, origin, installed),
                                                                            self.marked_kernels, version_id,
                                                                            newest_supported_in_series))

            kernel_list.append([version_id, version, pkg_version, kernel_type, page_label, label,
                                installed, used, title, installable, origin, support_status])
        del(kernel_list_prelim)

        # add kernels to UI
        pages_needed_sort.sort(reverse=True)
        for page in pages_needed_sort:
            page = page[1]
            scw = Gtk.ScrolledWindow()
            scw.set_shadow_type(Gtk.ShadowType.IN)
            list_box = Gtk.ListBox()
            list_box.set_header_func(list_header_func, None)
            list_box.set_selection_mode(Gtk.SelectionMode.NONE)
            list_box.set_activate_on_single_click(True)
            scw.add(list_box)
            self.ui_kernel_stack.add_titled(scw, page, page)

            for kernel in kernel_list:
                (version_id, version, pkg_version, kernel_type, page_label, label, installed, used, title, installable, origin, support_status) = kernel
                if used:
                    currently_using = _("You are currently using the following kernel:")
                    self.ui_current_label.set_markup("<b>%s %s%s%s</b>" % (currently_using, label, kernel_type, ' (%s)' % support_status if support_status else ''))
                if page_label == page:
                    row = KernelRow(version, pkg_version, kernel_type, label, installed, used, title,
                        installable, origin, support_status, self)
                    list_box.add(row)

            list_box.connect("row_activated", self.on_row_activated)
        self.ui_kernel_stack.show_all()
        self.ui_window.get_window().set_cursor(None)
        self.ui_stack.set_visible_child_name("kernels_page")
        self.ui_spinner.stop()

    def install_kernels(self, kernels):
        self.ui_window.set_sensitive(False)
        if len(kernels) > 0:
            to_install = []
            to_purge = []
            for kernel in kernels:
                _KERNEL_PKG_NAMES = KERNEL_PKG_NAMES.copy()
                if kernel.installed:
                    # also purge existing residual kernel packages
                    _KERNEL_PKG_NAMES.append("linux-image-unsigned-VERSION-KERNELTYPE")
                    _KERNEL_PKG_NAMES.append("linux-tools-VERSION")
                    _KERNEL_PKG_NAMES.append("linux-tools-VERSION-KERNELTYPE")

                for name in _KERNEL_PKG_NAMES:
                    name = name.replace("VERSION", kernel.version).replace("-KERNELTYPE", kernel.type)
                    if name in self.cache:
                        pkg = self.cache[name]
                        if kernel.installed:
                            if pkg.is_installed:
                                # skip kernel_type independent packages (headers) if another kernel of the
                                # same version but different type is installed
                                if kernel.type not in name and self.package_needed_by_another_kernel(kernel.version, kernel.type):
                                    continue
                                to_purge.append(name)
                        else:
                            to_install.append(name)

                    # Clean out left-over meta package
                    if kernel.installed:
                        def kernel_series(version):
                            return version.replace("-",".").split(".")[:3]

                        last_in_series = True
                        this_kernel_series = kernel_series(kernel.version)
                        for _type, _version in self.installed_kernels:
                            if (_type == kernel.type and _version != kernel.version and
                                kernel_series(_version) == this_kernel_series
                                ):
                                last_in_series = False
                        if last_in_series:
                            meta_names = []
                            _metas = [s for s in self.cache.keys() if s.startswith("linux" + kernel.type)]
                            if kernel.type == "-generic":
                                _metas.append("linux-virtual")
                            for meta in _metas:
                                shortname = meta.split(":")[0]
                                if shortname not in meta_names:
                                    meta_names.append(shortname)
                            for meta_name in meta_names:
                                if meta_name in self.cache:
                                    meta = self.cache[meta_name]
                                    if meta.is_installed and \
                                        kernel_series(meta.candidate.version) == this_kernel_series:
                                        to_purge.append(meta_name)
                                        to_purge.append(meta_name.replace("linux-","linux-image-"))
                                        to_purge.append(meta_name.replace("linux-","linux-headers-"))
                                        if meta_name == "linux-virtual":
                                            to_purge.append("linux-headers-generic")
            client = aptkit.simpleclient.SimpleAPTClient(self.ui_window)
            client.set_finished_callback(self.on_installation_finished)
            client.set_cancelled_callback(self.on_installation_finished)
            client.set_error_callback(self.on_installation_finished)
            client.commit_changes(install=to_install, purge=to_purge)
        else:
            self.on_installation_finished()

    @_idle
    def on_installation_finished(self, transaction=None, exit_state=None):
        self.ui_window.set_sensitive(True)
        if exit_state == aptkit.enums.EXIT_SUCCESS:
            self.refresh_kernels_list()

    def package_needed_by_another_kernel(self, version, current_kernel_type):
        for kernel_type in SUPPORTED_KERNEL_TYPES:
            if kernel_type == current_kernel_type:
                continue
            for name in KERNEL_PKG_NAMES:
                if "-KERNELTYPE" in name:
                    name = name.replace("VERSION", version).replace("-KERNELTYPE", kernel_type)
                    if name in self.cache:
                        pkg = self.cache[name]
                        if pkg.is_installed:
                            return True
        return False

    def destroy_window(self, widget):
        self.ui_window.destroy()
        if self.callback is not None:
            needs_refresh = self.initially_configured_kernel_type != CONFIGURED_KERNEL_TYPE
            self.callback(needs_refresh)

    def on_continue_clicked(self, widget):
        self.refresh_kernels_list()

    def on_info_checkbox_toggled(self, widget):
        self.settings.set_boolean("hide-kernel-update-warning", widget.get_active())

    def on_row_activated(self, list_box, row):
        row.show_hide_children(row)

    def show_help(self, widget):
        os.system("xdg-open https://linuxmint-user-guide.readthedocs.io/en/latest/mintupdate.html &")

    def show_confirmation_dialog(self, widget, title, kernel_list):
        self.ui_window.set_sensitive(False)
        for child in self.ui_confirmation_listbox.get_children():
            self.ui_confirmation_listbox.remove(child)
        for item in kernel_list:
            self.ui_confirmation_listbox.add(item)
        self.ui_confirmation_dialog.set_title(title)
        self.ui_confirmation_dialog.show_all()

    def on_cancel_clicked(self, widget, *args):
        self.ui_confirmation_dialog.hide()
        self.ui_window.set_sensitive(True)
        return True

    def on_confirm_clicked(self, widget):
        self.ui_confirmation_dialog.hide()
        if self.ui_confirmation_listbox.get_children():
            kernel_list = self.ui_confirmation_listbox.get_children()[0].kernel_list
        else:
            kernel_list = []
        self.install_kernels(kernel_list)

    @staticmethod
    def confirmation_listbox_sort(row_1, row_2):
        return row_1.kernel.version < row_2.kernel.version

if __name__ == "__main__":
    window = KernelWindow()
    Gtk.main()