# Changelog

## 0.1.0

First release.

- Transparent adb wrapper that forwards blocked LAN devices over loopback
- Resident daemon managed by launchd, starts at login, restarts on crash
- Device registry persists across reboots
- `install`, `uninstall`, `doctor`, `status` subcommands
- Python API: `command()`, `connect()`
- Pass-through on Linux, Windows, and macOS without the restriction
