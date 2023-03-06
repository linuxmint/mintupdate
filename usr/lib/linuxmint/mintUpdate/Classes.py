#!/usr/bin/python3

import gi
from gi.repository import Gio, GLib

import datetime
import gettext
import html
import json
import os
import subprocess
import time
import re

gettext.install("mintupdate", "/usr/share/locale")

# These updates take priority over other updates.
# If a new version of these packages is available, nothing else is listed.
PRIORITY_UPDATES = ['mintupdate', 'mint-upgrade-info']

settings = Gio.Settings(schema_id="com.linuxmint.updates")

SUPPORTED_KERNEL_TYPES = ["-generic", "-lowlatency", "-aws", "-azure", "-gcp", "-kvm", "-oem", "-oracle"]
KERNEL_PKG_NAMES = ['linux-headers-VERSION', 'linux-headers-VERSION-KERNELTYPE', 'linux-image-VERSION-KERNELTYPE', \
    'linux-modules-VERSION-KERNELTYPE', 'linux-modules-extra-VERSION-KERNELTYPE']
KERNEL_PKG_NAMES.append('linux-image-extra-VERSION-KERNELTYPE') # Naming convention in 16.04, until 4.15 series

CONFIGURED_KERNEL_TYPE = settings.get_string("selected-kernel-type")
if CONFIGURED_KERNEL_TYPE not in SUPPORTED_KERNEL_TYPES:
    CONFIGURED_KERNEL_TYPE = "-generic"

CONFIG_PATH = os.path.expanduser("~/.linuxmint/mintupdate")

def get_release_dates():
    """ Get distro release dates for support duration calculation """
    release_dates = {}
    distro_info = []
    if os.path.isfile("/usr/share/distro-info/ubuntu.csv"):
        distro_info += open("/usr/share/distro-info/ubuntu.csv", "r").readlines()
    if os.path.isfile("/usr/share/distro-info/debian.csv"):
        distro_info += open("/usr/share/distro-info/debian.csv", "r").readlines()
    if distro_info:
        for distro in distro_info[1:]:
            try:
                distro = distro.split(",")
                release_date = time.mktime(time.strptime(distro[4], '%Y-%m-%d'))
                release_date = datetime.datetime.fromtimestamp(release_date)
                support_end = time.mktime(time.strptime(distro[5].rstrip(), '%Y-%m-%d'))
                support_end = datetime.datetime.fromtimestamp(support_end)
                release_dates[distro[2]] = [release_date, support_end]
            except:
                pass
    return release_dates

class KernelVersion():

    def __init__(self, version):
        field_length = 3
        self.version = version
        self.version_id = []
        version_id = self.version.replace("-", ".").split(".")
        # Check if mainline rc kernel to ensure proper sorting vs mainline release kernels
        suffix = next((x for x in version_id if x.startswith("rc")), None)
        if not suffix:
            suffix = "z"
        # Copy numeric parts from version_id to self.version_id and fill up to field_length
        for element in version_id:
            if element.isnumeric():
                self.version_id.append("0" * (field_length - len(element)) + element)
        # Installed kernels always have len(self.version_id) >= 4 at this point,
        # create missing parts for not installed mainline kernels:
        while len(self.version_id) < 3:
            self.version_id.append("0" * field_length)
        if len(self.version_id) == 3:
            self.version_id.append("%s%s" % (''.join((x[:field_length - 2].lstrip('0') + x[field_length - 2:] for x in self.version_id)), suffix))
        elif len(self.version_id[3]) == 6:
            # installed release mainline kernel, add suffix for sorting
            self.version_id[3] += suffix
        self.series = tuple(self.version_id[:3])
        self.shortseries = tuple(self.version_id[:2])

