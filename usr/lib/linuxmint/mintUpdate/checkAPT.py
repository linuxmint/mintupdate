#!/usr/bin/python3

import os
import sys
import apt
import gi
import platform

from gi.repository import Gio

KERNEL_PKG_NAMES = ['linux-headers-VERSION', 'linux-headers-VERSION-generic', 'linux-image-VERSION-generic', 'linux-modules-VERSION-generic', 'linux-modules-extra-VERSION-generic']
KERNEL_PKG_NAMES.append('linux-image-extra-VERSION-generic') # Naming convention in 16.04, until 4.15 series

class KernelVersion():

    def __init__(self, version):
        self.version = version
        version_array = self.version.replace("-", ".").split(".")
        self.numeric_versions = []
        for i in range(4):
            element = version_array[i]
            if len(element) == 1:
                element = "00%s" % element
            elif len(element) == 2:
                element = "0%s" % element
            self.numeric_versions.append(element)
        self.numeric_representation = ".".join(self.numeric_versions)
        self.std_version = "%s.%s.%s-%s" % (version_array[0], version_array[1], version_array[2], version_array[3])
        self.series = "%s.%s.%s" % (version_array[0], version_array[1], version_array[2])

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
                    update_site = origin.site
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

                resultString = u"UPDATE###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % (package, newVersion, oldVersion, size, sourcePackage, update_type, update_origin, short_description, description, update_site)
                print(resultString.encode('ascii', 'xmlcharrefreplace'))

    try:
        if kernel_updates:

            # Get the uname version
            uname_kernel = KernelVersion(platform.release())

            # Get the recommended version
            if 'linux-image-generic' in cache:
                recommended_kernel = KernelVersion(cache['linux-image-generic'].candidate.version)
                if (uname_kernel.numeric_representation <= recommended_kernel.numeric_representation):
                    for pkgname in KERNEL_PKG_NAMES:
                        pkgname = pkgname.replace('VERSION', recommended_kernel.std_version)
                        if pkgname in cache:
                            pkg = cache[pkgname]
                            if not pkg.is_installed:
                                resultString = u"UPDATE###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % (pkgname, pkg.candidate.version, "", pkg.candidate.size, "linux", "kernel", "ubuntu", pkg.candidate.raw_description, pkg.candidate.description, "security.ubuntu.com")
                                print(resultString.encode('ascii', 'xmlcharrefreplace'))
                else:
                    # We're using a series which is more recent than the recommended one, so we should recommend the latest kernel in that series
                    max_kernel = uname_kernel
                    for pkg in cache:
                        package_name = pkg.name
                        if (package_name.startswith("linux-image-3") or package_name.startswith("linux-image-4")) and package_name.endswith("-generic"):
                            version = package_name.replace("linux-image-", "").replace("-generic", "")
                            kernel = KernelVersion(version)
                            if kernel.numeric_representation > max_kernel.numeric_representation and kernel.series == max_kernel.series:
                                max_kernel = kernel
                    if max_kernel.numeric_representation != uname_kernel.numeric_representation:
                        for pkgname in KERNEL_PKG_NAMES:
                            pkgname = pkgname.replace('VERSION', max_kernel.std_version)
                            if pkgname in cache:
                                pkg = cache[pkgname]
                                if not pkg.is_installed:
                                    resultString = u"UPDATE###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % (pkgname, pkg.candidate.version, "", pkg.candidate.size, "linux", "kernel", "ubuntu", pkg.candidate.raw_description, pkg.candidate.description, "security.ubuntu.com")
                                    print(resultString.encode('ascii', 'xmlcharrefreplace'))

    except Exception as e:
        pass

except Exception as error:
    print("CHECK_APT_ERROR---EOL---")
    print(sys.exc_info()[0])
    print(error)
    sys.exit(1)
