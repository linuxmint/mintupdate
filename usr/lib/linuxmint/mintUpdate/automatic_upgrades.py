#!/usr/bin/python3

import os
import subprocess
import time
import dbus
import gi
import locale
import signal
import argparse
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

parser = argparse.ArgumentParser(description="Script for automatic Upgrades")
parser.add_argument("-mb", "--min-bat", type=int, default=100, help="Minimum battery level to apply updates")
parser.add_argument("-d", "--only-download", action="store_true", help="only download upgrades and do not install them.")
parser.add_argument("-f", "--force", action="store_true", help="Ignore check of network connection, battery level and power supply")
args=parser.parse_args()
thres=args.min_bat

optionsfile = "/etc/mintupdate-automatic-upgrades.conf"
logfile = "/var/log/mintupdate.log"

log = open(logfile, "a")
log.write("\n-- Automatic Upgrade starting %s:\n" % time.strftime('%a %d %b %Y %H:%M:%S %Z'))
log.flush()

pkla_source = "/usr/share/linuxmint/mintupdate/automation/99-mintupdate-temporary.pkla"
pkla_target = "/etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla"


metered_values = [1 , 3]
supply_connected_values= [0,1,4,5]

DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()

networkManager = bus.get_object('org.freedesktop.NetworkManager','/org/freedesktop/NetworkManager')
upower = bus.get_object('org.freedesktop.UPower','/org/freedesktop/UPower/devices/DisplayDevice')
metered = networkManager.Get('org.freedesktop.NetworkManager','Metered',dbus_interface='org.freedesktop.DBus.Properties') in metered_values
supply = upower.Get('org.freedesktop.UPower.Device','State',dbus_interface='org.freedesktop.DBus.Properties') in supply_connected_values
level = upower.Get('org.freedesktop.UPower.Device','Percentage',dbus_interface='org.freedesktop.DBus.Properties')
online = networkManager.Get('org.freedesktop.NetworkManager','State',dbus_interface='org.freedesktop.DBus.Properties') >= 60
loop = None


def check(*prop,**prop2):
    global metered,supply,online,level
    if(prop[0]=='org.freedesktop.UPower.Device'):
        supply_prop = prop[1].get("State")
        if not supply_prop == None:
            if supply_prop in supply_connected_values:
                supply = True
            else:
                supply = False
        level_prop = prop[1].get("Percentage")
        if not level_prop == None:
            level=level_prop

            if updateConditions(metered,supply,level,online):
                log = open(logfile, "a")
                log.write("\n-- Automatic Upgrade starting %s:\n" % time.strftime('%a %d %b %Y %H:%M:%S %Z'))
                log.flush()
                update()
                log.close()
    else:
        metered_prop=prop[1].get("Metered")
        online_prop=prop[1].get("State")
        if not online_prop == None:
            online = online_prop>=60
        if not metered_prop == None:
            if metered_prop in metered_values:
                metered = True
            else:
                metered = False
            if updateConditions(metered,supply,level,online):
                log = open(logfile, "a")
                log.write("\n-- Automatic Upgrade starting %s:\n" % time.strftime('%a %d %b %Y %H:%M:%S %Z'))
                log.flush()
                update()
                log.close()

def update():
    if(loop != None):
        loop.quit()
    try:
        # Put shutdown and reboot blocker into place
        # No blocker is needed when applications are only downloaded
        if not args.only_download:
            os.symlink(pkla_source, pkla_target)
    except:
        pass

    try:
        # Parse options file
        arguments = []
        if os.path.isfile(optionsfile):
            with open(optionsfile) as options:
                for line in options:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        arguments.append(line)

        # Run mintupdate-cli through systemd-inhibit
        cmd = ["/bin/systemd-inhibit", '--why="Performing automatic updates"',
               '--who="Update Manager"',  "--what=shutdown", "--mode=block",
               "/usr/bin/mintupdate-cli", "upgrade", "--refresh-cache", "--yes"]
        if args.only_download :
            cmd[6] = "download"
        cmd.extend(arguments)
        subprocess.run(cmd, stdout=log, stderr=log)

    except:
        import traceback
        log.write("Exception occurred:\n")
        log.write(traceback.format_exc())

    try:
        # Remove shutdown and reboot blocker
        if not args.only_download:
            os.unlink(pkla_target)
    except:
        pass

    log.write("-- Automatic Upgrade completed\n")

def updateConditions(metered, power_supply, bat_level, online):
    if args.force:
        return True
    if (not metered) and online:
        if bat_level>thres or power_supply:
            return True
        return False
loop=GLib.MainLoop()
if updateConditions(metered,supply,level,online):
    update()
    log.close()
else:
    if metered:
        log.write("-- Metered Connection. Updates aborted.\n")
    if not supply:
        log.write("-- Power Supply not connected. Updates aborted.\n")
    if not online:
        log.write("-- No Internet Connection. Updates aborted.\n")
    log.flush()
    networkManager.connect_to_signal("PropertiesChanged", check, dbus_interface='org.freedesktop.DBus.Properties')
    upower.connect_to_signal("PropertiesChanged", check, dbus_interface='org.freedesktop.DBus.Properties')
    loop.run()
    log.close()
