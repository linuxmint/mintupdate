#!/usr/bin/python3

import subprocess, sys

if len(sys.argv) != 3:
    print("Missing arguments!")
    sys.exit(1)

codename = sys.argv[1]
window_id = sys.argv[2]
subprocess.run(["/usr/lib/linuxmint/mintUpdate/rel_upgrade_root.py", codename, window_id])
