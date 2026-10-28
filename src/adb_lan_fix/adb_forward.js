#!/usr/bin/env node
/**
 * One loopback TCP forwarder for one device.
 *
 * Usage: node adb_forward.js <listenPort> <targetHost> <targetPort>
 *
 * Runs inside a permitted runtime (Node), so it can reach the LAN even when the
 * adb binary cannot. It copies bytes and nothing else: it does not parse the
 * adb protocol, which is why every stream type passes through intact.
 */
"use strict";

const net = require("net");

const [, , listenArg, host, targetArg] = process.argv;
const listenPort = Number(listenArg);
const targetPort = Number(targetArg);

if (!listenPort || !host || !targetPort) {
  process.stderr.write("usage: adb_forward.js <listenPort> <targetHost> <targetPort>\n");
  process.exit(2);
}

const server = net.createServer((client) => {
  const upstream = net.connect(targetPort, host);
  client.setNoDelay(true);
  upstream.setNoDelay(true);

  const shutdown = () => {
    client.destroy();
    upstream.destroy();
  };

  client.on("error", shutdown);
  upstream.on("error", shutdown);
  client.on("close", () => upstream.destroy());
  upstream.on("close", () => client.destroy());

  client.pipe(upstream);
  upstream.pipe(client);
});

server.on("error", (err) => {
  // Another instance already serves this port: not an error worth reporting.
  if (err.code === "EADDRINUSE") process.exit(0);
  process.stderr.write(`[adb-lan-fix] ${err.message}\n`);
  process.exit(1);
});

server.listen(listenPort, "127.0.0.1", () => {
  if (process.env.ADB_LAN_FIX_VERBOSE) {
    process.stderr.write(`[adb-lan-fix] 127.0.0.1:${listenPort} -> ${host}:${targetPort}\n`);
  }
});

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 500).unref();
  });
}
