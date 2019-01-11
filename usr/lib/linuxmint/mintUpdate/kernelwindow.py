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
from gi.repository import Gio
import time
import datetime
import locale
from apt.utils import get_maintenance_end_date
from Classes import KERNEL_PKG_NAMES

KERNEL_INFO_DIR = "/usr/share/mint-kernel-info"

def list_header_func(row, before, user_data):
    if before and not row.get_header():
        row.set_header(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

class InstallKernelThread(threading.Thread):
    def __init__(self, kernels, application):
        threading.Thread.__init__(self)
        self.kernels = kernels
        self.application = application

    def run(self):
        self.application.window.set_sensitive(False)
        settings = Gio.Settings("com.linuxmint.updates")
        if settings.get_boolean("use-lowlatency-kernels"):
            kernel_type = "-lowlatency"
        else:
            kernel_type = "-generic"
        do_regular = False
        for (version, remove) in self.kernels:
            if not do_regular:
                do_regular = True
                f = tempfile.NamedTemporaryFile()
                cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                    "--non-interactive", "--parent-window-id", "%s" % self.application.window.get_window().get_xid(), \
                    "-o", "Synaptic::closeZvt=true", "--set-selections-file", "%s" % f.name]
                cache = apt.Cache()
            _KERNEL_PKG_NAMES = KERNEL_PKG_NAMES.copy()
            if remove:
                _KERNEL_PKG_NAMES.append('linux-image-unsigned-VERSION' + kernel_type) # mainline, remove only
            for name in _KERNEL_PKG_NAMES:
                name = name.replace("VERSION", version)
                if name in cache:
                    pkg = cache[name]
                    if remove:
                        if pkg.is_installed:
                            pkg_line = "%s\tpurge\n" % name
                            f.write(pkg_line.encode("utf-8"))
                    else:
                        pkg_line = "%s\tinstall\n" % name
                        f.write(pkg_line.encode("utf-8"))
            f.flush()

        if do_regular:
            comnd = subprocess.Popen(' '.join(cmd), stdout=self.application.logger.log, stderr=self.application.logger.log, shell=True)
            returnCode = comnd.wait()
            f.close()
        self.application.window.set_sensitive(True)

class MarkKernelRow(Gtk.ListBoxRow):
    def __init__(self, version, window):
        Gtk.ListBoxRow.__init__(self)
        self.window = window
        button = Gtk.CheckButton(version, False)
        button.connect("toggled", self.on_checked)
        Gtk.ToggleButton.set_active(button, True)
        self.add(button)

    def on_checked(self, widget):
        if widget.get_active():
            self.window.marked_kernels.append([widget.get_label(), True])
        else:
            self.window.marked_kernels.remove([widget.get_label(), True])

class KernelRow(Gtk.ListBoxRow):
    def __init__(self, version, pkg_version, text, installed, used, title, installable, origin, support_status, window, application):
        Gtk.ListBoxRow.__init__(self)

        self.application = application

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
            button.connect("clicked", self.install_kernel, version, installed, window)
            if used:
                button.set_tooltip_text(_("This kernel cannot be removed because it is currently in use."))
            else:
                button.set_sensitive(True)
        else:
            button.set_label(_("Install"))
            button.connect("clicked", self.install_kernel, version, installed, window)
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

    def install_kernel(self, widget, version, installed, window):
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
            thread = InstallKernelThread([[version, installed]], self.application)
            thread.start()
            window.hide()

