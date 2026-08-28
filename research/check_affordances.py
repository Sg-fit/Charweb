"""Can the agent even SEE the controls its task requires?

An LLM-driven agent acts only on elements that reach it in the listing
EXTRACT_JS builds. Anything not listed does not exist as far as the model is
concerned -- so a task can be impossible for reasons that look exactly like the
model being incapable. Across 239 sessions our agents produced zero comments
while searching successfully 87% of the time, which is the signature of a
missing affordance rather than a missing capability.

This walks the pages a task actually visits, AS A LOGGED-IN USER (the logged-out
view is a different page and tests nothing), and reports for each:

    listed        how many elements the agent is shown
    total         how many exist
    hidden        the ones it never sees -- the interesting number
    textarea      whether a comment/post box is present, and at what index

Run on the server, where playwright and the site are both reachable:

    cd /srv/charweb; set -a; . /etc/charweb.env; set +a
    ./venv/bin/python research/check_affordances.py
    ./venv/bin/python research/check_affordances.py --url https://charweb.net
"""
import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_llm_agent():
    """Import app/llm_agent.py without importing the app package (which pulls
    in Flask and a DB engine that this check does not need)."""
    spec = importlib.util.spec_from_file_location(
        "_llm_agent", ROOT / "app" / "llm_agent.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_llm_agent"] = mod
    spec.loader.exec_module(mod)
    return mod


def report(page, la, label):
    els = page.evaluate(la.EXTRACT_JS)
    total = page.evaluate(
        "() => document.querySelectorAll("
        "'a,button,input,textarea,select,[role=button]').length")
    tas = page.evaluate(
        "() => [...document.querySelectorAll('textarea')].map(t =>"
        " (t.name || t.placeholder || t.id || 'textarea'))")
    listed_tas = [e for e in els if e["tag"] == "textarea"]
    # Two different reasons an element is missing, and conflating them is how a
    # fixed cap kept reading as broken. Truncated = the cap cut the list short
    # and raising it would help. Not-rendered = display:none / zero-size, which
    # is correct behaviour (Charweb's comment forms are hidden until toggled)
    # and no cap change will surface them.
    invisible = page.evaluate(
        "() => [...document.querySelectorAll("
        "'a,button,input,textarea,select,[role=button]')].filter(el => {"
        "  const r = el.getBoundingClientRect();"
        "  if (r.width === 0 || r.height === 0) return true;"
        "  const st = window.getComputedStyle(el);"
        "  return st.visibility === 'hidden' || st.display === 'none';"
        "}).length")
    visible = total - invisible
    truncated = max(0, visible - len(els))
    print(f"\n{label}")
    print(f"  url        {page.url}")
    print(f"  listed     {len(els)} / {visible} visible  ({total} total, "
          f"cap {la.MAX_ELEMENTS})"
          + ("  <-- CAP TRUNCATING" if truncated else ""))
    if truncated:
        print(f"  TRUNCATED  {truncated} visible element(s) cut by the cap "
              f"-- raise CHARWEB_MAX_ELEMENTS")
    if invisible:
        print(f"  not rendered {invisible} (display:none / zero-size; expected "
              f"for collapsed comment forms)")
    print(f"  textareas  {len(tas)} on page {tas if tas else ''}")
    if tas and not listed_tas:
        print("  *** A textarea EXISTS but is NOT in the listing. The agent "
              "cannot type into it, so any task needing it is impossible. ***")
    elif listed_tas:
        print(f"  textarea visible to agent at index "
              f"{[e['index'] for e in listed_tas]}")
    return truncated, total, bool(tas), bool(listed_tas)


def main():
    ap = argparse.ArgumentParser(description="Affordance visibility check")
    ap.add_argument("--url", default="https://charweb.net")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    la = load_llm_agent()
    from playwright.sync_api import sync_playwright

    site = args.url.rstrip("/")
    findings = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not args.headed)
        ctx = b.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        import os
        acct = f"afford_{os.urandom(3).hex()}"
        if not la.scripted_signup(page, site, acct):
            print("WARNING: could not log in; results below are the LOGGED-OUT "
                  "view and do not reflect what the agent sees.")
        else:
            print(f"logged in as {acct}")

        for path, label in (("/index", "HOME FEED"),
                            ("/explore", "EXPLORE"),
                            ("/search?q=introduction", "SEARCH RESULTS")):
            try:
                page.goto(site + path, wait_until="networkidle", timeout=20000)
                findings.append(report(page, la, label))
            except Exception as e:
                print(f"\n{label}: could not load ({str(e)[:70]})")

        # The page the comment task actually needs: open the first post.
        try:
            page.goto(site + "/index", wait_until="networkidle", timeout=20000)
            link = page.locator("a[href*='/post/']").first
            if link.count():
                link.click()
                page.wait_for_load_state("networkidle")
                findings.append(report(page, la, "POST DETAIL (comment target)"))
            else:
                print("\nPOST DETAIL: no /post/ link found on the feed -- the "
                      "agent has no way to open a post, which alone would make "
                      "'open it and comment' impossible.")
        except Exception as e:
            print(f"\nPOST DETAIL: {str(e)[:90]}")

        b.close()

    print("\n" + "=" * 64)
    capped = any(t > 0 for t, _, _, _ in findings)
    unlisted = any(has_ta and not seen for _, _, has_ta, seen in findings)
    if capped:
        print(f"VERDICT: the {la.MAX_ELEMENTS}-element cap is still truncating "
              "visible controls. Raise CHARWEB_MAX_ELEMENTS before drawing any "
              "conclusion about agent capability.")
    elif unlisted:
        print("VERDICT: a rendered control exists but is not listed -- an "
              "INSTRUMENTATION limit, not a model limit. Check the selector "
              "and the ranking in EXTRACT_JS.")
    else:
        print(f"VERDICT: at cap {la.MAX_ELEMENTS} nothing visible is being "
              "truncated. Controls that remain unlisted are display:none and "
              "require a click to reveal (Charweb's comment forms work this "
              "way) -- so the task must TELL the agent to open them. Any "
              "remaining failure is now genuine agent behaviour.")


if __name__ == "__main__":
    main()