class Update():

    def __init__(self, package=None, input_string=None, source_name=None):
        self.package_names = []
        if package is not None:
            self.package_names.append(package.name)
            self.source_packages = {"%s=%s" % (package.candidate.source_name, package.candidate.source_version)}
            self.main_package_name = package.name
            self.package_name = package.name
            self.new_version = package.candidate.version
            if package.installed is None:
                self.old_version = ""
            else:
                self.old_version = package.installed.version
            self.size = package.candidate.size
            self.real_source_name = package.candidate.source_name
            if source_name is not None:
                self.source_name = source_name
            else:
                self.source_name = self.real_source_name
            self.display_name = self.source_name
            self.short_description = package.candidate.raw_description
            self.description = package.candidate.description
            self.archive = ""
            if (self.new_version != self.old_version):
                self.type = "package"
                self.origin = ""
                for origin in package.candidate.origins:
                    self.origin = origin.origin
                    self.site = origin.site
                    self.archive = origin.archive
                    if origin.origin == "Ubuntu":
                        self.origin = "ubuntu"
                    elif origin.origin == "Debian":
                        self.origin = "debian"
                    elif origin.origin.startswith("LP-PPA"):
                        self.origin = origin.origin
                    if origin.origin == "Ubuntu" and '-security' in origin.archive:
                        self.type = "security"
                        break
                    if origin.origin == "Debian" and '-Security' in origin.label:
                        self.type = "security"
                        break
                    if source_name in ["firefox", "thunderbird", "chromium"]:
                        self.type = "security"
                        break
                    if origin.origin == "linuxmint":
                        if origin.component == "romeo":
                            self.type = "unstable"
                            break
                if package.candidate.section == "kernel" or self.package_name.startswith("linux-headers") or self.real_source_name in ["linux", "linux-kernel", "linux-signed", "linux-meta"]:
                    self.type = "kernel"
        else:
            # Build the class from the input_string
            self.parse(input_string)

    def add_package(self, pkg):
        self.package_names.append(pkg.name)
        self.source_packages.add("%s=%s" % (pkg.candidate.source_name, pkg.candidate.source_version))
        self.size += pkg.candidate.size
        if self.main_package_name is None or pkg.name == self.source_name:
            self.overwrite_main_package(pkg)
            return

        if self.main_package_name != self.source_name:
            # Overwrite dev, dbg, common, arch packages
            for suffix in ["-dev", "-dbg", "-common", "-core", "-data", "-doc", ":i386", ":amd64"]:
                if (self.main_package_name.endswith(suffix) and not pkg.name.endswith(suffix)):
                    self.overwrite_main_package(pkg)
                    return
            # Overwrite lib packages
            for prefix in ["lib", "gir1.2"]:
                if (self.main_package_name.startswith(prefix) and not pkg.name.startswith(prefix)):
                    self.overwrite_main_package(pkg)
                    return
            for keyword in ["-locale-", "-l10n-", "-help-"]:
                if (keyword in self.main_package_name) and (keyword not in pkg.name):
                    self.overwrite_main_package(pkg)
                    return

    def overwrite_main_package(self, pkg):
        self.description = pkg.candidate.description
        self.short_description = pkg.candidate.raw_description
        self.main_package_name = pkg.name

    def serialize(self):
        output_string = u"###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % \
        (self.display_name, self.source_name, self.real_source_name, ", ".join(self.source_packages),
         self.main_package_name, ", ".join(self.package_names), self.new_version,
         self.old_version, self.size, self.type, self.origin,
         self.short_description, self.description, self.site, self.archive)
        print(output_string.encode('ascii', 'xmlcharrefreplace'))

    def parse(self, input_string):
        try:
            input_string = html.unescape(input_string)
        except:
            pass
        values = input_string.split("###")[1:]
        (self.display_name, self.source_name, self.real_source_name, source_packages,
         self.main_package_name, package_names, self.new_version,
         self.old_version, size, self.type, self.origin, self.short_description,
         self.description, self.site, self.archive) = values
        self.size = int(size)
        self.package_names = package_names.split(", ")
        self.source_packages = source_packages.split(", ")

