#!/usr/bin/python3

import gettext
gettext.install("mintupdate", "/usr/share/linuxmint/locale")

import html

class Rule():

    def __init__(self, name, version, level):
        self.name = name
        self.version = version
        self.level = level
        self.is_wildcard = False
        if self.name.startswith("*"):
            self.name = self.name.replace("*", "")
            self.is_wildcard = True

    def match(self, pkg_name, pkg_version):
        matches = False
        if (self.version == "*" or self.version == pkg_version):
            if self.is_wildcard:
                if (pkg_name.find(self.name) > -1):
                    matches = True
            else:
                if (pkg_name == self.name):
                    matches = True
        return matches

class Update():

    def __init__(self, package=None, input_string=None, source_name=None):
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
            self.real_source_name = package.candidate.source_name
            if source_name is not None:
                self.source_name = source_name
            else:
                self.source_name = self.real_source_name
            self.display_name = self.source_name
            self.short_description = package.candidate.raw_description
            self.description = package.candidate.description
            if (self.new_version != self.old_version):
                self.type = "package"
                self.origin = ""
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

            self.level = 2 # Level 2 by default
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
        output_string = u"###%d###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % (self.level, self.display_name, self.source_name, self.real_source_name, self.main_package_name, ", ".join(self.package_names), self.new_version, self.old_version, self.size, self.type, self.origin, self.short_description, self.description, self.site)
        print(output_string.encode('ascii', 'xmlcharrefreplace'))

    def parse(self, input_string):
        try:
            input_string = html.unescape(input_string)
        except:
            pass
        values = input_string.split("###")
        nothing, level, self.display_name, self.source_name, self.real_source_name, self.main_package_name, package_names, self.new_version, self.old_version, size, self.type, self.origin, self.short_description, self.description, self.site = values
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