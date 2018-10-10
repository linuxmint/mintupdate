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

from Classes import KERNEL_PKG_NAMES

KERNEL_INFO_DIR = "/usr/share/mint-kernel-info"

def list_header_func(row, before, user_data):
    if before and not row.get_header():
        row.set_header(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

class InstallKernelThread(threading.Thread):
    def __init__(self, version, application, window, remove=False):
        threading.Thread.__init__(self)
        self.version = version
        self.window = window
        self.remove = remove
        self.application = application

    def run(self):
        cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                "--non-interactive", "--parent-window-id", "%s" % self.application.window.get_window().get_xid(), "-o", "Synaptic::closeZvt=true"]
        f = tempfile.NamedTemporaryFile()
        cache = apt.Cache()
        if self.remove:
            KERNEL_PKG_NAMES.append('linux-image-unsigned-VERSION-generic') # mainline, remove only
        for name in KERNEL_PKG_NAMES:
            name = name.replace("VERSION", self.version)
            if name in cache:
                pkg = cache[name]
                if self.remove:
                    if pkg.is_installed:
                        pkg_line = "%s\tpurge\n" % name
                        f.write(pkg_line.encode("utf-8"))
                else:
                    pkg_line = "%s\tinstall\n" % name
                    f.write(pkg_line.encode("utf-8"))

        cmd.append("--set-selections-file")
        cmd.append("%s" % f.name)
        f.flush()
        comnd = subprocess.Popen(' '.join(cmd), stdout=self.application.logger.log, stderr=self.application.logger.log, shell=True)
        returnCode = comnd.wait()
        f.close()

class KernelRow(Gtk.ListBoxRow):
    def __init__(self, version, pkg_version, text, installed, used, title, installable, origin, window, application):
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
            thread = InstallKernelThread(version, self.application, window, installed)
            thread.start()
            window.hide()

class KernelWindow():
    def __init__(self, application):
        self.application = application
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

        kernels = subprocess.check_output("/usr/lib/linuxmint/mintUpdate/checkKernels.py", shell = True).decode("utf-8")
        kernels = kernels.split("\n")
        kernels.sort(reverse = True)
        kernel_list = []
        pages_needed = []
        for kernel in kernels:
            values = kernel.split('###')
            if len(values) == 8:
                (status, version_id, version, pkg_version, installed, used, installable, origin) = values
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

                page_label = label.split(".")[0] + "." + label.split(".")[1]
                kernel_list.append([version, pkg_version, page_label, label, installed, used, title, installable, origin])
                if page_label not in pages_needed:
                    pages_needed.append(page_label)

        for page in pages_needed:
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
                (version, pkg_version, page_label, label, installed, used, title, installable, origin) = kernel
                if used:
                    current_label.set_markup("<b>%s %s</b>" % (_("You are currently using the following kernel:"), kernel[3]))
                if page_label == page:
                    row = KernelRow(version, pkg_version, label, installed, used, title, installable, origin, self.window, self.application)
                    list_box.add(row)

            list_box.connect("row_activated", self.on_row_activated)

        self.main_stack.add_named(main_box, "main_box")

        if self.application.settings.get_boolean("hide-kernel-update-warning"):
            self.main_stack.set_visible_child(main_box)
        else:
            self.main_stack.set_visible_child(info_box)

        self.window.show_all()

    def hide_window(self, widget):
        self.window.hide()

    def on_continue_clicked(self, widget, main_box):
        self.main_stack.set_visible_child(main_box)

    def on_info_checkbox_toggled(self, widget):
        self.application.settings.set_boolean("hide-kernel-update-warning", widget.get_active())

    def on_row_activated(self, list_box, row):
        row.show_hide_children(row)

    def show_help(self, widget):
        os.system("yelp help:mintupdate/index &")