class Alias():
    def __init__(self, name, short_description, description):

        name = name.strip()
        short_description = short_description.strip()
        description = description.strip()

        if (name.startswith('_("') and name.endswith('")')):
            name = _(name[3:-2])
        if (short_description.startswith('_("') and short_description.endswith('")')):
            short_description = _(short_description[3:-2])
        if (description.startswith('_("') and description.endswith('")')):
            description = _(description[3:-2])

        self.name = name
        self.short_description = short_description
        self.description = description

class UpdateTracker():

    # Loads past updates from JSON file
    def __init__(self, settings, logger):
        os.system("mkdir -p %s" % CONFIG_PATH)
        self.path = os.path.join(CONFIG_PATH, "updates.json")

        # Test case
        self.test_mode = False
        test_path = "/usr/share/linuxmint/mintupdate/tests/%s.json" % os.getenv("MINTUPDATE_TEST")
        if os.path.exists(test_path):
            os.system("mkdir -p %s" % CONFIG_PATH)
            os.system("cp %s %s" % (test_path, self.path))
            self.test_mode = True

        self.tracker_version = 1 # version of the data structure
        self.settings = settings
        self.tracked_updates = {}
        self.refreshed_update_names = [] # updates which are seen in checkAPT
        self.today = datetime.date.today().strftime("%Y.%m.%d")
        self.max_days = 0 # oldest update (in number of days seen)
        self.oldest_since_date = self.today # oldest update (according to since date)
        self.active = True # False if the tracking was already done today
        self.security_only = self.settings.get_boolean("tracker-security-only")
        self.logger = logger

        try:
            with open(self.path) as f:
                self.tracked_updates = json.load(f)
                if self.tracked_updates['version'] < self.tracker_version:
                    raise Exception()
                if self.tracked_updates['checked'] > self.today:
                    raise Exception()
                if self.tracked_updates['notified'] > self.today:
                    raise Exception()
                if self.tracked_updates['checked'] == self.today:
                    # We already tracked updates today
                    self.active = False
        except Exception as e:
            self.logger.write("Tracker exception: " + str(e))
            self.tracked_updates['updates'] = {}
            self.tracked_updates['version'] = self.tracker_version
            self.tracked_updates['checked'] = self.today
            self.tracked_updates['notified'] = self.today

    # Updates the record for a particular update
    def update(self, update):
        self.refreshed_update_names.append(update.real_source_name)
        if update.real_source_name not in self.tracked_updates['updates']:
            update_record = {}
            update_record['type'] = update.type
            update_record['since'] = self.today
            update_record['days'] = 1
            self.tracked_updates['updates'][update.real_source_name] = update_record
        else:
            update_record = self.tracked_updates['updates'][update.real_source_name]
            update_record['type'] = update.type
            if self.today > self.tracked_updates['checked']:
                update_record['days'] += 1

        if update.type in ["security", "kernel"] or (not self.security_only):
            if self.max_days < update_record['days']:
                self.max_days = update_record['days']
            if self.oldest_since_date > update_record['since']:
                self.oldest_since_date = update_record['since']

    # Returns the number of days between today and the given date string
    def get_days_since_date(self, string, date_format):
        if string == None:
            return 999
        datetime_object = datetime.datetime.strptime(string, date_format)
        days = (datetime.date.today() - datetime_object.date()).days
        return days

    # Returns the number of days between today and the given timestamp
    def get_days_since_timestamp(self, timestamp):
        if timestamp == 0:
            return 999
        datetime_object = datetime.datetime.fromtimestamp(timestamp)
        days = (datetime.date.today() - datetime_object.date()).days
        return days

    def get_latest_apt_upgrade(self):
        latest_upgrade_date = None

        if os.path.exists("/var/log/apt/history.log"):
            logs = subprocess.getoutput("cat /var/log/apt/history.log")
            for event in logs.split("\n\n"):
                if "Upgrade: " not in event:
                    continue
                end_date = None
                upgrade = None
                for line in event.split("\n"):
                    line = line.strip()
                    if line.startswith("End-Date: "):
                        end_date = line.replace("End-Date: ", "")
                        end_date = end_date.split()[0]
                if end_date != None and (latest_upgrade_date == None or end_date > latest_upgrade_date):
                    latest_upgrade_date = end_date

        if latest_upgrade_date == None:
            try:
                logs = subprocess.getoutput("zcat /var/log/apt/history.log*gz")
                for event in logs.split("\n\n"):
                    if "Upgrade: " not in event:
                        continue
                    end_date = None
                    upgrade = None
                    for line in event.split("\n"):
                        line = line.strip()
                        if line.startswith("End-Date: "):
                            end_date = line.replace("End-Date: ", "")
                            end_date = end_date.split()[0]
                    if end_date != None and (latest_upgrade_date == None or end_date > latest_upgrade_date):
                        latest_upgrade_date = end_date
            except Exception as e:
                print("Failed to check compressed APT logs", e)

        return latest_upgrade_date

    # Returns true if a notification is required and updates the tracker
    # with the new notification date
    def notify(self):
        # Check notification enabled
        if self.settings.get_boolean("tracker-disable-notifications"):
            return False

        # Check notification age
        notified_age = self.get_days_since_date(self.tracked_updates['notified'], '%Y.%m.%d')
        if notified_age < self.settings.get_int("tracker-days-between-notifications"):
            self.logger.write("Tracker: Notification age is too small: %d days" % notified_age)
            return False

        notification_needed = False

        # Check maximum logged-in days
        if self.max_days >= self.settings.get_int("tracker-max-days"):
            self.logger.write("Tracker: Max days reached: %d days" % self.max_days)
            notification_needed = True
        else:
            max_age = self.get_days_since_date(self.oldest_since_date, '%Y.%m.%d')
            # Check maximum update age
            if max_age >= self.settings.get_int("tracker-max-age"):
                self.logger.write("Tracker: Max age reached: %d days" % max_age)
                notification_needed = True

        if not self.test_mode:
            # Check last time install button was pressed
            last_install_age = self.get_days_since_timestamp(self.settings.get_int("install-last-run"))
            if last_install_age <= self.settings.get_int("tracker-grace-period"):
                self.logger.write("Tracker: Mintupdate update button was pressed recently: %d days ago" % last_install_age)
                notification_needed = False
            else:
                # Check last time APT upgraded a package
                last_apt_upgrade = self.get_latest_apt_upgrade()
                last_apt_upgrade_age = self.get_days_since_date(last_apt_upgrade, '%Y-%m-%d')
                if last_apt_upgrade_age <= self.settings.get_int("tracker-grace-period"):
                    self.logger.write("Tracker: APT upgrades were taken recently: %d days ago" % last_apt_upgrade_age)
                    notification_needed = False

        if notification_needed:
            self.tracked_updates['notified'] = self.today
            return True
        else:
            return False

    # Records updates in JSON file and potentially notify
    def record(self):
        # Purge non-refreshed updates
        for name in list(self.tracked_updates['updates'].keys()):
            if name not in self.refreshed_update_names:
                del self.tracked_updates['updates'][name]
        # Update the check date
        self.tracked_updates['checked'] = self.today
        # Write JSON
        with open(self.path, "w") as f:
            json.dump(self.tracked_updates, f, indent=2)

