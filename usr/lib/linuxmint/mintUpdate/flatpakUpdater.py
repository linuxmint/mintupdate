#!/usr/bin/python3

import os
import json
import subprocess
import sys

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

try:
    if not GLib.find_program_in_path("flatpak"):
        raise Exception
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
except Exception:
    print("No Flatpak support - are flatpak and gir1.2-flatpak-1.0 installed?")
    raise NotImplementedError

from Classes import FlatpakUpdate

LOG_PATH = os.path.join(GLib.get_home_dir(), '.linuxmint', 'mintupdate', 'flatpak-updates.log')
UPDATE_WORKER_PATH = "/usr/lib/linuxmint/mintUpdate/flatpak-update-worker.py"

class FlatpakUpdater():
    def __init__(self):
        self.task = None

        self.updates = []
        self.error = None

    def refresh(self):
        self.kill_any_helpers()

        try:
            subprocess.run([UPDATE_WORKER_PATH, "--refresh"], timeout = 30, stdout=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            print("Flatpaks: timed out trying to refresh", str(e))

    def fetch_updates(self):
        self.kill_any_helpers()

        self.updates = []
        output = None

        try:
            output = subprocess.run([UPDATE_WORKER_PATH, "--fetch-updates"], timeout=30, stdout=subprocess.PIPE, encoding="utf-8").stdout
        except subprocess.TimeoutExpired as e:
            print("Flatpaks: timed out trying to get a list of updates", str(e))

        # print(output)

        if output is None:
            print("Flatpaks: no updates")
            return

        output = output.strip("\n")

        if output == "no-installed":
            print("Flatpaks: skipping update check - nothing installed")
            return

        if output.startswith("error:"):
            self.error = output[6:]
            print("Flatpaks: error from fetch-updates call", self.error)
            return

        try:
            json_data = json.loads(output)
            for item in json_data:
                self.updates.append(FlatpakUpdate.from_json(item))
        except json.JSONDecodeError as e:
            print("Flatpaks: unable to parse updates list", str(e))

        print("Flatpak: done generating updates")

    def prepare_start_updates(self, updates):
        argv = [UPDATE_WORKER_PATH, "--update-packages"] + [update.ref.format_ref() for update in updates]

        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding="utf-8")
        except Exception as e:
            print("Flatpak: Could not start worker: %s" % str(e))
            return False

        self.out_pipe = self.proc.stdout
        self.in_pipe = self.proc.stdin

        try:
            res = self.out_pipe.readline()
            if res:
                answer = res.strip("\n")
                if answer == "ready":
                    return
                else:
                    raise Exception("Unexpected response from worker - expected 'ready', got '%s'" % answer)
        except Exception as e:
            print("Flatpak: Could not set up to install updates: %s", str(e))
            self.terminate_helper()

    def confirm_start(self):
        try:
            self.in_pipe.write("confirm")
            self.in_pipe.flush()

            res = self.out_pipe.readline()
            if res:
                answer = res.strip("\n")
                if answer == "yes":
                    return True
                else:
                    self.terminate_helper()
                    return False
        except Exception as e:
            print("Flatpak: Could not complete confirmation: %s", str(e))
            self.terminate_helper()

        return False

    def perform_updates(self):
        try:
            self.in_pipe.write("start")
            self.in_pipe.flush()

            res = self.out_pipe.readline()
            if res:
                answer = res.strip("\n")
                if answer == "done":
                    print("Flatpaks: updates complete")
                elif answer.startswith("error:"):
                    self.error = answer[6:]
                    print("Flatpaks: error performing updates: %s" % self.error)
        except Exception as e:
            print("Flatpak: Could not perform updates: %s", str(e))

        self.terminate_helper()

    def terminate_helper(self):
        if self.proc is None:
            return

        try:
            self.in_pipe.close()
            self.out_pipe.close()
        except:
            pass

        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

        self.proc = None

    def kill_any_helpers(self):
        try:
            os.system("killall -q flatpak-update-worker")
        except Exception as e:
            print (e)
            print(sys.exc_info()[0])









