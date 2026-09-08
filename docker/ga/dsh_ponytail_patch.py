#!/usr/bin/env python3
"""dsh_ponytail_patch.py — allow `dsh web --host 0.0.0.0` in the published
@deepseek-ai/dsh-web-app package.

The published 0.1.1-rc.2 npm tarball hard-rejects a 0.0.0.0 bind
(`program.error("error: --host 0.0.0.0 is intentionally not supported yet
for safety …")`; verified string-for-string against the registry tarball).
The production Mac's global install carries the "ponytail" warn-only form
instead (the deployment serves dsh web through the Cloudflare tunnel +
the /api browser-trust fence with --trusted-host, so a container bind on
all interfaces is the intended exposure — the container's eth0 is only
reachable via the grid-net connector and the 127.0.0.1 host publish).

Applies exactly that patch. Fails the build when the target string is
absent (a future dsh version changed the guard — re-verify then).
"""
from __future__ import annotations

import sys

OLD = ('if (options.host === "0.0.0.0") program.error('
       '"error: --host 0.0.0.0 is intentionally not supported yet for safety: '
       'it would expose remote code execution to the network; use 127.0.0.1 instead");')
NEW = ('if (options.host === "0.0.0.0") console.warn('
       '"warning: --host 0.0.0.0 binds the Web GUI to all interfaces; '
       'ensure --trusted-host and/or a tunnel/VPN gates the /api fence.");')


def main(argv: list) -> int:
    path = argv[1]
    text = open(path).read()
    if NEW in text:
        print("dsh_ponytail_patch: already patched")
        return 0
    if OLD not in text:
        print("dsh_ponytail_patch: target guard not found — published "
              "dsh-web-app changed; re-verify the patch", file=sys.stderr)
        return 1
    open(path, "w").write(text.replace(OLD, NEW))
    print(f"dsh_ponytail_patch: patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
