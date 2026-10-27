"""Adapters that turn real, recorded public data into WORLDLINE scenarios.

An adapter only ever supplies the TRUTH stream (what happened, when it was published).
Roles, delivery latencies, decision rules and decision times stay declared models and
every generated scenario labels which parts are which.
"""
from .usgs import fetch_window, snapshot, to_scenario, load_snapshot, verify_snapshot, window_key

__all__ = ["fetch_window", "snapshot", "to_scenario", "load_snapshot", "verify_snapshot", "window_key"]
