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


def _get_upower_property(path, iface, name):
    # UPower's default policy allows property reads from any uid, so this works
    # for both root (system services) and user contexts.
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    ret = bus.call_sync(
        "org.freedesktop.UPower", path,
        "org.freedesktop.DBus.Properties", "Get",
        GLib.Variant("(ss)", (iface, name)),
        GLib.VariantType("(v)"),
        Gio.DBusCallFlags.NONE, 2000, None)
    return ret.unpack()[0]


def on_battery():
    # Returns False if UPower is unavailable (fail open — assume AC connected).
    try:
        return bool(_get_upower_property("/org/freedesktop/UPower",
                                         "org.freedesktop.UPower",
                                         "OnBattery"))
    except GLib.Error:
        return False


def get_battery_percentage():
    # UPower's DisplayDevice aggregates all system batteries, excluding peripherals.
    # Returns None if there's no battery or UPower is unavailable.
    path = "/org/freedesktop/UPower/devices/DisplayDevice"
    iface = "org.freedesktop.UPower.Device"
    try:
        if not _get_upower_property(path, iface, "IsPresent"):
            return None
        return _get_upower_property(path, iface, "Percentage")
    except GLib.Error:
        return None


class Inhibitor:
    def __init__(self, logger=None, window=None):
        self.logger = logger
        self.window = window
        self.cookie = 0
        self.logind_fd = None

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

    def _inhibit_logind(self, reason):
        """ Blocks sleep and shutdown without touching the display.

        xfce4-power-manager implements org.freedesktop.PowerManagement.Inhibit by
        disabling DPMS, which powers a blanked panel straight back on.  A scheduled
        refresh then wakes an idle machine's monitor every couple of hours.  logind
        has no display side effects, and the inhibitor fd is dropped automatically if
        we exit or crash, so it can't leave the session inhibited either.
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)

            ret, fd_list = bus.call_with_unix_fd_list_sync(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "Inhibit",
                GLib.Variant("(ssss)", ("sleep:shutdown", "mintupdate", reason, "block")),
                GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
                None
            )
            self.logind_fd = fd_list.get(ret.unpack()[0])
        except GLib.Error as e:
            self._log("Could not inhibit sleep and shutdown via logind: %s" % e.message)
            return False

        self._log("Inhibited sleep and shutdown")
        return True

    def _uninhibit_logind(self):
        try:
            os.close(self.logind_fd)
        except OSError as e:
            self._log("Could not release the logind inhibitor: %s" % e)

        self.logind_fd = None
        self._log("Resumed power management")

    def inhibit(self, reason):
        if self.cookie > 0 or self.logind_fd is not None:
            return

        # Xfce's inhibit interface disables DPMS - see _inhibit_logind().
        if os.environ.get("XDG_CURRENT_DESKTOP") == "XFCE" and self._inhibit_logind(reason):
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
        if self.logind_fd is not None:
            self._uninhibit_logind()
            return

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
