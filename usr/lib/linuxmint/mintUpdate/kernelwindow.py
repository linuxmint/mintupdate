#!/usr/bin/python3
import apt
import subprocess
import os
import tempfile
import threading
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0') # Needed to get xid
from gi.repository import Gtk
import time
from datetime import datetime
import locale
from apt.utils import get_maintenance_end_date
from Classes import get_release_dates
from Classes import KERNEL_PKG_NAMES, SUPPORTED_KERNEL_TYPES, CONFIGURED_KERNEL_TYPE

KERNEL_INFO_DIR = "/usr/share/mint-kernel-info"

def list_header_func(row, before, user_data):
    if before and not row.get_header():
        row.set_header(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

class InstallKernelThread(threading.Thread):
    def __init__(self, kernels, application, kernel_window):
        threading.Thread.__init__(self)
        self.kernels = kernels
        self.application = application
        self.kernel_window = kernel_window
        self.cache = None

    def run(self):
        self.application.window.set_sensitive(False)
        do_regular = False
        for (version, kernel_type, origin, remove) in self.kernels:
            if not do_regular:
                do_regular = True
                f = tempfile.NamedTemporaryFile()
                cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                    "--non-interactive", "--parent-window-id", "%s" % self.application.window.get_window().get_xid(), \
                    "-o", "Synaptic::closeZvt=true", "--set-selections-file", "%s" % f.name]
                if not self.cache:
                    self.cache = apt.Cache()
            _KERNEL_PKG_NAMES = KERNEL_PKG_NAMES.copy()
            if remove:
                _KERNEL_PKG_NAMES.append("linux-image-unsigned-VERSION-KERNELTYPE") # mainline, remove only
            for name in _KERNEL_PKG_NAMES:
                name = name.replace("VERSION", version).replace("-KERNELTYPE", kernel_type)
                if name in self.cache:
                    pkg = self.cache[name]
                    if remove:
                        if pkg.is_installed:
                            # skip kernel_type independent packages (headers) if another kernel of the
                            # same version but different type is installed
                            if not kernel_type in name and self.package_needed_by_another_kernel(version, kernel_type):
                                continue
                            pkg_line = "%s\tpurge\n" % name
                            f.write(pkg_line.encode("utf-8"))
                    else:
                        pkg_line = "%s\tinstall\n" % name
                        f.write(pkg_line.encode("utf-8"))

                # Clean out left-over meta package
                if remove:
                    def kernel_series(version):
                        return version.replace("-",".").split(".")[:3]

                    last_in_series = True
                    this_kernel_series = kernel_series(version)
                    for _type, _version in self.kernel_window.installed_kernels:
                        if (_type == kernel_type and _version != version and
                            kernel_series(_version) == this_kernel_series
                            ):
                            last_in_series = False
                    if last_in_series:
                        meta_names = []
                        _metas = [s for s in self.cache.keys() if s.startswith("linux" + kernel_type)]
                        if kernel_type == "-generic":
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
        self.cache = None
        self.application.window.set_sensitive(True)

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

