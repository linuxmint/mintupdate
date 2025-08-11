#!/usr/bin/python3
# -*- coding: utf-8 -*-

import gi
gi.require_version('Gtk', '3.0')
try:
    gi.require_version('Fwupd', '2.0')
except ValueError:
    pass

from gi.repository import Gtk, Gio, GLib
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

        _log("connecting to fwupd (async)")
        self.client.connect_async(None, self.on_client_connected, None)

        self.ui_window.show_all()
        try:
            self.ui_window.present()
        except Exception:
            pass
        _log("window shown")

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
        self.ui_label_device_name.set_text(dev.get_name() or dev.get_id())
        self.ui_label_device_vendor.set_text(dev.get_vendor() or "-")
        self.ui_label_device_version.set_text(dev.get_version() or "-")
        # Vendor IDs / GUIDs
        try:
            guids = []
            if hasattr(dev, 'get_guids'):
                guids = list(dev.get_guids()) or []
            self.ui_label_device_vendor_ids.set_text(
                ", ".join(guids) if guids else "-"
            )
        except Exception:
            self.ui_label_device_vendor_ids.set_text("-")
        # Plugin/Backend
        try:
            plugin = dev.get_plugin() if hasattr(dev, 'get_plugin') else None
            self.ui_label_device_plugin.set_text(plugin or "-")
        except Exception:
            self.ui_label_device_plugin.set_text("-")
        # Serial
        try:
            serial = dev.get_serial() if hasattr(dev, 'get_serial') else None
            self.ui_label_device_serial.set_text(serial or "-")
        except Exception:
            self.ui_label_device_serial.set_text("-")
        # Flags
        try:
            flags_val = dev.get_flags() if hasattr(dev, 'get_flags') else 0
            self.ui_label_device_flags.set_text(str(flags_val))
        except Exception:
            self.ui_label_device_flags.set_text("-")
        # Problems / Update error
        try:
            problems = []
            if hasattr(dev, 'get_update_error'):
                err = dev.get_update_error()
                if err:
                    problems.append(err)
            self.ui_label_device_update_error.set_text("; ".join(problems) if problems else "-")
        except Exception:
            self.ui_label_device_update_error.set_text("-")
        try:
            # fwupd may provide problem fields; if absent, leave "-"
            self.ui_label_device_problems.set_text("-")
        except Exception:
            self.ui_label_device_problems.set_text("-")

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
                self.add_info_row(self.ui_listbox_releases, "No releases available")
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
        flags = getattr(Fwupd.InstallFlags, "NONE", 0)
        try:
            _log(f"install clicked: device={self.current_device.get_id()} release={release.get_version()}")
        except Exception:
            pass
        if hasattr(self.client, "install_release_async"):
            _log("using install_release_async")
            self.client.install_release_async(
                self.current_device,
                release,
                flags,
                getattr(Fwupd.ClientDownloadFlags, "NONE", 0),
                None,
                self.on_install_done,
                None,
            )
        else:
            # older API
            _log("using install_release2_async")
            self.client.install_release2_async(
                self.current_device,
                release,
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
            set_label("label_release_description", release.get_description() or "-")
            set_label("label_release_vendor", release.get_vendor() or "-")
            set_label("label_release_filename", release.get_filename() or "-")
            try:
                size_val = getattr(release, 'get_size', None)
                size_str = f"{size_val()} B" if callable(size_val) and size_val() else "-"
                set_label("label_release_size", size_str)
            except Exception:
                set_label("label_release_size", "-")
            set_label("label_release_protocol", getattr(release, 'get_protocol', lambda: None)() or "-")
            set_label("label_release_remote", getattr(release, 'get_remote', lambda: None)() or "-")
            set_label("label_release_appstream_id", getattr(release, 'get_appstream_id', lambda: None)() or "-")
            set_label("label_release_license", getattr(release, 'get_license', lambda: None)() or "-")
            # flags
            try:
                flags_val = getattr(release, 'get_flags', lambda: 0)()
                set_label("label_release_flags", str(flags_val))
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
                set_label("label_release_categories", ", ".join(cats) if cats else "-")
            except Exception:
                set_label("label_release_categories", "-")
            # issues
            try:
                issues = getattr(release, 'get_issues', lambda: [])()
                set_label("label_release_issues", ", ".join(issues) if issues else "-")
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
        except Exception:
            pass


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


