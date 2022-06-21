#!/bin/sh

if [ ! -e "/var/lib/linuxmint/mintupdate-automatic-removals-enabled" ]
then
    exit
fi

ln -s /usr/share/linuxmint/mintupdate/automation/99-mintupdate-temporary.pkla /etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla
echo "\n-- Automatic Removal $(date):\n" >> /var/log/mintupdate.log
systemd-inhibit --why="Performing autoremoval" --who="Update Manager" --what=shutdown --mode=block /usr/bin/apt-get autoremove --purge -y >> /var/log/mintupdate.log 2>&1
rm -f /etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla
