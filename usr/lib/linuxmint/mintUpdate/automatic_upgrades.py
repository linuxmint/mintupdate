#!/usr/bin/python3

import os
import subprocess
import time
import argparse

if not (os.path.exists("/var/lib/linuxmint/mintupdate-automatic-upgrades-enabled") or os.path.exists("/var/lib/linuxmint/mintupdate-automatic-downloads-enabled")):
    exit(0)
if os.path.exists("/var/lib/linuxmint/mintupdate-automatic-downloads-enabled"):
    only_download = True
else:
    only_download = False

optionsfile = "/etc/mintupdate-automatic-upgrades.conf"
logfile = "/var/log/mintupdate.log"
power_connectfile="/sys/class/power_supply/AC/online"
log = open(logfile, "a")
if only_download:
    log.write("\n-- Automatic Upgrade will only be downloaded and not installed(can be changed in mintupdate settings) %s:\n" % time.strftime('%a %d %b %Y %H:%M:%S %Z'))
else:
    log.write("\n-- Automatic Upgrade starting %s:\n" % time.strftime('%a %d %b %Y %H:%M:%S %Z'))
log.flush()

pkla_source = "/usr/share/linuxmint/mintupdate/automation/99-mintupdate-temporary.pkla"
pkla_target = "/etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla"
try:
    power_supply_file = open(power_connectfile)
    powersupply = power_supply_file.read()[0]=='1'
    power_supply_file.close()
except:
    powersupply = True
    log.write(power_connectfile+" not found. Ignore power supply check.")
if powersupply:
    try:
        # Put shutdown and reboot blocker into place
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
    except:
        import traceback
        log.write("Exception occurred:\n")
        log.write(traceback.format_exc())
    # Run mintupdate-cli through systemd-inhibit
    cmd = ["/bin/systemd-inhibit", '--why="Performing automatic updates"',
           '--who="Update Manager"',  "--what=shutdown", "--mode=block",
           "/usr/bin/mintupdate-cli", "upgrade", "--refresh-cache", "--yes"]
    if(only_download):
        cmd[6] = "download"
    cmd.extend(arguments)
    subprocess.run(cmd, stdout=log, stderr=log)



    try:
        # Remove shutdown and reboot blocker
        os.unlink(pkla_target)
    except:
        pass

    log.write("-- Automatic Upgrade completed\n")
else:
    log.write("-- Power supply not connected, abort automatic update.\n")
log.close()
