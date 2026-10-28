"""Unit tests for the pure helpers. No network, no side effects."""

from __future__ import annotations

import pytest

from adb_lan_fix.net import is_loopback, is_private_ipv4, split_serial


@pytest.mark.parametrize(
    "value,expected",
    [
        ("192.168.1.50:5555", ("192.168.1.50", 5555)),
        ("10.0.0.1:5555", ("10.0.0.1", 5555)),
        ("172.16.0.9:5037", ("172.16.0.9", 5037)),
        ("127.0.0.1:15000", ("127.0.0.1", 15000)),
        ("192.168.66.117:55555", ("192.168.66.117", 55555)),
    ],
)
def test_split_serial_accepts_network_serials(value, expected):
    assert split_serial(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "emulator-5554",       # emulator name
        "HA1QKJRS",            # USB serial
        "192.168.1.50",        # no port
        "192.168.1.50:",       # empty port
        "192.168.1.50:abc",    # non-numeric port
        "192.168.1.50:0",      # port out of range
        "192.168.1.50:70000",  # port out of range
        "a:b:c",               # too many colons
        "",
    ],
)
def test_split_serial_rejects_non_network_serials(value):
    assert split_serial(value) is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_is_loopback_true(host):
    assert is_loopback(host) is True


@pytest.mark.parametrize("host", ["192.168.1.50", "10.0.0.1", "example.com"])
def test_is_loopback_false(host):
    assert is_loopback(host) is False


@pytest.mark.parametrize(
    "host",
    [
        "10.0.0.1",
        "10.255.255.254",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.0.1",
        "192.168.255.254",
        "169.254.1.1",
    ],
)
def test_is_private_ipv4_true(host):
    assert is_private_ipv4(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "8.8.8.8",          # public
        "172.15.0.1",       # just below the 172.16/12 range
        "172.32.0.1",       # just above it
        "192.169.0.1",      # not 192.168/16
        "11.0.0.1",
        "example.com",      # hostname
        "192.168.1",        # too few octets
        "192.168.1.1.1",    # too many octets
        "192.168.1.999",    # octet out of range
        "",
    ],
)
def test_is_private_ipv4_false(host):
    assert is_private_ipv4(host) is False
