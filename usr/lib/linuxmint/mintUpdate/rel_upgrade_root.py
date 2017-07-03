#!/usr/bin/python3

import os, sys, apt, tempfile, gettext
import subprocess

gettext.install("mintupdate", "/usr/share/linuxmint/locale")

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
    os.system("rm -f /etc/apt/sources.list.d/official-source-repositories.list")

os.system("cp %s /etc/apt/sources.list.d/official-package-repositories.list" % sources_list)

# STEP 2: UPDATE APT CACHE
#-------------------------

cache = apt.Cache()
cmd = ["sudo", "/usr/sbin/synaptic", "--hide-main-window", "--update-at-startup", "--non-interactive", "--parent-window-id", "%d" % window_id]
comnd = subprocess.Popen(' '.join(cmd), shell=True)
returnCode = comnd.wait()

# STEP 3: INSTALL MINT UPDATES
#--------------------------------

dist_upgrade = True

# Reopen the cache to reflect any updates
cache.open(None)
cache.upgrade(dist_upgrade)
changes = cache.get_changes()

cmd = ["sudo", "/usr/sbin/synaptic", "--hide-main-window", "--non-interactive", "--parent-window-id", "%s" % window_id]
cmd.append("-o")
cmd.append("Synaptic::closeZvt=true")
cmd.append("--progress-str")
cmd.append("\"" + _("Please wait, this can take some time") + "\"")
cmd.append("--finish-str")
cmd.append("\"" + _("Update is complete") + "\"")
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
comnd = subprocess.Popen(' '.join(cmd), shell=True)
returnCode = comnd.wait()

# STEP 4: UPDATE GRUB
#--------------------

try:
    subprocess.call("update-grub")
except Exception as detail:
    syslog.syslog("Couldn't update grub: %s" % detail)