#!/usr/bin/python3

import subprocess
import os
import re
import tempfile
import threading
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0') # Needed to get xid
from gi.repository import Gtk, Gdk, GdkX11

KERNEL_INFO_DIR = "/usr/share/mint-kernel-info"

# Using these two as hack to get around the lack of gtk_box_set_center_widget in 3.10
VERSION_GROUP = Gtk.SizeGroup(Gtk.SizeGroupMode.HORIZONTAL)
INFO_GROUP = Gtk.SizeGroup(Gtk.SizeGroupMode.HORIZONTAL)

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
                "--non-interactive", "--parent-window-id", "%s" % self.window.get_window().get_xid()]
        cmd.append("-o")
        cmd.append("Synaptic::closeZvt=true")
        cmd.append("--progress-str")
        cmd.append("\"" + _("Please wait, this can take some time") + "\"")
        cmd.append("--finish-str")
        if self.remove:
            cmd.append("\"" + _("The %s kernel was removed") % self.version + "\"")
        else:
            cmd.append("\"" + _("The %s kernel was installed") % self.version + "\"")
        f = tempfile.NamedTemporaryFile()

        for pkg in ['linux-headers-%s' % self.version, 'linux-headers-%s-generic' % self.version, 'linux-image-%s-generic' % self.version, 'linux-image-extra-%s-generic' % self.version]:
            if self.remove:
                pkg_line = "%s\tdeinstall\n" % pkg
            else:
                pkg_line = "%s\tinstall\n" % pkg
            f.write(pkg_line.encode("utf-8"))
        cmd.append("--set-selections-file")
        cmd.append("%s" % f.name)
        f.flush()
        comnd = subprocess.Popen(' '.join(cmd), stdout=self.application.logger.log, stderr=self.application.logger.log, shell=True)
        returnCode = comnd.wait()
        f.close()

class SidebarSwitcherRow(Gtk.ListBoxRow):
    def __init__(self, name, widget):
        Gtk.ListBoxRow.__init__(self)

        self.name = name
        self.add(widget)

class SidebarSwitcher(Gtk.Bin):
    def __init__(self):
        Gtk.Bin.__init__(self)

        self.stack = None
        scw = Gtk.ScrolledWindow()
        scw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scw.set_shadow_type(Gtk.ShadowType.IN)
        self.add(scw)

        self.list_box = Gtk.ListBox()
        self.list_box.set_header_func(list_header_func, None)
        scw.add(self.list_box)
        self.list_box.connect("row-activated", self.on_row_activated)
        Gtk.StyleContext.add_class(Gtk.Widget.get_style_context(self), "sidebar")

    def add_titled(self, name, title):
        label = Gtk.Label(title)
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        row = SidebarSwitcherRow(name, label)
        self.list_box.add(row)

    def on_row_activated(self, widget, row):
        self.stack.set_visible_child_name(row.name)

    def set_stack(self, stack):
        self.stack = stack

