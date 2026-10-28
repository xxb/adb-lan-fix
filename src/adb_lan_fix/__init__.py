"""adb-lan-fix: make `adb` reach LAN devices on macOS again.

macOS 15+ applies Local Network privacy per binary. The `adb` binary is refused
outbound connections to private-range addresses (`EHOSTUNREACH`, "No route to
host") even after the user grants permission, while other runtimes on the same
host connect fine. See docs/ROOT_CAUSE.md for the evidence.

This package routes adb's traffic through a permitted runtime over loopback,
which is not subject to the restriction. It is transparent: scripts keep using
the same serials and the same commands.

Quick start
-----------
    from adb_lan_fix import command
    subprocess.run(command("192.168.1.50:5555", "shell", "getprop", "ro.product.model"))

Or from a shell:

    adb-lan-fix install          # register the resident daemon
    adb-lan-fix doctor           # per-device reachability
    adb-lan-fix connect 192.168.1.50:5555

On Linux, Windows, and healthy macOS hosts every entry point is a no-op that
behaves exactly like calling `adb` directly.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .shim import (  # noqa: E402
    command,
    connect,
    daemon_alive,
    ensure_daemon,
    ensure_forward,
    find_real_adb,
    is_supported_platform,
    real_adb,
)

__all__ = [
    "__version__",
    "command",
    "connect",
    "daemon_alive",
    "ensure_daemon",
    "ensure_forward",
    "find_real_adb",
    "is_supported_platform",
    "real_adb",
]
