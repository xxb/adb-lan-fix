"""The transparent adb wrapper and daemon control.

Design notes
------------
The shim never parses the adb protocol. It only decides whether the device is
reachable directly, and if not, swaps the network serial for a loopback port
that a permitted runtime is forwarding. Because it does not understand the
protocol, it cannot corrupt it: every stream type (shell, sync, logcat,
forward, reverse) passes through untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import paths
from .net import is_loopback, is_private_ipv4, port_free, reachable, split_serial


# --------------------------------------------------------------------------- #
# platform / binary discovery
# --------------------------------------------------------------------------- #
def is_supported_platform() -> bool:
    """Only macOS needs the workaround; elsewhere the shim is a pass-through."""
    return sys.platform == "darwin"


def find_real_adb() -> str:
    """Locate the real adb, never resolving back to ourselves."""
    override = os.environ.get("ADB_LAN_FIX_REAL_ADB")
    if override:
        return override

    self_path = Path(sys.argv[0]).resolve()
    skip_dirs = {
        (paths.PROJECT_DIR / ".adb-lan-fix" / "bin").resolve(),
        (paths.STATE_DIR / "bin").resolve(),
    }

    seen: set[Path] = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).resolve() in skip_dirs:
                continue
        except OSError:
            pass
        candidate = Path(entry) / "adb"
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or resolved == self_path:
            continue
        seen.add(resolved)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            continue
        # Skip any launcher that would bounce back into this package.
        try:
            head = resolved.open("rb").read(400)
        except OSError:
            head = b""
        if b"adb_lan_fix" in head or b"adb-lan-fix" in head:
            continue
        return str(resolved)

    for fallback in (
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        str(Path.home() / "Library/Android/sdk/platform-tools/adb"),
        "/usr/bin/adb",
    ):
        if os.access(fallback, os.X_OK) and Path(fallback).resolve() != self_path:
            return fallback

    raise FileNotFoundError(
        "cannot find the real adb binary. Install Android platform-tools, or set "
        "ADB_LAN_FIX_REAL_ADB=/path/to/adb"
    )


def real_adb() -> str:
    """Public alias for find_real_adb."""
    return find_real_adb()


# --------------------------------------------------------------------------- #
# daemon control
# --------------------------------------------------------------------------- #
def _daemon_request(payload: dict, timeout: float = 6.0) -> dict | None:
    """Talk to the resident daemon. Returns None when it is not reachable."""
    if not paths.CONTROL_SOCKET.exists():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(paths.CONTROL_SOCKET))
            sock.sendall((json.dumps(payload) + "\n").encode())
            buffer = b""
            while b"\n" not in buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    return None
                buffer += chunk
            return json.loads(buffer.split(b"\n", 1)[0])
    except (OSError, ValueError):
        return None


def daemon_alive() -> bool:
    reply = _daemon_request({"op": "ping"}, timeout=2.0)
    return bool(reply and reply.get("ok"))


def node_binary() -> str | None:
    node = shutil.which("node")
    if node:
        return node
    for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _launch_agent_plist(node: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{paths.LAUNCH_AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{node}</string>
    <string>{paths.DAEMON_JS}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{paths.STATE_DIR / "launchd.out.log"}</string>
  <key>StandardErrorPath</key><string>{paths.STATE_DIR / "launchd.err.log"}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
"""


