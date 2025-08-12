#!/usr/bin/python3
# -*- coding: utf-8 -*-

import gi
gi.require_version('Gtk', '3.0')
try:
    gi.require_version('Fwupd', '2.0')
except ValueError:
    pass

from gi.repository import Gtk, Gio, GLib
from gi.repository import Gdk
import subprocess
import shutil
import gettext
import traceback

_ = gettext.gettext

try:
    from gi.repository import Fwupd
except Exception:  # fwupd GIR might not be available
    Fwupd = None


def _log(msg):
    print(f"[Firmware] {msg}")


class FirmwareWindow:
    def __init__(self):
        _log("init window: start")
        self.settings = Gio.Settings(schema_id="com.linuxmint.updates")
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintupdate")
        self.builder.add_from_file("/usr/share/linuxmint/mintupdate/firmware.ui")
        _log("ui loaded from firmware.ui")
        for widget in self.builder.get_objects():
            if issubclass(type(widget), Gtk.Buildable):
                name = "ui_%s" % Gtk.Buildable.get_name(widget)
                if "__" not in name:
                    setattr(self, name, widget)

        self.ui_window.set_title(self.ui_window.get_title() + " (dev)")
        try:
            # Center the window on screen
            self.ui_window.set_position(Gtk.WindowPosition.CENTER)
        except Exception:
            pass
        self.ui_window.connect("destroy", self.destroy_window)
        # key shortcuts
        try:
            self.ui_window.connect("key-press-event", self.on_key_press_event)
        except Exception:
            pass
        self.ui_spinner.start()
        self.ui_stack.set_visible_child_name("refresh_page")

        # list widgets
        self.ui_listbox_devices.connect("row-activated", self.on_device_selected)
        # select first device by default when devices are loaded

        # data
        self.client = None
        self.devices = []
        self.current_device = None
        # new labels
        self.ui_label_device_vendor_ids = self.builder.get_object("label_device_vendor_ids")
        self.ui_label_device_plugin = self.builder.get_object("label_device_plugin")
        self.ui_label_device_serial = self.builder.get_object("label_device_serial")
        self.ui_label_device_flags = self.builder.get_object("label_device_flags")
        self.ui_label_device_problems = self.builder.get_object("label_device_problems")
        self.ui_label_device_update_error = self.builder.get_object("label_device_update_error")
        # additional labels
        self.ui_label_device_version_lowest = self.builder.get_object("label_device_version_lowest")
        self.ui_label_device_version_bootloader = self.builder.get_object("label_device_version_bootloader")
        self.ui_label_device_branch = self.builder.get_object("label_device_branch")
        self.ui_label_device_install_duration = self.builder.get_object("label_device_install_duration")
        self.ui_label_device_flashes_left = self.builder.get_object("label_device_flashes_left")
        self.ui_label_device_lock_status = self.builder.get_object("label_device_lock_status")

        if Fwupd is None:
            # fwupd missing, offer to install (Ubuntu/Debian)
            _log("Fwupd GIR not available")
            self.offer_install_fwupd()
            return

        self.client = Fwupd.Client()
        _log("Fwupd.Client created")
        self.client.connect("notify::status", self.on_status_changed)
        self.client.connect("notify::percentage", self.on_status_changed)
        self.client.connect("device-added", self.on_device_signal)
        self.client.connect("device-removed", self.on_device_signal)
        self.client.connect("device-changed", self.on_device_signal)
        # Show user requests (manual actions) similar to gnome-firmware
        try:
            self.client.connect("request", self.on_client_request)
        except Exception:
            pass
        # buttons in banners
        try:
            self.builder.get_object('button_enable_lvfs').connect('clicked', self.on_enable_lvfs_clicked)
            self.builder.get_object('button_refresh_lvfs').connect('clicked', self.on_refresh_lvfs_clicked)
            self.builder.get_object('button_device_list').connect('clicked', self.on_device_list_clicked)
            self.builder.get_object('button_install_file').connect('clicked', self.on_install_file_clicked)
            self.builder.get_object('button_verify').connect('clicked', self.on_verify_clicked)
            self.builder.get_object('button_verify_update').connect('clicked', self.on_verify_update_clicked)
            # menu items (for accelerators visibility)
            mi_refresh = self.builder.get_object('menu_item_refresh')
            if mi_refresh:
                mi_refresh.connect('activate', lambda *_: self.on_refresh_lvfs_clicked(None))
                try:
                    mi_refresh.add_accelerator('activate', self.ui_window.get_accel_group(), *Gtk.accelerator_parse('<Control>R'))
                except Exception:
                    pass
            mi_install = self.builder.get_object('menu_item_install')
            if mi_install:
                mi_install.connect('activate', lambda *_: self.on_install_file_clicked(None))
                try:
                    mi_install.add_accelerator('activate', self.ui_window.get_accel_group(), *Gtk.accelerator_parse('<Control>I'))
                except Exception:
                    pass
        except Exception:
            pass

        # keyboard shortcuts
        try:
            accel = Gtk.AccelGroup()
            self.ui_window.add_accel_group(accel)
            key, mod = Gtk.accelerator_parse("<Control>R")
            self.ui_window.add_accelerator("activate", accel, key, mod, Gtk.AccelFlags.VISIBLE)
            self.ui_window.connect("activate", lambda *args: self.on_refresh_lvfs_clicked(None))
            key2, mod2 = Gtk.accelerator_parse("<Control>I")
            self.ui_window.add_accelerator("activate", accel, key2, mod2, Gtk.AccelFlags.VISIBLE)
        except Exception:
            pass

        _log("connecting to fwupd (async)")
        self.client.connect_async(None, self.on_client_connected, None)
        # progress widgets
        self.ui_progress_revealer = self.builder.get_object("revealer_progress")
        self.ui_progress_bar = self.builder.get_object("progress_bar")
        # device list dialog
        try:
            self.ui_device_list_dialog = self.builder.get_object("device_list_dialog")
            self.ui_device_list_textview = self.builder.get_object("device_list_textview")
            self.builder.get_object('button_device_list_close').connect('clicked', lambda *_: self.ui_device_list_dialog.response(Gtk.ResponseType.CLOSE))
            self.builder.get_object('button_device_list_copy').connect('clicked', self.on_device_list_copy_clicked)
            self.builder.get_object('button_device_list_upload').connect('clicked', self.on_device_list_upload_clicked)
        except Exception:
            self.ui_device_list_dialog = None
            self.ui_device_list_textview = None

        self.ui_window.show_all()
        try:
            self.ui_window.present()
        except Exception:
            pass
        _log("window shown")
        # state for install flow
        self._pending_release = None
        self._install_file_path = None

    def show_error_label(self, text):
        self.ui_spinner.stop()
        self.ui_stack.set_visible_child_name("firmware_page")
        self.ui_label_device_name.set_text(text)

    def offer_install_fwupd(self):
        dialog = Gtk.MessageDialog(transient_for=self.ui_window,
                                   flags=0,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE,
                                   text=_("Firmware support is not available."))
        dialog.format_secondary_text(_("Do you want to install required packages (fwupd, GIR)?"))
        dialog.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL,
                           _("Install"), Gtk.ResponseType.OK)
        resp = dialog.run()
        dialog.destroy()
        if resp != Gtk.ResponseType.OK:
            self.show_error_label("fwupd not available")
            return
        self.install_fwupd_packages()

    def _run_pkexec(self, argv):
        cmd = ["pkexec"] + argv
        _log(f"run: {' '.join(cmd)}")
        try:
            out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            _log(out.stdout)
            return out.returncode == 0
        except Exception as e:
            _log(f"pkexec failed: {e}")
            return False

    def _run_pkexec_shell(self, command_str):
        cmd = ["pkexec", "bash", "-lc", command_str]
        _log(f"run: {' '.join(cmd)}")
        try:
            out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            _log(out.stdout)
            return out.returncode == 0
        except Exception as e:
            _log(f"pkexec shell failed: {e}")
            return False

    def install_fwupd_packages(self):
        apt = shutil.which("apt-get") or "apt-get"
        # try 2.0 first, then 1.0
        install_cmd1 = [apt, "-y", "-o", "Dpkg::Use-Pty=0",
                        "install", "fwupd", "gir1.2-fwupd-2.0"]
        install_cmd2 = [apt, "-y", "-o", "Dpkg::Use-Pty=0",
                        "install", "fwupd", "gir1.2-fwupd-1.0"]
        # Run everything in one pkexec to avoid asking for password twice
        shell_cmd = (
            f"{apt} update || true; "
            f"{apt} -y -o Dpkg::Use-Pty=0 install fwupd gir1.2-fwupd-2.0 || "
            f"{apt} -y -o Dpkg::Use-Pty=0 install fwupd gir1.2-fwupd-1.0"
        )
        ok = self._run_pkexec_shell(shell_cmd)
        if not ok:
            self.show_error_label("Failed to install fwupd/GIR")
            return
        # Try to reload Fwupd and start the client
        try:
            gi.require_version('Fwupd', '2.0')
        except ValueError:
            try:
                gi.require_version('Fwupd', '1.0')
            except ValueError:
                pass
        global Fwupd
        try:
            from gi.repository import Fwupd as _Fw
            Fwupd = _Fw
        except Exception as e:
            _log(f"import after install failed: {e}")
            self.show_error_label("fwupd not available")
            return
        _log("Fwupd GIR installed, creating client")
        self.client = Fwupd.Client()
        self.client.connect("notify::status", self.on_status_changed)
        self.client.connect("notify::percentage", self.on_status_changed)
        self.client.connect("device-added", self.on_device_signal)
        self.client.connect("device-removed", self.on_device_signal)
        self.client.connect("device-changed", self.on_device_signal)
        self.client.connect_async(None, self.on_client_connected, None)

    def on_device_signal(self, *args):
        # Simply refresh device list on any signal
        try:
            _log("device signal received, refreshing devices")
        except Exception:
            pass
        try:
            self.client.get_devices_async(None, self.on_devices_ready, None)
        except Exception as e:
            _log(f"refresh after device signal failed: {e}")

    def destroy_window(self, *args):
        try:
            self.ui_window.hide()
            self.ui_window.destroy()
        except Exception:
            pass

    # fwupd wiring
    def on_client_connected(self, source, result, user_data):
        try:
            if not self.client.connect_finish(result):
                _log("connect_finish returned False")
                self.show_error_label("fwupd connect failed")
                return
        except GLib.Error as e:
            _log(f"connect_finish error: {e}")
            self.show_error_label(str(e))
            return

        # feature flags (best-effort)
        feature_flags_obj = None
        if hasattr(Fwupd, "FeatureFlags"):
            try:
                flags_value = 0
                try:
                    flags_value |= int(getattr(Fwupd.FeatureFlags, "SHOW_PROBLEMS", 0))
                except Exception:
                    pass
                try:
                    flags_value |= int(getattr(Fwupd.FeatureFlags, "UPDATE_ACTION", 0))
                except Exception:
                    pass
                feature_flags_obj = Fwupd.FeatureFlags(flags_value)
            except Exception:
                # fallback to NONE if it exists
                try:
                    feature_flags_obj = getattr(Fwupd.FeatureFlags, "NONE")
                except Exception:
                    feature_flags_obj = None
        if hasattr(self.client, "set_feature_flags_async") and feature_flags_obj is not None:
            _log(f"setting feature flags: {int(feature_flags_obj)}")
            self.client.set_feature_flags_async(feature_flags_obj, None, self.on_flags_set, None)
        else:
            # fetch devices immediately
            _log("feature flags API not available, fetching devices")
            self.client.get_devices_async(None, self.on_devices_ready, None)

    def on_flags_set(self, source, result, user_data):
        try:
            self.client.set_feature_flags_finish(result)
            _log("feature flags set")
        except GLib.Error as e:
            _log(f"set_feature_flags_finish error: {e}")
        self.client.get_devices_async(None, self.on_devices_ready, None)
        # also fetch remotes to update banners
        try:
            if hasattr(self.client, 'get_remotes_async'):
                self.client.get_remotes_async(None, self.on_remotes_ready, None)
        except Exception as e:
            _log(f"get_remotes_async failed: {e}")

    def on_devices_ready(self, source, result, user_data):
        try:
            self.devices = self.client.get_devices_finish(result)
            _log(f"devices fetched: {len(self.devices)}")
        except GLib.Error as e:
            _log(f"get_devices_finish error: {e}")
            self.show_error_label(str(e))
            return

        # fill the device list
        self.ui_listbox_devices.foreach(lambda w: self.ui_listbox_devices.remove(w))
        for dev in self.devices:
            name = dev.get_name() or dev.get_id()
            _log(f"device: {name} vendor={dev.get_vendor()} version={dev.get_version()}")
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            label = Gtk.Label(label=name, xalign=0)
            subtitle = Gtk.Label(label=(dev.get_vendor() or ""), xalign=0)
            subtitle.get_style_context().add_class("dim-label")
            box.pack_start(label, False, False, 0)
            box.pack_start(subtitle, False, False, 0)
            row.add(box)
            row.device = dev
            self.ui_listbox_devices.add(row)
        self.ui_listbox_devices.show_all()
        # auto-select first row if available
        try:
            first = self.ui_listbox_devices.get_row_at_index(0)
            if first is not None:
                self.ui_listbox_devices.select_row(first)
                self.on_device_selected(self.ui_listbox_devices, first)
        except Exception:
            pass

        # show UI
        self.ui_spinner.stop()
        self.ui_stack.set_visible_child_name("firmware_page")

    def on_device_selected(self, listbox, row):
        if row is None:
            return
        self.current_device = row.device
        try:
            _log(f"device selected: id={self.current_device.get_id()} name={self.current_device.get_name()}")
        except Exception:
            pass
        self.update_device_summary(self.current_device)
        self.load_releases(self.current_device)

    def update_device_summary(self, dev):
        self._set_label_pair("label_device_name_title", self.ui_label_device_name, dev.get_name() or dev.get_id())
        self._set_label_pair("label_device_vendor_title", self.ui_label_device_vendor, dev.get_vendor() or "")
        self._set_label_pair("label_device_version_title", self.ui_label_device_version, dev.get_version() or "")
        # Vendor IDs / GUIDs
        try:
            guids = []
            if hasattr(dev, 'get_guids'):
                guids = list(dev.get_guids()) or []
            guids_text = ", ".join(guids) if guids else ""
            self._set_label_pair("label_device_vendor_ids_title", self.ui_label_device_vendor_ids, guids_text)
            try:
                # also fill expander in monospace, multi-line
                expander_guids = self.builder.get_object('label_guids_expander')
                if expander_guids is not None:
                    expander_guids.set_text("\n".join(guids) if guids else "")
            except Exception:
                pass
        except Exception:
            self._set_label_pair("label_device_vendor_ids_title", self.ui_label_device_vendor_ids, "")
        # Plugin/Backend
        try:
            plugin = dev.get_plugin() if hasattr(dev, 'get_plugin') else None
            self._set_label_pair("label_device_plugin_title", self.ui_label_device_plugin, plugin or "")
        except Exception:
            self._set_label_pair("label_device_plugin_title", self.ui_label_device_plugin, "")
        # Serial
        try:
            serial = dev.get_serial() if hasattr(dev, 'get_serial') else None
            self._set_label_pair("label_device_serial_title", self.ui_label_device_serial, serial or "")
        except Exception:
            self._set_label_pair("label_device_serial_title", self.ui_label_device_serial, "")
        # Flags (friendly mapping)
        try:
            flags_text = self._format_device_flags(dev)
            self._set_label_pair("label_device_flags_title", self.ui_label_device_flags, flags_text if flags_text != "-" else "")
            try:
                expander_flags = self.builder.get_object('label_flags_expander')
                if expander_flags is not None:
                    expander_flags.set_text(flags_text if flags_text != "-" else "")
            except Exception:
                pass
        except Exception:
            self._set_label_pair("label_device_flags_title", self.ui_label_device_flags, "")
        # Problems / Update error
        try:
            problems_text = self._format_device_problems(dev)
            if problems_text:
                self._set_label_pair("label_device_problems_title", self.ui_label_device_problems, problems_text)
                self._set_label_pair("label_device_update_error_title", self.ui_label_device_update_error, "")
            else:
                self._set_label_pair("label_device_problems_title", self.ui_label_device_problems, "")
                err = getattr(dev, 'get_update_error', lambda: None)()
                self._set_label_pair("label_device_update_error_title", self.ui_label_device_update_error, err or "")
        except Exception:
            self._set_label_pair("label_device_problems_title", self.ui_label_device_problems, "")
            try:
                err = getattr(dev, 'get_update_error', lambda: None)()
                self._set_label_pair("label_device_update_error_title", self.ui_label_device_update_error, err or "")
            except Exception:
                self._set_label_pair("label_device_update_error_title", self.ui_label_device_update_error, "")
        # Additional fields (best-effort, guarded with hasattr)
        try:
            lowest = getattr(dev, 'get_version_lowest', lambda: None)()
            self._set_label_pair("label_device_version_lowest_title", self.ui_label_device_version_lowest, lowest or "")
        except Exception:
            self._set_label_pair("label_device_version_lowest_title", self.ui_label_device_version_lowest, "")
        try:
            bl = getattr(dev, 'get_version_bootloader', lambda: None)()
            self._set_label_pair("label_device_version_bootloader_title", self.ui_label_device_version_bootloader, bl or "")
        except Exception:
            self._set_label_pair("label_device_version_bootloader_title", self.ui_label_device_version_bootloader, "")
        try:
            branch = getattr(dev, 'get_branch', lambda: None)()
            self._set_label_pair("label_device_branch_title", self.ui_label_device_branch, branch or "")
        except Exception:
            self._set_label_pair("label_device_branch_title", self.ui_label_device_branch, "")
        try:
            dur = getattr(dev, 'get_install_duration', lambda: 0)()
            self._set_label_pair("label_device_install_duration_title", self.ui_label_device_install_duration, str(dur) if dur else "")
        except Exception:
            self._set_label_pair("label_device_install_duration_title", self.ui_label_device_install_duration, "")
        try:
            left = getattr(dev, 'get_flashes_left', lambda: 0)()
            self._set_label_pair("label_device_flashes_left_title", self.ui_label_device_flashes_left, str(left) if left else "")
        except Exception:
            self._set_label_pair("label_device_flashes_left_title", self.ui_label_device_flashes_left, "")
        try:
            locked = False
            if hasattr(dev, 'has_flag') and hasattr(Fwupd, 'DeviceFlag'):
                flag_locked = getattr(Fwupd.DeviceFlag, 'LOCKED', None)
                if flag_locked is not None:
                    locked = dev.has_flag(flag_locked)
            self._set_label_pair("label_device_lock_status_title", self.ui_label_device_lock_status, _("Locked") if locked else _("Unlocked"))
        except Exception:
            self._set_label_pair("label_device_lock_status_title", self.ui_label_device_lock_status, "")

        # Attestation buttons sensitivity
        try:
            btn_verify = self.builder.get_object('button_verify')
            btn_store = self.builder.get_object('button_verify_update')
            can_verify = self._device_has_flag(dev, 'CAN_VERIFY') or self._device_has_flag(dev, 'CAN_VERIFY_IMAGE')
            if btn_verify:
                btn_verify.set_sensitive(bool(can_verify))
            if btn_store:
                btn_store.set_sensitive(bool(can_verify))
        except Exception:
            pass

    # helpers
    def _format_device_flags(self, dev):
        flags_val = getattr(dev, 'get_flags', lambda: 0)() or 0
        # try has_flag
        def has_flag(flag_obj, bit_val=None):
            try:
                if hasattr(dev, 'has_flag') and flag_obj is not None:
                    return dev.has_flag(flag_obj)
            except Exception:
                pass
            if bit_val is not None:
                try:
                    return (int(flags_val) & int(bit_val)) != 0
                except Exception:
                    return False
            return False
        friendly = []
        DF = getattr(Fwupd, 'DeviceFlag', None)
        mapping = [
            ('UPDATABLE', 'Updatable'),
            ('INTERNAL', 'Internal'),
            ('NEEDS_REBOOT', 'Needs reboot'),
            ('NEEDS_SHUTDOWN', 'Needs shutdown'),
            ('REQUIRES_AC', 'Requires AC power'),
            ('IS_BOOTLOADER', 'Bootloader'),
            ('CAN_VERIFY', 'Can verify'),
            ('CAN_VERIFY_IMAGE', 'Can verify image'),
            ('LOCKED', 'Locked'),
            ('REMOVABLE', 'Removable'),
            ('USABLE_DURING_UPDATE', 'Usable during update'),
            ('AFFECTS_FDE', 'Affects full-disk encryption'),
        ]
        for name, label in mapping:
            obj = getattr(DF, name, None) if DF else None
            bit_val = int(obj) if obj is not None else None
            if has_flag(obj, bit_val):
                friendly.append(label)
        # include unknown bits for debugging
        if not friendly and flags_val:
            friendly.append(str(flags_val))
        return ", ".join(friendly) if friendly else "-"

    def _format_device_problems(self, dev):
        # Best-effort using DeviceProblem bitmask if available
        DP = getattr(Fwupd, 'DeviceProblem', None)
        problems_val = getattr(dev, 'get_problems', lambda: 0)()
        texts = []
        def has_problem(bit):
            try:
                if hasattr(dev, 'has_problem') and bit is not None:
                    return dev.has_problem(bit)
            except Exception:
                pass
            try:
                return (int(problems_val) & int(bit)) != 0
            except Exception:
                return False
        problem_map = [
            ('SYSTEM_POWER_TOO_LOW', 'System power is too low'),
            ('UNREACHABLE', 'Device is unreachable'),
            ('POWER_TOO_LOW', 'Device battery power is too low'),
            ('UPDATE_PENDING', 'Device is waiting for the update to be applied'),
            ('REQUIRE_AC_POWER', 'Device requires AC power'),
            ('LID_IS_CLOSED', 'Lid is closed'),
            ('IN_USE', 'Device is in use'),
            ('DISPLAY_REQUIRED', 'No displays connected'),
        ]
        for name, label in problem_map:
            bit = getattr(DP, name, None) if DP else None
            if bit is not None and has_problem(bit):
                texts.append(label)
        return "\n".join(texts) if texts else None

    def _set_label_pair(self, title_id, value_widget, text):
        """Show/hide a label row based on value and set the text."""
        try:
            title_widget = self.builder.get_object(title_id)
            visible = bool(text)
            if value_widget:
                if text:
                    value_widget.set_text(text)
                value_widget.set_visible(visible)
            if title_widget:
                title_widget.set_visible(visible)
        except Exception:
            pass

    def load_releases(self, dev):
        self.clear_listbox(self.ui_listbox_releases)
        _log(f"fetching releases for {dev.get_id()}")
        self.client.get_releases_async(dev.get_id(), None, self.on_releases_ready, None)
        # default: show list view until result arrives
        try:
            self.builder.get_object("releases_stack").set_visible_child_name("list")
        except Exception:
            pass

    def on_releases_ready(self, source, result, user_data):
        try:
            releases = self.client.get_releases_finish(result)
            _log(f"releases fetched: {len(releases)}")
        except GLib.Error as e:
            _log(f"get_releases_finish error: {e}")
            # Friendly handling of common error: "no version set (10)"
            msg = str(e)
            if "no version set" in msg.lower():
                self.add_info_row(self.ui_listbox_releases, _("No releases available for this device"))
            else:
                self.add_info_row(self.ui_listbox_releases, msg)
            return
        if not releases:
            # show empty-state centered message
            try:
                self.builder.get_object("releases_stack").set_visible_child_name("empty")
            except Exception:
                self.add_info_row(self.ui_listbox_releases, _("No releases available"))
            return
        for rel in releases:
            raw_version = rel.get_version() or ""
            unknown_version = (raw_version.strip() == "")
            version = raw_version if not unknown_version else _("Unknown")
            summary = rel.get_summary() or ""
            row = Gtk.ListBoxRow()
            h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            v.pack_start(Gtk.Label(label=version, xalign=0), False, False, 0)
            v.pack_start(Gtk.Label(label=summary, xalign=0), False, False, 0)
            try:
                btn = Gtk.Button(label=_("Install"))
            except Exception:
                btn = Gtk.Button(label="Install")
            btn.connect("clicked", self.on_install_clicked, rel)
            # Details button
            try:
                btn_details = Gtk.Button(label=_("Details"))
            except Exception:
                btn_details = Gtk.Button(label="Details")
            btn_details.connect("clicked", self.on_release_details_clicked, rel)
            if unknown_version:
                btn.set_sensitive(False)
                try:
                    btn.set_tooltip_text(_("Cannot install: release version is not available"))
                except Exception:
                    pass
            h.pack_start(v, True, True, 0)
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            btn_box.pack_end(btn, False, False, 0)
            btn_box.pack_end(btn_details, False, False, 0)
            h.pack_end(btn_box, False, False, 0)
            row.add(h)
            self.ui_listbox_releases.add(row)
        self.ui_listbox_releases.show_all()
        try:
            self.builder.get_object("releases_stack").set_visible_child_name("list")
        except Exception:
            pass

    def on_install_clicked(self, button, release):
        if self.current_device is None:
            return
        try:
            _log(f"install clicked: device={self.current_device.get_id()} release={release.get_version()}")
        except Exception:
            pass
        self._pending_release = release
        self._show_install_confirmation()

    def _get_release_flags_value(self, release):
        try:
            return int(getattr(release, 'get_flags', lambda: 0)() or 0)
        except Exception:
            return 0

    def _release_has_flag(self, release, flag_name):
        RF = getattr(Fwupd, 'ReleaseFlags', None)
        try:
            flag_obj = getattr(RF, flag_name, None) if RF else None
            if flag_obj is None:
                return False
            flags_val = self._get_release_flags_value(release)
            return (flags_val & int(flag_obj)) != 0
        except Exception:
            return False

    def _device_has_flag(self, device, flag_name):
        DF = getattr(Fwupd, 'DeviceFlag', None)
        try:
            flag_obj = getattr(DF, flag_name, None) if DF else None
            if flag_obj is None:
                return False
            if hasattr(device, 'has_flag'):
                return device.has_flag(flag_obj)
            flags_val = getattr(device, 'get_flags', lambda: 0)() or 0
            return (int(flags_val) & int(flag_obj)) != 0
        except Exception:
            return False

    def _show_install_confirmation(self):
        rel = self._pending_release
        if rel is None:
            return
        # Title by intent
        title = None
        if self._release_has_flag(rel, 'IS_UPGRADE'):
            title = _(f"Upgrade To {rel.get_version()}?")
        elif self._release_has_flag(rel, 'IS_DOWNGRADE'):
            title = _(f"Downgrade To {rel.get_version()}?")
        else:
            title = _(f"Reinstall {rel.get_version()}?")
        # Body: usability warning
        body = _("The device may be unusable while the update is installing.")
        if self._device_has_flag(self.current_device, 'USABLE_DURING_UPDATE'):
            body = _("The device will remain usable for the duration of the update.")
        dlg = Gtk.MessageDialog(transient_for=self.ui_window,
                                 flags=0,
                                 message_type=Gtk.MessageType.QUESTION,
                                 buttons=Gtk.ButtonsType.NONE,
                                 text=title)
        dlg.format_secondary_text(body)
        dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL,
                        _("Continue"), Gtk.ResponseType.OK)
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            self._pending_release = None
            return
        # Additional warnings
        self._maybe_show_branch_fde_warnings()

    def _maybe_show_branch_fde_warnings(self):
        rel = self._pending_release
        if rel is None:
            return
        warnings = []
        try:
            dev_vendor = self.current_device.get_vendor() if hasattr(self.current_device, 'get_vendor') else None
            rel_vendor = getattr(rel, 'get_vendor', lambda: None)()
            if rel_vendor and dev_vendor and rel_vendor != dev_vendor:
                warnings.append(_("Firmware not supplied by the device vendor. Proceed with caution."))
        except Exception:
            pass
        try:
            dev_branch = getattr(self.current_device, 'get_branch', lambda: None)()
            rel_branch = getattr(rel, 'get_branch', lambda: None)()
            if rel_branch and dev_branch and rel_branch != dev_branch:
                warnings.append(_("This release is from an alternate branch."))
        except Exception:
            pass
        if self._device_has_flag(self.current_device, 'AFFECTS_FDE'):
            warnings.append(_("Full Disk Encryption: some platform secrets may be invalidated. Ensure you have recovery keys."))
        if warnings:
            dlg = Gtk.MessageDialog(transient_for=self.ui_window,
                                    flags=0,
                                    message_type=Gtk.MessageType.WARNING,
                                    buttons=Gtk.ButtonsType.NONE,
                                    text=_("Additional Warnings"))
            dlg.format_secondary_text("\n\n".join(warnings))
            dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL,
                            _("Continue"), Gtk.ResponseType.OK)
            resp = dlg.run()
            dlg.destroy()
            if resp != Gtk.ResponseType.OK:
                self._pending_release = None
                return
        # Proceed to install
        self._perform_install()

    def _perform_install(self):
        rel = self._pending_release
        if rel is None:
            return
        flags = getattr(Fwupd.InstallFlags, "NONE", 0)
        try:
            if self._release_has_flag(rel, 'IS_DOWNGRADE'):
                flags |= getattr(Fwupd.InstallFlags, 'ALLOW_OLDER', 0)
            elif not self._release_has_flag(rel, 'IS_UPGRADE'):
                # reinstall
                flags |= getattr(Fwupd.InstallFlags, 'ALLOW_REINSTALL', 0)
        except Exception:
            pass
        if hasattr(self.client, "install_release_async"):
            _log("using install_release_async")
            self.client.install_release_async(
                self.current_device,
                rel,
                flags,
                getattr(Fwupd.ClientDownloadFlags, "NONE", 0),
                None,
                self.on_install_done,
                None,
            )
        else:
            _log("using install_release2_async")
            self.client.install_release2_async(
                self.current_device,
                rel,
                flags,
                getattr(Fwupd.ClientDownloadFlags, "NONE", 0),
                None,
                self.on_install_done,
                None,
            )

    def on_release_details_clicked(self, button, release):
        try:
            dlg = self.builder.get_object("release_dialog")
            if dlg is None:
                return
            # fill fields
            def set_label(name, text):
                w = self.builder.get_object(name)
                if w:
                    w.set_text(text or "-")
            set_label("label_release_summary", release.get_summary() or "-")
            # format description (strip simple XML/markup)
            desc_raw = getattr(release, 'get_description', lambda: None)() or ""
            set_label("label_release_description", self._strip_markup(desc_raw) or "-")
            set_label("label_release_vendor", release.get_vendor() or "-")
            set_label("label_release_filename", release.get_filename() or "-")
            try:
                size_val = getattr(release, 'get_size', None)
                size_num = size_val() if callable(size_val) else None
                set_label("label_release_size", self._format_size(size_num) if size_num else "-")
            except Exception:
                set_label("label_release_size", "-")
            set_label("label_release_protocol", getattr(release, 'get_protocol', lambda: None)() or "-")
            set_label("label_release_remote", getattr(release, 'get_remote', lambda: None)() or "-")
            set_label("label_release_appstream_id", getattr(release, 'get_appstream_id', lambda: None)() or "-")
            set_label("label_release_license", getattr(release, 'get_license', lambda: None)() or "-")
            # flags
            try:
                flags_val = getattr(release, 'get_flags', lambda: 0)()
                set_label("label_release_flags", self._format_release_flags(flags_val))
            except Exception:
                set_label("label_release_flags", "-")
            # install duration
            try:
                dur = getattr(release, 'get_install_duration', lambda: 0)()
                set_label("label_release_install_duration", str(dur) if dur else "-")
            except Exception:
                set_label("label_release_install_duration", "-")
            set_label("label_release_update_message", getattr(release, 'get_update_message', lambda: None)() or "-")
            # categories
            try:
                cats = getattr(release, 'get_categories', lambda: [])()
                set_label("label_release_categories", "\n".join(cats) if cats else "-")
            except Exception:
                set_label("label_release_categories", "-")
            # issues
            try:
                issues = getattr(release, 'get_issues', lambda: [])()
                set_label("label_release_issues", "\n".join(issues) if issues else "-")
            except Exception:
                set_label("label_release_issues", "-")
            # checksum(s)
            try:
                chks = getattr(release, 'get_checksums', lambda: [])()
                set_label("label_release_checksum", "\n".join(chks) if chks else "-")
            except Exception:
                set_label("label_release_checksum", "-")
            # wire close
            close_btn = self.builder.get_object("button_release_close")
            if close_btn:
                close_btn.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.CLOSE))
            dlg.set_transient_for(self.ui_window)
            dlg.show_all()
            dlg.run()
            dlg.hide()
        except Exception as e:
            _log(f"release details dialog failed: {e}")

    def on_install_done(self, source, result, user_data):
        try:
            if hasattr(self.client, "install_bytes_finish"):
                self.client.install_bytes_finish(result)
            _log("install finished successfully")
        except GLib.Error as e:
            _log(f"install error: {e}")
            self.add_info_row(self.ui_listbox_releases, str(e))
            return
        self.load_releases(self.current_device)
        # post actions: show any pending user requests (manual actions)
        try:
            reqs = getattr(self.client, 'get_requests', lambda: None)()
            if reqs:
                for req in reqs:
                    try:
                        self._show_request(req)
                    except Exception:
                        pass
        except Exception:
            pass
        # then reboot/shutdown prompts
        try:
            if self._device_has_flag(self.current_device, 'NEEDS_SHUTDOWN'):
                dlg = Gtk.MessageDialog(transient_for=self.ui_window,
                                        flags=0,
                                        message_type=Gtk.MessageType.INFO,
                                        buttons=Gtk.ButtonsType.NONE,
                                        text=_("Shutdown Required"))
                dlg.format_secondary_text(_("The update requires the system to shutdown to complete."))
                dlg.add_buttons(_("Later"), Gtk.ResponseType.CANCEL,
                                _("Shutdown"), Gtk.ResponseType.OK)
                resp = dlg.run()
                dlg.destroy()
                if resp == Gtk.ResponseType.OK:
                    subprocess.Popen(["pkexec", "systemctl", "poweroff"])  # best-effort
                return
            if self._device_has_flag(self.current_device, 'NEEDS_REBOOT'):
                dlg = Gtk.MessageDialog(transient_for=self.ui_window,
                                        flags=0,
                                        message_type=Gtk.MessageType.INFO,
                                        buttons=Gtk.ButtonsType.NONE,
                                        text=_("Reboot Required"))
                dlg.format_secondary_text(_("The update requires a reboot to complete."))
                dlg.add_buttons(_("Later"), Gtk.ResponseType.CANCEL,
                                _("Reboot"), Gtk.ResponseType.OK)
                resp = dlg.run()
                dlg.destroy()
                if resp == Gtk.ResponseType.OK:
                    subprocess.Popen(["pkexec", "systemctl", "reboot"])  # best-effort
        except Exception:
            pass

    # Fwupd request handling
    def on_client_request(self, client, request):
        try:
            self._show_request(request)
        except Exception as e:
            _log(f"request show failed: {e}")

    def _show_request(self, request):
        try:
            # Title by kind
            kind = getattr(request, 'get_kind', lambda: None)()
            FS = getattr(Fwupd, 'RequestKind', None)
            if FS and kind == getattr(FS, 'POST', None):
                title = _("Further Action Required")
            else:
                title = _("Action Required")
            message = getattr(request, 'get_message', lambda: None)() or ""
            dlg = Gtk.MessageDialog(transient_for=self.ui_window,
                                    flags=0,
                                    message_type=Gtk.MessageType.INFO,
                                    buttons=Gtk.ButtonsType.NONE,
                                    text=title)
            dlg.format_secondary_text(message)
            dlg.add_buttons(_("Continue"), Gtk.ResponseType.OK)
            # optional image download
            try:
                image_uri = getattr(request, 'get_image', lambda: None)()
                if image_uri:
                    # best-effort: show a stock image hint instead of download pipeline (GTK3)
                    img = Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.DIALOG)
                    dlg.get_message_area().pack_start(img, False, False, 6)
                    img.show()
            except Exception:
                pass
            dlg.run()
            dlg.destroy()
        except Exception as e:
            _log(f"_show_request failed: {e}")

    # utils
    def clear_listbox(self, listbox):
        children = []
        listbox.foreach(lambda w: children.append(w))
        for w in children:
            listbox.remove(w)

    def add_info_row(self, listbox, text):
        row = Gtk.ListBoxRow()
        row.add(Gtk.Label(label=text, xalign=0))
        listbox.add(row)
        listbox.show_all()

    def on_status_changed(self, *args):
        try:
            status = self.client.get_status()
            pct = self.client.get_percentage()
            _log(f"status={status} pct={pct}")
            # update progress UI
            # show revealer unless idle/unknown
            show = True
            try:
                if status in (getattr(Fwupd.Status, 'IDLE', -1), getattr(Fwupd.Status, 'UNKNOWN', -2)):
                    show = False
            except Exception:
                pass
            if self.ui_progress_revealer:
                self.ui_progress_revealer.set_reveal_child(show)
            if self.ui_progress_bar:
                if pct and pct > 0:
                    self.ui_progress_bar.set_fraction(min(1.0, float(pct)/100.0))
                    try:
                        self.ui_progress_bar.set_text(self._status_to_text(status, pct))
                    except Exception:
                        pass
                else:
                    self.ui_progress_bar.pulse()
                    try:
                        self.ui_progress_bar.set_text("")
                    except Exception:
                        pass
        except Exception:
            pass

    def _status_to_text(self, status, pct):
        try:
            S = getattr(Fwupd, 'Status', None)
            mapping = {
                getattr(S, 'IDLE', -1): _('Idle'),
                getattr(S, 'UNKNOWN', -2): _('Unknown'),
                getattr(S, 'DECOMPRESSING', -2): _('Decompressing'),
                getattr(S, 'LOADING', -3): _('Loading'),
                getattr(S, 'DOWNLOADING', -3): _('Downloading'),
                getattr(S, 'SCHEDULING', -4): _('Scheduling'),
                getattr(S, 'INSTALLING', -5): _('Installing'),
                getattr(S, 'DEVICE_RESTART', -8): _('Restarting device'),
                getattr(S, 'DEVICE_WRITE', -9): _('Writing to device'),
                getattr(S, 'DEVICE_READ', -10): _('Reading from device'),
                getattr(S, 'DEVICE_ERASE', -11): _('Erasing device'),
                getattr(S, 'DEVICE_VERIFY', -12): _('Verifying device'),
                getattr(S, 'DEVICE_BUSY', -7): _('Device busy'),
                getattr(S, 'REPLUG', -9): _('Replug device'),
                getattr(S, 'REBOOTING', -6): _('Rebooting'),
                getattr(S, 'WAITING_FOR_AUTH', -13): _('Waiting for authentication'),
                getattr(S, 'WAITING_FOR_USER', -14): _('Waiting for user'),
            }
            label = mapping.get(status, _('Working'))
            if pct and pct > 0:
                return f"{label} ({pct}%)"
            return label
        except Exception:
            return f"{pct}%" if pct else ""

    def _strip_markup(self, text):
        try:
            import re
            return re.sub(r"<[^>]+>", "", text)
        except Exception:
            return text

    def _format_size(self, size_bytes):
        try:
            size = float(size_bytes)
            units = ['B', 'KB', 'MB', 'GB']
            idx = 0
            while size >= 1000 and idx < len(units)-1:
                size /= 1000.0
                idx += 1
            if idx == 0:
                return f"{int(size)} {units[idx]}"
            return f"{size:.1f} {units[idx]}"
        except Exception:
            return f"{size_bytes} B"

    def _format_release_flags(self, flags_val):
        try:
            RF = getattr(Fwupd, 'ReleaseFlags', None)
            items = []
            mapping = [
                ('TRUSTED_METADATA', _('Trusted metadata')),
                ('IS_UPGRADE', _('Upgrade')),
                ('IS_DOWNGRADE', _('Downgrade')),
                ('ALLOW_REINSTALL', _('Reinstall allowed')),
                ('ALLOW_OLDER', _('Older version allowed')),
                ('IS_ALTERNATE_BRANCH', _('Alternate branch')),
                ('BLOCKED_VERSION', _('Blocked version')),
                ('BLOCKED_APPROVAL', _('Not approved')),
            ]
            for name, label in mapping:
                bit = getattr(RF, name, None) if RF else None
                if bit is not None and (int(flags_val) & int(bit)) != 0:
                    items.append(label)
            return "\n".join(items) if items else "-"
        except Exception:
            return str(flags_val)

    # remotes/banners
    def on_remotes_ready(self, source, result, user_data):
        try:
            remotes = self.client.get_remotes_finish(result)
        except Exception as e:
            _log(f"get_remotes_finish error: {e}")
            return
        # determine LVFS state and needs refresh
        lvfs_enabled = False
        lvfs_needs_refresh = False
        enabled_any_download_remote = False
        try:
            for remote in remotes or []:
                # API shape differs by version; try multiple getters
                try:
                    rid = remote.get_id()
                except Exception:
                    rid = getattr(remote, 'id', None)
                try:
                    enabled = remote.get_enabled() if hasattr(remote, 'get_enabled') else False
                except Exception:
                    enabled = getattr(remote, 'enabled', False)
                try:
                    kind = remote.get_kind() if hasattr(remote, 'get_kind') else None
                except Exception:
                    kind = getattr(remote, 'kind', None)
                if str(rid) == 'lvfs':
                    lvfs_enabled = bool(enabled)
                    try:
                        lvfs_needs_refresh = bool(remote.needs_refresh()) if hasattr(remote, 'needs_refresh') else False
                    except Exception:
                        lvfs_needs_refresh = False
                if enabled and (str(kind).lower() == 'download' or kind == 1):
                    enabled_any_download_remote = True
        except Exception as e:
            _log(f"remotes parse error: {e}")
        # update banners
        try:
            inf_enable = self.builder.get_object('infobar_enable_lvfs')
            inf_refresh = self.builder.get_object('infobar_refresh_lvfs')
            if not lvfs_enabled and not enabled_any_download_remote:
                inf_enable.set_visible(True)
                inf_refresh.set_visible(False)
            elif lvfs_needs_refresh:
                inf_enable.set_visible(False)
                inf_refresh.set_visible(True)
            else:
                inf_enable.set_visible(False)
                inf_refresh.set_visible(False)
        except Exception:
            pass

    def on_refresh_lvfs_clicked(self, button):
        try:
            if hasattr(self.client, 'refresh_remote_async') and getattr(self, '_lvfs_remote', None) is not None:
                flags = getattr(Fwupd, 'ClientDownloadFlags', None)
                dl_flag = getattr(flags, 'NONE', 0) if flags else 0
                self.client.refresh_remote_async(self._lvfs_remote, dl_flag, None, self._on_refresh_remote_finished, None)
            else:
                subprocess.Popen(["pkexec", "fwupdmgr", "refresh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            subprocess.Popen(["pkexec", "fwupdmgr", "refresh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def on_enable_lvfs_clicked(self, button):
        try:
            if hasattr(self.client, 'modify_remote_async'):
                self.client.modify_remote_async('lvfs', 'Enabled', 'true', None, self._on_modify_remote_finished, None)
            else:
                subprocess.Popen(["pkexec", "fwupdmgr", "enable-remote", "lvfs"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _on_modify_remote_finished(self, source, result, user_data):
        try:
            self.client.modify_remote_finish(result)
        except Exception as e:
            _log(f"modify_remote_finish error: {e}")
            return
        try:
            if hasattr(self.client, 'get_remotes_async'):
                self.client.get_remotes_async(None, self.on_remotes_ready, None)
        except Exception:
            pass

    def _on_refresh_remote_finished(self, source, result, user_data):
        try:
            self.client.refresh_remote_finish(result)
        except Exception as e:
            _log(f"refresh_remote_finish error: {e}")
        try:
            if hasattr(self.client, 'get_remotes_async'):
                self.client.get_remotes_async(None, self.on_remotes_ready, None)
        except Exception:
            pass

    def on_device_list_clicked(self, button):
        # Show device list (JSON) and allow upload
        try:
            if (not hasattr(self, 'ui_device_list_dialog')) or (self.ui_device_list_dialog is None) or (self.ui_device_list_textview is None):
                # fallback: immediate upload
                self.on_device_list_upload_clicked(button)
                return
            output = subprocess.run(["fwupdmgr", "get-devices", "--json"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            buf = self.ui_device_list_textview.get_buffer()
            buf.set_text(output.stdout or "{}")
            self.ui_device_list_dialog.set_transient_for(self.ui_window)
            self.ui_device_list_dialog.show_all()
            self.ui_device_list_dialog.run()
            self.ui_device_list_dialog.hide()
        except Exception as e:
            _log(f"device list dialog failed: {e}")

    def on_device_list_upload_clicked(self, button):
        # Upload device list: prefer API, fallback to CLI
        try:
            if hasattr(self.client, 'build_report_devices') and getattr(self, 'devices', None) is not None:
                payload = None
                try:
                    payload = self.client.build_report_devices(self.devices, getattr(self, '_report_metadata', {}) or {})
                except Exception as e:
                    _log(f"build_report_devices failed: {e}")
                try:
                    report_uri = None
                    remote = getattr(self, '_lvfs_remote', None)
                    if remote and hasattr(remote, 'get_report_uri'):
                        report_uri = remote.get_report_uri()
                    if payload and report_uri and hasattr(self.client, 'upload_report_async'):
                        self.client.upload_report_async(report_uri, payload, None, 0, None, None)
                        return
                except Exception as e:
                    _log(f"upload_report via API failed: {e}")
            # fallback CLI
            subprocess.Popen(["pkexec", "fwupdmgr", "report-history"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            try:
                subprocess.Popen(["pkexec", "fwupdmgr", "report-history"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def on_device_list_copy_clicked(self, button):
        try:
            if not self.ui_device_list_textview:
                return
            buf = self.ui_device_list_textview.get_buffer()
            start, end = buf.get_start_iter(), buf.get_end_iter()
            text = buf.get_text(start, end, True)
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
        except Exception as e:
            _log(f"copy device list failed: {e}")

    # Install Firmware Archive (.cab)
    def on_install_file_clicked(self, button):
        try:
            dlg = Gtk.FileChooserDialog(title=_("Install Firmware Archive"), parent=self.ui_window, action=Gtk.FileChooserAction.OPEN)
            dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Install"), Gtk.ResponseType.OK)
            filter_cab = Gtk.FileFilter()
            filter_cab.set_name("Cabinet files")
            filter_cab.add_pattern("*.cab")
            dlg.add_filter(filter_cab)
            resp = dlg.run()
            path = dlg.get_filename() if resp == Gtk.ResponseType.OK else None
            dlg.destroy()
            if not path:
                return
            # Prefer fwupd API: get details -> choose compatible device -> confirm -> install
            self._install_file_path = path
            if hasattr(self.client, 'get_details_async'):
                self.client.get_details_async(path, None, self._on_get_details_for_file, None)
            else:
                subprocess.Popen(["pkexec", "fwupdmgr", "install", path])
        except Exception as e:
            _log(f"install file failed: {e}")

    def _on_get_details_for_file(self, source, result, user_data):
        try:
            devices = self.client.get_details_finish(result)
        except GLib.Error as e:
            _log(f"get_details_finish error: {e}")
            # fallback
            if self._install_file_path:
                subprocess.Popen(["pkexec", "fwupdmgr", "install", self._install_file_path])
            return
        if not devices:
            self.add_info_row(self.ui_listbox_releases, _("No compatible devices in archive"))
            return
        # pick first updatable device if any
        chosen = None
        try:
            DF = getattr(Fwupd, 'DeviceFlag', None)
            for d in devices:
                is_updatable = False
                if hasattr(d, 'has_flag') and DF is not None:
                    flag = getattr(DF, 'UPDATABLE', None)
                    is_updatable = flag is not None and d.has_flag(flag)
                else:
                    flags_val = getattr(d, 'get_flags', lambda: 0)() or 0
                    up_flag = getattr(DF, 'UPDATABLE', 0) if DF else 0
                    is_updatable = (int(flags_val) & int(up_flag)) != 0 if up_flag else True
                if is_updatable:
                    chosen = d
                    break
        except Exception:
            pass
        if chosen is None:
            chosen = devices[0]
        # show confirmation similar to release confirmation
        rel = getattr(chosen, 'get_release_default', lambda: None)()
        if rel is None:
            # no release bound; just confirm generic install
            title = _("Install firmware file?")
            body = _("The device may be unusable while the update is installing.")
            dlg = Gtk.MessageDialog(transient_for=self.ui_window, flags=0, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE, text=title)
            dlg.format_secondary_text(body)
            dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Install"), Gtk.ResponseType.OK)
            resp = dlg.run()
            dlg.destroy()
            if resp != Gtk.ResponseType.OK:
                return
            subprocess.Popen(["pkexec", "fwupdmgr", "install", self._install_file_path])
            return
        # release-based confirmation
        version = getattr(rel, 'get_version', lambda: '')() or ''
        title = _((f"Install {version}?")) if version else _("Install firmware?")
        body = _("The device may be unusable while the update is installing.")
        dlg = Gtk.MessageDialog(transient_for=self.ui_window, flags=0, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE, text=title)
        dlg.format_secondary_text(body)
        dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Install"), Gtk.ResponseType.OK)
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        # try API install to any device, else fallback
        try:
            if hasattr(self.client, 'install_async'):
                flags = getattr(Fwupd.InstallFlags, 'NONE', 0)
                self.client.install_async(getattr(Fwupd, 'DEVICE_ID_ANY', '*'), self._install_file_path, flags, None, self.on_install_done, None)
            else:
                subprocess.Popen(["pkexec", "fwupdmgr", "install", self._install_file_path])
        except Exception:
            subprocess.Popen(["pkexec", "fwupdmgr", "install", self._install_file_path])

    # Attestation: Verify/Store
    def on_verify_clicked(self, button):
        try:
            if self.current_device is None:
                return
            # CLI fallback as Python API may not provide verify
            subprocess.Popen(["pkexec", "fwupdmgr", "verify", self.current_device.get_id()])
        except Exception as e:
            _log(f"verify failed: {e}")

    def on_verify_update_clicked(self, button):
        try:
            if self.current_device is None:
                return
            subprocess.Popen(["pkexec", "fwupdmgr", "verify-update", self.current_device.get_id()])
        except Exception as e:
            _log(f"verify-update failed: {e}")


_FW_WIN = None


def open_firmware_window():
    global _FW_WIN
    try:
        if _FW_WIN and getattr(_FW_WIN, 'ui_window', None):
            _log("present existing window")
            _FW_WIN.ui_window.present()
            _FW_WIN.ui_window.show_all()
            return
    except Exception:
        pass
    _log("create new window")
    _FW_WIN = FirmwareWindow()


