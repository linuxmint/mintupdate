#!/usr/bin/python3

import apt
import aptkit.simpleclient
import configparser
import gettext
import gi
import os
import subprocess

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gio
from Classes import _idle, _async
gettext.install("mintupdate", "/usr/share/locale")

class Assistant:

    def __init__(self):

        self.assistant = Gtk.Assistant()
        self.assistant.set_position(Gtk.WindowPosition.CENTER)
        self.assistant.set_title(_("System Upgrade"))
        self.assistant.connect("apply", self.apply_button_pressed)
        self.assistant.connect("cancel", self.cancel_button_pressed)
        self.assistant.connect("close", self.close_button_pressed)
        self.assistant.set_resizable(True)
        self.assistant.set_default_size(640, 480)

        # Intro page
        self.vbox_intro = Gtk.VBox()
        self.vbox_intro.set_border_width(60)
        page = self.assistant.append_page(self.vbox_intro)
        self.assistant.set_page_type(self.vbox_intro, Gtk.AssistantPageType.INTRO)
        self.assistant.set_page_title(self.vbox_intro, _("Introduction"))
        self.assistant.set_icon_name("mintupdate-release-upgrade")

        if not os.path.exists("/etc/linuxmint/info"):
            self.show_message('/usr/lib/linuxmint/mintUpdate/rel_upgrades/failure.png', _("Your system is missing critical components. A package corresponding to your edition of Linux Mint should provide the virtual package 'mint-info' and the file /etc/linuxmint/info."))
        else:
            self.current_codename = 'unknown'
            self.current_edition = 'unknown'
            self.edition = 'unknown'
            self.settings = None
            with open("/etc/linuxmint/info", "r") as info:
                for line in info:
                    line = line.strip()
                    if "EDITION=" in line:
                        self.current_edition = line.split('=')[1].replace('"', '').split()[0]
                        self.edition = self.current_edition.lower()
                    if "CODENAME=" in line:
                        self.current_codename = line.split('=')[1].replace('"', '').split()[0]

            rel_path = f"/usr/share/mint-upgrade-info/{self.current_codename}"
            if not os.path.exists(rel_path):
                self.show_message('/usr/lib/linuxmint/mintUpdate/rel_upgrades/info.png', _("No upgrades were found."))
            else:
                config = configparser.ConfigParser()
                config.read(f"{rel_path}/info")
                self.rel_target_name = config['general']['target_name']
                self.rel_target_codename = config['general']['target_codename']
                self.rel_editions = config['general']['editions']
                if self.edition in self.rel_editions:
                    label = Gtk.Label()
                    label.set_markup(_("A new version of Linux Mint is available!"))
                    self.vbox_intro.pack_start(label, False, False, 6)
                    image = Gtk.Image.new_from_file(f"{rel_path}/{self.edition}.png")
                    self.vbox_intro.pack_start(image, False, False, 0)
                    label = Gtk.Label()
                    label.set_markup("<b>%s</b>" % self.rel_target_name)
                    self.vbox_intro.pack_start(label, False, False, 0)
                    self.assistant.set_page_complete(self.vbox_intro, True)

                    # All good
                    self.meta = f"mint-meta-{self.edition}"
                    self.settings = None
                    self.screensaver_enabled = False
                    if self.edition == "cinnamon":
                        self.settings = Gio.Settings(schema="org.cinnamon.desktop.screensaver")
                        self.screensaver_enabled = self.settings.get_boolean("lock-enabled")
                    elif self.edition == "mate":
                        self.settings = Gio.Settings(schema="org.mate.screensaver")
                        self.screensaver_enabled = self.settings.get_boolean("lock-enabled")
                    self.build_assistant()
                else:
                    self.show_message('/usr/lib/linuxmint/mintUpdate/rel_upgrades/info.png', _("An upgrade was found but it is not available yet for the %s edition.") % self.current_edition)

        self.assistant.show_all()

    def build_assistant(self):
        # Known issues
        self.vbox_rel_notes = Gtk.VBox()
        self.vbox_rel_notes.set_border_width(60)
        self.assistant.append_page(self.vbox_rel_notes)
        self.assistant.set_page_title(self.vbox_rel_notes, _("Release notes"))
        self.assistant.set_page_type(self.vbox_rel_notes, Gtk.AssistantPageType.CONTENT)
        vbox_content = Gtk.HBox()
        image = Gtk.Image.new_from_file('/usr/lib/linuxmint/mintUpdate/rel_upgrades/info.png')
        vbox_content.pack_start(image, False, False, 0)
        label = Gtk.Label()
        label.set_line_wrap(True)
        label.set_markup(_("Please read the release notes before upgrading. They explain all the known issues, workarounds and solutions associated with the new version."))
        vbox_content.pack_start(label, False, False, 6)
        self.vbox_rel_notes.pack_start(vbox_content, False, False, 6)
        link = Gtk.Label()
        link.set_markup("<a href='https://www.linuxmint.com/rel_%s.php'><b>%s</b></a>" % (self.rel_target_codename, _("Release notes for %s") % self.rel_target_name))
        self.vbox_rel_notes.pack_start(link, False, False, 6)
        label = Gtk.Label()
        label.set_markup("<i><b>%s</b></i>" % _("Click on the link to open the release notes."))
        self.vbox_rel_notes.pack_start(label, False, False, 6)
        self.assistant.set_page_complete(self.vbox_rel_notes, True)

        # New features
        self.vbox_new_features = Gtk.VBox()
        self.vbox_new_features.set_border_width(60)
        self.assistant.append_page(self.vbox_new_features)
        self.assistant.set_page_title(self.vbox_new_features, _("New features"))
        self.assistant.set_page_type(self.vbox_new_features, Gtk.AssistantPageType.CONTENT)
        vbox_content = Gtk.HBox()
        image = Gtk.Image.new_from_file('/usr/lib/linuxmint/mintUpdate/rel_upgrades/features.png')
        vbox_content.pack_start(image, False, False, 0)
        label = Gtk.Label()
        label.set_line_wrap(True)
        label.set_markup(_("Please look at the new features introduced in the new version."))
        vbox_content.pack_start(label, False, False, 6)
        self.vbox_new_features.pack_start(vbox_content, False, False, 6)
        link = Gtk.Label()
        link.set_markup("<a href='https://www.linuxmint.com/rel_%s_whatsnew.php'><b>%s</b></a>" % (self.rel_target_codename, _("New features in %s") % self.rel_target_name))
        self.vbox_new_features.pack_start(link, False, False, 6)
        label = Gtk.Label()
        label.set_markup("<i><b>%s</b></i>" % _("Click on the link to browse the new features."))
        self.vbox_new_features.pack_start(label, False, False, 6)
        self.assistant.set_page_complete(self.vbox_new_features, True)

        # Warnings and risks
        self.vbox_prerequesites = Gtk.VBox()
        self.vbox_prerequesites.set_border_width(60)
        self.assistant.append_page(self.vbox_prerequesites)
        self.assistant.set_page_title(self.vbox_prerequesites, _("Requirements"))
        self.assistant.set_page_type(self.vbox_prerequesites, Gtk.AssistantPageType.CONFIRM)

        self.vbox_meta = Gtk.VBox()
        vbox_content = Gtk.HBox()
        image = Gtk.Image.new_from_file('/usr/lib/linuxmint/mintUpdate/rel_upgrades/failure.png')
        vbox_content.pack_start(image, False, False, 0)
        label = Gtk.Label()
        label.set_line_wrap(True)
        label.set_markup(_("The package %s needs to be installed before upgrading.") % self.meta)
        vbox_content.pack_start(label, False, False, 6)
        self.vbox_meta.pack_start(vbox_content, False, False, 6)
        button = Gtk.Button()
        button.set_label(_("Install %s") % self.meta)
        packages = [self.meta]
        button.connect("button-release-event", self.install_pkgs, packages)
        self.vbox_meta.pack_start(button, False, False, 6)
        label = Gtk.Label()
        label.set_markup("<i><b>%s</b></i>" % _("Click on the button to install the missing package."))
        self.vbox_meta.pack_start(label, False, False, 6)
        self.vbox_meta.pack_start(Gtk.Separator(), False, False, 6)
        self.vbox_prerequesites.pack_start(self.vbox_meta, False, False, 10)

        if self.check_meta():
            self.vbox_meta.set_no_show_all(True)
            self.vbox_meta.hide()

        vbox_content = Gtk.HBox()
        image = Gtk.Image.new_from_file('/usr/lib/linuxmint/mintUpdate/rel_upgrades/risks.png')
        vbox_content.pack_start(image, False, False, 0)
        label = Gtk.Label()
        label.set_line_wrap(True)
        label.set_markup(_("New releases provide bug fixes and new features but they also sometimes introduce new issues. Upgrading always represents a risk. Your data is safe but new issues can potentially affect your operating system."))
        vbox_content.pack_start(label, False, False, 6)
        self.vbox_prerequesites.pack_start(vbox_content, False, False, 6)
        self.check_button = Gtk.CheckButton()
        self.check_button.set_label(_("I understand the risk. I want to upgrade to %s.") % self.rel_target_name)
        self.check_button.connect("toggled", self.understood)
        self.vbox_prerequesites.pack_start(self.check_button, False, False, 6)

        self.assistant.set_page_complete(self.vbox_prerequesites, False)

        # Summary
        self.vbox_summary = Gtk.VBox()
        self.vbox_summary.set_border_width(60)
        self.assistant.append_page(self.vbox_summary)
        self.assistant.set_page_title(self.vbox_summary, _("Summary"))
        self.assistant.set_page_type(self.vbox_summary, Gtk.AssistantPageType.SUMMARY)


    def install_pkgs(self, widget, event, packages):
        client = aptkit.simpleclient.SimpleAPTClient(self.assistant)
        client.set_finished_callback(self.on_installation_finished)
        client.set_cancelled_callback(self.on_installation_finished)
        client.set_error_callback(self.on_installation_finished)
        client.install_packages(packages)

    def on_installation_finished(self, transaction=None, exit_state=None):
        self.check_reqs()

    def check_meta(self):
        cache = apt.Cache()
        if self.meta in cache:
            if cache[self.meta].is_installed:
                return True
        return False

    def understood(self, button):
        self.check_reqs()

    def check_reqs(self):
        if self.check_meta():
            self.vbox_meta.hide()
            if self.check_button.get_active():
                self.assistant.set_page_complete(self.vbox_prerequesites, True)
            else:
                self.assistant.set_page_complete(self.vbox_prerequesites, False)

    def show_message(self, icon, msg):
        vbox_content = Gtk.HBox()
        image = Gtk.Image.new_from_file(icon)
        vbox_content.pack_start(image, False, False, 0)
        label = Gtk.Label()
        label.set_line_wrap(True)
        label.set_markup(msg)
        vbox_content.pack_start(label, False, False, 6)
        self.vbox_intro.pack_start(vbox_content, True, True, 0)
        self.assistant.set_page_complete(self.vbox_intro, False)

    def cancel_button_pressed(self, assistant):
        Gtk.main_quit()

    def close_button_pressed(self, assistant):
        Gtk.main_quit()

    def apply_button_pressed(self, assistant):
        # Turn off the screensaver during the upgrade
        if self.settings is not None:
            self.settings.set_boolean("lock-enabled", False)
        self.assistant.set_sensitive(False)
        self.launch_root_upgrade()

    @_async
    def launch_root_upgrade(self):
        subprocess.run(['pkexec', '/usr/bin/mint-release-upgrade-root', self.current_codename])
        self.show_result()

    @_idle
    def show_result(self):
        # Reset the screensaver the way it was before the upgrade
        if self.settings is not None:
            self.settings.set_boolean("lock-enabled", self.screensaver_enabled)

        self.assistant.set_sensitive(True)

        image_result = "failure"
        message_text = _("The upgrade did not succeed. Make sure you are connected to the Internet and try to upgrade again.")
        if os.path.exists("/etc/linuxmint/info"):
            with open("/etc/linuxmint/info", "r") as info:
                for line in info:
                    line = line.strip()
                    if "CODENAME=" in line:
                        new_codename = line.split('=')[1].replace('"', '').split()[0]
                        if new_codename == self.rel_target_codename:
                            image_result = "success"
                            message_text = _("Your operating system was successfully upgraded. Please reboot your computer for all changes to take effect.")
                            break

        vbox_content = Gtk.HBox()
        image = Gtk.Image.new_from_file(f'/usr/lib/linuxmint/mintUpdate/rel_upgrades/{image_result}.png')
        vbox_content.pack_start(image, False, False, 0)
        label = Gtk.Label()
        label.set_line_wrap(True)
        label.set_markup(message_text)
        vbox_content.pack_start(label, False, False, 6)
        self.vbox_summary.pack_start(vbox_content, False, False, 6)
        self.vbox_summary.show_all()
        self.assistant.set_page_complete(self.vbox_summary, True)

Assistant()
Gtk.main()
