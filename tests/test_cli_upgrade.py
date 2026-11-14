#!/usr/bin/python3

# apt-get command line built by "mintupdate-cli upgrade".
# Run with: /usr/bin/python3 -m unittest discover -s tests

import os
import runpy
import sys
import unittest
from unittest import mock

from test_blacklist import FakeAptUpdater, libPath
import aptUpdater

def apt_arguments(*args):
    # Runs "mintupdate-cli upgrade" on the stub packages and returns the
    # apt-get arguments instead of running apt-get
    real_exists = os.path.exists
    def exists(path):
        return False if path == "/etc/mintupdate.blacklist" else real_exists(path)
    FakeAptUpdater.gui_blacklist = []
    with mock.patch.object(aptUpdater, "AptUpdater", FakeAptUpdater), \
            mock.patch("os.path.exists", exists), \
            mock.patch.dict(os.environ), \
            mock.patch.object(sys, "argv", ["mintupdate-cli", "upgrade"] + list(args)), \
            mock.patch("subprocess.call", return_value=0) as call:
        runpy.run_path(os.path.join(libPath, "mintupdate-cli.py"), run_name="__main__")
    return call.call_args.args[0]

class TestUpgrade(unittest.TestCase):

    def test_yes_never_removes(self):
        arguments = apt_arguments("--yes")
        self.assertIn("--assume-yes", arguments)
        self.assertIn("--no-remove", arguments)

    def test_interactive_asks(self):
        # Without --yes apt-get shows the removals and asks
        self.assertNotIn("--no-remove", apt_arguments())

if __name__ == "__main__":
    unittest.main()
