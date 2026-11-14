#!/usr/bin/python3

import os
import subprocess
import sys
import time

sys.path.insert(0, "/usr/lib/linuxmint/mintUpdate")
from util import on_battery, get_battery_percentage

MIN_BATTERY_PERCENTAGE = 75

if not os.path.exists("/var/lib/linuxmint/mintupdate-automatic-upgrades-enabled"):
    exit(0)

optionsfile = "/etc/mintupdate-automatic-upgrades.conf"
logfile = "/var/log/mintupdate.log"
log = open(logfile, "a", buffering=1)
log.write("\n-- Automatic Upgrade starting %s:\n" % time.strftime('%a %d %b %Y %H:%M:%S %Z'))

pkla_source = "/usr/share/linuxmint/mintupdate/automation/99-mintupdate-temporary.pkla"
pkla_target = "/etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla"

can_upgrade = True
if on_battery():
    battery_percentage = get_battery_percentage()
    if battery_percentage is not None and battery_percentage >= MIN_BATTERY_PERCENTAGE:
        log.write("-- On battery power (%d%%), proceeding.\n" % battery_percentage)
    else:
        can_upgrade = False

if can_upgrade:
    try:
        # Put shutdown and reboot blocker into place
        os.symlink(pkla_source, pkla_target)
    except:
        pass

    returncode = 1
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
        cmd.extend(arguments)
        returncode = subprocess.run(cmd, stdout=log, stderr=log).returncode

    except:
        import traceback
        log.write("Exception occurred:\n")
        log.write(traceback.format_exc())

    try:
        # Remove shutdown and reboot blocker
        os.unlink(pkla_target)
    except:
        pass

    if returncode == 0:
        log.write("-- Automatic Upgrade completed\n")
    else:
        log.write("-- Automatic Upgrade failed (exit %d)\n" % returncode)
else:
    log.write("-- Power supply not connected and battery below %d%%, abort automatic update.\n" % MIN_BATTERY_PERCENTAGE)
    returncode = 0
log.close()
sys.exit(returncode)
