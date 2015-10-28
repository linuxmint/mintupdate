#!/usr/bin/python2.7

import os
import commands
import apt
import sys

try:
    current_version = commands.getoutput("uname -r").replace("-generic", "")

    cache = apt.Cache()

    recommended_kernel = None
    if 'linux-kernel-generic' in cache:
        recommended_kernel = cache['linux-kernel-generic'].candidate.version

    for pkg in cache:
        installed = 0
        used = 0
        recommended = 0
        installable = 0
        pkg_version = ""
        package = pkg.name
        if (package.startswith("linux-image-3") or package.startswith("linux-image-4")) and package.endswith("-generic"):
            version = package.replace("linux-image-", "").replace("-generic", "")
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
                recommended = 1

            resultString = u"KERNEL###%s###%s###%s###%s###%s###%s" % (version, pkg_version, installed, used, recommended, installable)
            print resultString.encode('ascii', 'xmlcharrefreplace');

except Exception, detail:
    print "ERROR###ERROR###ERROR###ERROR"
    print detail
    sys.exit(1)
