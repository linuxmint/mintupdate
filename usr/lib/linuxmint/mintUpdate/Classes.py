#!/usr/bin/python3

import gettext
gettext.install("mintupdate", "/usr/share/linuxmint/locale")

class Update():

    def __init__(self, package=None, input_string=None):
        self.package_names = []
        if package is not None:
            self.package_names.append(package.name)
            self.main_package_name = package.name
            self.package_name = package.name
            self.new_version = package.candidate.version
            if package.installed is None:
                self.old_version = ""
            else:
                self.old_version = package.installed.version
            self.size = package.candidate.size
            self.source_name = package.candidate.source_name
            self.display_name = self.source_name
            self.short_description = package.candidate.raw_description
            self.description = package.candidate.description
            if (self.new_version != self.old_version):
                self.type = "package"
                for origin in package.candidate.origins:
                    self.origin = origin.origin
                    self.site = origin.site
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
                    if origin.origin == "linuxmint":
                        if origin.component == "romeo":
                            self.type = "unstable"
                            break
                if self.source_name in ["linux", "linux-kernel"]:
                    self.type = "kernel"

            # Find the update level
            self.level = 3 # Level 3 by default
            if self.origin == "linuxmint":
                self.level = 1 # Level 1 by default
            rulesFile = open("/usr/lib/linuxmint/mintUpdate/rules","r")
            rules = rulesFile.readlines()
            foundPackageRule = False # whether we found a rule with the exact package name or not
            for rule in rules:
                rule_fields = rule.split("|")
                if (len(rule_fields) == 5):
                    rule_name = rule_fields[0]
                    rule_version = rule_fields[1]
                    rule_level = int(rule_fields[2])
                    if (rule_name == self.source_name):
                        foundPackageRule = True
                        if (rule_version == self.new_version):
                            self.level = rule_level
                            break
                        else:
                            if (rule_version == "*"):
                                self.level = rule_level
                    else:
                        if (rule_name.startswith("*")):
                            keyword = rule_name.replace("*", "")
                            index = self.source_name.find(keyword)
                            if (index > -1 and foundPackageRule == False):
                                self.level = rule_level

            rulesFile.close()


        else:
            # Build the class from the input_string
            self.parse(input_string)

    def add_package(self, pkg):
        self.package_names.append(pkg.name)
        self.size += pkg.candidate.size
        overwrite_main_package = False
        if self.main_package_name is None or pkg.name == self.source_name:
            overwrite_main_package = True
        else:
            if self.main_package_name == self.source_name:
                overwrite_main_package = False
            else:
                # Overwrite dev, dbg, common, arch packages
                for suffix in ["-dev", "-dbg", "-common", "-core", "-data", "-doc", ":i386", ":amd64"]:
                    if (self.main_package_name.endswith(suffix) and not pkg.name.endswith(suffix)):
                        overwrite_main_package = True
                        break
                # Overwrite lib packages
                for prefix in ["lib", "gir1.2"]:
                    if (self.main_package_name.startswith(prefix) and not pkg.name.startswith(prefix)):
                        overwrite_main_package = True
                        break
                for keyword in ["-locale-", "-l10n-", "-help-"]:
                    if (keyword in self.main_package_name) and (keyword not in pkg.name):
                        overwrite_main_package = True
                        break
        if overwrite_main_package:
            self.description = pkg.candidate.description
            self.short_description = pkg.candidate.raw_description
            self.main_package_name  = pkg.name

    def serialize(self):
        output_string = u"###%d###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % (self.level, self.display_name, self.source_name, self.main_package_name, ", ".join(self.package_names), self.new_version, self.old_version, self.size, self.type, self.origin, self.short_description, self.description, self.site)
        print(output_string.encode('ascii', 'xmlcharrefreplace'))

    def parse(self, input_string):
        values = input_string.split("###")
        nothing, level, self.display_name, self.source_name, self.main_package_name, package_names, self.new_version, self.old_version, size, self.type, self.origin, self.short_description, self.description, self.site = values
        self.level = int(level)
        self.size = int(size)
        for package_name in package_names.split(", "):
            self.package_names.append(package_name)

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