class KernelWindow():
    def __init__(self, application):
        self.application = application
        self.application.window.set_sensitive(False)
        gladefile = "/usr/share/linuxmint/mintupdate/kernels.ui"
        builder = Gtk.Builder()
        builder.set_translation_domain("mintupdate")
        builder.add_from_file(gladefile)
        self.window = builder.get_object("window1")
        self.window.set_title(_("Kernels"))
        listbox_series = builder.get_object("listbox_series")
        scrolled_series = builder.get_object("box7")
        kernel_stack_box = builder.get_object("box1")
        main_box = builder.get_object("main_vbox")
        info_box = builder.get_object("intro_box")
        current_label = builder.get_object("label6")
        self.remove_kernels_window = builder.get_object("confirmation_window")

        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.window.add(self.main_stack)

        # Setup the kernel warning page
        self.main_stack.add_named(info_box, "info_box")
        builder.get_object("button_continue").connect("clicked", self.on_continue_clicked, main_box)
        builder.get_object("button_help").connect("clicked", self.show_help)
        hide_info_checkbox = builder.get_object("checkbutton1").connect("toggled", self.on_info_checkbox_toggled)

        # Setup the main kernel page
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)

        stack_switcher = Gtk.StackSidebar()
        stack_switcher.set_stack(stack)
        scrolled_series.pack_start(stack_switcher, True, True, 0)
        kernel_stack_box.pack_start(stack, True, True, 0)

        builder.get_object("button_close").connect("clicked", self.hide_window)
        self.window.connect("destroy", self.hide_window)
        builder.get_object("button_massremove").connect("clicked", self.show_remove_kernels_window, self.remove_kernels_window)

        # Set up the kernel mass removal confirmation window
        builder.get_object("b_cancel").connect("clicked", self.on_cancel_clicked, self.remove_kernels_window)
        builder.get_object("b_remove").connect("clicked", self.on_remove_clicked, self.remove_kernels_window)
        remove_kernels_listbox = builder.get_object("box_list")

        # Get distro release dates for support duration calculation
        release_dates = {}
        if os.path.isfile("/usr/share/distro-info/ubuntu.csv"):
            distro_info = open("/usr/share/distro-info/ubuntu.csv", "r").readlines()
            for distro in distro_info[1:]:
                distro = distro.split(",")
                release_date = time.mktime(time.strptime(distro[4], '%Y-%m-%d'))
                release_date = datetime.datetime.fromtimestamp(release_date)
                support_end = time.mktime(time.strptime(distro[5].rstrip(), '%Y-%m-%d'))
                support_end = datetime.datetime.fromtimestamp(support_end)
                release_dates[distro[2]] = [release_date, support_end]
        now = datetime.datetime.now()
        hwe_support_duration = {}

        try:
            kernels = subprocess.check_output("/usr/lib/linuxmint/mintUpdate/checkKernels.py").decode("utf-8")
        except subprocess.CalledProcessError as e:
            print("Update Manager: Error in checkKernels.py output")
            kernels = e.output.decode("utf-8")
        kernels = kernels.split("\n")
        kernels.sort()
        kernel_list_prelim = []
        pages_needed = []
        pages_needed_sort = []
        self.marked_kernels = []
        for kernel in kernels:
            values = kernel.split('###')
            if len(values) == 10:
                (version_id, version, pkg_version, installed, used, installable, origin, archive, support_duration) = values[1:]
                installed = (installed == "1")
                used = (used == "1")
                title = ""
                if used:
                    title = _("Active")
                elif installed:
                    title = _("Installed")
                if origin == "0":
                    title += " (local)"

                installable = (installable == "1")
                label = version
                page_label = ".".join(label.replace("-",".").split(".")[:2])

                support_duration = int(support_duration)

                release = archive.split("-", 1)[0]
                if support_duration and origin == "1":
                    if not release in hwe_support_duration:
                        hwe_support_duration[release] = []
                    if not [x for x in hwe_support_duration[release] if x[0] == page_label]:
                        hwe_support_duration[release].append([page_label, support_duration])

                kernel_list_prelim.append([version_id, version, pkg_version, page_label, label, installed, used, title, \
                    installable, origin, release, support_duration])
                if page_label not in pages_needed:
                    pages_needed.append(page_label)
                    pages_needed_sort.append([version_id,page_label])
                if installed and not used:
                    remove_kernels_listbox.add(MarkKernelRow(version, self))

        kernel_support_info = {}
        for release in hwe_support_duration:
            if release not in release_dates.keys():
                continue
            kernel_support_info[release] = []
            kernel_count = len(hwe_support_duration[release])
            time_since_release = (now.year - release_dates[release][0].year) * 12 + (now.month - release_dates[release][0].month)
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
                            support_duration = (release_dates[release][1].year - release_dates[release][0].year) * 12 + \
                                (release_dates[release][1].month - release_dates[release][0].month)
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
                (support_end_year, support_end_month) = get_maintenance_end_date(release_dates[release][0], support_duration)
                is_end_of_life = (now.year > support_end_year or (now.year == support_end_year and now.month > support_end_month))
                if not is_end_of_life:
                    support_end_str = "%s %s" % (locale.nl_langinfo(getattr(locale,"MON_%d" %support_end_month)), support_end_year)

                kernel_support_info[release].append([page_label, support_duration, support_end_str, is_end_of_life])

        kernel_list_prelim.sort(reverse=True)
        kernel_list = []
        supported_list = []
        for kernel in kernel_list_prelim:
            (version_id, version, pkg_version, page_label, label, installed, used, title, installable, origin, release, support_duration) = kernel
            support_status = ""
            if support_duration and origin == "1":
                if release in kernel_support_info.keys():
                    support_info = [x for x in kernel_support_info[release] if x[0] == page_label]
                else:
                    support_info = None
                if support_info:
                    (page_label, support_duration, support_end_str, is_end_of_life) = support_info[0]
                    if support_end_str:
                        if not page_label in supported_list:
                            supported_list.append(page_label)
                            support_status = '%s %s' % (_("Supported until"), support_end_str)
                        else:
                            support_status = _("Superseded")
                    elif is_end_of_life:
                        support_status = _("End of Life")
            else:
                support_status = _("Unsupported")
            kernel_list.append([version_id, version, pkg_version, page_label, label, installed, used, title, installable, origin, support_status])
        del(kernel_list_prelim)
        pages_needed_sort.sort(reverse=True)

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
            stack.add_titled(scw, page, page)
            # stack_switcher.add_titled(page, page)

            for kernel in kernel_list:
                (version_id, version, pkg_version, page_label, label, installed, used, title, installable, origin, support_status) = kernel
                if used:
                    currently_using = _("You are currently using the following kernel:")
                    current_label.set_markup("<b>%s %s%s</b>" % (currently_using, label, " (%s)" % support_status if support_status else support_status))
                if page_label == page:
                    row = KernelRow(version, pkg_version, label, installed, used, title, installable, origin, support_status, self.window, self.application)
                    list_box.add(row)

            list_box.connect("row_activated", self.on_row_activated)

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

    def hide_window(self, widget):
        self.window.hide()
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
            thread = InstallKernelThread(self.marked_kernels, self.application)
            thread.start()
            self.window.hide()
        else:
            self.window.set_sensitive(True)