class KernelRow(Gtk.ListBoxRow):
    def __init__(self, version, pkg_version, text, installed, used, title, installable, window, application):
        Gtk.ListBoxRow.__init__(self)

        self.application = application

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.set_margin_top(8)
        hbox.set_margin_bottom(8)
        hbox.set_margin_left(20)
        hbox.set_margin_right(20)
        vbox.pack_start(hbox, True, True, 0)
        version_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        hbox.pack_start(version_box, False, False, 0)
        version_label = Gtk.Label()
        version_label.set_markup("%s" % text)
        version_box.pack_start(version_label, False, False, 0)
        VERSION_GROUP.add_widget(version_box)
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        info_box.set_spacing(6)
        hbox.pack_end(info_box, False, False, 0)
        INFO_GROUP.add_widget(info_box)

        if title != "":
            label = Gtk.Label()
            label.set_margin_right(6)
            label.set_margin_left(6)
            label.props.xalign = 0.5
            label.set_markup("<i>%s</i>" % title)
            Gtk.StyleContext.add_class(Gtk.Widget.get_style_context(label), "dim-label")
            hbox.pack_start(label, True, False, 0)

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(150)
        vbox.pack_start(self.revealer, True, True, 0)
        hidden_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hidden_box.set_margin_right(20)
        hidden_box.set_margin_left(20)
        hidden_box.set_margin_bottom(6)
        self.revealer.add(hidden_box)

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
        builder.get_object("button_continue").set_label(_("Continue"))
        hide_info_checkbox = builder.get_object("checkbutton1").connect("toggled", self.on_info_checkbox_toggled)
        builder.get_object("checkbutton1").set_label(_("Do not show this message again"))

        builder.get_object("title_warning").set_markup("<span font_weight='bold' size='x-large'>%s</span>" % _("Warning!"))
        builder.get_object("sub_title_warning").set_markup("<big><b>%s</b></big>" % _("Proceed with caution"))
        builder.get_object("label_warning").set_markup(_("The Linux kernel is a critical part of the system. Regressions can lead to lack of networking, lack of sound, lack of graphical environment or even the inability to boot the computer. Only install or remove kernels if you are experienced with kernels, drivers, DKMS modules and you know how to recover a non-booting computer."))
        builder.get_object("label_more_info_1").set_markup("%s" % _("You can install multiple kernels on your computer and you can select the one you want to use from the advanced options in the boot menu."))
        builder.get_object("label_more_info_2").set_markup("%s" % _("By default, your computer will boot with the most recent kernel installed."))
        builder.get_object("label_more_info_3").set_markup("%s" % _("If you are using proprietary drivers, or DKMS modules, please be aware that they will only work with the most recent kernel installed on your computer. They get recompiled every time a new kernel is installed or removed. To use proprietary drivers or DKMS modules with one particular kernel, make sure to remove all the kernels which are more recent."))

        # Setup the main kernel page
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)

        stack_switcher = SidebarSwitcher()
        stack_switcher.set_stack(stack)
        scrolled_series.pack_start(stack_switcher, True, True, 0)
        kernel_stack_box.pack_start(stack, True, True, 0)

        builder.get_object("button_close").connect("clicked", self.hide_window)
        builder.get_object("button_close").set_label(_("Close"))

        kernels = subprocess.check_output("/usr/lib/linuxmint/mintUpdate/checkKernels.py | sort -r | grep \"###\"", shell = True).decode("utf-8")
        kernels = kernels.split("\n")
        kernel_list = []
        pages_needed = []
        for kernel in kernels:
            values = kernel.split('###')
            if len(values) == 9:
                status = values[0]
                if status != "KERNEL":
                    continue
                (status, version_id, version, pkg_version, installed, used, recommended_stability, recommended_security, installable) = values
                installed = (installed == "1")
                used = (used == "1")
                title = ""
                if used:
                    title = _("Active")
                elif installed:
                    title = _("Installed")
                recommend = None
                if recommended_security == "1":
                    recommend = _("Recommended for security")
                elif recommended_stability == "1":
                    recommend = _("Recommended for stability")

                if recommend is not None:
                    if title == "":
                        title = recommend
                    else:
                        title = "%s - %s" % (recommend, title)

                installable = (installable == "1")
                label = version

                page_label = label.split(".")[0] + "." + label.split(".")[1]
                kernel_list.append([version, pkg_version, page_label, label, installed, used, title, installable])
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
            stack_switcher.add_titled(page, page)

            for kernel in kernel_list:
                (version, pkg_version, page_label, label, installed, used, title, installable) = kernel
                if used:
                    current_label.set_markup("<b>%s %s</b>" % (_("You are currently using the following kernel:"), kernel[3]))
                if page_label == page:
                    row = KernelRow(version, pkg_version, label, installed, used, title, installable, self.window, self.application)
                    list_box.add(row)

            list_box.connect("row_activated", self.on_row_activated)

        stack_switcher.list_box.select_row(stack_switcher.list_box.get_row_at_index(0))

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