try:
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
except:
    pass

class FlatpakUpdate():
    def __init__(self, op=None, installer=None, ref=None, installed_ref=None, remote_ref=None, pkginfo=None):
        if op is None:
            # json decoding
            return

        self.op = op

        # nullable
        self.installed_ref = installed_ref
        self.remote_ref = remote_ref
        self.pkginfo = pkginfo
        #

        # to be jsonified
        self.ref = ref
        self.ref_name = ref.get_name()
        self.metadata = op.get_metadata()
        self.size = op.get_download_size()
        self.link = installer.get_homepage_url(pkginfo) if pkginfo else None
        self.flatpak_type = "app" if ref.get_kind() == Flatpak.RefKind.APP else "runtime"
        self.old_version = ""
        self.new_version = ""
        self.name = ""
        self.summary = ""
        self.description = ""
        self.real_source_name = ""
        self.source_packages = []
        self.package_names = [self.ref_name]
        self.sub_updates = []
        self.origin = ""
        ##################

        # ideal:           old-version                     new-version
        # versions same:   old-version (commit)            new-version (commit)
        # no versions      commit                          commit

        # old version
        if installed_ref:
            iref_version = self.installed_ref.get_appdata_version()
            old_commit = installed_ref.get_commit()[:10]
        else:
            iref_version = ""
            old_commit = ""

        # new version
        if pkginfo:
            appstream_version = installer.get_version(pkginfo)

        new_commit = op.get_commit()[:10]

        if iref_version != "" and appstream_version != "":
            if iref_version != appstream_version:
                self.old_version = iref_version
                self.new_version = appstream_version
            else:
                self.old_version = "%s (%s)" % (iref_version, old_commit)
                self.new_version = "%s (%s)" % (appstream_version, new_commit)
        else:
            self.old_version = old_commit
            self.new_version = new_commit

        if pkginfo:
            self.name = installer.get_display_name(pkginfo)
        elif installed_ref:
            self.name = installed_ref.get_appdata_name()
        else:
            self.name = ref.get_name()

        if pkginfo:
            self.summary = installer.get_summary(pkginfo)
            description = installer.get_description(pkginfo)
            self.description = re.sub(r'\n+', '\n\n', description).rstrip()
        elif installed_ref:
            self.summary = installed_ref.get_appdata_summary()
            self.description = ""
        else:
            self.summary = ""
            self.description = ""

        if self.description == "" and self.flatpak_type == "runtime":
            self.summary = self.description = _("A Flatpak runtime package")

        self.real_source_name = self.ref_name
        self.source_packages = ["%s=%s" % (self.ref_name, self.new_version)]
        self.package_names = [self.ref_name]
        self.sub_updates = []

        if installed_ref:
            self.origin = installed_ref.get_origin().capitalize()
        elif remote_ref:
            self.origin = remote_ref.get_remote_name()
        else:
            self.origin = ""

    def add_package(self, update):
        self.sub_updates.append(update)
        self.package_names.append(update.ref_name)
        self.size += update.size
        # self.source_packages.append("%s=%s" % (update.ref_name, update.new_version))

    def to_json(self):
        trimmed_dict = {}

        for key in ("flatpak_type",
                    "name",
                    "origin",
                    "old_version",
                    "new_version",
                    "size",
                    "summary",
                    "description",
                    "real_source_name",
                    "source_packages",
                    "package_names",
                    "sub_updates",
                    "link"):
            trimmed_dict[key] = self.__dict__[key]
        trimmed_dict["metadata"] = self.metadata.to_data()[0]
        trimmed_dict["ref"] = self.ref.format_ref()

        return trimmed_dict

    @classmethod
    def from_json(cls, json_data:dict):
        inst = cls()
        inst.flatpak_type = json_data["flatpak_type"]
        inst.ref = Flatpak.Ref.parse(json_data["ref"])
        inst.ref_name = inst.ref.get_name()
        inst.name = json_data["name"]
        inst.origin = json_data["origin"]
        inst.old_version = json_data["old_version"]
        inst.new_version = json_data["new_version"]
        inst.size = json_data["size"]
        inst.summary = json_data["summary"]
        inst.description = json_data["description"]
        inst.real_source_name = json_data["real_source_name"]
        inst.source_packages = json_data["source_packages"]
        inst.package_names = json_data["package_names"]
        inst.sub_updates = json_data["sub_updates"]
        inst.link = json_data["link"]
        inst.metadata = GLib.KeyFile()

        try:
            b = GLib.Bytes.new(json_data["metadata"].encode())
            inst.metadata.load_from_bytes(b, GLib.KeyFileFlags.NONE)
        except GLib.Error as e:
            print("unable to decode op metadata: %s" % e.message)
            pass

        return inst
