#!/usr/bin/python3

import argparse
import fnmatch
import os
import subprocess
import sys
import traceback

from checkAPT import APTCheck
from Classes import PRIORITY_UPDATES

if __name__ == "__main__":

    def is_blacklisted(blacklisted_packages, source_name, version):
        for blacklist in blacklisted_packages:
            if "=" in blacklist:
                (bl_pkg, bl_ver) = blacklist.split("=", 1)
            else:
                bl_pkg = blacklist
                bl_ver = None
            if fnmatch.fnmatch(source_name, bl_pkg) and (not bl_ver or bl_ver == version):
                return True
        return False

    parser = argparse.ArgumentParser(prog="mintupdate-cli")
    parser.add_argument("command", help="command to run (possible commands are: list, upgrade)")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-k", "--only-kernel", action="store_true", help="only include kernel updates")
    group.add_argument("-s", "--only-security", action="store_true", help="only include security updates")
    parser.add_argument("-i", "--ignore", help="list of updates to ignore (comma-separated list of source package names). Note: You can also blacklist updates by adding them to /etc/mintupdate.blacklist, one source package per line. To ignore a specific version, use the format package=version.")
    parser.add_argument("-r", "--refresh-cache", action="store_true", help="refresh the APT cache")
    parser.add_argument("-d", "--dry-run", action="store_true", help="simulation mode, don't upgrade anything")
    parser.add_argument("-y", "--yes", action="store_true", help="automatically answer yes to all questions and always install new configuration files (unless you also use \"--keep-configuration\" option)")
    parser.add_argument("--install-recommends", action="store_true", help="install recommended packages")
    parser.add_argument("--keep-configuration", action="store_true", default=False, help="always keep local changes in configuration files (use with caution)")
    parser.add_argument("-v", "--version", action="version", version="__DEB_VERSION__", help="Display the current version")

    args = parser.parse_args()
    try:
        if args.refresh_cache:
            subprocess.run("sudo /usr/bin/mint-refresh-cache", shell=True)
        check = APTCheck()
        check.find_changes()

        blacklisted = []
        if os.path.exists("/etc/mintupdate.blacklist"):
            with open("/etc/mintupdate.blacklist") as blacklist_file:
                for line in blacklist_file:
                    line = line.strip()
                    if line.startswith("#"):
                        continue
                    blacklisted.append(line)
        if args.ignore:
            blacklisted.extend(args.ignore.split(","))

        updates = []
        for source_name in sorted(check.updates.keys()):
            update = check.updates[source_name]
            if source_name in PRIORITY_UPDATES:
                updates.append(update)
            elif args.only_kernel and update.type != "kernel":
                continue
            elif args.only_security and update.type != "security":
                continue
            elif is_blacklisted(blacklisted, update.real_source_name, update.new_version):
                continue
            else:
                updates.append(update)

        if args.command == "list":
            for update in updates:
                print ("%-15s %-45s %s" % (update.type, update.source_name, update.new_version))
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
    except:
        traceback.print_exc()
        sys.exit(1)
