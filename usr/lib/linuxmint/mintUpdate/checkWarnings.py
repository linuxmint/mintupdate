#!/usr/bin/python2.7

import sys
import apt_pkg


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

        apt_pkg.init()
        cache = apt_pkg.Cache(None)

        depcache = apt_pkg.DepCache(cache)
        depcache.init()

        with apt_pkg.ActionGroup(depcache):
            for package in selection:
                pkg = cache[package]
                #print "Marking : %s to install" % pkg.Name
                depcache.mark_install(pkg)

        depcache.fix_broken()

        #print "Install : %d" % depcache.inst_count
        #print "Remove : %d" % depcache.del_count

        # Get changes
        for pkg in cache.packages:
            if not depcache.marked_keep(pkg):
                if depcache.marked_install(pkg) or depcache.marked_upgrade(pkg):
                    if not pkg.name in selection:
                        if not '%s:%s' % (pkg.name, pkg.architecture) in selection:
                            if not pkg in packages_to_install:
                                packages_to_install.append(pkg)
            if depcache.marked_delete(pkg):
                    if not pkg in packages_to_remove:
                        packages_to_remove.append(pkg)
        installations = ' '.join(pkg.name for pkg in packages_to_install)
        removals = ' '.join(pkg.name for pkg in packages_to_remove)
        print "%s###%s" % (installations, removals)
except Exception, detail:
    print detail
