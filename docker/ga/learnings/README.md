# GA learnings ledger — the self-improvement loop

This directory is the GA agent's durable memory: a single, version-controlled
ledger of what operating and improving this deployment actually taught us.
The loop is **session → learning → ledger → (when warranted) code change →
commit → guarded push**, so the same problem is never rediscovered twice.

## What a learning is

A learning is **knowledge that survives the session** — a gotcha, a verified
fact about the environment, an incident root-cause, a counter-intuitive
behavior, or a fix that worked. It must be:

- **Verified true** — only what you actually observed or confirmed in the code.
  Never invent, never guess, never copy stale docs.
- **Concise and actionable** — a future reader (GA, or a human) can act on it
  without re-deriving the context.
- **Written for the future** — assume the reader has NOT seen this session.

## Cardinal rule: KNOWLEDGE, not logs

Learnings are distilled knowledge. They are **not** a session log. A learning
entry must contain **no secrets** (tokens, cookies, session ids, account
credentials — never), **no raw dumps** (no pasted API responses, logs, or
stack traces beyond the one line that matters), and **no transient progress**
("I did X then Y then Z…"). If a detail is only interesting to reconstruct what
happened, it does not belong here. Reference the file/line/issue instead of
quoting it wholesale.

## When to write one

- **End of every substantive session** — the (4) SELF-IMPROVE persona duty:
  distill the session's findings into one entry (or consciously decide there
  is nothing durable to record).
- **After any incident or fix** — as soon as the root cause is understood.
- **When a gotcha recurs** — the second time something bites, it must be in
  this ledger; bump the existing entry rather than writing a near-duplicate.

## File format

`ledger.md` is reverse-chronological (newest first), plain Markdown, one entry
per learning:

```markdown
## YYYY-MM-DD — Short imperative title

One or two short paragraphs of verified knowledge: the symptom or question,
the root cause or answer, and the consequence for operating this deployment.

Changes:
- docker/ga/foo.py — what changed because of this learning (if anything)
```

- Date = the day the learning was **learned** (not the day of the incident).
- The optional `Changes:` list names the files that the learning caused to be
  changed. Omit it when the learning is knowledge-only.

## The journaling tool

`docker/ga/ga_learn.py` (python3 stdlib only; no network; no secrets) is the
only sanctioned writer:

```sh
python3 docker/ga/ga_learn.py add --title "…" --body "…" [--changes "a.py,b.sh"]
python3 docker/ga/ga_learn.py tail [--count 5]
```

It resolves the ledger relative to `$GA_REPO` (default `/srv/tvcli`), falling
back to its own repo root, and **only ever writes inside
`docker/ga/learnings/`**. It performs **no git operations** — no commit, no
push. Committing and pushing stay subject to the existing push guardrail:
**no push to main without explicit human confirmation** (a push to main
auto-deploys the grid-autonomy container and rebuilds grid-ga for
`docker/ga/**` changes). Learnings and GA-stack changes ride the same guarded
main path.
