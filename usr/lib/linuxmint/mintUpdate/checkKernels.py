#!/usr/bin/python3

import os
import subprocess
import apt
import sys
from Classes import KernelType

try:
    current_version = subprocess.check_output("uname -r", shell = True).decode("utf-8").replace("-" + KernelType().kernel_type, "").strip()

    cache = apt.Cache()

    recommended_kernel = None
    if 'linux-kernel-' + KernelType().kernel_type in cache:
        recommended_kernel = cache['linux-kernel-' + KernelType().kernel_type].candidate.version
    recommended_image = None
    if 'linux-image-' + KernelType().kernel_type in cache:
        try:
            recommended_image = cache['linux-image-' + KernelType().kernel_type].candidate.version
            versions = recommended_image.split(".")
            recommended_image = "%s.%s.%s-%s" % (versions[0], versions[1], versions[2], versions[3])
        except:
            pass #best effort

    for pkg in cache:
        installed = 0
        used = 0
        recommended_stability = 0
        recommended_security = 0
        installable = 0
        pkg_version = ""
        package = pkg.name
        if (package.startswith("linux-image-3") or package.startswith("linux-image-4")) and package.endswith("-" + KernelType().kernel_type):
            version = package.replace("linux-image-", "").replace("-" + KernelType().kernel_type, "")
            if pkg.is_installed:
                installed = 1
                pkg_version = pkg.installed.version
            else:
                if pkg.candidate and pkg.candidate.downloadable:
                    installable = 1
                    pkg_version = pkg.candidate.version
            if version == current_version:
                used = 1
            if recommended_kernel is not None and version in recommended_kernel:
                recommended_stability = 1
            if recommended_image is not None and recommended_image in version:
                recommended_security = 1

            # provide a representation of the version which helps sorting the kernels
            version_array = pkg_version.replace("-", ".").split(".")
            versions = []
            for element in version_array:
                if len(element) == 1:
                    element = "00%s" % element
                elif len(element) == 2:
                    element = "0%s" % element
                versions.append(element)

            resultString = "KERNEL###%s###%s###%s###%s###%s###%s###%s###%s" % (".".join(versions), version, pkg_version, installed, used, recommended_stability, recommended_security, installable)
            print(resultString.encode("utf-8").decode('ascii', 'xmlcharrefreplace'))

except:
    print("ERROR###ERROR###ERROR###ERROR")
    print(sys.exc_info()[0])
    sys.exit(1)
