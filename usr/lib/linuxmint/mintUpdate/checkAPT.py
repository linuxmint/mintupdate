#!/usr/bin/python3

import apt
import codecs
import fnmatch
import gettext
import os
import platform
import re
import sys
from html.parser import HTMLParser
import traceback

from gi.repository import Gio

from Classes import Update, Alias, Rule, KERNEL_PKG_NAMES, CONFIGURED_KERNEL_TYPE

gettext.install("mintupdate", "/usr/share/locale")

class KernelVersion():

    def __init__(self, version):
        self.version = version
        version_array = self.version.replace("-", ".").split(".")
        self.numeric_versions = []
        for i in range(4):
            element = version_array[i]
            if len(element) == 1:
                element = "00%s" % element
            elif len(element) == 2:
                element = "0%s" % element
            self.numeric_versions.append(element)
        self.numeric_representation = ".".join(self.numeric_versions)
        self.std_version = "%s.%s.%s-%s" % (version_array[0], version_array[1], version_array[2], version_array[3])
        self.series = "%s.%s.%s" % (version_array[0], version_array[1], version_array[2])

# These updates take priority over other updates.
# If a new version of these packages is available,
# nothing else is listed.
PRIORITY_UPDATES = ['mintupdate', 'mint-upgrade-info']

class APTCheck():

    def __init__(self):
        self.settings = Gio.Settings("com.linuxmint.updates")
        self.cache = apt.Cache()
        self.priority_updates_available = False
        self.load_rules()

    def load_aliases(self):
        self.aliases = {}
        with open("/usr/lib/linuxmint/mintUpdate/aliases") as alias_file:
            for line in alias_file:
                if not line.startswith('#'):
                    splitted = line.split("#####")
                    if len(splitted) == 4:
                        (alias_packages, alias_name, alias_short_description, alias_description) = splitted
                        alias_object = Alias(alias_name, alias_short_description, alias_description)
                        for alias_package in alias_packages.split(','):
                            alias_package = alias_package.strip()
                            self.aliases[alias_package] = alias_object

    def load_rules(self):
        self.named_rules = {}
        self.wildcard_rules = {}
        with open("/usr/lib/linuxmint/mintUpdate/rules","r") as rulesFile:
            rules = rulesFile.readlines()
            for rule in rules:
                if "|" not in rule:
                    continue
                rule_fields = rule.strip().split("|")
                if (len(rule_fields) == 3):
                    rule_level = int(rule_fields[0])
                    rule_name = rule_fields[1]
                    rule_version = rule_fields[2]
                    rule = Rule(rule_name, rule_version, rule_level)
                    if rule.is_wildcard:
                        self.wildcard_rules[rule.name] = rule
                    else:
                        self.named_rules[rule.name] = rule

    def refresh_cache(self):
        if os.getuid() == 0 :
            if "--use-synaptic" in sys.argv:
                window_id = int(sys.argv[2])
                from subprocess import Popen
                cmd = ["sudo", "/usr/sbin/synaptic", "--hide-main-window", "--update-at-startup", "--non-interactive", "--parent-window-id", "%d" % window_id]
                comnd = Popen(' '.join(cmd), shell=True)
                comnd.wait()
            else:
                self.cache.update()

    def find_changes(self):
        # Reopen the cache to reflect any updates
        self.cache.open(None)
        self.cache.upgrade(self.settings.get_boolean("dist-upgrade"))
        changes = self.cache.get_changes()

        self.updates = {}

        # Package updates
        for pkg in changes:
            if (pkg.is_installed and pkg.marked_upgrade and pkg.candidate.version != pkg.installed.version):
                self.add_update(pkg)

        # Kernel updates
        meta_names = []
        _metas = [s for s in self.cache.keys() if s.startswith("linux" + CONFIGURED_KERNEL_TYPE)]
        for meta in _metas:
            shortname = meta.split(":")[0]
            if shortname not in meta_names:
                meta_names.append(shortname)

        try:
            # Get the uname version
            uname_kernel = KernelVersion(platform.release())

            # Check if any meta is installed..
            meta_installed = False
            for meta_name in meta_names:
                if meta_name in self.cache:
                    meta = self.cache[meta_name]
                    if meta.is_installed:
                        meta_installed = True
                        return

            # If no meta is installed, try to recommend one
            if not meta_installed:
                for meta_name in meta_names:
                    if meta_name in self.cache:
                        meta = self.cache[meta_name]
                        recommended_kernel = KernelVersion(meta.candidate.version)
                        if (uname_kernel.numeric_representation <= recommended_kernel.numeric_representation):
                            # This meta version is >= to the uname version, add it as an update
                            self.add_update(meta, kernel_update=True)
                            # Return because we only want to add one meta, and we don't want to recommend latest kernels in the series
                            return

            # We've gone past all the metas, so we should recommend the latest kernel on the series we're in
            max_kernel = uname_kernel
            for pkgname in self.cache.keys():
                match = re.match(r'^(?:linux-image-)(?:unsigned-)?(\d.+?)%s$' % CONFIGURED_KERNEL_TYPE, pkgname)
                if match:
                    kernel = KernelVersion(match.group(1))
                    if kernel.numeric_representation > max_kernel.numeric_representation and kernel.series == max_kernel.series:
                        max_kernel = kernel
            if max_kernel.numeric_representation != uname_kernel.numeric_representation:
                for pkgname in KERNEL_PKG_NAMES:
                    pkgname = pkgname.replace('VERSION', max_kernel.std_version).replace("-KERNELTYPE", CONFIGURED_KERNEL_TYPE)
                    if pkgname in self.cache:
                        pkg = self.cache[pkgname]
                        if not pkg.is_installed:
                            self.add_update(pkg, kernel_update=True)

        except Exception:
            print(sys.exc_info()[0])

    def add_update(self, package, kernel_update=False):

        if package.name in ['linux-libc-dev', 'linux-kernel-generic']:
            source_name = package.name
        elif package.candidate.source_name in ['linux', 'linux-meta', 'linux-hwe', 'linux-hwe-edge']:
            source_name = "linux-%s" % package.candidate.version
        elif package.name.startswith("linux-image") or package.name.startswith("linux-headers") or package.name.startswith("linux-modules") or package.name.startswith("linux-tools"):
            source_name = "linux-%s" % package.candidate.version
        else:
            source_name = package.candidate.source_name

        # ignore packages blacklisted by the user
        for blacklist in self.settings.get_strv("blacklisted-packages"):
            if "=" in blacklist:
                (bl_pkg, bl_ver) = blacklist.split("=", 1)
            else:
                bl_pkg = blacklist
                bl_ver = None
            if fnmatch.fnmatch(source_name, bl_pkg) and (not bl_ver or bl_ver == package.candidate.version):
                return

        if source_name in PRIORITY_UPDATES:
            if self.priority_updates_available == False and len(self.updates) > 0:
                self.updates = {}
            self.priority_updates_available = True
        if (source_name in PRIORITY_UPDATES) or self.priority_updates_available == False:
            if source_name in self.updates:
                update = self.updates[source_name]
                update.add_package(package)
            else:
                update = Update(package, source_name=source_name)

                if source_name in self.named_rules.keys():
                    rule = self.named_rules[source_name]
                    if (rule.match(source_name, update.new_version)):
                        update.level = rule.level
                else:
                    for rule_name in self.wildcard_rules.keys():
                        rule = self.wildcard_rules[rule_name]
                        if rule.match(source_name, update.new_version):
                            update.level = rule.level
                            break

                if update.level < 5:
                    self.updates[source_name] = update
            if kernel_update:
                update.type = "kernel"

    def serialize_updates(self):
        # Print updates
        for source_name in sorted(self.updates.keys()):
            update = self.updates[source_name]
            update.serialize()

    def list_updates(self):
        # Print updates
        for source_name in sorted(self.updates.keys()):
            update = self.updates[source_name]
            update.serialize()

    def apply_aliases(self):
        for source_name in self.updates.keys():
            update = self.updates[source_name]
            if source_name in self.aliases.keys():
                alias = self.aliases[source_name]
                update.display_name = alias.name
                update.short_description = alias.short_description
                update.description = alias.description
            elif update.type == "kernel" and source_name not in ['linux-libc-dev', 'linux-kernel-generic']:
                update.display_name = _("Linux kernel %s") % update.new_version
                update.short_description = _("The Linux kernel.")
                update.description = _("The Linux Kernel is responsible for hardware and drivers support. Note that this update will not remove your existing kernel. You will still be able to boot with the current kernel by choosing the advanced options in your boot menu. Please be cautious though.. kernel regressions can affect your ability to connect to the Internet or to log in graphically. DKMS modules are compiled for the most recent kernels installed on your computer. If you are using proprietary drivers and you want to use an older kernel, you will need to remove the new one first.")

    def apply_l10n_descriptions(self):
        if os.path.exists("/var/lib/apt/lists"):
            try:
                super_buffer = []
                for file in os.listdir("/var/lib/apt/lists"):
                    if ("i18n_Translation") in file and not file.endswith("Translation-en"):
                        fd = codecs.open(os.path.join("/var/lib/apt/lists", file), "r", "utf-8")
                        super_buffer += fd.readlines()

                parser = HTMLParser()

                i = 0
                while i < len(super_buffer):
                    line = super_buffer[i].strip()
                    if line.startswith("Package: "):
                        try:
                            pkgname = line.replace("Package: ", "")
                            if pkgname in self.updates.keys():
                                update = self.updates[pkgname]
                                j = 2 # skip md5 line after package name line
                                while True:
                                    if (i+j >= len(super_buffer)):
                                        break
                                    line = super_buffer[i+j].strip()
                                    if line.startswith("Package: "):
                                        break
                                    if j==2:
                                        try:
                                            # clean short description
                                            value = line
                                            try:
                                                value = parser.unescape(value)
                                            except:
                                                print ("Unable to unescape '%s'" % value)
                                            # Remove "Description-xx: " prefix
                                            value = re.sub(r'Description-(\S+): ', r'', value)
                                            # Only take the first line and trim it
                                            value = value.split("\n")[0].strip()
                                            value = value.split("\\n")[0].strip()
                                            # Capitalize the first letter
                                            value = value[:1].upper() + value[1:]
                                            # Add missing punctuation
                                            if len(value) > 0 and value[-1] not in [".", "!", "?"]:
                                                value = "%s." % value
                                            update.short_description = value
                                            update.description = ""
                                        except Exception as e:
                                            print(e)
                                            print(sys.exc_info()[0])
                                    else:
                                        description = "\n" + line
                                        try:
                                            try:
                                                description = parser.unescape(description)
                                            except:
                                                print ("Unable to unescape '%s'" % description)
                                            dlines = description.split("\n")
                                            value = ""
                                            num = 0
                                            newline = False
                                            for dline in dlines:
                                                dline = dline.strip()
                                                if len(dline) > 0:
                                                    if dline == ".":
                                                        value = "%s\n" % (value)
                                                        newline = True
                                                    else:
                                                        if (newline):
                                                            value = "%s%s" % (value, self.capitalize(dline))
                                                        else:
                                                            value = "%s %s" % (value, dline)
                                                        newline = False
                                                    num += 1
                                            value = value.replace("  ", " ").strip()
                                            # Capitalize the first letter
                                            value = value[:1].upper() + value[1:]
                                            # Add missing punctuation
                                            if len(value) > 0 and value[-1] not in [".", "!", "?"]:
                                                value = "%s." % value
                                            update.description += description
                                        except Exception as e:
                                            print (e)
                                            print(sys.exc_info()[0])
                                    j += 1

                        except Exception as e:
                            print (e)
                            print(sys.exc_info()[0])
                    i += 1
                del super_buffer
            except Exception as e:
                print (e)
                print("Could not fetch l10n descriptions..")
                print(sys.exc_info()[0])

    def clean_descriptions(self):
        for source_name in self.updates.keys():
            update = self.updates[source_name]
            if "\n" in update.short_description:
                update.short_description = update.short_description.split("\n")[0]
            if update.short_description.endswith("."):
                update.short_description = update.short_description[:-1]
            update.short_description = self.capitalize(update.short_description)
            if "& " in update.short_description:
                update.short_description = update.short_description.replace('&', '&amp;')
            if "& " in update.description:
                update.description = update.description.replace('&', '&amp;')

    def capitalize(self, string):
        if len(string) > 1:
            return (string[0].upper() + string[1:])
        else:
            return (string)

if __name__ == "__main__":
    try:
        check = APTCheck()
        check.refresh_cache()
        check.find_changes()
        check.apply_l10n_descriptions()
        check.load_aliases()
        check.apply_aliases()
        check.clean_descriptions()
        check.serialize_updates()
        if os.getuid() == 0 and os.path.exists("/usr/bin/mintinstall-update-pkgcache"):
            # Spawn the cache update asynchronously
            # We're using os.system with & here to make sure it's async and detached
            # from the caller (which will die before the child process is finished)
            # stdout/stderr is also directed to /dev/null so it doesn't interfere
            # or block the output from checkAPT
            os.system("/usr/bin/mintinstall-update-pkgcache > /dev/null 2>&1 &")
    except Exception as error:
        print("CHECK_APT_ERROR---EOL---")
        print(sys.exc_info()[0])
        print("Error: %s" % error)
        traceback.print_exc()
        sys.exit(1)
