#!/usr/bin/python3

import os
import subprocess
import apt
import sys

from gi.repository import Gio

try:
    current_version = subprocess.check_output("uname -r", shell = True).decode("utf-8").replace("-generic", "").strip()

    settings = Gio.Settings("com.linuxmint.updates")
    kernel_type = "-generic"
    if settings.get_boolean("use-lowlatency-kernels"):
        kernel_type = "-lowlatency"
    cache = apt.Cache()
    for pkg in cache:
        installed = 0
        used = 0
        installable = 0
        pkg_version = ""
        package = pkg.name
        if (package.startswith("linux-image-3") or package.startswith("linux-image-4")) and package.endswith(kernel_type):
            version = package.replace("linux-image-", "").replace("-generic", "").replace("-lowlatency", "")
            if pkg.is_installed:
                installed = 1
                pkg_version = pkg.installed.version
            else:
                if pkg.candidate and pkg.candidate.downloadable:
                    installable = 1
                    pkg_version = pkg.candidate.version
            if version == current_version:
                used = 1

            # provide a representation of the version which helps sorting the kernels
            version_array = pkg_version.replace("-", ".").split(".")
            versions = []
            for element in version_array:
                if len(element) == 1:
                    element = "00%s" % element
                elif len(element) == 2:
                    element = "0%s" % element
                versions.append(element)

            resultString = "KERNEL###%s###%s###%s###%s###%s###%s" % (".".join(versions), version, pkg_version, installed, used, installable)
            print(resultString.encode("utf-8").decode('ascii', 'xmlcharrefreplace'))

except:
    print("ERROR###ERROR###ERROR###ERROR")
    print(sys.exc_info()[0])
    sys.exit(1)
