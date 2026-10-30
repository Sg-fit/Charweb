---
# Charweb — agent behavioural attribution study (ISEF)
Site: https://charweb.net | VPS: Hetzner CX22 | code: /srv/charweb
Zero human subjects. Axes: harness / model / perception.

## Rules for Claude
- Save tokens. Follow instructions only. Explain only when asked.
- Batch independent commands into one block.
- If a reply would hit the token limit, split into parts; never truncate silently.

## Credentials — NEVER commit
- No API keys in the repo or in /etc/charweb.env (that file has only
  SECRET_KEY, FLASK_APP, DATABASE_URL).
- Keys are passed at runtime: CHARWEB_LLM_KEY, NVIDIA_KEY, GROQ_KEY,
  GEMINI_KEY, CEREBRAS_KEY. Prompt with: read -rs -p "key: " VAR

## MANDATORY before ANY script that imports the Flask app
export CHARWEB_LOG_DIR=/tmp/charweb-analysis
Reason: running as root, RotatingFileHandler rotates logs/Charweb.log to
root:root at 10KB and crash-loops charweb.service. This caused a 10-day
silent outage (2026-09-23). See claude/outage_2026-09-23.md.

## Standard preamble
cd /srv/charweb
set -a; . /etc/charweb.env; set +a
export CHARWEB_LOG_DIR=/tmp/charweb-analysis
./venv/bin/python research/<script>.py

## Status
M1 closed (labeler repaired, kappa 0.863). M2 done. M3 done (CIs, LOTO).
Phase 2 E3 done (4-rung tamper ladder, rung 4 rule-undetectable, no
measurable degradation). H1 variance decomposition run; crossed subset
pending. E4 recommended dropped.
Next: H1 crossed subset -> M4 write-up (abstract + poster).

## Outstanding
- Rotate NVIDIA key (exposed in chat repeatedly)
- Rotate SECRET_KEY if the GitHub repo was ever public (old commits with the
  SQLite backups may still be served by hash / forks until GitHub support GCs them)
- VPS: `git reflog expire --expire=now --all && git gc --prune=now`; confirm the
  nightly DB backup actually writes to /srv/backups (it was empty 2026-10-03)
- Untrack logs/Charweb.log.* (tracked, churns on every rotation)
- Local SQLite backups (*.db.bak.*, *.db.old_orphaned_chain) are gitignored and
  gone from GitHub history (rewritten 2026-10-03) but still contain real emails
  + password hashes on disk — delete when no longer needed
