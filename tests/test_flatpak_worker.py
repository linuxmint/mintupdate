#!/usr/bin/python3

# Reading mintupdate's messages in flatpak-update-worker.
# Run with: /usr/bin/python3 -m unittest discover -s tests
# No flatpak operation is done: the worker reads from a local pipe.

import importlib.util
import os
import sys
import time
import unittest
from unittest import mock

import gi
gi.require_version('GioUnix', '2.0')
from gi.repository import GLib, Gio, GioUnix

myPath = os.path.dirname(os.path.abspath(__file__))
libPath = os.path.join(myPath, "..", "usr", "lib", "linuxmint", "mintUpdate")
sys.path.insert(0, libPath)

spec = importlib.util.spec_from_file_location("flatpak_update_worker",
                                              os.path.join(libPath, "flatpak-update-worker.py"))
flatpak_update_worker = importlib.util.module_from_spec(spec)
# mintcommon's installer needs a display, and none of it is used here
mintcommon = mock.MagicMock()
with mock.patch.dict(sys.modules, {"mintcommon": mintcommon, "mintcommon.installer": mintcommon.installer}), \
        mock.patch("setproctitle.setproctitle"):
    spec.loader.exec_module(flatpak_update_worker)

def run_reader(messages):
    # Feeds messages to message_from_updater() the way mintupdate does, then closes the pipe
    worker = flatpak_update_worker.FlatpakUpdateWorker.__new__(flatpak_update_worker.FlatpakUpdateWorker)
    worker.cancellable = Gio.Cancellable()
    worker.task = None
    worker.quit = mock.Mock(side_effect=worker.cancellable.cancel)
    worker.start_updates = mock.Mock()

    reads = []
    def message_from_updater(pipe, res):
        reads.append(res)
        flatpak_update_worker.FlatpakUpdateWorker.message_from_updater(worker, pipe, res)
    worker.message_from_updater = message_from_updater

    read_fd, write_fd = os.pipe()
    for message in messages:
        os.write(write_fd, message.encode())
    os.close(write_fd)

    pipe = GioUnix.InputStream.new(read_fd, True)
    pipe.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, worker.message_from_updater)

    context = GLib.MainContext.default()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and len(reads) < 100:
        if worker.cancellable.is_cancelled() and not context.pending():
            break
        context.iteration(False)

    worker.cancellable.cancel()
    while context.pending():
        context.iteration(False)
    pipe.close()
    return worker, len(reads)

class TestMessagesFromUpdater(unittest.TestCase):

    def test_quits_when_pipe_closed(self):
        worker, reads = run_reader([])
        self.assertEqual(reads, 1)
        worker.quit.assert_called_once()

    def test_start_then_pipe_closed(self):
        worker, reads = run_reader(["start"])
        self.assertEqual(reads, 2)
        worker.start_updates.assert_called_once()
        worker.quit.assert_called_once()

if __name__ == "__main__":
    unittest.main()
