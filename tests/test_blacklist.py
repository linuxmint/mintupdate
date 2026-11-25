#!/usr/bin/python3

# Blacklist matching in the update list (AptUpdater) and in mintupdate-cli.
# Run with: /usr/bin/python3 -m unittest discover -s tests
# Nothing is read from APT or dconf: packages are stubs and GSettings uses
# the memory backend.

import contextlib
import io
import os
import runpy
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ["GSETTINGS_BACKEND"] = "memory"
myPath = os.path.dirname(os.path.abspath(__file__))
libPath = os.path.join(myPath, "..", "usr", "lib", "linuxmint", "mintUpdate")
sys.path.insert(0, libPath)

import aptUpdater
from aptUpdater import AptUpdater

def make_package(name, source_name, source_version, version, old_version):
    candidate = SimpleNamespace(source_name=source_name, source_version=source_version,
                                version=version, size=1, raw_description="", description="",
                                origins=[], section="admin")
    return SimpleNamespace(name=name, candidate=candidate, is_installed=True,
                           installed=SimpleNamespace(version=old_version))

# bsdutils has an epoch, the other util-linux binaries don't
UTIL_LINUX = [
    make_package("util-linux", "util-linux", "2.39.3-9ubuntu6.6", "2.39.3-9ubuntu6.6", "2.39.3-9ubuntu6"),
    make_package("mount", "util-linux", "2.39.3-9ubuntu6.6", "2.39.3-9ubuntu6.6", "2.39.3-9ubuntu6"),
    make_package("bsdutils", "util-linux", "2.39.3-9ubuntu6.6", "1:2.39.3-9ubuntu6.6", "1:2.39.3-9ubuntu6"),
]
# -signed source packages have their own version, unrelated to the binary one
GRUB_SIGNED = [
    make_package("grub-efi-amd64-signed", "grub2-signed", "1.202.5", "1.202.5+2.12-1ubuntu7.3", "1.202+2.12-1ubuntu7"),
]
PACKAGES = UTIL_LINUX + GRUB_SIGNED

class FakeSettings():

    def __init__(self, blacklist):
        self.blacklist = blacklist

    def get_strv(self, key):
        return list(self.blacklist)

class FakeAptUpdater(AptUpdater):
    # Real AptUpdater.add_update() on stub packages, with an empty GUI blacklist
    # unless one is given

    gui_blacklist = []

    def __init__(self, ui_window=None):
        super().__init__(ui_window)
        self.settings = FakeSettings(self.gui_blacklist)

    def load_cache(self):
        self.cache = {package.name: package for package in PACKAGES}

    def find_changes(self):
        self.updates = {}
        for package in PACKAGES:
            self.add_update(package)

def listed_updates(gui_blacklist):
    FakeAptUpdater.gui_blacklist = gui_blacklist
    updater = FakeAptUpdater()
    updater.load_cache()
    updater.find_changes()
    return {source_name: sorted(update.package_names) for source_name, update in updater.updates.items()}

def run_cli(*args):
    # Runs mintupdate-cli.py as a script, on the stub packages, without /etc/mintupdate.blacklist
    real_exists = os.path.exists
    def exists(path):
        return False if path == "/etc/mintupdate.blacklist" else real_exists(path)
    FakeAptUpdater.gui_blacklist = []
    output = io.StringIO()
    with mock.patch.object(aptUpdater, "AptUpdater", FakeAptUpdater), \
            mock.patch("os.path.exists", exists), \
            mock.patch.object(sys, "argv", ["mintupdate-cli"] + list(args)), \
            contextlib.redirect_stdout(output), \
            mock.patch("sys.exit") as exit:
        runpy.run_path(os.path.join(libPath, "mintupdate-cli.py"), run_name="__main__")
    exit.assert_called_once_with(0)
    return [line.split()[1] for line in output.getvalue().splitlines()]

class TestUpdateList(unittest.TestCase):

    def test_nothing_ignored(self):
        self.assertEqual(listed_updates([]), {
            "util-linux": ["bsdutils", "mount", "util-linux"],
            "grub2-signed": ["grub-efi-amd64-signed"],
        })

    def test_ignore_current_update_epoch(self):
        # "Ignore the current update" stores source=source_version
        self.assertNotIn("util-linux", listed_updates(["util-linux=2.39.3-9ubuntu6.6"]))

    def test_ignore_current_update_signed(self):
        self.assertNotIn("grub2-signed", listed_updates(["grub2-signed=1.202.5"]))

    def test_ignore_binary_version(self):
        # A version typed in the preferences, as shown in the update list
        self.assertNotIn("grub2-signed", listed_updates(["grub2-signed=1.202.5+2.12-1ubuntu7.3"]))

    def test_ignore_all_versions_pattern(self):
        self.assertNotIn("grub2-signed", listed_updates(["grub2-*"]))

    def test_other_version_not_ignored(self):
        self.assertEqual(listed_updates(["util-linux=2.39.3-9ubuntu6", "grub2-signed=1.202"]),
                         listed_updates([]))

class TestCli(unittest.TestCase):

    def test_nothing_ignored(self):
        self.assertEqual(run_cli("list"), ["grub2-signed", "util-linux"])

    def test_ignore_current_update_epoch(self):
        self.assertEqual(run_cli("list", "-i", "util-linux=2.39.3-9ubuntu6.6"), ["grub2-signed"])

    def test_ignore_current_update_signed(self):
        self.assertEqual(run_cli("list", "-i", "grub2-signed=1.202.5"), ["util-linux"])

    def test_ignore_binary_version(self):
        self.assertEqual(run_cli("list", "-i", "grub2-signed=1.202.5+2.12-1ubuntu7.3"), ["util-linux"])

    def test_ignore_all_versions_pattern(self):
        self.assertEqual(run_cli("list", "-i", "grub2-*,util-*"), [])

    def test_other_version_not_ignored(self):
        self.assertEqual(run_cli("list", "-i", "util-linux=2.39.3-9ubuntu6,grub2-signed=1.202"),
                         ["grub2-signed", "util-linux"])

if __name__ == "__main__":
    unittest.main()