def install_launch_agent() -> bool:
    """Register the daemon with launchd: starts at login, restarts on crash.

    Idempotent and silent, so it is safe to call from any adb invocation.
    """
    if not is_supported_platform():
        return False
    if paths.LAUNCH_AGENT.exists() and daemon_alive():
        return True

    node = node_binary()
    if not node:
        return False

    try:
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        paths.LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        paths.LAUNCH_AGENT.write_text(_launch_agent_plist(node))
    except OSError:
        return False

    uid = os.getuid()
    for args in (
        ["launchctl", "bootout", f"gui/{uid}/{paths.LAUNCH_AGENT_LABEL}"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(paths.LAUNCH_AGENT)],
        ["launchctl", "enable", f"gui/{uid}/{paths.LAUNCH_AGENT_LABEL}"],
    ):
        try:
            subprocess.run(args, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

    deadline = time.time() + 6.0
    while time.time() < deadline:
        if daemon_alive():
            return True
        time.sleep(0.2)
    return False


def start_daemon_detached() -> bool:
    """Fallback when launchd is unavailable: start the daemon ourselves."""
    if daemon_alive():
        return True
    node = node_binary()
    if not node or not paths.DAEMON_JS.is_file():
        return False
    try:
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [node, str(paths.DAEMON_JS)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    deadline = time.time() + 6.0
    while time.time() < deadline:
        if daemon_alive():
            return True
        time.sleep(0.2)
    return False


def ensure_daemon() -> bool:
    """Make sure the resident daemon is running. Best effort, never raises."""
    if not is_supported_platform():
        return False
    if daemon_alive():
        return True
    if install_launch_agent():
        return True
    return start_daemon_detached()


# --------------------------------------------------------------------------- #
# forwarding
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    try:
        return json.loads(paths.STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        paths.STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    except OSError:
        pass


def ensure_forward(host: str, port: int) -> int:
    """Return a loopback port forwarding to host:port, starting it if needed.

    Prefers the resident daemon (survives reboots, self-heals). Falls back to a
    one-shot forwarder only when the daemon cannot start at all.
    """
    key = f"{host}:{port}"

    state = _load_state()
    existing = state.get(key)
    if existing and reachable("127.0.0.1", int(existing)):
        return int(existing)

    if ensure_daemon():
        reply = _daemon_request({"op": "ensure", "host": host, "port": port})
        if reply and reply.get("ok"):
            chosen = int(reply["port"])
            state[key] = chosen
            _save_state(state)
            if os.environ.get("ADB_LAN_FIX_VERBOSE"):
                sys.stderr.write(f"adb-lan-fix: {key} -> 127.0.0.1:{chosen} (daemon)\n")
            return chosen

    return _ensure_forward_oneshot(host, port, key, state)


def _ensure_forward_oneshot(host: str, port: int, key: str, state: dict) -> int:
    """Last-resort forwarder that lives only as long as this adb server."""
    node = node_binary()
    if not node:
        raise RuntimeError(
            f"cannot reach {key} and `node` is not on PATH, so no forwarder can start. "
            "Install Node.js, or run on a host where adb connects directly."
        )
    if not paths.FORWARDER_JS.is_file():
        raise RuntimeError(f"forwarder missing at {paths.FORWARDER_JS}")

    used = {str(v) for v in state.values()}
    chosen = None
    for offset in range(paths.FORWARD_PORT_SPAN):
        candidate = paths.FORWARD_PORT_BASE + offset
        if str(candidate) in used:
            continue
        if port_free(candidate):
            chosen = candidate
            break
    if chosen is None:
        raise RuntimeError("no free loopback port available for the forwarder")

    subprocess.Popen(
        [node, str(paths.FORWARDER_JS), str(chosen), host, str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if reachable("127.0.0.1", chosen, timeout=0.3):
            state[key] = chosen
            _save_state(state)
            if os.environ.get("ADB_LAN_FIX_VERBOSE"):
                sys.stderr.write(f"adb-lan-fix: {key} -> 127.0.0.1:{chosen}\n")
            return chosen
        time.sleep(0.1)

    raise RuntimeError(f"forwarder for {key} did not come up in time")


# --------------------------------------------------------------------------- #
# argument rewriting
# --------------------------------------------------------------------------- #
def extract_serial(argv: list[str]) -> tuple[str | None, int | None, bool]:
    """Find the serial in argv. Returns (serial, index, compact).

    index is the position of the serial itself for `-s X`, or the position of the
    combined token for `-sX`. compact says which form it was.
    """
    for i, arg in enumerate(argv):
        if arg == "-s" and i + 1 < len(argv):
            return argv[i + 1], i + 1, False
        if arg.startswith("-s") and len(arg) > 2 and not arg.startswith("--"):
            return arg[2:], i, True
    env_serial = os.environ.get("ANDROID_SERIAL")
    if env_serial:
        return env_serial, None, False
    return None, None, False


def _needs_forward(host: str, port: int) -> bool:
    """Only private-range hosts are worth rewriting, and only when blocked."""
    if is_loopback(host) or not is_private_ipv4(host):
        return False
    return not reachable(host, port, timeout=paths.PROBE_TIMEOUT)


def _rewrite_address_command(argv: list[str]) -> list[str] | None:
    """`connect` / `disconnect` / `pair` take the address positionally."""
    for i, arg in enumerate(argv):
        if arg not in {"connect", "disconnect", "pair"}:
            continue
        for j in range(i + 1, len(argv)):
            candidate = argv[j]
            if candidate.startswith("-"):
                continue
            split = split_serial(candidate)
            if not split:
                return None
            host, port = split
            if not _needs_forward(host, port):
                return None
            forward_port = ensure_forward(host, port)
            out = list(argv)
            out[j] = f"127.0.0.1:{forward_port}"
            return out
        return None
    return None


def _rewrite_serial(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    serial, index, compact = extract_serial(argv)
    if not serial:
        return argv, {}

    split = split_serial(serial)
    if not split:
        return argv, {}
    host, port = split
    if not _needs_forward(host, port):
        return argv, {}

    forward_port = ensure_forward(host, port)
    new_serial = f"127.0.0.1:{forward_port}"
    if index is None:
        # Came from ANDROID_SERIAL; let the env var carry the rewrite.
        return argv, {"ANDROID_SERIAL": new_serial}

    out = list(argv)
    out[index] = ("-s" + new_serial) if compact else new_serial
    return out, {}


def rewrite(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    """Rewrite argv for forwarding when needed. Returns (argv, env_overrides)."""
    rewritten = _rewrite_address_command(argv)
    if rewritten is not None:
        return rewritten, {}
    return _rewrite_serial(argv)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def command(serial: str | None, *args: str) -> list[str]:
    """Build an adb argv for `serial`, forwarding over loopback if required.

    On macOS this returns an invocation of this package's shim by absolute path,
    so it works regardless of PATH and needs no setup step. Elsewhere it returns
    a plain `adb` argv, identical to calling adb directly.
    """
    argv = ["adb"]
    if serial:
        argv += ["-s", serial]
    argv += list(args)

    if not is_supported_platform():
        return argv

    try:
        rewritten, _env = rewrite(argv)
    except (RuntimeError, FileNotFoundError):
        # Forwarding unavailable; fall back so the caller sees adb's own error.
        return argv

    # Invoke the shim by absolute path: no PATH dependency, no launcher install.
    return [sys.executable, "-m", "adb_lan_fix.cli", *rewritten[1:]]


def connect(serial: str) -> list[str]:
    """Build an `adb connect <serial>` argv, forwarded if required."""
    return command(None, "connect", serial)


def exec_adb(argv: list[str]) -> int:
    """Rewrite argv and hand off to the real adb. Used by the CLI entry point."""
    real = find_real_adb()

    if not is_supported_platform():
        os.execv(real, [real, *argv])

    try:
        new_argv, env_overrides = rewrite(argv)
    except (RuntimeError, FileNotFoundError) as exc:
        sys.stderr.write(f"adb-lan-fix: {exc}\n")
        return 1

    # connect/pair/disconnect manage their own address; everything else needs
    # the adb server to already know the loopback serial.
    if not (argv and argv[0] in {"connect", "disconnect", "pair"}):
        _ensure_server_knows(real, new_argv)

    env = os.environ.copy()
    env.update(env_overrides)
    os.execve(real, [real, *new_argv], env)
    return 0


def _ensure_server_knows(real: str, argv: list[str]) -> None:
    """Make sure the adb server knows the serial we are about to use.

    adb's server tracks connections by serial. Handed a loopback serial it has
    never seen, it reports "device not found" instead of dialling it. `adb
    connect` is idempotent, so call it whenever the serial is unknown.
    """
    serial, _index, _compact = extract_serial(argv)
    if not serial or not split_serial(serial):
        return

    def known() -> set[str]:
        try:
            listing = subprocess.run(
                [real, "devices"], capture_output=True, text=True, timeout=15
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return set()
        return {line.split()[0] for line in listing.splitlines()[1:] if line.strip()}

    if serial in known():
        return

    for _attempt in range(2):
        try:
            subprocess.run(
                [real, "connect", serial],
                capture_output=True,
                timeout=20,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return
        if serial in known():
            return
