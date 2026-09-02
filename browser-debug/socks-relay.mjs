#!/usr/bin/env node
/**
 * socks-relay.mjs — minimal SOCKS5 → SOCKS5(username/password) relay.
 *
 * Listens with NO auth on 127.0.0.1:<listenPort> and forwards every CONNECT
 * to an upstream SOCKS5 that requires username/password auth (e.g. IPVanish).
 *
 * Why: the free darwin-arm64 CloakBrowser Chromium (v145) does not parse
 * inline credentials in `--proxy-server=socks5://user:pass@host:port`
 * (ERR_NO_SUPPORTED_PROXIES). Chromium DOES support a no-auth local SOCKS5,
 * so this relay adds the upstream auth and presents a clean local endpoint.
 *
 * Usage:
 *   node socks-relay.mjs <listenPort> <upstreamUrl>
 *   node socks-relay.mjs 11080 socks5://user:pass@nyc.socks.ipvanish.com:1080
 *
 * Then point Chromium at:  --proxy-server=socks5://127.0.0.1:<listenPort>
 */
import net from 'node:net';

const [listenPortStr, upstreamUrl] = process.argv.slice(2);
if (!listenPortStr || !upstreamUrl) {
  console.error('usage: node socks-relay.mjs <listenPort> <socks5://user:pass@host:port>');
  process.exit(1);
}
const listenPort = Number(listenPortStr);
let upstream = upstreamUrl;
if (!/^socks5h?:\/\//i.test(upstream)) upstream = `socks5://${upstream}`;
const u = new URL(upstream);
const upHost = u.hostname;
const upPort = Number(u.port) || 1080;
const upUser = decodeURIComponent(u.username);
const upPass = decodeURIComponent(u.password);
if (!upUser || upPass === undefined) {
  console.error('upstream SOCKS5 requires username:password in the URL');
  process.exit(1);
}

// ponytail: single upstream; if throughput matters run one relay per region.

function shakeUpstream(targetHost, targetPort, cb) {
  const sock = net.connect(upPort, upHost);
  let buf = Buffer.alloc(0);
  let stage = 0;
  let done = false;
  const finish = (err, up, extra) => { if (done) return; done = true; cb(err, up, extra); };
  // Send SOCKS5 method selection: offer username/password (0x02).
  sock.on('connect', () => sock.write(Buffer.from([0x05, 0x01, 0x02])));
  sock.on('data', (chunk) => {
    buf = Buffer.concat([buf, chunk]);
    if (stage === 0) { // method selection reply
      if (buf.length < 2) return;
      if (buf[0] !== 0x05 || buf[1] !== 0x02) { // 0x02 = username/password
        sock.destroy(); return cb(new Error('upstream refused auth method'));
      }
      buf = buf.subarray(2);
      stage = 1;
      // RFC 1929 username/password sub-negotiation
      const userB = Buffer.from(upUser, 'utf8');
      const passB = Buffer.from(upPass, 'utf8');
      const auth = Buffer.allocUnsafe(3 + userB.length + passB.length);
      auth.writeUInt8(0x01, 0);
      auth.writeUInt8(userB.length, 1);
      userB.copy(auth, 2);
      auth.writeUInt8(passB.length, 2 + userB.length);
      passB.copy(auth, 3 + userB.length);
      sock.write(auth);
      return;
    }
    if (stage === 1) { // auth reply
      if (buf.length < 2) return;
      if (buf[0] !== 0x01 || buf[1] !== 0x00) { sock.destroy(); return finish(new Error('upstream auth failed')); }
      buf = buf.subarray(2);
      stage = 2;
      // SOCKS5 CONNECT request
      const req = Buffer.allocUnsafe(7 + targetHost.length);
      req.writeUInt8(0x05, 0); // VER
      req.writeUInt8(0x01, 1); // CMD=CONNECT
      req.writeUInt8(0x00, 2); // RSV
      req.writeUInt8(0x03, 3); // ATYP=DOMAIN
      req.writeUInt8(targetHost.length, 4);
      Buffer.from(targetHost, 'utf8').copy(req, 5);
      req.writeUInt16BE(targetPort, 5 + targetHost.length);
      sock.write(req);
      return;
    }
    if (stage === 2) { // CONNECT reply
      if (buf.length < 7) return;
      if (buf[0] !== 0x05 || buf[1] !== 0x00) { sock.destroy(); return finish(new Error(`upstream connect failed: 0x${buf[1].toString(16)}`)); }
      const atyp = buf[3];
      const skip = atyp === 0x01 ? 4 + 2 : atyp === 0x03 ? 1 + buf[4] + 2 : 16 + 2; // ipv4 / domain / ipv6
      const used = 4 + skip;
      if (buf.length < used) return;
      const extra = buf.subarray(used);
      stage = 3;
      finish(null, sock, extra);
    }
  });
  sock.on('error', (e) => finish(e));
}

net.createServer((client) => {
  let cbuf = Buffer.alloc(0);
  let stage = 0;
  let targetHost = null, targetPort = 0;
  let upstreamSock = null;
  let closed = false;
  const cleanup = () => { if (!closed) { closed = true; client.destroy(); upstreamSock?.destroy(); } };

  client.on('data', (chunk) => {
    if (upstreamSock) return; // handshake done — ignore (shouldn't happen, we pipe)
    cbuf = Buffer.concat([cbuf, chunk]);
    if (stage === 0) { // method selection
      if (cbuf.length < 2) return;
      if (cbuf[0] !== 0x05) { client.destroy(); return; }
      const nmethods = cbuf[1];
      if (cbuf.length < 2 + nmethods) return;
      // accept no-auth (0x00)
      client.write(Buffer.from([0x05, 0x00]));
      cbuf = cbuf.subarray(2 + nmethods);
      stage = 1;
    }
    if (stage === 1 && !upstreamSock) { // CONNECT request
      if (cbuf.length < 4) return;
      if (cbuf[0] !== 0x05 || cbuf[1] !== 0x01) { client.destroy(); return; }
      const atyp = cbuf[3];
      let hlen, off;
      if (atyp === 0x01) { hlen = 4; off = 4; }
      else if (atyp === 0x03) { hlen = cbuf[4]; off = 5; }
      else if (atyp === 0x04) { hlen = 16; off = 4; }
      else { client.destroy(); return; }
      const need = off + hlen + 2;
      if (cbuf.length < need) return;
      targetHost = atyp === 0x03
        ? cbuf.subarray(off, off + hlen).toString('utf8')
        : Array.from({ length: hlen }, (_, i) => cbuf[off + i]).join(atyp === 0x01 ? '.' : ':');
      targetPort = cbuf.readUInt16BE(off + hlen);
      const pending = cbuf.subarray(need); // client data after request (rare for SOCKS)
      cbuf = Buffer.alloc(0);
      stage = 2;
      shakeUpstream(targetHost, targetPort, (err, up, extra) => {
        if (err) {
          // 0x05, REP=0x01 (general failure)
          try { client.write(Buffer.from([0x05, 0x01, 0x00, 0x01, 0, 0, 0, 0, 0, 0])); } catch {}
          return cleanup();
        }
        upstreamSock = up;
        // reply success (bound addr 0.0.0.0:0)
        client.write(Buffer.from([0x05, 0x00, 0x00, 0x01, 0, 0, 0, 0, 0, 0]));
        if (extra && extra.length) upstreamSock.write(extra);
        if (pending && pending.length) upstreamSock.write(pending);
        client.pipe(upstreamSock);
        upstreamSock.pipe(client);
        upstreamSock.on('error', cleanup);
        upstreamSock.on('close', cleanup);
      });
    }
  });
  client.on('error', cleanup);
  client.on('close', cleanup);
}).listen(listenPort, '127.0.0.1', () => {
  console.log(`socks-relay: 127.0.0.1:${listenPort} -> ${upHost}:${upPort} (auth)`);
});

process.on('SIGINT', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));
