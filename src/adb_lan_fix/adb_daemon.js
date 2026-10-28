#!/usr/bin/env node
/**
 * Resident forwarder supervisor, managed by launchd.
 *
 * Written in Node on purpose. macOS Local Network authorization is inherited
 * along the process lineage, and a launchd job whose main program is Node can
 * reach LAN devices while a Python-parented child cannot. Verified:
 *
 *   launchd -> node            connects
 *   launchd -> node -> node    connects
 *   launchd -> python3 -> node EHOSTUNREACH
 *
 * So the daemon must be Node all the way down. Do not reintroduce a Python
 * supervisor in this chain.
 *
 * Keeps one loopback forwarder alive per registered device, across reboots and
 * adb server restarts. Devices register themselves the first time the shim
 * needs a forward; the registry persists.
 *
 * Control protocol: newline-delimited JSON over a unix socket.
 *   -> {"op":"ensure","host":"192.168.1.50","port":5555}
 *   <- {"ok":true,"port":15000}
 *   -> {"op":"list"}
 *   <- {"ok":true,"devices":{"192.168.1.50:5555":15000}}
 *   -> {"op":"ping"}
 *   <- {"ok":true,"pid":123}
 */
"use strict";

const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const STATE_DIR =
  process.env.ADB_LAN_FIX_STATE ||
  path.join(os.homedir(), ".local", "state", "adb-lan-fix");
const REGISTRY = path.join(STATE_DIR, "devices.json");
const PORTS = path.join(STATE_DIR, "ports.json");
const SOCKET = path.join(STATE_DIR, "control.sock");
const LOG = path.join(STATE_DIR, "daemon.log");
const FORWARDER = path.join(__dirname, "adb_forward.js");

const PORT_BASE = 15000;
const PORT_SPAN = 2000;
const RESPAWN_DELAY = 1000;
const MAX_RESPAWN_DELAY = 30000;
const VERBOSE = Boolean(process.env.ADB_LAN_FIX_VERBOSE);

let stopping = false;
const children = new Map(); // key -> ChildProcess
const delays = new Map(); // key -> ms
let devices = {}; // key -> loopback port

function log(message) {
  const line = `${new Date().toISOString()} ${message}\n`;
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    fs.appendFileSync(LOG, line);
    if (fs.statSync(LOG).size > 512 * 1024) {
      const kept = fs.readFileSync(LOG, "utf8").split("\n").slice(-1000).join("\n");
      fs.writeFileSync(LOG, kept + "\n");
    }
  } catch (_) {
    /* logging must never break the daemon */
  }
  if (VERBOSE) process.stderr.write(line);
}

function loadJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (_) {
    return fallback;
  }
}

function saveJson(file, value) {
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(value, null, 2));
    fs.renameSync(tmp, file);
  } catch (err) {
    log(`state write failed for ${path.basename(file)}: ${err.message}`);
  }
}

function reachable(port, timeout = 400) {
  return new Promise((resolve) => {
    const sock = net.connect(port, "127.0.0.1");
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      sock.destroy();
      resolve(value);
    };
    sock.setTimeout(timeout);
    sock.on("connect", () => done(true));
    sock.on("error", () => done(false));
    sock.on("timeout", () => done(false));
  });
}

function portFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once("error", () => resolve(false));
    srv.once("listening", () => srv.close(() => resolve(true)));
    srv.listen(port, "127.0.0.1");
  });
}

async function allocatePort() {
  const used = new Set(Object.values(devices));
  for (let offset = 0; offset < PORT_SPAN; offset += 1) {
    const candidate = PORT_BASE + offset;
    if (used.has(candidate)) continue;
    if (await portFree(candidate)) return candidate;
  }
  return null;
}

function spawnForwarder(key, host, port) {
  const existing = children.get(key);
  if (existing && existing.exitCode === null) return;
  const listen = devices[key];
  if (!listen) return;
  try {
    const child = spawn(process.execPath, [FORWARDER, String(listen), host, String(port)], {
      stdio: "ignore",
      detached: true,
    });
    child.unref();
    children.set(key, child);
    delays.set(key, RESPAWN_DELAY);
    log(`started forwarder ${key} -> 127.0.0.1:${listen} (pid ${child.pid})`);
    child.on("exit", (code) => log(`forwarder ${key} exited (${code})`));
  } catch (err) {
    log(`spawn failed for ${key}: ${err.message}`);
  }
}

