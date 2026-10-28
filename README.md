# adb-lan-fix

**Make `adb` reach LAN devices on macOS again.**

On macOS 15+, `adb connect 192.168.1.50:5555` fails with `No route to host` even
though the device is reachable, debugging is enabled, and you granted every
permission the system asked for. This package fixes that, transparently.

```bash
pip install adb-lan-fix
adb-lan-fix install
adb-lan-fix connect 192.168.1.50:5555
```

Your existing scripts keep working. Same serials, same commands, no changes.

---

## The problem

macOS applies **Local Network privacy per binary**. The `adb` binary is refused
outbound TCP connections to private-range addresses, while other runtimes on the
same machine connect fine:

| Binary | Reaches LAN device |
| --- | --- |
| `node` | yes |
| `ruby` | yes |
| `nc` | yes |
| **`adb`** | **no** — `EHOSTUNREACH` (errno 65) |
| `python3` | no |
| `curl` | no |
| hand-written C | no |

It is not a firewall, not routing, and not a permission prompt you missed.
Granting Local Network permission to a wrapper app *does* register the
authorization — the system logs confirm it — and adb is still refused.

Full evidence, including the decisive Network.framework test and the eleven
approaches that do **not** work, is in [docs/ROOT_CAUSE.md](docs/ROOT_CAUSE.md).

## How it works

A permitted runtime (Node) carries the bytes over loopback, which is not subject
to the restriction:

```
adb  --(127.0.0.1:15000)-->  node forwarder  --(LAN)-->  device:5555
```

The shim rewrites the network serial only when the device is actually blocked.
When adb can reach the device directly, arguments pass through untouched.

**It never parses the adb protocol.** It copies bytes. That is why every stream
type works unchanged — `shell`, `sync` (push/pull), `logcat`, `forward`,
`reverse`, `jdwp`, and anything added later.

## Install

Requires Python 3.9+ and Node.js (the forwarder runs inside Node).

```bash
pip install adb-lan-fix
adb-lan-fix install
```

`install` registers a launchd job so the daemon starts at login and restarts if
it crashes. Nothing else to configure.

Optionally, put an `adb` launcher on your PATH so interactive shells get the fix
too:

```bash
adb-lan-fix install --bin-dir ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.zshrc
```

## Use

### From Python

```python
import subprocess
from adb_lan_fix import command

subprocess.run(command("192.168.1.50:5555", "shell", "getprop", "ro.product.model"))
subprocess.run(command(None, "connect", "192.168.1.50:5555"))
```

`command()` returns a plain argv list, so it drops into `subprocess.run`,
`check_output`, and `Popen` alike. On macOS it returns an invocation of this
package by absolute path, so it does not depend on PATH at all.

### From a shell

```bash
adb-lan-fix -s 192.168.1.50:5555 shell getprop ro.product.model
adb-lan-fix connect 192.168.1.50:5555
adb-lan-fix devices -l
```

Any argument that is not a subcommand is passed to the real adb with forwarding
applied. So `adb-lan-fix` behaves like `adb`.

### Diagnostics

```bash
adb-lan-fix doctor     # per-device reachability and forward status
adb-lan-fix status     # daemon state and registered forwards
```

```
$ adb-lan-fix status
platform     : darwin
supported    : yes
state dir    : ~/.local/state/adb-lan-fix
launch agent : ~/Library/LaunchAgents/io.github.adb-lan-fix.daemon.plist (present)
daemon       : running
forwards     :
  192.168.1.50:5555   -> 127.0.0.1:15000  [up]
```

## Wireless pairing

`adb pair` needs the same treatment, since the pairing port is also on the LAN:

```bash
adb-lan-fix pair 192.168.1.50:39807 123456
```

The shim forwards it automatically. Pairing codes are single-use: retrying the
same code gives `protocol fault (couldn't read status message)`, which means the
code was already consumed, not that forwarding failed.

## What it does not do

- **It does not grant adb Local Network permission.** It cannot; that decision
  is per-binary and below the API layer. It routes around the block instead.
- **It does not work on iOS devices or anything that requires adb to be the
  connecting process.** Nothing does; adb is always the client.
- **It does not touch non-private addresses.** Public IPs are not subject to the
  restriction, so they are never rewritten.

## Platform support

| Platform | Behaviour |
| --- | --- |
| macOS 15+ | forwarding applied where needed |
| macOS 14 and older | pass-through (no restriction) |
| Linux | pass-through |
| Windows | pass-through |

On every non-macOS platform the package is a no-op: `command()` returns a plain
`adb` argv, and `install` prints that there is nothing to do.

## Uninstall

```bash
adb-lan-fix uninstall              # removes launchd job and state
adb-lan-fix uninstall --keep-state # keeps the device registry and logs
pip uninstall adb-lan-fix
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `No route to host` persists | not going through the shim | use `adb-lan-fix` or `command()` |
| `device '127.0.0.1:15xxx' not found` | adb server does not know the forward | `adb kill-server`, then retry |
| `cannot find the real adb binary` | platform-tools missing | install platform-tools or set `ADB_LAN_FIX_REAL_ADB` |
| `node is not on PATH` | no Node.js | install Node.js |
| daemon not running | launchd job missing | `adb-lan-fix install` |
| `protocol fault` on `pair` | code already used | generate a fresh code |

Logs:

```bash
cat ~/.local/state/adb-lan-fix/daemon.log
launchctl print gui/$(id -u)/io.github.adb-lan-fix.daemon
```

Set `ADB_LAN_FIX_VERBOSE=1` to make the shim and daemon narrate their decisions.

## License

MIT
