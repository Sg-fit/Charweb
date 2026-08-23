"""
Two-axis (model, harness) session labeling for Phase I evaluation.

Gap this fills: nothing in the repo tagged a session with which model
produced it and which automation tooling executed it, so LOAO grouped CV
(research/loao_eval.py) had no group key to group by. This module is that
label, derived from the username convention every harness script in app/
is expected to follow (see CONVENTION below) -- ported from the identity
parsing in the sibling ESAP/SYSTEM project (train/build_dataset.py's
parse_identity), extended with a `harness` axis.

Four columns, and why each exists:

    arch     -- the LOAO / cross-validation grouping key. One arch per
                model identity (ai_L1, llm_gpt, fenris, human_P04, ...).
                Sessions sharing an arch are not independent samples --
                running the same script twice does not create a second
                architecture.
    family   -- coarse bucket for headline reporting:
                  playwright_tier -- ai_agent.py / advanced_agent.py.
                      Fixed timing config, no model in the loop at all.
                  llm_scripted    -- gptTest.py/gemini.py/grok.py/copilat.py.
                      Persona-TUNED but still a fixed script -- these do
                      NOT call any model API (verified by reading them; no
                      openai/anthropic import exists in any of the four).
                      They exist as reproducible stand-ins for what each
                      product's real agent-mode traffic looks like.
                  llm_live        -- app/claude.py. The only driver where
                      the model actually chooses each action live, via a
                      real Anthropic tool-use loop.
                  custom          -- fenris. Its own automation, not
                      Playwright.
                  human           -- real subjects.
    harness  -- the automation TOOLING, independent of which model/persona
                is driving it. standard_playwright for everything routed
                through this repo's Playwright scripts (including
                claude.py -- it's a live model, but still driving Playwright
                the same way the scripted personas do); fenris_native for
                Fenris's own, not-in-this-repo automation; human for real
                subjects. Two sessions can share an arch (fenris) but
                differ in harness -- that pairing is the whole point: it
                isolates "did detection come from the harness" from "did
                it come from the model" (ESAP/SYSTEM found fenris_native
                at 0.00 recall; is that Fenris, or is it whatever makes
                Fenris's automation not look like Playwright?).
    task     -- scripted task tier where encoded in the name (t1-t4), else
                "unknown".
    label    -- "human" or "ai". The target variable.

CONVENTION every harness script's --username must follow (enforced here by
matching order, not by the scripts -- if a script's output doesn't match
any pattern below it silently falls into the human bucket, which is exactly
the failure mode this module exists to prevent, so treat a "human_<weird
username>" arch showing up for what you know was a bot run as a bug report):

    ai_agent.py       -> ai_L{1-4}_t{n}[...]      (existing convention)
    advanced_agent.py -> ai_L5_...                (see its own docstring --
                                                     NOT a model name)
    gptTest.py        -> gpt_...
    gemini.py         -> gemini_...
    grok.py           -> grok_...
    copilat.py        -> copilat_...  or  copilot_...
    claude.py         -> claude_...
    fenris.py         -> fenris_standard_...  or  fenris_playwright_...
    Fenris (native, not in this repo) -> fenris_native_...  or bare fenris...

Usage
-----
    # against the live DB (every distinct User.username)
    python research/label_architecture.py --db -o architecture_labels.csv

    # against a CSV with a `username` column (e.g. an export or manifest)
    python research/label_architecture.py --csv manifest.csv -o architecture_labels.csv
"""
import argparse
import csv
import os
import re
import sys

# (regex, arch_template, family, harness) -- matched top to bottom, first
# hit wins. arch_template's {0}, {1}, ... refer to the regex's capture
# groups, so 'ai_L{0}' + match group '2' -> 'ai_L2'.
_PATTERNS = [
    (re.compile(r'^ai_l(\d)', re.I), 'ai_L{0}', 'playwright_tier', 'standard_playwright'),
    (re.compile(r'^fenris_native', re.I), 'fenris', 'custom', 'fenris_native'),
    (re.compile(r'^fenris_(?:standard|playwright)', re.I), 'fenris', 'custom', 'standard_playwright'),
    (re.compile(r'^fenris', re.I), 'fenris', 'custom', 'fenris_native'),  # bare "fenris" = native, per ESAP/SYSTEM
    (re.compile(r'^gpt', re.I), 'llm_gpt', 'llm_scripted', 'standard_playwright'),
    (re.compile(r'^gemini', re.I), 'llm_gemini', 'llm_scripted', 'standard_playwright'),
    (re.compile(r'^grok', re.I), 'llm_grok', 'llm_scripted', 'standard_playwright'),
    (re.compile(r'^copilat|^copilot', re.I), 'llm_copilot', 'llm_scripted', 'standard_playwright'),
    (re.compile(r'^claude', re.I), 'llm_claude', 'llm_live', 'standard_playwright'),
]

