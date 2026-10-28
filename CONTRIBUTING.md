# Contributing

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

## Ground rules

**Do not add adb protocol parsing.** The shim rewrites strings and copies bytes.
The moment it understands `CNXN`/`OPEN`/`WRTE`, it inherits every problem a
protocol proxy has: version drift, unhandled stream types, subtle framing bugs.
If a change needs protocol knowledge, it probably belongs upstream in adb.

**Do not put Python in the daemon's process chain.** macOS Local Network
authorization follows the lineage: `launchd → python3 → node` loses it. The
daemon is Node for that reason. See `docs/ROOT_CAUSE.md`.

**Keep the non-macOS path a true pass-through.** `command()` must return a plain
`adb` argv off macOS. No forwarding, no daemon, no probing.

## Testing

Unit tests must not touch the network. Patch `_needs_forward` and
`ensure_forward` to simulate blocked and reachable devices.

For a real end-to-end check, point the state directory elsewhere so you do not
disturb an existing install:

```bash
export ADB_LAN_FIX_STATE="$HOME/.local/state/adb-lan-fix-dev"
adb-lan-fix install
adb-lan-fix connect <device-ip>:<port>
adb-lan-fix doctor
```

## Reporting a bug

Include:

- macOS version (`sw_vers`)
- adb version and where it came from (`which adb`, `adb version`)
- `adb-lan-fix doctor` output
- `~/.local/state/adb-lan-fix/daemon.log`
- whether `nc -z <device-ip> <port>` succeeds (this separates a network problem
  from the permission problem this package addresses)
