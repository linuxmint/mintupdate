#!/usr/bin/python3
import os, sys

# Silly workaround because synaptic parses synaptic.conf after
# command line parameters, overwriting them
if len(sys.argv) != 2 or sys.argv[1] not in ("enable", "disable"):
    sys.exit(1)

SYNAPTIC_CONF = "/root/.synaptic/synaptic.conf"
WORKAROUND = "/root/.synaptic/synaptic-mintupdate-workaround.conf"
if sys.argv[1] == "enable" and os.path.exists(SYNAPTIC_CONF):
    os.rename(SYNAPTIC_CONF, WORKAROUND)
elif sys.argv[1] == "disable" and not os.path.exists(SYNAPTIC_CONF) and os.path.exists(WORKAROUND):
    os.rename(WORKAROUND, SYNAPTIC_CONF)
