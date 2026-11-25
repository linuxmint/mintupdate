#!/usr/bin/python3

# Exit status of mintupdate-cli and mint-refresh-cache.
# Run with: /usr/bin/python3 -m unittest discover -s tests

import os
import runpy
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from test_blacklist import FakeAptUpdater, libPath, myPath
import aptUpdater

def cli_exit_status(refresh_status, apt_status):
    # Runs "mintupdate-cli upgrade --refresh-cache" on the stub packages, with
    # mint-refresh-cache and apt-get replaced by the given exit statuses
    real_exists = os.path.exists
    def exists(path):
        return False if path == "/etc/mintupdate.blacklist" else real_exists(path)
    FakeAptUpdater.gui_blacklist = []
    with mock.patch.object(aptUpdater, "AptUpdater", FakeAptUpdater), \
            mock.patch("os.path.exists", exists), \
            mock.patch.object(sys, "argv", ["mintupdate-cli", "upgrade", "--refresh-cache"]), \
            mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=refresh_status)), \
            mock.patch("subprocess.call", return_value=apt_status), \
            mock.patch("sys.exit") as exit:
        runpy.run_path(os.path.join(libPath, "mintupdate-cli.py"), run_name="__main__")
    exit.assert_called_once()
    return exit.call_args.args[0]

class TestCli(unittest.TestCase):

    def test_success(self):
        self.assertEqual(cli_exit_status(0, 0), 0)

    def test_apt_get_failed(self):
        self.assertEqual(cli_exit_status(0, 100), 100)

    def test_refresh_failed(self):
        # The upgrade still runs on the old cache, but the failure is reported
        self.assertEqual(cli_exit_status(1, 0), 1)

    def test_both_failed(self):
        self.assertEqual(cli_exit_status(1, 100), 100)

class TestRefreshCache(unittest.TestCase):

    @unittest.skipIf(os.getuid() == 0, "needs a regular user")
    def test_not_root(self):
        script = os.path.join(myPath, "..", "usr", "bin", "mint-refresh-cache")
        result = subprocess.run([sys.executable, script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires admin privileges", result.stdout)

if __name__ == "__main__":
    unittest.main()
