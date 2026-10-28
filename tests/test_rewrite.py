"""Tests for argument rewriting. These run on any platform.

The rewriting logic is the part most likely to break silently, so it is tested
without touching the network: `_needs_forward` is monkeypatched to a fixed
answer and `ensure_forward` to a fixed port.
"""

from __future__ import annotations

import pytest

from adb_lan_fix import shim


@pytest.fixture
def blocked(monkeypatch):
    """Simulate a device that macOS blocks, forwarding to port 15000."""
    monkeypatch.setattr(shim, "_needs_forward", lambda host, port: True)
    monkeypatch.setattr(shim, "ensure_forward", lambda host, port: 15000)


@pytest.fixture
def reachable_direct(monkeypatch):
    """Simulate a device adb can reach directly."""
    monkeypatch.setattr(shim, "_needs_forward", lambda host, port: False)


# --------------------------------------------------------------------------- #
# serial extraction
# --------------------------------------------------------------------------- #
def test_extract_serial_separated_form():
    assert shim.extract_serial(["-s", "192.168.1.50:5555", "shell", "id"]) == (
        "192.168.1.50:5555",
        1,
        False,
    )


def test_extract_serial_compact_form():
    assert shim.extract_serial(["-s192.168.1.50:5555", "shell", "id"]) == (
        "192.168.1.50:5555",
        0,
        True,
    )


def test_extract_serial_from_env(monkeypatch):
    monkeypatch.setenv("ANDROID_SERIAL", "192.168.1.50:5555")
    assert shim.extract_serial(["shell", "id"]) == ("192.168.1.50:5555", None, False)


def test_extract_serial_absent(monkeypatch):
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    assert shim.extract_serial(["shell", "id"]) == (None, None, False)


def test_extract_serial_does_not_confuse_long_flags():
    # `--serial` style flags must not be read as `-s`.
    assert shim.extract_serial(["--serial", "x"]) == (None, None, False)


# --------------------------------------------------------------------------- #
# serial rewriting
# --------------------------------------------------------------------------- #
def test_rewrites_separated_serial(blocked):
    argv, env = shim.rewrite(["-s", "192.168.1.50:5555", "shell", "id"])
    assert argv == ["-s", "127.0.0.1:15000", "shell", "id"]
    assert env == {}


def test_rewrites_compact_serial(blocked):
    argv, env = shim.rewrite(["-s192.168.1.50:5555", "shell", "id"])
    assert argv == ["-s127.0.0.1:15000", "shell", "id"]
    assert env == {}


def test_rewrites_env_serial_via_env_not_argv(blocked, monkeypatch):
    monkeypatch.setenv("ANDROID_SERIAL", "192.168.1.50:5555")
    argv, env = shim.rewrite(["shell", "id"])
    assert argv == ["shell", "id"]
    assert env == {"ANDROID_SERIAL": "127.0.0.1:15000"}


def test_leaves_reachable_device_alone(reachable_direct):
    argv, env = shim.rewrite(["-s", "192.168.1.50:5555", "shell", "id"])
    assert argv == ["-s", "192.168.1.50:5555", "shell", "id"]
    assert env == {}


def test_leaves_usb_serial_alone(blocked):
    argv, env = shim.rewrite(["-s", "HA1QKJRS", "shell", "id"])
    assert argv == ["-s", "HA1QKJRS", "shell", "id"]
    assert env == {}


def test_leaves_emulator_alone(blocked):
    argv, env = shim.rewrite(["-s", "emulator-5554", "shell", "id"])
    assert argv == ["-s", "emulator-5554", "shell", "id"]
    assert env == {}


def test_leaves_public_address_alone(monkeypatch):
    # Public IPs are not subject to the Local Network restriction, so the
    # private-range gate must reject them before any probe or forward happens.
    monkeypatch.setattr(shim, "reachable", lambda host, port, timeout=None: False)
    assert shim._needs_forward("8.8.8.8", 5555) is False
    argv, env = shim.rewrite(["-s", "8.8.8.8:5555", "shell", "id"])
    assert argv == ["-s", "8.8.8.8:5555", "shell", "id"]
    assert env == {}


