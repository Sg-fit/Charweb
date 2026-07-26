#!/usr/bin/env bash
# Run from inside the repo during an unresolved merge. For each conflicted
# file, reports whether "ours" (stage 2) and "theirs" (stage 3) are
# byte-identical (trivial -- just accept either side) or different (needs
# a real look), plus a line-count diff for the latter.
set -u
for f in $(git diff --name-only --diff-filter=U); do
    echo "=== $f ==="
    ours=$(git show :2:"$f" 2>/dev/null)
    theirs=$(git show :3:"$f" 2>/dev/null)
    if [ -z "$ours" ] && [ -z "$theirs" ]; then
        echo "  (no stage 2/3 content -- check manually, may be a delete/modify conflict)"
    elif [ "$ours" = "$theirs" ]; then
        echo "  IDENTICAL on both sides -- trivial, safe to just 'git add' either version"
    else
        echo "  DIFFERENT -- diff stat:"
        diff <(echo "$ours") <(echo "$theirs") | grep -c "^[<>]" | xargs echo "  changed/differing lines:"
    fi
    echo
done
