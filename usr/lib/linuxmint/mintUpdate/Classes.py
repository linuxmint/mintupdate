#!/usr/bin/python3

import gettext
gettext.install("mintupdate", "/usr/share/locale")
import html
from gi.repository import Gio


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

def get_release_dates():
    """ Get distro release dates for support duration calculation """
    import os
    import time
    from datetime import datetime

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
                release_date = datetime.fromtimestamp(release_date)
                support_end = time.mktime(time.strptime(distro[5].rstrip(), '%Y-%m-%d'))
                support_end = datetime.fromtimestamp(support_end)
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
