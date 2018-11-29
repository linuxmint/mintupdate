#!/usr/bin/python3

import argparse
import os
import subprocess
import sys
import traceback

from Classes import Update
from checkAPT import APTCheck

# These updates take priority over other updates.
# If a new version of these packages is available,
# nothing else is listed. These packages are also always listed.
PRIORITY_UPDATES = ['mintupdate', 'mint-upgrade-info']

if __name__ == "__main__":

    parser = argparse.ArgumentParser(prog="mintupdate-cli")
    parser.add_argument("command", help="command to run (possible commands are: list, upgrade)")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-k", "--only-kernel", action="store_true", help="only include kernel updates")
    group.add_argument("-s", "--only-security", action="store_true", help="only include security updates")
    group.add_argument("-l", "--only-levels", help="only include certain levels (only use for troubleshooting, list of level numbers, comma-separated)")

    parser.add_argument("-i", "--ignore", help="list of updates to ignore (comma-separated). Note: You can also blacklist updates by adding their name to /etc/mintupdate.blacklist.")
    parser.add_argument("-r", "--refresh-cache", action="store_true", help="refresh the APT cache")
    parser.add_argument("-d", "--dry-run", action="store_true", help="simulation mode, don't upgrade anything")
    parser.add_argument("-y", "--yes", action="store_true", help="automatically answer yes to all questions and always install new configuration files (unless you also use \"--keep-configuration\" option)")
    parser.add_argument("--install-recommends", action="store_true", help="install recommended packages (use with caution)")
    parser.add_argument("--keep-configuration", action="store_true", default=False, help="always keep local changes in configuration files (use with caution)")
    parser.add_argument("-v", "--version", action="version", version=subprocess.getoutput("/usr/lib/linuxmint/common/version.py mintupdate"), help="Display the current version")

    args = parser.parse_args()
    try:
        check = APTCheck()
        if args.refresh_cache:
            check.refresh_cache()
        check.find_changes()

        blacklisted = []
        if os.path.exists("/etc/mintupdate.blacklist"):
            with open("/etc/mintupdate.blacklist") as blacklist_file:
                for line in blacklist_file:
                    line = line.strip()
                    if line.startswith("#"):
                        continue
                    blacklisted.append(line)

        updates = []
        for source_name in sorted(check.updates.keys()):
            update = check.updates[source_name]
            if source_name in PRIORITY_UPDATES:
                updates.append(update)
            elif args.only_kernel and update.type != "kernel":
                continue
            elif args.only_security and update.type != "security":
                continue
            elif args.only_levels is not None and str(update.level) not in args.only_levels:
                continue
            elif args.ignore is not None and update.source_name in args.ignore.split(","):
                continue
            elif update.source_name in blacklisted:
                continue
            else:
                updates.append(update)

        if args.command == "list":
            for update in updates:
                print ("%s %-15s %-45s %s" % (update.level, update.type, update.source_name, update.new_version))
        elif args.command == "upgrade":
            packages = []
            for update in updates:
                packages += update.package_names
            arguments = ["apt-get", "install"]
            if args.dry_run:
                arguments.append("--simulate")
            if args.yes:
                environment = os.environ
                environment.update({"DEBIAN_FRONTEND": "noninteractive"})
                arguments.append("--assume-yes")
                if not args.keep_configuration:
                    arguments.extend(["--option", "Dpkg::Options::=--force-confnew"])
            else:
                environment = None
            if args.install_recommends:
                arguments.append("--install-recommends")
            if args.keep_configuration:
                arguments.extend(["--option", "Dpkg::Options::=--force-confold"])
            subprocess.call(arguments + packages, env=environment)
    except Exception as error:
        traceback.print_exc()
        sys.exit(1)
