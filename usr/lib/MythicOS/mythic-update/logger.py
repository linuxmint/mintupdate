#!/usr/bin/python3

import os
import datetime
import tempfile
import traceback

class Logger():

    def __init__(self):
        self._create_log()
        self.callback = None

    def _create_log(self):
        path = os.path.join(tempfile.gettempdir(), "mintUpdate/")
        if not os.path.exists(path):
            os.umask(0)
            os.makedirs(path)
        self.log = tempfile.NamedTemporaryFile(mode="w", prefix=path, delete=False)
        try:
            os.chmod(self.log.name, 0o666)
        except:
            traceback.print_exc()

    def _log_ready(self):
        if self.log.closed:
            return False
        if not os.path.exists(self.log.name):
            self.log.close()
            self._create_log()
        return True

    def _write(self, separator, line):
        timestamp = datetime.datetime.now().strftime('%Y.%m.%d@%H:%M')
        line = f"{timestamp} {separator} {line}\n"
        if self._log_ready():
            self.log.write(line)
            self.log.flush()
        if self.callback:
            self.callback(line)

    def write(self, line):
        self._write("++", line)

    def write_error(self, line):
        self._write("--", line)

    def read(self):
        if not os.path.exists(self.log.name):
            self._create_log()
            return ""
        else:
            with open(self.log.name) as f:
                return f.read()

    def close(self):
        self.log.close()

    def set_callback(self, callback):
        self.callback = callback

    def remove_callback(self):
        self.callback = None
