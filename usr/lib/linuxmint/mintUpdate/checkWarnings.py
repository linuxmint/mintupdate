#!/usr/bin/env python

import os
import commands
import sys
import string
import gtk
import gtk.glade
import gobject
import tempfile
import threading
import time
import gettext
import apt
from user import home

def checkDependencies(packages, cache):
    foundSomething = False
    for pkg in packages:
        for dep in pkg.candidate.dependencies:
            for o in dep.or_dependencies:
                try:                    
                    if cache[o.name].is_upgradable:
                        pkgFound = False
                        for pkg2 in packages:
                            if o.name == pkg2.name:
                                pkgFound = True
                        if pkgFound == False:                            
                            newPkg = cache[o.name]
                            packages.append(newPkg)                            
                            foundSomething = True
                except Exception, detail:
                    pass # don't know why we get these..
    if (foundSomething):
        packages = checkDependencies(packages, cache)
    return packages

try:      
    selection = sys.argv[1:]
    #print ' '.join(str(pkg) for pkg in selection)    
    cache = apt.Cache()
    packages = []
    packages_to_install = []
    packages_to_remove = []
    for pkgname in selection:
        packages.append(cache[pkgname])
    packages = checkDependencies(packages, cache)    
    for pkg in packages:        
        if (not pkg.is_installed):
            packages_to_install.append(pkg)
        else:
            newVersion = pkg.candidate.version
            oldVersion = pkg.installed.version
            if (newVersion == oldVersion):
                packages_to_remove.append(pkg)
    installations = ' '.join(pkg.name for pkg in packages_to_install)
    removals = ' '.join(pkg.name for pkg in packages_to_remove)
    print "%s###%s" % (installations, removals)
except Exception, detail:    
    print detail
