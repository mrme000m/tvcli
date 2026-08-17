# Layer 7: Backend API — Pine Facade (HTTP)

> **Prerequisite:** Layer 6 (Plots). The HTTP API for script lifecycle management — compile, create, update, delete, search.

---

## Base URL & Auth

```
Base: https://pine-facade.tradingview.com/pine-facade
Auth: Cookie header with SESSION + SIGNATURE (+ DEVICE_T optional)
```

**Required cookies (from browser DevTools):**
- `sessionid` → `SESSION` env var
- `sessionid_sign` → `SIGNATURE` env var
- `device_t` → `DEVICE_T` env var (optional, helps auth)

**Headers sent by tvcli (`pinefacade/client.go:baseHeaders`):**
```go
Cookie: <SESSION>; <SIGNATURE>; <DEVICE_T>
Origin: https://www.tradingview.com
Referer: https://www.tradingview.com/
User-Agent: Mozilla/5.0...
X-Requested-With: XMLHttpRequest
X-Userid: <TV_USER>  // Required for write ops
```

---

## Endpoints Reference

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| GET | `/translate/<pineId>/<version>` | Get script source + metaInfo | Read |
| GET | `/get/<pineId>/<version>` | Get raw source only (no metaInfo) | Read |
| POST | `/translate_light` | Compile/validate syntax | Read |
| POST | `/save/new` | Create new private script | Write |
| POST | `/save/next/<pineId>` | Update existing script | Write |
| POST | `/delete/<pineId>` | Delete script | Write |
| GET | `/versions/<pineId>` | Version history | Read |
| GET | `/list?filter=saved` | List your private scripts | Read |
| GET | `/pubscripts-suggest-json/` | Search public scripts | Public |

---

## Compile/Validate — `/translate_light`

```bash
# tvcli
tvcli compile script.pine

# Raw HTTP (multipart/form-data)
POST /pine-facade/translate_light?user_name=YOUR_USER&v=3
Cookie: <cookies>
Content-Type: multipart/form-data

source=<url_encoded_pine_source>
```

**Response (success):**
```json
{
  "success": true,
  "result": {
    "version": "1.0",
    "errors": []
  }
}
```

**Response (failure):**
```json
{
  "success": false,
  "result": {
    "errors": [
      {"start": {"line": 10, "column": 5}, "message": "Undeclared identifier 'xyz'"},
      {"start": {"line": 15, "column": 1}, "message": "Type mismatch: float vs int"}
    ]
  }
}
```

**tvcli parses this** (`compile.go:79-83`) and prints first 5 errors.

---

## Create Script — `/save/new`

```bash
# tvcli
tvcli create script.pine --name "My Indicator"

# Raw HTTP
POST /pine-facade/save/new?name=My%20Indicator&user_name=YOUR_USER&allow_overwrite=true
Cookie: <cookies>
Content-Type: multipart/form-data

source=<url_encoded_pine_source>
```

**Response:**
```json
{
  "success": true,
  "result": {
    "pineId": "USER;abc123def456",
    "version": "1.0"
  }
}
```

**tvcli extracts Pine ID** (`create.go:83-88`), normalizes `USER;USER;` → `USER;`, stores in local metadata (`.tv-meta.json`).

---

## Update Script — `/save/next/<pineId>`

```bash
# tvcli
tvcli push script.pine
tvcli push 1  # by local numeric ID

# Raw HTTP
POST /pine-facade/save/next/USER%3Babc123def456?user_name=YOUR_USER
Cookie: <cookies>
Content-Type: multipart/form-data

source=<url_encoded_pine_source>
```

**tvcli does:**
1. Compile first (validate)
2. SHA256 hash local source — skip push if unchanged (`push.go:67-72`)
3. Push → get new version
4. Update local metadata with new hash + version

---

## Delete Script — `/delete/<pineId>`

```bash
# tvcli
tvcli delete 1 --yes

# Raw HTTP
POST /pine-facade/delete/USER%3Babc123def456?user_name=YOUR_USER
Cookie: <cookies>
```

---

## Get Script — `/translate/<pineId>/<version>`

```bash
# tvcli
tvcli pull 1
tvcli pull USER;abc123

# Raw HTTP
GET /pine-facade/translate/USER%3Babc123def456/last
Cookie: <cookies>
```

**Returns full metaInfo** (inputs, plots, styles, palettes, script source). tvcli uses this for:
- `pull` — save source locally
- `run` — extract inputs, plots for WebSocket study creation
- `inputs` command — side-by-side diff

---

## Get Raw Source Only — `/get/<pineId>/<version>`

```bash
# tvcli (internal use)
GET /pine-facade/get/USER%3Babc123def456/last
```

**Returns:** `{source, scriptName, version, scriptAccess, created}` — no metaInfo.

Used by tvcli as fallback when `/translate` fails.

---

## Version History — `/versions/<pineId>`

```bash
GET /pine-facade/versions/USER%3Babc123def456
```

**Response:** Array of version objects. tvcli normalizes and finds latest (`client.go:153-193`).

---

## List Private Scripts — `/list?filter=saved`

```bash
# tvcli
tvcli list --remote

# Raw HTTP
GET /pine-facade/list?filter=saved
Cookie: <cookies>
```

**Response:** Array of script summaries with pineId, name, version, modified.

---

## Search Public Scripts — `/pubscripts-suggest-json/`

```bash
# tvcli
tvcli search "EMA crossover" --limit 20

# Raw HTTP
GET https://www.tradingview.com/pubscripts-suggest-json/?search=EMA%20crossover
# Note: Different domain (www.tradingview.com), no auth needed
```

**Response:**
```json
{
  "results": [
    {
      "scriptIdPart": "abc123",
      "title": "EMA Crossover Strategy",
      "scriptName": "EMA_Crossover",
      "author": {"username": "TraderJohn"},
      "type": "strategy",
      "access": "open",
      "version": "2.1"
    }
  ]
}
```

**Public script Pine ID format:** `PUB;abc123` (use with `tvcli run PUB;abc123`)

---

## Go Client Implementation (`pkg/pinefacade/client.go`)

```go
// Key methods
client.Compile(source, cookie)          // → translate_light
client.SaveNew(source, name, cookie)    // → save/new
client.SaveNext(pineID, source, cookie) // → save/next
client.Delete(pineID, cookie)           // → delete
client.Get(pineID, version, cookie)     // → translate/
client.GetSource(pineID, cookie)        // → get/
client.ListSaved(cookie)                // → list
client.SearchPublicScripts(query, cookie)
client.ListPublicScripts(offset)
```

**Multipart helper** (`postMultipart`): Builds `multipart/form-data` with `source` field.

**Pine ID normalization** (`util.go`): `USER;USER;abc` → `USER;abc`, `%3B` ↔ `;`.

**SHA256 change detection** (`util.go:SHA256`): Only push if source changed.

---

## Error Handling

| HTTP Status | Meaning | tvcli Action |
|-------------|---------|--------------|
| 200 | OK (check JSON `success`) | Parse result |
| 401 | Unauthorized | "Auth required" — check cookies |
| 403 | Forbidden | "Permission denied" — tier or ownership |
| 404 | Not found | "Script not found" |
| 429 | Rate limited | Retry with backoff |
| 5xx | Server error | Retry |

**Common error: "study limit" on run** — not a Pine Facade error. See Layer 8 (WebSocket).

---

## Next Layer

→ **Layer 8: WebSocket Runtime** → `api/websocket.md`

Covers: Real-time execution, chart sessions, studies, protocol framing, and the Go `tradingview` package.