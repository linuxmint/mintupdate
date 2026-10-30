#!/usr/bin/env python3
"""Hermetic characterization for #603 v3 broad Preferences dirty state."""
import os
import sys
import types
import importlib.util

BASE = os.path.join(os.path.dirname(__file__), "usr/lib/linuxmint/mintUpdate")
sys.path.insert(0, BASE)

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

prefs_mod = load("prefs_v3", "preferences.py")
update_mod = load("update_v3", "mintUpdate.py")

class Settings:
    def __init__(self): self.handlers = {}; self.next_id = 0
    def connect(self, signal, callback):
        self.next_id += 1; self.handlers[self.next_id] = callback; return self.next_id
    def disconnect(self, ident): self.handlers.pop(ident)
    def emit(self, key):
        for callback in list(self.handlers.values()): callback(self, key)

class Win:
    def __init__(self, dirty=False): self.preferences_changed = dirty

# GSettings mutation marks dirty; no signal stays clean.
p = prefs_mod.PreferencesWindow.__new__(prefs_mod.PreferencesWindow)
p.preferences_changed = False
p.settings = Settings()
p._settings_changed_handler = p.settings.connect("changed", p._on_settings_changed)
p.settings.emit("tracker-max-days")
assert p.preferences_changed is True
p._on_preferences_destroyed(None)
assert not p.settings.handlers, "destroy must disconnect session handler"

p2 = prefs_mod.PreferencesWindow.__new__(prefs_mod.PreferencesWindow)
p2.preferences_changed = False
p2.settings = Settings()
p2._settings_changed_handler = p2.settings.connect("changed", p2._on_settings_changed)
assert p2.preferences_changed is False, "no-op stays clean"

# Close contract: no-op -> none, dirty -> one refresh, restart wins.
app = update_mod.MintUpdate.__new__(update_mod.MintUpdate)
app.ui_window = types.SimpleNamespace(set_sensitive=lambda _: None)
app.preferences_window = object()
app.app_restart_required = False
calls = []
app.refresh = lambda force: calls.append(("refresh", force))
app.restart_app = lambda: calls.append(("restart",))
app._on_preferences_destroyed(Win(False))
assert calls == [], calls
app.preferences_window = object()
app._on_preferences_destroyed(Win(True))
assert calls == [("refresh", False)], calls
app.preferences_window = object()
app.app_restart_required = True
app._on_preferences_destroyed(Win(True))
assert calls == [("refresh", False), ("restart",)], calls

# Automation: only a successful state transition dirties.
class Toggle:
    def __init__(self, active): self.active = active
    def get_active(self): return self.active
    def set_active(self, v): self.active = v

old_isfile, old_run = prefs_mod.os.path.isfile, prefs_mod.subprocess.run
try:
    prefs_mod.AUTOMATIONS["test"] = (None, None, "/tmp/v3-touch")
    state = {"exists": False}
    prefs_mod.os.path.isfile = lambda _: state["exists"]
    def success(_): state["exists"] = True
    prefs_mod.subprocess.run = success
    p3 = prefs_mod.PreferencesWindow.__new__(prefs_mod.PreferencesWindow)
    p3.preferences_changed = False
    p3._toggle_automation(Toggle(True), "test")
    assert p3.preferences_changed is True
finally:
    prefs_mod.os.path.isfile, prefs_mod.subprocess.run = old_isfile, old_run
    prefs_mod.AUTOMATIONS.pop("test", None)

print("V3 HARNESS OK: GSettings broad signal, no-op, destroy gate, restart precedence, automation transition")
