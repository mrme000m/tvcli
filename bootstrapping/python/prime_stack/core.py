"""Core primitives: stage envelopes, dry-run-aware IO and process execution.

Everything a stage does that mutates the machine MUST go through `Context`
(`mutate`, `write_text`, `mkdir`, `touch`) so that --dry-run is a faithful
preview. Read-only probing uses `Context.exec` (always runs, even in
dry-run). File-mutating helpers are split into pure text functions +
`Context.write_text` IO so the pure parts are unit-testable without a
filesystem-side-effect harness.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("prime-stack")


class StageFailure(Exception):
    """Raise inside a stage to fail it with a human explanation.

    `details` is merged into the JSON envelope (never put secret values
    there — envelopes are printed to stdout and logs).
    """

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.details = details or {}


@dataclass
class StageResult:
    """Machine-readable outcome of one stage (the JSON envelope)."""

    stage: str
    changed: bool = False
    skipped: bool = False
    failed: bool = False
    error: str = ""
    warnings: list = field(default_factory=list)
    details: dict = field(default_factory=dict)
    summary_line: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "changed": self.changed,
            "skipped": self.skipped,
            "failed": self.failed,
            "error": self.error,
            "warnings": self.warnings,
            "details": self.details,
            "summary_line": self.summary_line
            or ("FAILED" if self.failed else ("SKIPPED" if self.skipped else ("changed" if self.changed else "unchanged"))),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=False)


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class Context:
    """Dry-run-aware execution and IO context handed to every stage."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.planned: list = []

    # -- process execution ------------------------------------------------

    def exec(self, cmd: str, *, logfile: Optional[str] = None,
             timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        """Run a read-only probe command; always executes (even in dry-run)."""
        log.info("+ %s", cmd)
        proc = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout
        )
        if logfile and (proc.stdout or proc.stderr):
            try:
                Path(logfile).write_text(proc.stdout + proc.stderr)
            except OSError as exc:  # pragma: no cover - /tmp unwritable
                log.warning("cannot write %s: %s", logfile, exc)
        if proc.returncode != 0:
            out = (proc.stdout + proc.stderr).strip()
            log.error("command failed (rc=%s):\n%s", proc.returncode, out)
        return proc

    def mutate(self, cmd: str, *, logfile: Optional[str] = None,
               timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        """Run a machine-mutating command; recorded no-op in dry-run."""
        if self.dry_run:
            self.planned.append(cmd)
            log.info("[dry-run] would run: %s", cmd)
            return subprocess.CompletedProcess(["bash", "-c", cmd], 0, "", "")
        return self.exec(cmd, logfile=logfile, timeout=timeout)

    # -- filesystem --------------------------------------------------------

    def mkdir(self, path) -> None:
        if self.dry_run:
            self.planned.append(f"mkdir -p {path}")
            return
        Path(path).mkdir(parents=True, exist_ok=True)

    def write_text(self, path, content: str, mode: Optional[int] = None) -> bool:
        """Idempotent write; returns True when the on-disk state would change."""
        path = Path(path)
        if self.dry_run:
            if not path.exists() or path.read_text() != content:
                self.planned.append(f"write {path}")
                log.info("[dry-run] would write: %s", path)
                return True
            return False
        existing = path.read_text() if path.exists() else None
        changed = existing != content
        if changed:
            atomic_write_text(path, content, mode=mode)
            log.info("wrote %s", path)
        elif mode is not None and path.exists():
            current = os.stat(path).st_mode & 0o777
            if current != mode:
                os.chmod(path, mode)
                changed = True
        return changed

    def touch(self, path, mode: Optional[int] = None) -> bool:
        return self.write_text(path, "", mode=mode)

    # -- polling -----------------------------------------------------------

    def wait_until(self, predicate: Callable[[], bool], seconds: int,
                   interval: float = 1.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()


# ---------------------------------------------------------------------------
# pure filesystem-text helpers (unit-tested, no IO)
# ---------------------------------------------------------------------------

def atomic_write_text(path, content: str, mode: Optional[int] = None) -> None:
    """Write via same-dir tmp + rename, then enforce the file mode."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)
    if mode is not None:
        os.chmod(path, mode)


def marker_block_text(existing: str, begin: str, end: str, block: str) -> tuple:
    """Ensure a marker-bounded block, replicating blockinfile semantics.

    Tolerant of other marker generations on disk (e.g. ansible's default
    ``... ANSIBLE MANAGED BLOCK`` suffix or doubled leading ``#``): the
    first line containing `begin` through the first line containing `end`
    is replaced wholesale with the canonical block, so every legacy
    variant converges to exactly one canonical block instead of
    duplicating. Returns (new_text, changed).
    """
    wanted = f"{begin}\n{block}\n{end}"
    pattern = re.compile(
        r"^[^\n]*?" + re.escape(begin) + r"[^\n]*?\n"
        r".*?"
        r"[^\n]*?" + re.escape(end) + r"[^\n]*\n?",
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(existing)
    if m:
        new_text = existing[: m.start()] + wanted + "\n" + existing[m.end():]
        return new_text, new_text != existing
    if not existing.strip():
        return wanted + "\n", True
    text = existing if existing.endswith("\n") else existing + "\n"
    if text.strip():
        text += "\n"
    return text + wanted + "\n", True


def version_lt(a: str, b: str) -> bool:
    """Numeric-aware dotted-version compare ('v10.1' style prefixes ok)."""

    def parts(v: str):
        m = re.search(r"\d+(\.\d+)*", v or "")
        if not m:
            return ()
        seg = []
        for p in m.group(0).split("."):
            try:
                seg.append(int(p))
            except ValueError:
                seg.append(0)
        return tuple(seg)

    pa, pb = parts(a), parts(b)
    if pa and pb:
        return pa < pb
    return (a or "") < (b or "")


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def pem_sha256_fingerprint(pem: str) -> Optional[str]:
    """sha256 of a PEM cert's DER payload, lowercase hex, no colons.

    Used by the dsh-mobile gateway: its `instanceId` must equal the
    fingerprint256 of the pairing CA, so we derive it from the actual CA
    instead of trusting a drifted stored value.
    """
    import base64
    import hashlib

    try:
        der = base64.b64decode(
            "".join(l for l in pem.splitlines() if not l.startswith("---"))
        )
        return hashlib.sha256(der).hexdigest()
    except Exception:
        return None


def load_json_file(path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
        return doc if isinstance(doc, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def json_text(doc) -> str:
    return json.dumps(doc, indent=2) + "\n"