async function ensure(host, port) {
  const key = `${host}:${port}`;
  const existing = devices[key];
  if (existing && (await reachable(existing))) return existing;

  if (!existing) {
    const allocated = await allocatePort();
    if (allocated === null) {
      log(`no free port for ${key}`);
      return null;
    }
    devices[key] = allocated;
    saveJson(REGISTRY, devices);
    log(`registered ${key} -> ${allocated}`);
  }
  spawnForwarder(key, host, port);

  const target = devices[key];
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (await reachable(target, 300)) {
      saveJson(PORTS, devices);
      return target;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  log(`forwarder for ${key} did not come up`);
  return null;
}

async function supervise() {
  while (!stopping) {
    for (const [key, listen] of Object.entries(devices)) {
      const child = children.get(key);
      const alive = child && child.exitCode === null;
      if (alive && (await reachable(listen))) {
        delays.set(key, RESPAWN_DELAY);
        continue;
      }
      if (alive) continue; // still starting
      const wait = delays.get(key) || RESPAWN_DELAY;
      log(`forwarder ${key} down; respawning in ${Math.round(wait / 1000)}s`);
      await new Promise((r) => setTimeout(r, wait));
      delays.set(key, Math.min(wait * 2, MAX_RESPAWN_DELAY));
      if (devices[key]) {
        const cut = key.lastIndexOf(":");
        spawnForwarder(key, key.slice(0, cut), Number(key.slice(cut + 1)));
      }
    }
    saveJson(PORTS, devices);
    await new Promise((r) => setTimeout(r, 2000));
  }
}

function handle(payload) {
  const op = payload && payload.op;
  if (op === "ping") return { ok: true, pid: process.pid };
  if (op === "list") return { ok: true, devices };
  if (op === "ensure") {
    const host = String(payload.host || "");
    const port = Number(payload.port || 0);
    if (!host || !port) return { ok: false, error: "host and port required" };
    return ensure(host, port).then((allocated) =>
      allocated === null
        ? { ok: false, error: "could not allocate a forward" }
        : { ok: true, port: allocated }
    );
  }
  return { ok: false, error: `unknown op ${op}` };
}

function startServer() {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  try {
    fs.unlinkSync(SOCKET);
  } catch (_) {
    /* no stale socket */
  }
  const server = net.createServer((conn) => {
    conn.setTimeout(15000);
    let buffer = "";
    conn.on("data", (chunk) => {
      buffer += chunk.toString("utf8");
      const nl = buffer.indexOf("\n");
      if (nl === -1) return;
      const line = buffer.slice(0, nl);
      buffer = buffer.slice(nl + 1);
      let payload;
      try {
        payload = JSON.parse(line);
      } catch (_) {
        conn.end(JSON.stringify({ ok: false, error: "bad json" }) + "\n");
        return;
      }
      Promise.resolve(handle(payload))
        .then((reply) => conn.end(JSON.stringify(reply) + "\n"))
        .catch((err) => conn.end(JSON.stringify({ ok: false, error: String(err) }) + "\n"));
    });
    conn.on("error", () => {});
  });
  server.on("error", (err) => log(`control socket error: ${err.message}`));
  server.listen(SOCKET, () => {
    try {
      fs.chmodSync(SOCKET, 0o600);
    } catch (_) {}
    log(`daemon listening on ${SOCKET}`);
  });
  return server;
}

function shutdown(signal) {
  if (stopping) return;
  stopping = true;
  log(`shutting down (${signal})`);
  for (const child of children.values()) {
    if (child.exitCode === null) {
      try {
        child.kill("SIGTERM");
      } catch (_) {}
    }
  }
  try {
    fs.unlinkSync(SOCKET);
  } catch (_) {}
  process.exit(0);
}

function main() {
  devices = loadJson(REGISTRY, {});
  for (const sig of ["SIGTERM", "SIGINT"]) process.on(sig, () => shutdown(sig));
  startServer();
  saveJson(PORTS, devices);
  log(`daemon started with ${Object.keys(devices).length} device(s)`);
  supervise().catch((err) => log(`supervisor crashed: ${err.message}`));
}

main();