def test_needs_forward_true_for_blocked_private_address(monkeypatch):
    monkeypatch.setattr(shim, "reachable", lambda host, port, timeout=None: False)
    assert shim._needs_forward("192.168.1.50", 5555) is True


def test_needs_forward_false_when_already_reachable(monkeypatch):
    monkeypatch.setattr(shim, "reachable", lambda host, port, timeout=None: True)
    assert shim._needs_forward("192.168.1.50", 5555) is False


def test_needs_forward_false_for_loopback(monkeypatch):
    monkeypatch.setattr(shim, "reachable", lambda host, port, timeout=None: False)
    assert shim._needs_forward("127.0.0.1", 15000) is False


# --------------------------------------------------------------------------- #
# address commands
# --------------------------------------------------------------------------- #
def test_rewrites_connect_address(blocked):
    argv, env = shim.rewrite(["connect", "192.168.1.50:5555"])
    assert argv == ["connect", "127.0.0.1:15000"]
    assert env == {}


def test_rewrites_pair_address(blocked):
    argv, env = shim.rewrite(["pair", "192.168.1.50:39807", "123456"])
    assert argv == ["pair", "127.0.0.1:15000", "123456"]
    assert env == {}


def test_rewrites_disconnect_address(blocked):
    argv, env = shim.rewrite(["disconnect", "192.168.1.50:5555"])
    assert argv == ["disconnect", "127.0.0.1:15000"]
    assert env == {}


def test_leaves_connect_alone_when_reachable(reachable_direct):
    argv, env = shim.rewrite(["connect", "192.168.1.50:5555"])
    assert argv == ["connect", "192.168.1.50:5555"]
    assert env == {}


def test_connect_without_address_is_untouched(blocked):
    argv, env = shim.rewrite(["connect"])
    assert argv == ["connect"]
    assert env == {}


# --------------------------------------------------------------------------- #
# command() construction
# --------------------------------------------------------------------------- #
def test_command_returns_plain_adb_off_macos(monkeypatch):
    monkeypatch.setattr(shim, "is_supported_platform", lambda: False)
    assert shim.command("192.168.1.50:5555", "shell", "id") == [
        "adb",
        "-s",
        "192.168.1.50:5555",
        "shell",
        "id",
    ]


def test_command_without_serial_off_macos(monkeypatch):
    monkeypatch.setattr(shim, "is_supported_platform", lambda: False)
    assert shim.command(None, "devices") == ["adb", "devices"]


def test_command_uses_shim_on_macos(blocked, monkeypatch):
    monkeypatch.setattr(shim, "is_supported_platform", lambda: True)
    argv = shim.command("192.168.1.50:5555", "shell", "id")
    assert argv[0].endswith("python") or "python" in argv[0]
    assert argv[1:3] == ["-m", "adb_lan_fix.cli"]
    assert argv[3:] == ["-s", "127.0.0.1:15000", "shell", "id"]


def test_command_falls_back_when_forwarding_fails(monkeypatch):
    monkeypatch.setattr(shim, "is_supported_platform", lambda: True)
    monkeypatch.setattr(shim, "_needs_forward", lambda host, port: True)

    def boom(host, port):
        raise RuntimeError("no node")

    monkeypatch.setattr(shim, "ensure_forward", boom)
    # Falls back to the direct form so adb's own error reaches the caller.
    assert shim.command("192.168.1.50:5555", "shell", "id") == [
        "adb",
        "-s",
        "192.168.1.50:5555",
        "shell",
        "id",
    ]


def test_connect_helper(blocked, monkeypatch):
    monkeypatch.setattr(shim, "is_supported_platform", lambda: True)
    argv = shim.connect("192.168.1.50:5555")
    assert argv[3:] == ["connect", "127.0.0.1:15000"]
