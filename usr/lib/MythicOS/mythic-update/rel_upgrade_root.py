#!/usr/bin/python3

import apt
import aptkit.simpleclient
import gettext
import gi
import os
import subprocess
import sys

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

gettext.install("mintupdate", "/usr/share/locale")

if os.getuid() != 0:
    print("Run this code as root!")
    sys.exit(1)

if len(sys.argv) != 2:
    print("Missing arguments!")
    sys.exit(1)

codename = sys.argv[1]
sources_list = f"/usr/share/mint-upgrade-info/{codename}/official-package-repositories.list"
if not os.path.exists(sources_list):
    print("Unrecognized release: %s" % codename)
    sys.exit(1)

def file_to_list(filename):
    returned_list = []
    if os.path.exists(filename):
        with open(filename, 'r') as file_handle:
            for line in file_handle:
                line = line.strip()
                if line == "" or line.startswith("#"):
                    continue
                returned_list.append(line)
    return returned_list

class Upgrader():

    def __init__(self):
        self.refresh_done = False
        self.client = aptkit.simpleclient.SimpleAPTClient()
        self.additions = file_to_list(f"/usr/share/mint-upgrade-info/{codename}/additions")
        self.removals = file_to_list(f"/usr/share/mint-upgrade-info/{codename}/removals")
        self.blacklist = file_to_list(f"/usr/share/mint-upgrade-info/{codename}/blacklist")

    def run(self):
        self.update_cache()

    def update_cache(self):
        # STEP 1: UPDATE APT SOURCES
        if os.path.exists("/etc/apt/sources.list.d/official-source-repositories.list"):
            subprocess.run(["rm", "-f", "/etc/apt/sources.list.d/official-source-repositories.list"])
        subprocess.run(["cp", sources_list, "/etc/apt/sources.list.d/official-package-repositories.list"])

        # STEP 2: UPDATE APT CACHE
        self.client.set_finished_callback(self.on_cache_updated)
        self.client.update_cache()

    def on_cache_updated(self, transaction=None, exit_state=None):
        # STEP 3: INSTALL MINT UPDATES
        cache = apt.Cache()
        cache.open(None)
        cache.upgrade(True)
        packages = []
        for pkg in cache.get_changes():
            # upgradeable..
            if pkg.is_installed and pkg.marked_upgrade:
                # not in blacklist..
                if pkg.candidate.source_name not in self.blacklist:
                    # with a new version..
                    if (pkg.candidate.version != pkg.installed.version):
                        # from the Mint repos..
                        for origin in pkg.candidate.origins:
                            if origin.origin == "linuxmint":
                                if origin.component != "romeo" and pkg.name != "linux-kernel-generic":
                                    packages.append(pkg.name)

        if len(packages) > 0:
            self.client.set_finished_callback(self.on_install_finished)
            self.client.install_packages(packages)
        else:
            self.on_install_finished()

    def on_install_finished(self, transaction=None, exit_state=None):
        # STEP 4: ADD PACKAGES
        if len(self.additions) > 0:
            self.client.set_finished_callback(self.on_additions_finished)
            self.client.install_packages(self.additions)
        else:
            self.on_additions_finished()

    def on_additions_finished(self, transaction=None, exit_state=None):
        # STEP 5: REMOVE PACKAGES
        removals = []
        cache = apt.Cache()
        cache.open(None)
        for name in self.removals:
            if name in cache and cache[name].is_installed:
                removals.append(name)
        if len(removals) > 0:
            self.client.set_finished_callback(self.on_removals_finished)
            self.client.remove_packages(removals)
        else:
            self.on_removals_finished()

    def on_removals_finished(self, transaction=None, exit_state=None):
        # STEP 6: UPDATE GRUB
        try:
            subprocess.run(["update-grub"])
            if os.path.exists("/usr/share/ubuntu-system-adjustments/systemd/adjust-grub-title"):
                subprocess.run(["/usr/share/ubuntu-system-adjustments/systemd/adjust-grub-title"])
        except Exception as detail:
            syslog.syslog("Couldn't update grub: %s" % detail)
        Gtk.main_quit()
        sys.exit(0)

if __name__ == "__main__":
    upgrader =Upgrader()
    upgrader.run()
    Gtk.main()
