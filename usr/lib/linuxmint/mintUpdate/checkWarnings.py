#!/usr/bin/env python

import sys
import apt

try:      
    selection = sys.argv[1:]
    #print ' '.join(str(pkg) for pkg in selection)    
    packages_to_install = []
    packages_to_remove = []    
    cache = apt.Cache()            
    for package in selection: 
        pkg = cache[package]
        pkg.mark_upgrade()
        changes = cache.get_changes()
        for pkg in changes:
            if (pkg.is_installed):
                if (pkg.candidate.version == pkg.installed.version):
                    if not pkg in packages_to_remove:
                        packages_to_remove.append(pkg)
            else:
                if not pkg in packages_to_install:
                    packages_to_install.append(pkg)

    installations = ' '.join(pkg.name for pkg in packages_to_install)
    removals = ' '.join(pkg.name for pkg in packages_to_remove)
    print "%s###%s" % (installations, removals)
except Exception, detail:    
    print detail
