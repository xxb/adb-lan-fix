# How the pieces fit

Three processes, one job: get adb's bytes to the device when adb itself is not
allowed to send them.

```
  your script / shell
        │
        │  adb -s 192.168.1.50:5555 shell ...
        ▼
  ┌─────────────────┐
  │  shim (Python)  │   decides: direct or forward?
  └────────┬────────┘
           │  adb -s 127.0.0.1:15000 shell ...
           ▼
  ┌─────────────────┐
  │  real adb       │   talks to its server on 5037 as usual
  └────────┬────────┘
           │  connects to 127.0.0.1:15000
           ▼
  ┌─────────────────┐
  │ forwarder (Node)│   loopback is not restricted
  └────────┬────────┘
           │
           ▼
     device 192.168.1.50:5555
```

## The four moving parts

### `shim.py` — the decision maker

Runs on every adb invocation. It:

1. Finds the serial in argv (`-s X`, `-sX`, or `ANDROID_SERIAL`).
2. If the serial is a private-range `host:port` and a TCP probe fails, asks the
   daemon for a loopback port and rewrites the serial.
3. For `connect` / `disconnect` / `pair`, rewrites the positional address
   instead, since those commands manage their own connection.
4. Makes sure the adb server knows the loopback serial before handing off.
   adb's server tracks connections by serial; handed an unknown one it reports
   `device not found` rather than dialling it. `adb connect` is idempotent, so
   the shim calls it when the serial is unseen.

It never parses the adb protocol. It copies bytes and rewrites strings.

### `adb_daemon.js` — the resident supervisor

A launchd job (`io.github.adb-lan-fix.daemon`) that keeps one forwarder alive per
registered device. It:

- Starts at login (`RunAtLoad`), restarts on crash (`KeepAlive`)
- Persists the device registry to `~/.local/state/adb-lan-fix/devices.json`, so a
  device that worked once keeps working across reboots
- Accepts new devices over a unix socket (`control.sock`), newline-delimited JSON
- Respawns dead forwarders with exponential backoff

**Written in Node deliberately.** macOS Local Network authorization follows the
process lineage: `launchd → python3 → node` loses it, `launchd → node` keeps it.
A Python supervisor would break every forwarder. See
[ROOT_CAUSE.md](ROOT_CAUSE.md#a-real-constraint-authorization-follows-the-process-lineage).

### `adb_forward.js` — one device, one port

A TCP pipe. `client.pipe(upstream)` and `upstream.pipe(client)`. No framing, no
parsing, no state. Because it does not understand the protocol, it cannot corrupt
it.

### `cli.py` — the entry point

Subcommands (`install`, `uninstall`, `doctor`, `status`, `version`) manage the
daemon. Anything else is adb arguments, forwarded and handed to the real adb via
`execve`.

## Why loopback

The restriction applies to connections to *other devices on the local network*.
Loopback is not a local-network peer, so it is exempt. A permitted runtime
(`node`) makes the LAN connection on adb's behalf.

This is why the fix is a byte pipe and not a protocol proxy: the interception
point is `connect()`, not the adb wire format. Reimplementing adb's smart-socket
multiplexer would be far more code, would need to track protocol version drift,
and would break on any stream type it did not anticipate. A pipe has none of
those failure modes.

## State layout

```
~/.local/state/adb-lan-fix/
├── devices.json      # device key -> loopback port (survives reboots)
├── ports.json        # last known port assignments
├── control.sock      # daemon control socket (mode 0600)
├── daemon.log        # decisions, respawns, errors
├── launchd.out.log
└── launchd.err.log
```

Override the location with `ADB_LAN_FIX_STATE` (used by the test suite).

## Port allocation

Forwarders listen on `127.0.0.1` in the range 15000–16999. A port is allocated on
first use, checked for availability, and recorded in `devices.json` so it stays
stable across restarts.

## Failure modes and what happens

| Failure | Behaviour |
| --- | --- |
| daemon not running | shim starts it (launchd, else detached) |
| daemon cannot start | one-shot forwarder for this adb session |
| no Node.js | `command()` falls back to plain `adb`, so adb's own error surfaces |
| device unreachable | forward is attempted; if it fails, adb's error reaches you |
| adb server restarted | shim re-`connect`s the loopback serial on next use |
| forwarder dies | daemon respawns with backoff |
| machine reboots | launchd starts the daemon; registry reloads |

Nothing here raises into your script. The worst case is the behaviour you had
before installing.
