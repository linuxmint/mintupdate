import os
import sys
import threading

from gi.repository import Gio, GLib


# Used as a decorator to run things in the background
def _async(func):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper


# Used as a decorator to run things in the main loop, from another thread
def _idle(func):
    def wrapper(*args):
        GLib.idle_add(func, *args)
    return wrapper


def get_session_type():
    return os.environ.get("XDG_SESSION_TYPE", "x11")


def on_battery():
    # Query UPower over the system D-Bus. Works for both root (system services)
    # and user contexts; UPower's default policy allows property reads from any uid.
    # Returns False if UPower is unavailable (fail open — assume AC connected).
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.UPower",
            "/org/freedesktop/UPower",
            "org.freedesktop.UPower",
            None)
        prop = proxy.get_cached_property("OnBattery")
        if prop is None:
            return False
        return prop.unpack()
    except GLib.Error:
        return False


class Inhibitor:
    def __init__(self, logger=None, window=None):
        self.logger = logger
        self.window = window
        self.cookie = 0

    def _log(self, msg):
        if self.logger is not None:
            self.logger.write(msg)
        else:
            print(msg, file=sys.stderr)

    def _get_info(self, reason):
        session = os.environ.get("XDG_CURRENT_DESKTOP")

        if session == "XFCE":
            name = "org.freedesktop.PowerManagement"
            path = "/org/freedesktop/PowerManagement/Inhibit"
            iface = "org.freedesktop.PowerManagement.Inhibit"
            args = GLib.Variant("(ss)", ("mintupdate", reason))
            uninhibit_method = "UnInhibit"
        else:
            # https://github.com/linuxmint/cinnamon-session/blob/master/cinnamon-session/csm-inhibitor.h#L51-L58
            #       LOGOUT | SUSPEND
            flags =      1 | 4

            xid = 0
            if self.window is not None and get_session_type() == "x11":
                try:
                    xid = self.window.get_window().get_xid()
                except Exception:
                    pass

            name = "org.gnome.SessionManager"
            path = "/org/gnome/SessionManager"
            iface = "org.gnome.SessionManager"
            args = GLib.Variant("(susu)", ("mintupdate", xid, reason, flags))
            uninhibit_method = "Uninhibit"

        return name, path, iface, args, uninhibit_method

    def inhibit(self, reason):
        if self.cookie > 0:
            return

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        except GLib.Error as e:
            self._log("Couldn't get session bus to inhibit power management: %s" % e.message)
            return

        name, path, iface, args, unused = self._get_info(reason)

        try:
            ret = bus.call_sync(
                name,
                path,
                iface,
                "Inhibit",
                args,
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None
            )
        except GLib.Error as e:
            self._log("Could not inhibit power management: %s" % e.message)
            return

        self._log("Inhibited power management")
        self.cookie = ret.unpack()[0]

    def uninhibit(self):
        if self.cookie <= 0:
            return

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        except GLib.Error as e:
            self._log("Couldn't get session bus to uninhibit power management: %s" % e.message)
            return

        name, path, iface, unused_args, uninhibit_method = self._get_info("none")

        try:
            bus.call_sync(
                name,
                path,
                iface,
                uninhibit_method,
                GLib.Variant("(u)", (self.cookie,)),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None
            )
        except GLib.Error as e:
            self._log("Could not uninhibit power management: %s" % e.message)
            return

        self._log("Resumed power management")
        self.cookie = 0
