#!/usr/bin/python3
import apt
import sys
import os
import re
from Classes import CONFIGURED_KERNEL_TYPE, SUPPORTED_KERNEL_TYPES

try:
    current_version = os.uname().release
    cache = apt.Cache()
    signed_kernels = ['']
    local_kernels = {}
    r = re.compile(r'^(?:linux-image-)(?:unsigned-)?(\d.+?)(%s)$' % "|".join(SUPPORTED_KERNEL_TYPES))
    for pkg in cache:
        installed = 0
        used = 0
        installable = 0
        pkg_version = ""
        pkg_match = r.match(pkg.name)
        if pkg_match:
            pkg_data = None
            if pkg.candidate:
                pkg_data = pkg.candidate
            elif pkg.installed:
                pkg_data = pkg.installed
            else:
                continue
            version = pkg_match.group(1)
            kernel_type = pkg_match.group(2)
            full_version = version + kernel_type
            if pkg.is_installed:
                installed = 1
                pkg_version = pkg.installed.version
            else:
                # only offer to install same-type kernels
                if not kernel_type == CONFIGURED_KERNEL_TYPE:
                    continue
                if pkg.candidate and pkg.candidate.downloadable:
                    installable = 1
                    pkg_version = pkg.candidate.version
            # filter duplicates (unsigned kernels where signed exists)
            if full_version in signed_kernels:
                continue
            signed_kernels.append(full_version)
            if full_version == current_version:
                used = 1
            if not pkg_data.origins[0].origin:
                origin = 0
            elif pkg_data.origins[0].origin == 'Ubuntu':
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

            archive = pkg_data.origins[0].archive

            # get support duration
            if pkg_data.record.has_key("Supported") and pkg_data.record["Supported"]:
                if pkg_data.record["Supported"].endswith("y"):
                    # override support duration for HWE kernels in LTS releases,
                    # these will be handled by the kernel window
                    if "-hwe" in pkg_data.source_name:
                        support_duration = -1
                    else:
                        support_duration = int(pkg_data.record["Supported"][:-1]) * 12
                elif pkg_data.record["Supported"].endswith("m"):
                    support_duration = int(pkg_data.record["Supported"][:-1])
                else:
                    # unexpected support tag
                    support_duration = 0
            else:
                # unsupported
                support_duration = 0

            resultString = "KERNEL###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s" % \
                (".".join(versions), version, pkg_version, installed, used, installable,
                    origin, archive, support_duration, kernel_type)
            print(resultString.encode("utf-8").decode('ascii', 'xmlcharrefreplace'))

except Exception as e:
    print("ERROR###ERROR###ERROR###ERROR")
    print("%s: %s\n" % (e.__class__.__name__, e), file=sys.stderr)
    sys.exit(1)
