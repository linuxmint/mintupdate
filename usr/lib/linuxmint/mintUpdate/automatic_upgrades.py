#!/usr/bin/python3

import os
import subprocess
import time

optionsfile = "/etc/mintupdate-automatic-upgrades.conf"
logfile = "/var/log/mintupdate.log"
log = open(logfile, "a")
log.write(f"\n-- Automatic Upgrade starting {time.strftime('%a %d %b %Y %H:%M:%S %Z')}:\n")
log.flush()

pkla_source = "/usr/share/linuxmint/mintupdate/automation/99-mintupdate-temporary.pkla"
pkla_target = "/etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla"
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

    # Run mintupdate-cli through systemd-inhibit
    cmd = ["/bin/systemd-inhibit", '--why="Performing automatic updates"',
           '--who="Update Manager"',  "--what=shutdown", "--mode=block",
           "/usr/bin/mintupdate-cli", "upgrade", "--refresh-cache", "--yes"]
    cmd.extend(arguments)
    subprocess.run(cmd, stdout=log, stderr=log)

except:
    import traceback
    log.write("Exception occurred:\n")
    log.write(traceback.format_exc())

try:
    # Remove shutdown and reboot blocker
    os.unlink(pkla_target)
except:
    pass

log.write("-- Automatic Upgrade completed\n")
log.close()
