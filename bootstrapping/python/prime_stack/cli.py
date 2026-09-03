"""CLI dispatcher.

stdout protocol: one JSON envelope per executed stage, one line each; when
several stages run in one invocation, a final aggregate envelope is printed
as the LAST line. Machine consumers (the Ansible playbook) parse the last
stdout line. Human logs go to stderr.

Exit code: 0 when every stage succeeded (or --exit-zero); 1 as soon as a
stage failed. Groups fail fast — the first failure stops the run, matching
the playbook's stop-on-failure semantics.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import Config
from .core import Context, StageResult
from .stages import GROUPS, STAGES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prime-stack",
        description="Modular bootstrap engine for the DSH prime-orchestrator "
                    "stack (see bootstrapping/README.md).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "stages", nargs="*", metavar="STAGE",
        help="stage names or groups: " + ", ".join(list(STAGES) + list(GROUPS)),
    )
    parser.add_argument("--workspace", help="repo root (default: $TV_WORKSPACE or cwd)")
    parser.add_argument("--strict", action="store_true", default=None,
                        help="missing Cloudflare secrets are fatal (env: PRIME_STACK_STRICT)")
    parser.add_argument("--force-settings", action="store_true", default=None,
                        help="replace an existing ~/.dsh/settings.yaml after backup "
                             "(env: PRIME_STACK_FORCE_SETTINGS)")
    parser.add_argument("--dry-run", action="store_true", default=None,
                        help="record writes/installs without executing "
                             "(env: PRIME_STACK_DRY_RUN)")
    parser.add_argument("--quiet", action="store_true", help="only warnings on stderr")
    parser.add_argument("--exit-zero", action="store_true",
                        help="always exit 0 (the envelope carries failure)")
    parser.add_argument("--list", action="store_true", help="list stages and groups")
    return parser


def expand(names) -> list:
    out = []
    for name in names:
        if name in GROUPS:
            out.extend(GROUPS[name])
        elif name in STAGES:
            out.append(name)
        else:
            raise SystemExit(
                f"unknown stage: {name} (known: {', '.join(list(STAGES) + list(GROUPS))})"
            )
    seen = set()
    ordered = []
    for name in out:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def aggregate(stage_label: str, results: list) -> StageResult:
    return StageResult(
        stage=stage_label,
        changed=any(r.changed for r in results),
        skipped=all(r.skipped for r in results) and bool(results),
        failed=any(r.failed for r in results),
        error="; ".join(r.error for r in results if r.error),
        warnings=[w for r in results for w in r.warnings],
        details={"stages": [r.to_dict() for r in results]},
        summary_line="; ".join(
            f"{r.stage}: {'FAILED' if r.failed else ('changed' if r.changed else ('skipped' if r.skipped else 'ok'))}"
            for r in results
        ),
    )


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING if args.quiet else logging.INFO,
        format="[prime-stack] %(levelname)s %(message)s",
    )

    if args.list:
        print("stages:")
        for name in STAGES:
            print(f"  {name}")
        print("groups:")
        for name, members in GROUPS.items():
            print(f"  {name} = {' '.join(members)}")
        return 0
    if not args.stages:
        build_parser().print_usage(sys.stderr)
        print("error: at least one STAGE is required (or --list)", file=sys.stderr)
        return 2

    names = expand(args.stages)
    cfg = Config.from_env(args)
    ctx = Context(dry_run=cfg.dry_run)
    results = []
    for name in names:
        try:
            result = STAGES[name](cfg, ctx)
        except Exception as exc:  # noqa: BLE001 - the envelope must always be printed
            result = StageResult(
                name, failed=True,
                error=f"{type(exc).__name__}: {exc}",
                summary_line=f"{name}: FAILED ({type(exc).__name__})",
            )
        print(result.to_json(), flush=True)
        results.append(result)
        if result.failed:
            break

    if len(results) > 1:
        print(aggregate("+".join(names), results).to_json(), flush=True)
        overall_failed = any(r.failed for r in results)
    else:
        overall_failed = results[0].failed if results else True

    if ctx.dry_run and ctx.planned:
        logging.getLogger("prime-stack").info(
            "dry-run planned %d mutation(s): %s", len(ctx.planned), "; ".join(ctx.planned[:5])
        )
    return 0 if (not overall_failed or args.exit_zero) else 1
