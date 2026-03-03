#!/usr/bin/python3

from pathlib import Path
import os
import subprocess
import time

if not Path("/var/lib/linuxmint/mintupdate-automatic-upgrades-enabled").exists():
    exit(0)

optionsfile = Path("/etc/mintupdate-automatic-upgrades.conf")
logfile = Path("/var/log/mintupdate.log")
main_powered  = False
sufficient_battery = False

# Check for power status
# See https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-class-power
power_supplies = Path('/sys/class/power_supply').glob("*")
for power_supply in power_supplies:
    power_supply_type = Path(f"{power_supply}/type").read_text().strip()
    # One of  "Battery", "UPS", "Mains", "USB", "Wireless"
    if power_supply_type == "Mains":
        try:
            if Path(f"{power_supply}/online").read_text().strip() != "0":
                # Indicates if VBUS is present for the supply. When the supply is
                # online, and the supply allows it, then it's possible to switch
                # between online states (e.g. Fixed -> Programmable for a PD_PPS
                # USB supply so voltage and current can be controlled).
                # Valid values:
                #   0: Offline
                #   1: Online Fixed - Fixed Voltage Supply
                #   2: Online Programmable - Programmable Voltage Supply
                main_powered = True
                break
        except FileNotFoundError:
            pass
    elif power_supply_type == "Battery":
        try:
            if Path(f"{power_supply}/scope").exists() and Path(f"{power_supply}/scope").read_text().strip() == "Device":
                # Skip batteries of connected (undocumented Logitech) devices
                continue
            battery_level = Path(f"{power_supply}/capacity_level").read_text().strip()
            # Coarse representation of battery capacity.
            # Valid values: "Unknown", "Critical", "Low", "Normal", "High", "Full"
            if battery_level in ["Normal", "High","Full"]:
                try:
                    battery_capacity = int(Path(f"{power_supply}/capacity").read_text().strip())
                    # Fine grain representation of battery capacity.
                    # Valid values: 0 - 100 (percent)
                    if battery_capacity >= 75:
                        sufficient_battery = True
                    else:
                        sufficient_battery = False
                        # stop checking, even when a single battery
                        # on a multi powered device is unsufficient.
                        break
                except FileNotFoundError:
                    pass
            elif battery_level in ["Critical", "Low"]:
                sufficient_battery = False
                break
            else:
                pass
        except FileNotFoundError:
            pass
    else:
        pass

with logfile.open("a") as log:
    if main_powered or sufficient_battery:
        log.write(f"\n-- Automatic Upgrade starting {time.strftime('%a %d %b %Y %H:%M:%S %Z')}:\n")
        log.flush()

        pkla_source = "/usr/share/linuxmint/mintupdate/automation/99-mintupdate-temporary.pkla"
        pkla_target = "/etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla"
        try:
            os.symlink(pkla_source, pkla_target)
            # Parse options file
            arguments = []
            if optionsfile.is_file():
                for line in open(optionsfile, "r"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        arguments.append(line)

            # Run mintupdate-cli through systemd-inhibit
            cmd = ["/bin/systemd-inhibit", '--why="Performing automatic updates"',
                   '--who="Update Manager"',  "--what=shutdown", "--mode=block",
                   "/usr/bin/mintupdate-cli", "upgrade", "--refresh-cache", "--yes"]
            cmd.extend(arguments)
            subprocess.run(cmd, stdout=log, stderr=log)
        except Exception as e:
            import traceback
            log.write(f"Exception occurred: {e}\n")
            log.write(traceback.format_exc())
            log.flush()
        finally:
            # Remove shutdown and reboot blocker
            os.unlink(pkla_target)
            log.write(f"-- Automatic Upgrade completed\n")
            log.flush()
    else:
        log.write(f"\n-- Automatic Upgrade skipped by power status {time.strftime('%a %d %b %Y %H:%M:%S %Z')}:\n")
        log.flush()
