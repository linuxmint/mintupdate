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
        
        cache = apt.Cache(None)
        
        for package in selection:
            pkg = cache[package]
            #print "Marking : %s to install" % pkg.Name
            pkg.mark_upgrade()
        
        #print "Install : %d" % cache.inst_count
        #print "Remove : %d" % cache.delete_count
        
        # Get changes
        for pkg in cache:
            if not pkg.marked_keep:
                if pkg.marked_install:
                    if not pkg.name in selection:
                        if not '%s:%s' % (pkg.name, pkg.architecture) in selection:
                            if not pkg in packages_to_install:
                                packages_to_install.append(pkg)
            if pkg.marked_delete:
                    if not pkg in packages_to_remove:
                        packages_to_remove.append(pkg)                   
        installations = ' '.join(pkg.name for pkg in packages_to_install)
        removals = ' '.join(pkg.name for pkg in packages_to_remove)
        print "%s###%s" % (installations, removals)
except Exception, detail:    
    print detail