_TASK_RE = re.compile(r'_t(\d)(?:_|$)', re.I)


def parse_identity(username):
    """username/subject_id -> dict(arch, family, harness, task, label).

    Order matters: fenris_native/fenris_standard/fenris_playwright must be
    checked before the bare 'fenris' catch-all, which is why they're listed
    first among the fenris rows above.
    """
    s = str(username)

    for pattern, arch_tpl, family, harness in _PATTERNS:
        m = pattern.match(s)
        if m:
            arch = arch_tpl.format(*m.groups()) if m.groups() else arch_tpl
            tm = _TASK_RE.search(s)
            task = f't{tm.group(1)}' if tm else 'unknown'
            return {'arch': arch, 'family': family, 'harness': harness,
                    'task': task, 'label': 'ai'}

    # Unrecognized -> human, keyed by their own username so distinct people
    # never collapse into one CV group the way two runs of the same bot do.
    # Don't double the prefix for a username that's already "human_..." --
    # human_test_shared (the shared test-credentials account) is real and
    # would otherwise become the confusing "human_human_test_shared".
    arch = s if s.lower().startswith('human_') else f'human_{s}'
    return {'arch': arch, 'family': 'human', 'harness': 'human',
            'task': 'unknown', 'label': 'human'}


def _usernames_from_db():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import app, db
    from app.models import User

    with app.app_context():
        rows = db.session.execute(db.select(User.username)).scalars().all()
        return sorted(set(rows))


def _usernames_from_csv(path, column='username'):
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if column not in (reader.fieldnames or []):
            raise SystemExit(f"{path}: no '{column}' column (found {reader.fieldnames})")
        return sorted({row[column] for row in reader if row.get(column)})


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--db', action='store_true', help='read distinct usernames from the live app DB')
    src.add_argument('--csv', help='CSV file with a username column')
    ap.add_argument('--column', default='username', help='column name when using --csv (default: username)')
    ap.add_argument('-o', '--out', default='architecture_labels.csv')
    args = ap.parse_args()

    usernames = _usernames_from_db() if args.db else _usernames_from_csv(args.csv, args.column)
    if not usernames:
        print('No usernames found.', file=sys.stderr)
        return 1

    rows = []
    for u in usernames:
        row = {'username': u}
        row.update(parse_identity(u))
        rows.append(row)

    with open(args.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['username', 'arch', 'family', 'harness', 'task', 'label'])
        w.writeheader()
        w.writerows(rows)

    by_family = {}
    for r in rows:
        by_family.setdefault(r['family'], set()).add(r['arch'])
    print(f'{len(rows)} usernames labeled -> {args.out}\n')
    print('archs found per family:')
    for family, archs in sorted(by_family.items()):
        print(f'  {family:16s} {len(archs):3d} arch(es): {", ".join(sorted(archs))}')

    # A username that LOOKS like a bot run (starts with a known prefix) but
    # fell into the human bucket means a pattern above has a gap -- flag it
    # rather than silently mislabeling a bot session as a human one.
    unexpected_human = [r['username'] for r in rows if r['family'] == 'human'
                        and re.match(r'^(ai|gpt|gemini|grok|copila?t|claude|fenris)', r['username'], re.I)]
    if unexpected_human:
        print(f'\nWARNING: {len(unexpected_human)} username(s) look like a bot run but matched no '
              f'pattern and fell into the human bucket -- check the CONVENTION section in this '
              f'file\'s docstring:')
        print('  ' + ', '.join(unexpected_human[:10]))

    return 0


if __name__ == '__main__':
    sys.exit(main())
