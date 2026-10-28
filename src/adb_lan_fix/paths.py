"""Path and state locations, resolved without assuming an install layout."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent

# Where the forwarder and daemon live. They ship inside the package so an
# installed copy is self-contained.
FORWARDER_JS = PACKAGE_DIR / "adb_forward.js"
DAEMON_JS = PACKAGE_DIR / "adb_daemon.js"

# Persistent state: survives reboots so a device that worked once keeps working.
# Override with ADB_LAN_FIX_STATE for tests or unusual layouts.
STATE_DIR = Path(
    os.environ.get("ADB_LAN_FIX_STATE")
    or (Path.home() / ".local" / "state" / "adb-lan-fix")
)
STATE_FILE = STATE_DIR / "forwards.json"
REGISTRY_FILE = STATE_DIR / "devices.json"
CONTROL_SOCKET = STATE_DIR / "control.sock"
DAEMON_LOG = STATE_DIR / "daemon.log"

LAUNCH_AGENT_LABEL = "io.github.adb-lan-fix.daemon"
LAUNCH_AGENT = (
    Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
)

# Loopback ports handed out to forwarders. Kept high and narrow so collisions
# with common dev servers are unlikely.
FORWARD_PORT_BASE = 15000
FORWARD_PORT_SPAN = 2000

PROBE_TIMEOUT = 1.5
