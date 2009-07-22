#!/usr/bin/python

import apt
import sys

try:
	cache = apt.Cache()	
	pkg = cache["mintupdate"]
	print pkg.installedVersion
except:
	pass


