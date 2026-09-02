# Bun Runtime Migration Evaluation

**Last Updated:** 2025-12-03
**Status:** Research Complete
**Context:** [Anthropic acquired Bun](https://www.anthropic.com/news/anthropic-acquires-bun-as-claude-code-reaches-usd1b-milestone) (December 2025) as Claude Code reached $1B milestone

---

## Executive Summary

| Metric | Assessment |
|--------|------------|
| **Feasibility** | High - bdg uses standard Node.js APIs |
| **Effort** | Medium (1-2 weeks migration + testing) |
| **Risk** | Low-Medium |
| **Strategic Value** | High - aligns with Claude Code ecosystem |

---

## Strategic Context

Anthropic's acquisition of Bun signals a shift toward Bun as the preferred JavaScript runtime for AI-assisted development tooling. For bdg (a CLI designed for AI agents), this creates alignment opportunities:

- **Claude Code integration** - Bun-compiled binaries may become first-class citizens
- **Distribution simplicity** - Single binary, no Node.js dependency
- **Performance gains** - Faster startup, lower memory footprint
- **Ecosystem alignment** - bdg + Claude Code + Bun as unified stack

---

## Pros

### 1. Zero-Dependency Distribution
```bash
bun build --compile --target=bun ./src/index.ts --outfile=bdg
```
- Single 50-100MB executable
- No Node.js installation required
- Cross-compile for Linux/macOS/Windows from any platform

### 2. Cross-Platform Binary Distribution (Killer Feature)

Build once, distribute everywhere:
```bash
# From any OS, compile for all targets
bun build --compile --target=bun-linux-x64 ./src/index.ts --outfile=bdg-linux
bun build --compile --target=bun-darwin-arm64 ./src/index.ts --outfile=bdg-macos
bun build --compile --target=bun-windows-x64 ./src/index.ts --outfile=bdg.exe
```

**Why this matters:**
- Windows users don't need WSL or Node.js setup
- Linux servers get a single binary, no `npm install`
- CI/CD can download binary instead of installing dependencies
- Agents on any platform get identical tool behavior

### 3. Performance Improvements (Benchmarked)

#### Startup Time
| Runtime | Cold Start | Improvement |
|---------|-----------|-------------|
| Node.js | ~42ms | baseline |
| Bun | ~8ms | **5x faster** |

#### WebSocket Performance (CDP Connection)
| Metric | Node.js + ws | Bun native | Improvement |
|--------|--------------|------------|-------------|
| Throughput | 19K msg/s | 27-38K msg/s | **2x faster** |
| Latency p50 | 18ms | 6ms | **3x faster** |
| Latency p99 | 78ms | 24ms | **3x faster** |
| Memory/conn | 12 KB | 4 KB | **67% less** |

Sources: [Lemire benchmark](https://lemire.me/blog/2023/11/25/a-simple-websocket-benchmark-in-javascript-node-js-versus-bun/), [Strapi comparison](https://strapi.io/blog/bun-vs-nodejs-performance-comparison-guide)

#### Is it noticeable?

**Honest assessment:** For human users, probably not. Individual commands feel instant either way.

**For agent workflows:** Yes, it compounds. An agent running 100 bdg commands:
- Saves ~3.4 seconds on startup alone (34ms × 100)
- CDP-heavy operations (DOM traversal, network inspection) benefit from lower WebSocket latency
- Memory reduction helps when running alongside other tools

**Where you'll actually feel it:**
- `bun install` (2-3s) vs `npm install` (20-60s) during development
- `bun test` runs 5-10x faster than Jest
- CI pipelines complete faster

### 4. Simplified Build Pipeline

| Current (Node.js) | After (Bun) |
|-------------------|-------------|
| `tsc && tsc-alias` | `bun build --compile` |
| `tsx --test` | `bun test` |
| `npm install` (~10s) | `bun install` (~2s) |
| Path aliases via `tsc-alias` | Native `tsconfig.json` paths |

### 5. Strategic Ecosystem Alignment
- Anthropic owns Bun; future Claude Code features may prioritize Bun
- bdg as Claude Code skill benefits from same-runtime optimization
- Potential for deeper integration (Bun APIs for agent tooling)

### 6. All Dependencies Compatible
| Package | Status | Notes |
|---------|--------|-------|
| `chrome-launcher` | Works | Tested in Bun v0.6.7+ |
| `commander` | Works | Pure JavaScript |
| `devtools-protocol` | Works | Type definitions only |
| `ws` | Works | Can optionally use native Bun WebSocket |

---

## Cons

### 1. Binary Size Overhead
- **50-100MB** executable vs ~2KB npm package entry point
- Mitigation: `--minify` saves MBs; UPX compression can reduce by ~67%
- Trade-off: Larger download but zero runtime dependencies

### 2. Detached Process Spawning (Daemon Architecture)
```typescript
// Current pattern in src/daemon/launcher.ts:100-109
spawn(process.execPath, [daemonScriptPath], {
  detached: true,  // ⚠️ Partial support in Bun
  stdio: 'ignore',
});
```
- `detached: true` has open [feature request](https://github.com/oven-sh/bun/issues/1442)
- Workaround: Use `Bun.spawn()` with `unref()` - behavior may differ
- Risk: Daemon might not fully detach; requires testing

### 3. IPC Limitations
- Cannot pass socket handles between processes (bdg doesn't use this)
- File descriptors cannot be passed between workers
- JSON serialization works; binary protocols need validation

### 4. Less Mature Error Reporting
- Bun's error messages sometimes less informative than Node.js
- Stack traces may differ in edge cases
- Debugging unfamiliar issues harder initially

### 5. Documentation Gaps
- Rapidly improving but Node.js has 15+ years of Stack Overflow answers
- Some edge cases undocumented
- Community smaller (though growing fast: 82K GitHub stars)

### 6. Testing Unknown Unknowns
- WebSocket ping/pong timing edge cases
- Unix socket behavior under load
- Signal handling (SIGTERM, SIGINT) in daemon
- fsevents (macOS) native module in dev dependencies

---

## Bun-Specific Features for bdg

Beyond runtime migration, Bun offers APIs that could improve bdg's architecture.

### 1. `bun:sqlite` - Replace JSON Session Files

**Current:** Session state stored as JSON files (`~/.bdg/session.meta.json`)

**With Bun SQLite:**
```typescript
import { Database } from "bun:sqlite";

const db = new Database("~/.bdg/session.db");
db.run(`CREATE TABLE IF NOT EXISTS queries (
  selector TEXT PRIMARY KEY,
  nodeIds TEXT,
  navigationId TEXT,
  timestamp INTEGER
)`);

// Query cache with automatic invalidation
db.query("SELECT * FROM queries WHERE navigationId = ?").all(currentNavId);
```

**Benefits:**
- 3-6x faster than better-sqlite3, 8-9x faster than Deno SQLite
- Query cache survives daemon restarts
- Atomic transactions (no corrupt JSON on crash)
- SQL queries for telemetry analysis

---

### 2. Native `WebSocket` - Replace `ws` Library

**Current:** `ws` npm package with Node.js event API
```typescript
import WebSocket from 'ws';
const ws = new WebSocket(cdpUrl, {
  perMessageDeflate: false,
  handshakeTimeout: 5000,
});
ws.on('message', (data) => { ... });
ws.ping();  // Manual keepalive
```

**With Bun:**
```typescript
// No import - global like browsers
const ws = new WebSocket(cdpUrl);
ws.addEventListener("message", (event) => {
  const data = event.data;  // string or ArrayBuffer
});
// Ping/pong handled automatically by runtime
```

**Migration changes:**
| Aspect | `ws` library | Bun native |
|--------|--------------|------------|
| Import | `import WebSocket from 'ws'` | Global |
| Events | `.on('message', cb)` | `.addEventListener()` |
| Ping/pong | Manual `ws.ping()` | Automatic |
| Backpressure | Manual | `.send()` returns status |

**Benefits:**
- Remove `ws` from dependencies
- 7x throughput improvement (Bun benchmark)
- Backpressure handling: `.send()` returns `-1` (queued), `0` (dropped), `1+` (sent)
- Binary data support for future CDP features

---

### 3. `Bun.$` Shell - Cross-Platform Process Management

**Current:** Platform-specific spawn with manual escaping
```typescript
import { spawn } from 'child_process';

// Platform-specific logic
if (process.platform === 'darwin') {
  spawn('pkill', ['-f', 'Google Chrome.*--remote-debugging']);
} else if (process.platform === 'linux') {
  spawn('pkill', ['-f', 'chrome.*--remote-debugging']);
}
// Windows: completely different approach needed
```

**With Bun Shell:**
```typescript
import { $ } from "bun";

// Cross-platform - same code everywhere
const result = await $`pgrep -f "chrome.*remote-debugging"`.nothrow().text();
const pids = result.trim().split('\n').filter(Boolean);

for (const pid of pids) {
  await $`kill ${pid}`.nothrow();
}

// Session cleanup with glob
await $`rm -f ~/.bdg/*.{sock,pid,json}`.nothrow();

// Check Chrome installed
const chrome = await $`which google-chrome || which chromium`.nothrow().text();
```

**Key features:**
| Feature | Description |
|---------|-------------|
| `.nothrow()` | Don't crash on non-zero exit |
| `.text()` | Capture output as string |
| `.lines()` | Iterate output line-by-line |
| `.quiet()` | Suppress printing |
| Glob patterns | `*.{sock,pid}` works natively |
| Auto-escaping | `${variable}` is injection-safe |

**Benefits:**
- Same code runs on Windows/Linux/macOS
- Built-in commands: `rm`, `mkdir`, `cat`, `which`, `ls`
- No shell injection vulnerabilities
- Cleaner than spawn + manual argument arrays

---

### 4. `Bun.file()` / `Bun.write()` - Faster HAR Export

**With Bun:**
```typescript
await Bun.write(harPath, JSON.stringify(harData, null, 2));
```

**Benefits:**
- 2x faster for large files (uses `sendfile`/`copy_file_range` on Linux)
- Lazy file loading (doesn't read until needed)

---

### Feature Adoption Priority

| Feature | Impact | Effort | Recommendation |
|---------|--------|--------|----------------|
| SQLite for session/cache | High | Medium | Yes - significant improvement |
| Native WebSocket | High | Low | Yes - drop `ws` dependency |
| Bun.$ shell | Medium | Low | Yes - cleaner cleanup code |
| Bun.file/write | Low | Low | Nice to have |
| Bun.sleep | Trivial | Trivial | Minor cleanup |

**Biggest wins:** SQLite for query cache + native WebSocket for CDP connection.

---

## Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Daemon doesn't detach properly | High | Medium | Test early; fallback to separate process |
| WebSocket reconnection differs | Medium | Low | Keep `ws` library initially |
| Binary too large for distribution | Low | Low | UPX compression; users accept trade-off |
| Edge case Node.js API gaps | Medium | Low | Comprehensive test suite catches issues |
| Build breaks on Bun updates | Low | Medium | Pin Bun version; test before upgrading |

---

## Migration Plan

### Phase 1: Compatibility Testing (2-3 days)
1. Run existing tests with `bun test`
2. Test daemon spawn/unref behavior manually
3. Test Unix socket IPC end-to-end
4. Test WebSocket CDP communication under load

### Phase 2: Build Pipeline (1-2 days)
1. Create `bun build --compile` script
2. Test cross-platform compilation (Linux, macOS, Windows)
3. Add binary size optimization flags (`--minify`, `--bytecode`)
4. Update CI/CD for binary releases (GitHub Actions)

### Phase 3: Code Adjustments (2-3 days)
1. Replace `child_process.spawn` with `Bun.spawn` if needed
2. Optionally migrate `ws` to native Bun WebSocket
3. Remove `tsc-alias` dependency (Bun handles paths natively)
4. Update package.json scripts

### Phase 4: Testing & Polish (3-4 days)
1. End-to-end testing on all target platforms
2. Smoke tests for binary distribution
3. Performance benchmarking vs Node.js
4. Documentation updates

---

## Recommended Approach

**Hybrid Strategy** - maintain both runtimes initially:

```json
{
  "scripts": {
    "build": "tsc && tsc-alias",
    "build:binary": "bun build --compile --minify --bytecode ./src/index.ts --outfile=bdg",
    "build:binary:linux": "bun build --compile --minify --target=bun-linux-x64 ./src/index.ts --outfile=bdg-linux",
    "build:binary:macos": "bun build --compile --minify --target=bun-darwin-arm64 ./src/index.ts --outfile=bdg-macos",
    "build:binary:windows": "bun build --compile --minify --target=bun-windows-x64 ./src/index.ts --outfile=bdg.exe"
  }
}
```

**Benefits:**
- Node.js remains primary for development and npm distribution
- Bun binary as optional distribution for zero-dependency installs
- Gradual migration path; can fully switch when confident
- npm package users unaffected

---

## Decision Matrix

| If you prioritize... | Recommendation |
|---------------------|----------------|
| Ecosystem alignment with Claude Code | Migrate to Bun |
| Zero-dependency distribution | Migrate to Bun |
| Maximum compatibility confidence | Stay on Node.js |
| Smallest possible package | Stay on Node.js (npm) |
| Fastest CLI startup | Migrate to Bun |
| Enterprise stability guarantees | Wait 6 months, then migrate |

---

## References

- [Anthropic Acquires Bun](https://www.anthropic.com/news/anthropic-acquires-bun-as-claude-code-reaches-usd1b-milestone)
- [Bun Single-file Executable Docs](https://bun.com/docs/bundler/executables)
- [Bun Node.js Compatibility](https://bun.com/docs/runtime/nodejs-apis)
- [Bun net.Socket Reference](https://bun.sh/reference/node/net/Socket)
- [Bun Child Process Spawn](https://bun.com/docs/runtime/child-process)
- [Bun Binary Size Discussion](https://github.com/oven-sh/bun/issues/5854)
- [Bun Detached Process Feature Request](https://github.com/oven-sh/bun/issues/1442)
- [Bun Cross-Compile Support](https://developer.mamezou-tech.com/en/blogs/2024/05/20/bun-cross-compile/)
- [Bun SQLite Documentation](https://bun.com/docs/api/sqlite)
- [Bun Shell Documentation](https://bun.com/docs/runtime/shell)
- [Bun WebSocket Documentation](https://bun.com/docs/runtime/http/websockets)
- [Bun File I/O Documentation](https://bun.com/docs/api/file-io)

---

## Changelog

- **2025-12-03** - Expanded WebSocket migration details (API changes, backpressure) and Bun Shell examples (cleanup, globs)
- **2025-12-03** - Added Bun-specific features section (SQLite, native WebSocket, Bun.$, file I/O)
- **2025-12-03** - Added benchmarked performance data, cross-platform distribution details, honest "is it noticeable?" assessment
- **2025-12-03** - Initial research following Anthropic's Bun acquisition announcement
