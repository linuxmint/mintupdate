#!/usr/bin/python3
import apt
import locale
import os
import subprocess
import tempfile
import threading
from datetime import datetime

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

from apt.utils import get_maintenance_end_date

from Classes import (CONFIGURED_KERNEL_TYPE, KERNEL_PKG_NAMES,
                     SUPPORTED_KERNEL_TYPES, get_release_dates)


def list_header_func(row, before, user_data):
    if before and not row.get_header():
        row.set_header(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

class RefreshKernelsThread(threading.Thread):
    """ Get list of installed and available kernels via checkKernels.py """
    def __init__(self, application):
        threading.Thread.__init__(self)
        self.application = application

    def run(self):
        kernels = subprocess.run(["/usr/lib/linuxmint/mintUpdate/checkKernels.py", CONFIGURED_KERNEL_TYPE],
            stdout=subprocess.PIPE).stdout.decode()
        self.application.build_kernels_list(kernels)
        self.application.refresh_kernels_list_done()

class InstallKernelThread(threading.Thread):
    def __init__(self, kernels, application, kernel_window):
        threading.Thread.__init__(self)
        self.kernels = kernels
        self.application = application
        self.kernel_window = kernel_window
        self.cache = None

    def run(self):
        Gdk.threads_enter()
        self.application.window.set_sensitive(False)
        Gdk.threads_leave()
        do_regular = False
        self.application.cache_watcher.pause()
        for kernel in self.kernels:
            if not do_regular:
                do_regular = True
                f = tempfile.NamedTemporaryFile()
                cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                    "--non-interactive", "--parent-window-id", "%s" % self.application.window.get_window().get_xid(), \
                    "-o", "Synaptic::closeZvt=true", "--set-selections-file", "%s" % f.name]
                if not self.cache:
                    self.cache = apt.Cache()
            _KERNEL_PKG_NAMES = KERNEL_PKG_NAMES.copy()
            if kernel.installed:
                _KERNEL_PKG_NAMES.append("linux-image-unsigned-VERSION-KERNELTYPE") # mainline, remove only
            for name in _KERNEL_PKG_NAMES:
                name = name.replace("VERSION", kernel.version).replace("-KERNELTYPE", kernel.type)
                if name in self.cache:
                    pkg = self.cache[name]
                    if kernel.installed:
                        if pkg.is_installed:
                            # skip kernel_type independent packages (headers) if another kernel of the
                            # same version but different type is installed
                            if not kernel.type in name and self.package_needed_by_another_kernel(kernel.version, kernel.type):
                                continue
                            pkg_line = "%s\tpurge\n" % name
                            f.write(pkg_line.encode("utf-8"))
                    else:
                        pkg_line = "%s\tinstall\n" % name
                        f.write(pkg_line.encode("utf-8"))

                # Clean out left-over meta package
                if kernel.installed:
                    def kernel_series(version):
                        return version.replace("-",".").split(".")[:3]

                    last_in_series = True
                    this_kernel_series = kernel_series(kernel.version)
                    for _type, _version in self.kernel_window.installed_kernels:
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
                                    f.write(("%s\tpurge\n" % meta_name).encode("utf-8"))
                                    f.write(("%s\tpurge\n" % meta_name.replace("linux-","linux-image-")).encode("utf-8"))
                                    f.write(("%s\tpurge\n" % meta_name.replace("linux-","linux-headers-")).encode("utf-8"))
                                    if meta_name == "linux-virtual":
                                        f.write(("linux-headers-generic\tpurge\n").encode("utf-8"))
            f.flush()

        if do_regular:
            subprocess.run(["sudo","/usr/lib/linuxmint/mintUpdate/synaptic-workaround.py","enable"])
            subprocess.run(cmd, stdout=self.application.logger.log, stderr=self.application.logger.log)
            subprocess.run(["sudo","/usr/lib/linuxmint/mintUpdate/synaptic-workaround.py","disable"])
            f.close()
        self.application.refresh()
        self.cache = None
        Gdk.threads_enter()
        self.application.window.set_sensitive(True)
        Gdk.threads_leave()

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
        button = Gtk.CheckButton("%s %s%s" % (action, kernel.version, kernel.type), False)
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
                 installable, origin, support_status, window, application, kernel_window):
        Gtk.ListBoxRow.__init__(self)

        self.application = application
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
            status_label.set_margin_right(0)
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
            link.set_markup("<a href='http://changelogs.ubuntu.com/changelogs/pool/main/l/linux/linux_%s/changelog'>Changelog</a>" % changelog_version)
            link.set_line_wrap(True)
            box.pack_start(link, False, False, 2)
            link = Gtk.Label()
            link.set_markup("<a href='https://people.canonical.com/~ubuntu-security/cve/pkg/linux'>CVE Tracker</a>")
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
        d = Gtk.MessageDialog(self.kernel_window.window, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                             Gtk.MessageType.INFO, Gtk.ButtonsType.YES_NO, message)
        d.set_default_response(Gtk.ResponseType.NO)
        r = d.run()
        d.hide()
        d.destroy()
        if r == Gtk.ResponseType.YES:
            if self.application.dpkg_locked():
                self.application.show_dpkg_lock_msg(self.kernel_window)
            else:
                thread = InstallKernelThread([kernel],
                                             self.application, self.kernel_window)
                thread.start()
                self.kernel_window.window.hide()

    def queue_kernel(self, widget, kernel):
        widget.set_sensitive(False)
        if kernel not in self.kernel_window.queued_kernels:
            self.kernel_window.button_do_queue.set_sensitive(True)
            self.kernel_window.queued_kernels_listbox.append(MarkKernelRow(kernel, self.kernel_window.queued_kernels))

