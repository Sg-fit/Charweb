"""Verify the corpus assumptions the instruction conditions depend on.

Two conditions make claims about what is on the site, and both silently
degrade if the claim stops holding:

  targeted_search  searches for FINDABLE_TERM and must SUCCEED.
                   If no post contains it, this condition becomes a second
                   impossible_goal and the contrast between them is lost.

  impossible_goal  searches for ABSENT_PROBE and must FAIL.
                   Agents in free_explore and single_action write posts. The
                   day one of them writes a post containing the probe, this
                   condition quietly becomes targeted_search -- and nothing
                   in the data would show it.

Neither failure raises an error during collection; the sessions look fine and
the labels look fine. That is exactly why this runs BEFORE a batch.

    cd /srv/charweb; set -a; . /etc/charweb.env; set +a
    ./venv/bin/python research/check_conditions.py

Exit code 0 = safe to collect, 1 = do not collect until fixed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa

from app import app, db
from app.models import Post
from instructions import (ABSENT_PROBE, FINDABLE_TERM, MIN_FINDABLE_POSTS,
                          CONDITIONS)


def count_posts_containing(term):
    """Same matching Charweb's search actually does: with no Elasticsearch
    configured, Post.search falls back to body ILIKE '%term%'."""
    return db.session.scalar(
        sa.select(sa.func.count()).select_from(Post)
        .where(Post.body.ilike(f"%{term}%"))) or 0


def main():
    with app.app_context():
        total = db.session.scalar(sa.select(sa.func.count()).select_from(Post)) or 0
        findable = count_posts_containing(FINDABLE_TERM)
        absent = count_posts_containing(ABSENT_PROBE)

        print(f"posts in corpus: {total}")
        print(f"conditions defined: {len(CONDITIONS)} "
              f"({', '.join(sorted(CONDITIONS))})")
        print()

        ok = True

        print(f"targeted_search -- needs >={MIN_FINDABLE_POSTS} posts containing "
              f"{FINDABLE_TERM!r}")
        print(f"  found: {findable}")
        if findable < MIN_FINDABLE_POSTS:
            ok = False
            print("  FAIL: not findable enough. targeted_search would collapse "
                  "into impossible_goal.")
            print("  Fix: seed a few posts containing the term, or change "
                  "FINDABLE_TERM in app/instructions.py to a word that is "
                  "actually present.")
        else:
            print("  OK")

        print()
        print(f"impossible_goal -- needs EXACTLY 0 posts containing {ABSENT_PROBE!r}")
        print(f"  found: {absent}")
        if absent:
            ok = False
            print("  FAIL: the 'impossible' target now exists, so this condition "
                  "is no longer impossible.")
            print("  Fix: change ABSENT_PROBE in app/instructions.py to a term "
                  "no post contains, or remove the offending post(s):")
            for p in db.session.scalars(
                    sa.select(Post).where(Post.body.ilike(f"%{ABSENT_PROBE}%"))
                    .limit(5)):
                print(f"    post id={p.id}: {p.body[:70]!r}")
        else:
            print("  OK")

        print()
        if ok:
            print("All corpus assumptions hold -- safe to collect.")
            return 0
        print("DO NOT COLLECT until the above is fixed: the affected condition "
              "would produce data that looks valid but tests the wrong thing.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
