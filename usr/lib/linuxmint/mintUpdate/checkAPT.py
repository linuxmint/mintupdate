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
		use_synaptic = False
		if (len(sys.argv) > 1):
			if sys.argv[1] == "--use-synaptic":
				use_synaptic = True
		
		if use_synaptic:
			from subprocess import Popen, PIPE
			cmd = ["sudo", "/usr/sbin/synaptic", "--hide-main-window", "--update-at-startup", "--non-interactive"]
			#cmd.append("--progress-str")
			#cmd.append("\"" + _("Please wait, this can take some time") + "\"")
			comnd = Popen(' '.join(cmd), shell=True)
			returnCode = comnd.wait()				
			#sts = os.waitpid(comnd.pid, 0)			
		else:			
			cache = apt.Cache()
			cache.update()

	cache = apt.Cache()
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
	cache.upgrade(dist_upgrade)		
	changes = cache.getChanges()
except Exception, detail:
	print "ERROR###ERROR###ERROR###ERROR###ERROR###ERROR"	
	print detail
	sys.exit(1)

# Add dependencies
changes = checkDependencies(changes, cache)

for pkg in changes:
	package = pkg.name				
	newVersion = pkg.candidateVersion
	oldVersion = pkg.installedVersion
	size = pkg.packageSize
	description = pkg.description
	print "UPDATE" + "###" + str(package) + "###" + str(newVersion) + "###" + str(oldVersion) + "###" + str(size) + "###" + str(description)


