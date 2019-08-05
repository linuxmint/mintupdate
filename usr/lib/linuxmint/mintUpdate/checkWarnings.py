#!/usr/bin/python3

import sys
import apt_pkg

try:
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
        if (not depcache.marked_keep(pkg) and
            (depcache.marked_install(pkg) or depcache.marked_upgrade(pkg)) and
            not pkg.name in selection and
            not "%s:%s" % (pkg.name, pkg.architecture) in selection and
            not pkg.name in packages_to_install
            ):
            packages_to_install.append(pkg.name)
        if depcache.marked_delete(pkg) and not pkg.name in packages_to_remove:
            packages_to_remove.append(pkg.name)
    installations = ' '.join(packages_to_install)
    removals = ' '.join(packages_to_remove)
    print("%s###%s" % (installations, removals))
except Exception as e:
    print(e)
    print(sys.exc_info()[0])