class KernelWindow():

    def __init__(self, application):
        self.application = application
        self.application.window.set_sensitive(False)
        gladefile = "/usr/share/linuxmint/mintupdate/kernels.ui"
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintupdate")
        self.builder.add_from_file(gladefile)
        self.window = self.builder.get_object("window1")
        self.window.set_title(_("Kernels"))
        main_box = self.builder.get_object("main_vbox")
        info_box = self.builder.get_object("intro_box")

        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.window.add(self.main_stack)

        # status_refreshing page
        self.main_stack.add_named(self.builder.get_object("status_refreshing"), "status_refreshing")
        self.status_refreshing_spinner = self.builder.get_object("status_refreshing_spinner")

        # Setup the kernel warning page
        self.main_stack.add_named(info_box, "info_box")
        self.builder.get_object("button_continue").connect("clicked", self.on_continue_clicked, main_box)
        self.builder.get_object("button_help").connect("clicked", self.show_help)
        self.builder.get_object("checkbutton1").connect("toggled", self.on_info_checkbox_toggled)

        # Setup the main kernel page
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        stack_switcher = Gtk.StackSidebar()
        stack_switcher.set_stack(self.stack)
        scrolled_series = self.builder.get_object("box7")
        scrolled_series.pack_start(stack_switcher, True, True, 0)
        kernel_stack_box = self.builder.get_object("box1")
        kernel_stack_box.pack_start(self.stack, True, True, 0)

        self.builder.get_object("button_close").connect("clicked", self.destroy_window)
        self.window.connect("destroy", self.destroy_window)

        self.current_label = self.builder.get_object("current_label")

        # Set up the kernel mass operation confirmation window and associated buttons
        self.confirmation_dialog = self.builder.get_object("confirmation_window")
        self.confirmation_dialog.connect("destroy", self.on_cancel_clicked)
        self.confirmation_dialog.connect("delete-event", self.on_cancel_clicked)
        self.builder.get_object("b_confirmation_confirm").connect("clicked", self.on_confirm_clicked)
        self.builder.get_object("b_confirmation_cancel").connect("clicked", self.on_cancel_clicked)
        self.confirmation_listbox = self.builder.get_object("confirmation_listbox")
        self.confirmation_listbox.set_sort_func(self.confirmation_listbox_sort)
        self.remove_kernels_listbox = []
        self.queued_kernels_listbox = []
        self.queued_kernels = []
        self.button_massremove = self.builder.get_object("button_massremove")
        self.button_massremove.connect("clicked", self.show_confirmation_dialog, _("Remove Kernels"), self.remove_kernels_listbox)
        self.button_do_queue = self.builder.get_object("button_do_queue")
        self.button_do_queue.connect("clicked", self.show_confirmation_dialog, _("Perform Queued Actions"), self.queued_kernels_listbox)

        # Get distro release dates for support duration calculation
        self.release_dates = get_release_dates()

        self.allow_kernel_type_selection = False
        self.initially_configured_kernel_type = CONFIGURED_KERNEL_TYPE
        if not self.allow_kernel_type_selection and \
           self.application.settings.get_boolean("allow-kernel-type-selection"):
            self.allow_kernel_type_selection = True
        if self.allow_kernel_type_selection:
            # Set up the kernel type selection dropdown
            self.kernel_type_selector = self.builder.get_object("cb_kernel_type")
            for index, kernel_type in enumerate(SUPPORTED_KERNEL_TYPES):
                self.kernel_type_selector.append_text(kernel_type[1:])
                if kernel_type[1:] == CONFIGURED_KERNEL_TYPE[1:]:
                    self.kernel_type_selector.set_active(index)

            # Refresh window on kernel type selection change
            def on_kernel_type_selector_changed(widget):
                global CONFIGURED_KERNEL_TYPE
                CONFIGURED_KERNEL_TYPE = "-" + widget.get_active_text()
                self.application.settings.set_string("selected-kernel-type", CONFIGURED_KERNEL_TYPE)
                self.refresh_kernels_list()
            self.kernel_type_selector.connect("changed", on_kernel_type_selector_changed)

        self.main_stack.add_named(main_box, "main_box")

        # Center on main window
        window_size = self.window.get_size()
        parent_size = self.application.window.get_size()
        parent_position = self.application.window.get_position()
        parent_center_x = parent_position.root_x + parent_size.width / 2
        parent_center_y = parent_position.root_y + parent_size.height / 2
        self.window.move(parent_center_x - window_size.width / 2,
                         parent_center_y - window_size.height / 2)

        self.window.show_all()
        self.builder.get_object("cb_label").set_visible(self.allow_kernel_type_selection)
        self.builder.get_object("cb_kernel_type").set_visible(self.allow_kernel_type_selection)

        if self.application.settings.get_boolean("hide-kernel-update-warning"):
            self.refresh_kernels_list()
        else:
            self.main_stack.set_visible_child(info_box)

    def refresh_kernels_list(self):
        self.status_refreshing_spinner.start()
        self.main_stack.set_visible_child_name("status_refreshing")
        self.window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        self.remove_kernels_listbox.clear()
        for child in self.stack.get_children():
            child.destroy()
        RefreshKernelsThread(self).start()

    def refresh_kernels_list_done(self):
        Gdk.threads_enter()
        self.stack.show_all()
        self.window.get_window().set_cursor(None)
        self.main_stack.set_visible_child_name("main_box")
        self.status_refreshing_spinner.stop()
        Gdk.threads_leave()

    def build_kernels_list(self, kernels):
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
                    if not release in hwe_support_duration:
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
                is_end_of_life = False
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
                        if not kernel_type in supported_kernels.keys():
                            supported_kernels[kernel_type] = []
                        if not page_label in supported_kernels[kernel_type]:
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
                    Gdk.threads_enter()
                    self.button_massremove.set_sensitive(True)
                    Gdk.threads_leave()
                    self.remove_kernels_listbox.append(MarkKernelRow(Kernel(version, kernel_type, origin, installed),
                                                                            self.marked_kernels, version_id,
                                                                            newest_supported_in_series))

            kernel_list.append([version_id, version, pkg_version, kernel_type, page_label, label,
                                installed, used, title, installable, origin, support_status])
        del(kernel_list_prelim)

        # add kernels to UI
        Gdk.threads_enter()
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
            self.stack.add_titled(scw, page, page)

            for kernel in kernel_list:
                (version_id, version, pkg_version, kernel_type, page_label, label, installed, used, title, installable, origin, support_status) = kernel
                if used:
                    currently_using = _("You are currently using the following kernel:")
                    self.current_label.set_markup("<b>%s %s%s%s</b>" % (currently_using, label, kernel_type, ' (%s)' % support_status if support_status else ''))
                if page_label == page:
                    row = KernelRow(version, pkg_version, kernel_type, label, installed, used, title,
                        installable, origin, support_status, self.window, self.application, self)
                    list_box.add(row)

            list_box.connect("row_activated", self.on_row_activated)
        Gdk.threads_leave()

    def destroy_window(self, widget):
        self.window.destroy()
        if self.initially_configured_kernel_type != CONFIGURED_KERNEL_TYPE:
            self.application.refresh()
        self.application.window.set_sensitive(True)

    def on_continue_clicked(self, widget, main_box):
        self.refresh_kernels_list()

    def on_info_checkbox_toggled(self, widget):
        self.application.settings.set_boolean("hide-kernel-update-warning", widget.get_active())

    def on_row_activated(self, list_box, row):
        row.show_hide_children(row)

    def show_help(self, widget):
        os.system("yelp help:mintupdate/index &")

    def show_confirmation_dialog(self, widget, title, kernel_list):
        self.window.set_sensitive(False)
        for child in self.confirmation_listbox.get_children():
            self.confirmation_listbox.remove(child)
        for item in kernel_list:
            self.confirmation_listbox.add(item)
        self.confirmation_dialog.set_title(title)
        self.confirmation_dialog.show_all()

    def on_cancel_clicked(self, widget, *args):
        self.confirmation_dialog.hide()
        self.window.set_sensitive(True)
        return True

    def on_confirm_clicked(self, widget):
        self.confirmation_dialog.hide()
        if self.confirmation_listbox.get_children():
            kernel_list = self.confirmation_listbox.get_children()[0].kernel_list
        else:
            kernel_list = None
        if kernel_list:
            if self.application.dpkg_locked():
                self.application.show_dpkg_lock_msg(self.window)
                self.window.set_sensitive(True)
            else:
                thread = InstallKernelThread(kernel_list, self.application, self)
                thread.start()
                self.window.hide()
        else:
            self.window.set_sensitive(True)

    @staticmethod
    def confirmation_listbox_sort(row_1, row_2):
        return row_1.kernel.version < row_2.kernel.version