class MarkKernelRow(Gtk.ListBoxRow):
    def __init__(self, version_id, supported, version, kernel_type, window):
        Gtk.ListBoxRow.__init__(self)
        self.window = window
        button = Gtk.CheckButton(version + kernel_type, False)
        button.kernel_version = version
        button.kernel_type = kernel_type
        button.connect("toggled", self.on_checked)
        Gtk.ToggleButton.set_active(button, not supported and ACTIVE_KERNEL_VERSION > version_id)
        self.add(button)

    def on_checked(self, widget):
        if widget.get_active():
            self.window.marked_kernels.append([widget.kernel_version, widget.kernel_type, None, True])
        else:
            self.window.marked_kernels.remove([widget.kernel_version, widget.kernel_type, None, True])

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
            Gtk.StyleContext.add_class(Gtk.Widget.get_style_context(label), "dim-label")
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
            link.set_markup("<a href='https://people.canonical.com/~ubuntu-security/cve/pkg/linux.html'>CVE Tracker</a>")
            link.set_line_wrap(True)
            box.pack_start(link, False, False, 2)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        button = Gtk.Button.new_with_label("")
        Gtk.StyleContext.add_class(Gtk.Widget.get_style_context(button), "text-button")
        button.set_sensitive(False)
        button.set_tooltip_text("")
        if installed:
            button.set_label(_("Remove"))
            button.connect("clicked", self.install_kernel, version, installed, window, origin, kernel_type)
            if used:
                button.set_tooltip_text(_("This kernel cannot be removed because it is currently in use."))
            else:
                button.set_sensitive(True)
        else:
            button.set_label(_("Install"))
            button.connect("clicked", self.install_kernel, version, installed, window, origin, kernel_type)
            if not installable:
                button.set_tooltip_text(_("This kernel is not installable."))
            else:
                button.set_sensitive(True)
        button_box.pack_end(button, False, False, 0)
        hidden_box.pack_start(button_box, False, False, 0)

    def show_hide_children(self, widget):
        if self.revealer.get_child_revealed():
            self.revealer.set_reveal_child(False)
        else:
            self.revealer.set_reveal_child(True)

    def install_kernel(self, widget, version, installed, window, origin, kernel_type):
        if installed:
            message = _("Are you sure you want to remove the %s kernel?") % version
        else:
            message = _("Are you sure you want to install the %s kernel?") % version
        d = Gtk.MessageDialog(window, Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT, Gtk.MessageType.INFO, Gtk.ButtonsType.YES_NO, message)
        d.set_default_response(Gtk.ResponseType.NO)
        r = d.run()
        d.hide()
        d.destroy()
        if r == Gtk.ResponseType.YES:
            if self.application.dpkg_locked():
                self.application.show_dpkg_lock_msg(window)
            else:
                thread = InstallKernelThread([[version, kernel_type, origin, installed]],
                                             self.application, self.kernel_window)
                thread.start()
                window.hide()

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
        self.remove_kernels_window = self.builder.get_object("confirmation_window")

        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.window.add(self.main_stack)

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
        self.builder.get_object("button_massremove").connect("clicked", self.show_remove_kernels_window, self.remove_kernels_window)

        self.current_label = self.builder.get_object("current_label")
        self.remove_kernels_listbox = self.builder.get_object("box_list")

        # Set up the kernel mass removal confirmation window
        self.builder.get_object("b_cancel").connect("clicked", self.on_cancel_clicked, self.remove_kernels_window)
        self.builder.get_object("b_remove").connect("clicked", self.on_remove_clicked, self.remove_kernels_window)

        # Get distro release dates for support duration calculation
        self.release_dates = get_release_dates()

        # Build kernels list
        self.allow_kernel_type_selection = False
        self.build_kernels_list()

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
                for child in self.stack.get_children():
                    child.destroy()
                for child in self.remove_kernels_listbox.get_children():
                    child.destroy()
                self.build_kernels_list()
                self.stack.show_all()
            self.kernel_type_selector.connect("changed", on_kernel_type_selector_changed)

        self.main_stack.add_named(main_box, "main_box")

        if self.application.settings.get_boolean("hide-kernel-update-warning"):
            self.main_stack.set_visible_child(main_box)
        else:
            self.main_stack.set_visible_child(info_box)

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

    def build_kernels_list(self):
        # get list of installed and available kernels from apt
        now = datetime.now()
        hwe_support_duration = {}

        kernels = subprocess.run(["/usr/lib/linuxmint/mintUpdate/checkKernels.py", CONFIGURED_KERNEL_TYPE],
            stdout=subprocess.PIPE).stdout.decode()
        kernels = kernels.split("\n")
        kernels.sort()
        kernel_list_prelim = []
        pages_needed = []
        pages_needed_sort = []
        self.marked_kernels = []
        for kernel in kernels:
            values = kernel.split('###')
            if len(values) == 11:
                (version_id, version, pkg_version, installed, used, installable, origin, archive, support_duration, kernel_type) = values[1:]
                installed = (installed == "1")
                used = (used == "1")
                title = ""
                if used:
                    title = _("Active")
                    # ACTIVE_KERNEL_VERSION is used by the MarkKernelRow class
                    global ACTIVE_KERNEL_VERSION
                    ACTIVE_KERNEL_VERSION = version_id
                elif installed:
                    title = _("Installed")
                if origin == "0":
                    title += " (local)"

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

                kernel_list_prelim.append([version_id, version, pkg_version, kernel_type, page_label, label, installed, used, title, \
                    installable, origin, release, support_duration])
                if page_label not in pages_needed:
                    pages_needed.append(page_label)
                    pages_needed_sort.append([version_id,page_label])

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
                    self.remove_kernels_listbox.add(MarkKernelRow(version_id, newest_supported_in_series, version, kernel_type, self))

            kernel_list.append([version_id, version, pkg_version, kernel_type, page_label, label, installed, used, title, installable, origin, support_status])
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
            self.stack.add_titled(scw, page, page)

            for kernel in kernel_list:
                (version_id, version, pkg_version, kernel_type, page_label, label, installed, used, title, installable, origin, support_status) = kernel
                if used:
                    currently_using = _("You are currently using the following kernel:")
                    self.current_label.set_markup("<b>%s %s%s</b>" % (currently_using, label, " (%s)" % support_status if support_status else support_status))
                if page_label == page:
                    row = KernelRow(version, pkg_version, kernel_type, label, installed, used, title,
                        installable, origin, support_status, self.window, self.application, self)
                    list_box.add(row)

            list_box.connect("row_activated", self.on_row_activated)

    def destroy_window(self, widget):
        self.window.destroy()
        if self.initially_configured_kernel_type != CONFIGURED_KERNEL_TYPE:
            self.application.refresh()
        self.application.window.set_sensitive(True)

    def on_continue_clicked(self, widget, main_box):
        self.main_stack.set_visible_child(main_box)

    def on_info_checkbox_toggled(self, widget):
        self.application.settings.set_boolean("hide-kernel-update-warning", widget.get_active())

    def on_row_activated(self, list_box, row):
        row.show_hide_children(row)

    def show_help(self, widget):
        os.system("yelp help:mintupdate/index &")

    def show_remove_kernels_window(self, widget, window):
        self.window.set_sensitive(False)
        window.show_all()

    def on_cancel_clicked(self, widget, window):
        self.window.set_sensitive(True)
        window.hide()

    def on_remove_clicked(self, widget, window):
        window.hide()
        if self.marked_kernels:
            if self.application.dpkg_locked():
                self.application.show_dpkg_lock_msg(self.window)
                self.window.set_sensitive(True)
            else:
                thread = InstallKernelThread(self.marked_kernels, self.application, self)
                thread.start()
                self.window.hide()
        else:
            self.window.set_sensitive(True)
