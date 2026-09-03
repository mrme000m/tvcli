"""prime_stack — modular bootstrap engine for the DSH prime-orchestrator stack.

The engine is the single source of truth for every install/config step
(bootstrapping/python/prime_stack/stages/). The Ansible playbook
(bootstrapping/ansible/prime-stack.yml) only owns what Ansible is good at —
apt packages, orchestration, tags — and delegates each stage to:

    PYTHONPATH=bootstrapping/python python3 -m prime_stack <stage>

Contract shared by every stage:

  * stdout carries exactly one JSON envelope per executed stage (the last
    line is always the aggregate when several stages run in one command);
    stderr carries human logs. Machine consumers never have to parse logs.
  * the envelope has: stage, changed, skipped, failed, error, warnings,
    details, summary_line.
  * every stage is idempotent: it checks before it writes, and reports
    changed=false when nothing was touched.
  * --dry-run (or PRIME_STACK_DRY_RUN=true) turns all writes and installer
    invocations into recorded no-ops, so `prime-stack --dry-run all` is a
    safe "what would change" preview.
  * secrets (CLOUDFLARE_*, WT_*) are read directly from the process
    environment or vault-provisioned runtime files; they are never printed,
    never logged, and never flow through Ansible task arguments.
"""

__version__ = "1.0.0"
