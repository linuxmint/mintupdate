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

def checkDependencies(changes, cache):
	foundSomething = False
	for pkg in changes:					      							
		for dep in pkg.candidateDependencies:
			for o in dep.or_dependencies:	
				try:
					if cache[o.name].isUpgradable:
						pkgFound = False
						for pkg2 in changes:
							if o.name == pkg2.name:
								pkgFound = True														
						if pkgFound == False:
							newPkg = cache[o.name]
							changes.append(newPkg)
							foundSomething = True
				except Exception, detail:
					pass # don't know why we get these.. 
	if (foundSomething):
		changes = checkDependencies(changes, cache)
	return changes


try:
	if os.getuid() == 0 :			
		os.system("apt-get update")
	cache = apt.Cache()	
	distupgrade = commands.getoutput("cat /usr/lib/linuxmint/mintUpdate/config/distupgrade")
	distupgrade.strip()
	if (distupgrade == "yes"):
		cache.upgrade(True)
	else:
		cache.upgrade(False)
	changes = cache.getChanges()
	if (distupgrade == "yes"):
		cache.upgrade(True)
	else:
		cache.upgrade(False)
	changes = cache.getChanges()
except Exception, detail:
	print "ERROR###ERROR###ERROR###ERROR###ERROR###ERROR"	
	print detail
	raise detail	

# Add dependencies
changes = checkDependencies(changes, cache)

for pkg in changes:
	package = pkg.name				
	newVersion = pkg.candidateVersion
	oldVersion = pkg.installedVersion
	size = pkg.packageSize
	description = pkg.description
	print "UPDATE" + "###" + str(package) + "###" + str(newVersion) + "###" + str(oldVersion) + "###" + str(size) + "###" + str(description)


