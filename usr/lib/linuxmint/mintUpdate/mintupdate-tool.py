#!/usr/bin/python3

import argparse
import subprocess
import sys
import traceback

from Classes import Update
from checkAPT import APTCheck

from gi.repository import Gio

def usage():
    print

if __name__ == "__main__":

    settings = Gio.Settings("com.linuxmint.updates")

    parser = argparse.ArgumentParser()
    parser.add_argument("command", help="a command")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-k", "--kernel", action="store_true", help="ignore settings and include all kernel updates")
    group.add_argument("-nk", "--no-kernel", action="store_true", help="ignore settings and exclude all kernel updates")

    parser.add_argument("-s", "--security", action="store_true", help="ignore settings and include all security updates")
    parser.add_argument("-r", "--refresh-cache", action="store_true", help="refresh the APT cache")
    parser.add_argument("-d", "--dry-run", action="store_true", help="simulation mode, don't upgrade anything")
    parser.add_argument("-y", "--yes", action="store_true", help="automatically answer yes to all questions")
    parser.add_argument("--install-recommends", action="store_true", help="install recommended packages (use with caution)")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--levels", help="ignore settings and restrict to this list of levels")

    args = parser.parse_args()
    try:
        check = APTCheck()
        if args.refresh_cache:
            check.refresh_cache()
        check.find_changes()

        include_kernel = settings.get_boolean("kernel-updates-are-safe")
        include_security = settings.get_boolean("security-updates-are-safe")

        include_level = {}
        for level in ["1", "2", "3", "4", "5"]:
            include_level[level] = settings.get_boolean("level%s-is-safe" % level)
            if args.levels is not None:
                if level in args.levels:
                    include_level[level] = True
                else:
                    include_level[level] = False

        if args.kernel:
            include_kernel = True
        elif args.no_kernel:
            include_kernel = False

        if args.security:
            include_security = True

        if args.command == "list":
            for source_name in sorted(check.updates.keys()):
                update = check.updates[source_name]
                if include_kernel and update.type == "kernel":
                    print ("%s %-15s %-45s %s" % (update.level, update.type, source_name, update.new_version))
                elif include_security and update.type == "security":
                    print ("%s %-15s %-45s %s" % (update.level, update.type, source_name, update.new_version))
                else:
                    level = str(update.level)
                    if include_level[level]:
                        print ("%s %-15s %-45s %s" % (update.level, update.type, source_name, update.new_version))
        elif args.command == "upgrade":
            packages = []
            for source_name in sorted(check.updates.keys()):
                update = check.updates[source_name]
                if include_kernel and update.type == "kernel":
                    packages += update.package_names
                elif include_security and update.type == "security":
                    packages += update.package_names
                else:
                    level = str(update.level)
                    if include_level[level]:
                        packages += update.package_names
            arguments = ["apt-get", "install"]
            if args.dry_run:
                arguments.append("-s")
            if args.yes:
                arguments.append("-y")
            if args.install_recommends:
                arguments.append("--install-recommends")
            subprocess.call(arguments + packages)

    except Exception as error:
        traceback.print_exc()
        sys.exit(1)
