The goal of this project is to create a version of mintupdate.py that has an option of running it without user interaction.
This is useful when managing a farm of LM systems. The initial objective is to implement a "no-interactive" option that
allows mintupdate.py to run unattended on the farm systems. The first use of this feature is to employ mintupdate.py
using the Salt distributed managment application. The Salt master will distribute commands that use mintupdate.py to upgrade
the operating system of each farm system (called in Salt a minion) to a specific version.
