#!/usr/bin/python3

import os, sys, apt, tempfile, gettext
import subprocess

gettext.install("mintupdate", "/usr/share/locale")

if os.getuid() != 0:
    print("Run this code as root!")
    sys.exit(1)

if len(sys.argv) != 3:
    print("Missing arguments!")
    sys.exit(1)

codename = sys.argv[1]
window_id = int(sys.argv[2])
sources_list = "/usr/share/mint-upgrade-info/%s/official-package-repositories.list" % codename
blacklist_filename = "/usr/share/mint-upgrade-info/%s/blacklist" % codename

if not os.path.exists(sources_list):
    print("Unrecognized release: %s" % codename)
    sys.exit(1)

blacklist = []
if os.path.exists(blacklist_filename):
    blacklist_file = open(blacklist_filename, 'r')
    blacklist = [line.strip() for line in blacklist_file.readlines()]

# STEP 1: UPDATE APT SOURCES
#---------------------------
if os.path.exists("/etc/apt/sources.list.d/official-source-repositories.list"):
    subprocess.run(["rm", "-f", "/etc/apt/sources.list.d/official-source-repositories.list"])

subprocess.run(["cp", sources_list, "/etc/apt/sources.list.d/official-package-repositories.list"])

# STEP 2: UPDATE APT CACHE
#-------------------------

cache = apt.Cache()
subprocess.run(["sudo", "/usr/sbin/synaptic", "--hide-main-window", "--update-at-startup", "--non-interactive", "--parent-window-id", "%d" % window_id])

# STEP 3: INSTALL MINT UPDATES
#--------------------------------

dist_upgrade = True

# Reopen the cache to reflect any updates
cache.open(None)
cache.upgrade(dist_upgrade)
changes = cache.get_changes()

cmd = ["sudo", "/usr/sbin/synaptic", "--hide-main-window", "--non-interactive", "--parent-window-id", "%s" % window_id, "-o", "Synaptic::closeZvt=true"]
f = tempfile.NamedTemporaryFile()

for pkg in changes:
    if (pkg.is_installed and pkg.marked_upgrade):
        package = pkg.name
        newVersion = pkg.candidate.version
        oldVersion = pkg.installed.version
        size = pkg.candidate.size
        sourcePackage = pkg.candidate.source_name
        short_description = pkg.candidate.raw_description
        description = pkg.candidate.description
        if sourcePackage not in blacklist:
            if (newVersion != oldVersion):
                update_type = "package"
                for origin in pkg.candidate.origins:
                    if origin.origin == "linuxmint":
                        if origin.component != "romeo" and package != "linux-kernel-generic":
                            pkg_line = "%s\tinstall\n" % package
                            f.write(pkg_line.encode("utf-8"))

cmd.append("--set-selections-file")
cmd.append("%s" % f.name)
f.flush()
subprocess.run(cmd)

# STEP 4: UPDATE GRUB
#--------------------

try:
    subprocess.run(["update-grub"])
    if os.path.exists("/usr/share/ubuntu-system-adjustments/systemd/adjust-grub-title"):
        subprocess.run(["/usr/share/ubuntu-system-adjustments/systemd/adjust-grub-title"])
except Exception as detail:
    syslog.syslog("Couldn't update grub: %s" % detail)