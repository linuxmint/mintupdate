#!/usr/bin/env python

import sys
import apt

try:
    sys.path.append('/usr/lib/linuxmint/common')
    from configobj import ConfigObj
    config = ConfigObj("/etc/linuxmint/mintUpdate.conf")
    try:
        if (config['update']['dist_upgrade'] == "True"):
            dist_upgrade = True
        else:
            dist_upgrade = False
    except:
        dist_upgrade = True
    
    if dist_upgrade:          
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
