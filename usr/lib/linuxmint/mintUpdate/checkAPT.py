#!/usr/bin/python3

import os
import sys
import apt

from gi.repository import Gio, Gtk

from aptdaemon import client
from aptdaemon.errors import NotAuthorizedError, TransactionFailed
from aptdaemon.gtk3widgets import AptErrorDialog, AptProgressDialog

try:
    cache = apt.Cache()

    if os.getuid() == 0 :
        use_synaptic = False
        if (len(sys.argv) > 1):
            if sys.argv[1] == "--use-synaptic":
                use_synaptic = True

        if use_synaptic:
            window_id = int(sys.argv[2])
            from subprocess import Popen, PIPE
            cmd = ["sudo", "/usr/sbin/synaptic", "--hide-main-window", "--update-at-startup", "--non-interactive", "--parent-window-id", "%d" % window_id]
            #cmd.append("--progress-str")
            #cmd.append("\"" + _("Please wait, this can take some time") + "\"")
            comnd = Popen(' '.join(cmd), shell=True)
            returnCode = comnd.wait()
            #sts = os.waitpid(comnd.pid, 0)
        else:
            cache.update()

    settings = Gio.Settings("com.linuxmint.updates")
    dist_upgrade = settings.get_boolean("dist-upgrade")
    kernel_updates = settings.get_boolean("kernel-updates-are-visible")

    # Reopen the cache to reflect any updates
    cache.open(None)
    cache.upgrade(dist_upgrade)
    changes = cache.get_changes()

    for pkg in changes:
        if (pkg.is_installed and pkg.marked_upgrade):
            package = pkg.name
            newVersion = pkg.candidate.version
            oldVersion = pkg.installed.version
            size = pkg.candidate.size
            sourcePackage = pkg.candidate.source_name
            short_description = pkg.candidate.raw_description
            description = pkg.candidate.description
            if (newVersion != oldVersion):
                update_type = "package"
                update_origin = "linuxmint"
                for origin in pkg.candidate.origins:
                    if origin.origin == "Ubuntu":
                        update_origin = "ubuntu"
                    elif origin.origin == "Debian":
                        update_origin = "debian"
                    elif origin.origin.startswith("LP-PPA"):
                        update_origin = origin.origin
                    if origin.origin == "Ubuntu" and '-security' in origin.archive:
                        update_type = "security"
                        break
                    if origin.origin == "Debian" and '-Security' in origin.label:
                        update_type = "security"
                        break
                    if origin.origin == "linuxmint":
                        if origin.component == "romeo":
                            update_type = "unstable"
                            break
                        else:
                            update_type = "linuxmint"

                resultString = u"UPDATE###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % (package, newVersion, oldVersion, size, sourcePackage, update_type, update_origin, short_description, description)
                print(resultString.encode('ascii', 'xmlcharrefreplace'))

    if kernel_updates:
        if 'linux-image-generic' in cache:
            versions = cache['linux-image-generic'].candidate.version.split(".")
            if len(versions) > 3:
                version = "%s.%s.%s-%s" % (versions[0], versions[1], versions[2], versions[3])
                for pkgname in ['linux-headers-VERSION', 'linux-headers-VERSION-generic', 'linux-image-VERSION-generic', 'linux-image-extra-VERSION-generic']:
                    pkgname = pkgname.replace('VERSION', version)
                    if pkgname in cache:
                        pkg = cache[pkgname]
                        if not pkg.is_installed:
                            resultString = u"UPDATE###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % (pkgname, pkg.candidate.version, "", pkg.candidate.size, "linux-kernel", "kernel", "ubuntu", pkg.candidate.raw_description, pkg.candidate.description)
                            print(resultString.encode('ascii', 'xmlcharrefreplace'))

except Exception as error:
    print("CHECK_APT_ERROR---EOL---")
    print(sys.exc_info()[0])
    print(error)
    sys.exit(1)
