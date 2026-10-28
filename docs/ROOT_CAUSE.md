# Root cause: why macOS blocks the `adb` binary

This document records what was measured, so nobody has to rediscover it. Every
claim below was verified on the machine described at the end.

## Symptom

```
$ adb connect 192.168.66.117:55555
failed to connect to '192.168.66.117:55555': No route to host
```

Meanwhile:

```
$ nc -z 192.168.66.117 55555 && echo open
open
$ ping -c1 192.168.66.117
64 bytes from 192.168.66.117: icmp_seq=0 ttl=64 time=4.1 ms
```

The device is up, the port is open, and adb cannot reach it.

## What it is

macOS Local Network privacy, applied **per binary**. The authorization decision
happens below the socket API, so every API a program could choose is refused
equally.

### Per-binary allowlist

Measured on the same host, same moment, same target address:

| Binary | Result |
| --- | --- |
| `node` | connects |
| `ruby` | connects |
| `nc` | connects |
| `adb` | `EHOSTUNREACH` (errno 65) |
| `python3` | `EHOSTUNREACH` |
| `curl` | `EHOSTUNREACH` |
| hand-written C | `EHOSTUNREACH` |

`node`, `ruby` and `nc` are in the system allowlist from earlier authorizations.
`adb` is not, and cannot be added by the means below.

### The decisive test

Apple's own Network.framework, same process, two targets:

| Target | Result |
| --- | --- |
| device `192.168.66.117:55555` | `NW_CONNECTION: WAITING errno=0` |
| gateway `192.168.66.6:80` | `READY (connected)` |

`WAITING` means the connection is parked on the Local Network authorization
decision. The gateway is reachable because it is the router, not a peer. This
proves the block is in the authorization layer, not in routing, not in the
firewall, and not in the API a program picks.

### Authorization *is* granted, and it does not help

Wrapping adb in an `.app` bundle and launching it via `open -a` does produce a
successful authorization. The system logs confirm it:

```
nehelper: Caching 2 UUID(s) for local.adbhelper
nehelper: UUID: Found for local.adbhelper
```

No `Deny` record appears. adb is still refused.

## What does not work

All of these were tried and failed. Do not spend time on them.

| Approach | Result |
| --- | --- |
| wrap adb in a `.app` bundle, launch via `open -a` | authorization registered, still refused |
| keep adb's original Google signature | refused |
| re-sign adb (ad-hoc, or as `Identifier=node`) | refused |
| `com.apple.security.network.client` entitlement | refused |
| launchd job running adb directly | refused |
| `sandbox-exec -p '(version 1)(allow default)'` | refused |
| IPv6 link-local address | refused |
| `AF_INET6` mapped socket | refused |
| non-blocking `connect()` | refused |
| `-mmacosx-version-min` 13.5 / 14 / 15 / 26 / 27 | refused |
| `adb -L tcp:5037 nodaemon server` | refused |

Not tried: Apple notarized signing (requires a developer account). Expected to
fail for the same reason as the others — the problem is not signature trust, it
is the authorization decision.

## A wrong diagnosis worth correcting

A [widely-linked issue][shield] proposes that the adb *server* inherits Local
Network permission from whatever app started it, and that `adb kill-server`
followed by a retry fixes the problem.

**That is not what is happening here.** Measured:

```
$ adb kill-server
$ adb connect 192.168.66.117:55555      # server started from Terminal
failed to connect: No route to host

$ adb kill-server
$ node -e 'execFileSync("adb", ["connect", "192.168.66.117:55555"])'
failed to connect: No route to host     # server started from node, which IS allowed
```

Starting the adb server from a permitted parent does not help, because the
blocked binary is `adb` itself, not the server's parentage. The restart trick
may work in a different configuration (where the *server* is the blocked party),
but it does not apply to the per-binary block described here.

[shield]: https://github.com/siewers/shield-commander/issues/35

## A real constraint: authorization follows the process lineage

This one matters for anyone building a daemon. Measured:

| Process chain | Reaches LAN |
| --- | --- |
| launchd → `node` | **yes** |
| launchd → `node` → `node` | **yes** |
| launchd → `python3` → `node` | **no** — `EHOSTUNREACH` |

A Node process spawned by a Python parent loses the authorization. So a
long-running forwarder must be Node all the way down, launched directly by
launchd. This package's daemon is written in Node for exactly this reason, and
`paths.py` / `adb_daemon.js` carry comments saying so.

## The fix

Route adb's traffic through a permitted runtime over loopback:

```
adb  --(127.0.0.1:15000)-->  node forwarder  --(LAN)-->  device:5555
```

Loopback is not subject to the Local Network restriction. The forwarder copies
bytes and does not parse the adb protocol, so every stream type passes through
intact: `shell`, `sync`, `logcat`, `forward`, `reverse`, `jdwp`.

The shim rewrites the serial only when the device is actually blocked, and only
for private-range addresses. When adb can reach the device, arguments pass
through unchanged.

## Test environment

- macOS 27.0.1 (26A434), Apple silicon (M2, Mac14,3)
- adb 37.0.1 from Homebrew `android-platform-tools`, Google-signed
  (`TeamIdentifier=EQHXZ8M8AV`)
- Devices: Lenovo TB138FC (Android 14), and two Android TV boards
  (Android 12 / 14), all on `192.168.66.0/24`
- Firewall off, no EDR, no USB devices attached
- Verified working with `shell`, `sync` push/pull (byte-identical), and `logcat`
