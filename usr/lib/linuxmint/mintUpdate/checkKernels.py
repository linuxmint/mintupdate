#!/usr/bin/python3
import subprocess
import apt
import sys
import re

from gi.repository import Gio

try:
    settings = Gio.Settings("com.linuxmint.updates")
    if settings.get_boolean("use-lowlatency-kernels"):
        kernel_type = "-lowlatency"
    else:
        kernel_type = "-generic"
    current_version = subprocess.check_output("uname -r", shell = True).decode("utf-8").replace(kernel_type, "").strip()
    cache = apt.Cache()
    signed_kernels = ['']
    r = re.compile(r'^(?:linux-image-)(?:unsigned-)?(\d.+?)' + kernel_type + '$')
    for pkg in cache:
        installed = 0
        used = 0
        installable = 0
        pkg_version = ""
        package = pkg.name
        if r.match(package):
            if not pkg.candidate:
                continue
            version = r.sub(r'\1', package)
            # filter duplicates (unsigned kernels where signed exists)
            if version in signed_kernels:
                continue
            if pkg.is_installed:
                installed = 1
                pkg_version = pkg.installed.version
            else:
                if pkg.candidate.downloadable:
                    installable = 1
                    pkg_version = pkg.candidate.version
            if version == current_version:
                used = 1
            if not pkg.candidate.origins[0].origin:
                origin = 0
            elif pkg.candidate.origins[0].origin == 'Ubuntu':
                origin = 1
            else:
                origin = 2

            # provide a representation of the version which helps sorting the kernels
            version_array = pkg_version.replace("-", ".").split(".")
            versions = []
            for element in version_array:
                if len(element) == 1:
                    element = "00%s" % element
                elif len(element) == 2:
                    element = "0%s" % element
                versions.append(element)

            signed_kernels.append(version)

            archive = pkg.candidate.origins[0].archive

            # get support duration
            if pkg.candidate.record.has_key("Supported") and pkg.candidate.record["Supported"]:
                if pkg.candidate.record["Supported"].endswith("y"):
                    # override support duration for HWE kernels in LTS releases,
                    # these will be handled by the kernel window
                    if "-hwe" in pkg.candidate.source_name:
                        support_duration = -1
                    else:
                        support_duration = int(pkg.candidate.record["Supported"][:-1]) * 12
                elif pkg.candidate.record["Supported"].endswith("m"):
                    support_duration = int(pkg.candidate.record["Supported"][:-1])
                else:
                    # unexpected support tag
                    support_duration = 0
            else:
                # unsupported
                support_duration = 0

            resultString = "KERNEL###%s###%s###%s###%s###%s###%s###%s###%s###%s" % \
                (".".join(versions), version, pkg_version, installed, used, installable, origin, archive, support_duration)
            print(resultString.encode("utf-8").decode('ascii', 'xmlcharrefreplace'))

except:
    print("ERROR###ERROR###ERROR###ERROR")
    print(sys.exc_info()[0])
    sys.exit(1)
