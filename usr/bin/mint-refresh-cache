#!/usr/bin/python3
import os
import sys

if __name__ == "__main__":
    if os.getuid() == 0:
        if len(sys.argv) > 2 and sys.argv[1] == "--use-synaptic":
            import subprocess
            subprocess.run(["/usr/sbin/synaptic", "--hide-main-window",
                            "--update-at-startup", "--non-interactive",
                            "--parent-window-id", sys.argv[2]],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                import apt
                cache = apt.Cache()
                cache.update()
            except Exception as e:
                print("Error: mint-refresh-cache could not update the cache.")
                print(e)
    else:
        print("Error: mint-refresh-cache requires admin privileges")
