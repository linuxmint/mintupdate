#!/usr/bin/env python

import os
import commands
import apt

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
        package = pkg.name
        if package.startswith("linux-image-3") and package.endswith("-generic"):
            version = package.replace("linux-image-", "").replace("-generic", "")            
            if pkg.is_installed:
                installed = 1
            if version == current_version:
                used = 1            
            if recommended_kernel is not None and version in recommended_kernel:
                recommended = 1                    

            resultString = u"KERNEL###%s###%s###%s###%s" % (version, installed, used, recommended)
            print resultString.encode('ascii', 'xmlcharrefreplace');
    
except Exception, detail:
    print "ERROR###ERROR###ERROR###ERROR"
    print detail
    sys.exit(1)


