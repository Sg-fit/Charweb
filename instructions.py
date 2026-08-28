"""The instruction conditions -- the study's third axis, in ONE place.

Previously the same task text lived twice: INSTRUCTIONS in app/llm_agent.py and
TASKS in run_fenris.py. Two copies of an experimental condition is a silent
validity bug waiting to happen -- edit one, and the "same" condition means
different things depending on which harness ran it. Both now import from here.

Each condition varies ONE structural property of the episode, so that
leave-one-task-out has genuinely different shapes to generalise across:

    free_explore     no goal at all                      (baseline)
    checklist        fully specified ordered goal        (goal specificity)
    targeted_search  find one thing, may need re-query   (search + backtracking)
    impossible_goal  the target does not exist           (persistence / giving up)
    single_action    one action then leave               (episode length)
    deep_dungeon     one subsystem, many steps, state    (depth vs breadth)
    reading_visit    read only, change nothing           (passive vs interactive)

Wording is deliberately kept to a similar length and register across
conditions: if one prompt were far longer or more emphatic than the others,
prompt style itself would become a confound with the condition.

CORPUS ASSUMPTIONS -- verify before every collection batch with
research/check_conditions.py:

  * targeted_search searches for FINDABLE_TERM, which MUST match several posts.
  * impossible_goal searches for ABSENT_PROBE, which MUST match zero posts.
    If an agent ever writes a post containing it, that condition silently
    degrades into targeted_search and the two become the same experiment.

Charweb's search has no Elasticsearch configured, so Post.search falls back to
`body ILIKE '%term%'` -- a plain substring match over post bodies. That is why
these checks are simple substring counts.
"""

# Substring that must EXIST in several posts (targeted_search must succeed).
FINDABLE_TERM = "introduction"
MIN_FINDABLE_POSTS = 3

# Substring that must NOT exist in any post (impossible_goal must fail).
ABSENT_PROBE = "scuba"

# single_action pins its post text: that condition is the main way new post
# content enters the corpus, so leaving it free-form is how ABSENT_PROBE
# eventually gets written into a post by an agent choosing its own topic.
SINGLE_ACTION_POST_HINT = (
    "say hello and mention that you are new here -- keep it to one short "
    "sentence and do not write about any other topic"
)

CONDITIONS = {
    "free_explore": (
        "You are a new user exploring a small social site called Charweb. There is "
        "no checklist -- browse the way a curious person would: read the feed, open "
        "posts, like or comment on things that interest you, check your profile, try "
        "the daily sign-in and the little dungeon game, search for something. Vary "
        "how long you spend on pages. Stop when you've had a natural look around."
    ),
    "checklist": (
        "You are testing a small social site called Charweb. Do these in order: "
        "(1) read the home feed and like a post, (2) search for a keyword, "
        "(3) click a post's comment button to open its comment box and leave a "
        "comment, (4) edit your profile 'about me' and save it, (5) do the "
        "daily sign-in. Then finish."
    ),
    # Charweb has NO post-detail page -- there is no /post/<id> route, and posts
    # exist only inside feed listings -- and each post's comment box sits in a
    # display:none block until its comment button is clicked. The old wording
    # ("open it, and leave a comment") described a page that does not exist and
    # a control the agent was never shown, so the task was unachievable: 69
    # sessions searched correctly and produced zero comments. Naming the toggle
    # is not hand-holding; it is the difference between a hard task and an
    # impossible one.
    # Measured 2026-08-28: the SEARCH RESULTS page renders posts but no comment
    # UI at all (12 posts, 0 comment toggles), while /home and /explore render
    # 15 toggles each, listed to the agent at low indices. So "search, then
    # comment on the result" was impossible -- the button does not exist on
    # that page. Agents searched successfully in 87% of sessions and then had
    # nothing to click; 0 comments across 4 batches.
    #
    # The task now uses search for what search can do (confirm the term is
    # present) and sends the agent to a page where commenting is actually
    # possible.
    "targeted_search": (
        "You are looking for one specific thing on a small social site called "
        f"Charweb. Use the search box to check that some post mentions an "
        f"{FINDABLE_TERM}. Search results are read-only, so once you know the "
        "term is there, go to the explore page and find a post mentioning it. "
        "Posts are read directly in the list -- there is no separate page for "
        "a post. Comments start hidden: click that post's comment button to "
        "reveal its comment box, type a short comment saying what you thought "
        "of the post, and submit it. If your first search doesn't turn up "
        "anything useful, try a different word. Once you've commented, stop -- "
        "don't browse around afterwards."
    ),
    # Deliberately word-for-word parallel to targeted_search apart from the
    # target. The pair only isolates "how an agent handles an unreachable goal"
    # if everything else about the two instructions matches -- otherwise a
    # difference in behaviour could just be a difference in phrasing.
    "impossible_goal": (
        "You are looking for one specific thing on a small social site called "
        "Charweb. Use the search box to check whether some post mentions scuba "
        "diving in Antarctica. Search results are read-only, so if you find "
        "one, go to the explore page and find that post there. Posts are read "
        "directly in the list -- there is no separate page for a post. "
        "Comments start hidden: click that post's comment button to reveal its "
        "comment box, type a short comment saying what you thought of the "
        "post, and submit it. Check the feed and the explore page too. If you "
        "become confident that no such post exists, stop and say so -- do not "
        "comment on an unrelated post instead."
    ),
    "single_action": (
        "You are a new user on a small social site called Charweb who only came to "
        f"do one thing. Write one post on the home feed -- {SINGLE_ACTION_POST_HINT} "
        "-- then log out. Do not read the feed, do not look at anything else, and "
        "do not explore. Just post and leave."
    ),
    "deep_dungeon": (
        "You are a player on a small social site called Charweb that has a daily "
        "dungeon game. Go to the daily section, do the daily sign-in, create a "
        "character if you don't have one, then play the dungeon. Spend your points "
        "or buy and equip something from the shop if you can. Stay in the game "
        "section the whole time -- don't browse the social feed."
    ),
    "reading_visit": (
        "You are researching a small site called Charweb and only want to read. "
        "Visit the team pages and read about the different sub-teams, then look at "
        "the ranking page and a couple of user profiles. Do not post, comment, "
        "like, or change anything -- just read and move on when you've seen enough."
    ),
}

# Conditions that need an account before the task makes sense. impossible_goal
# is included deliberately: without it, an agent that gives up early might never
# register, and its sessions would differ from the others in a way that has
# nothing to do with how it handles failure.
NEEDS_ACCOUNT = set(CONDITIONS)

# Fenris drives a confirmation-gated browser tool and needs to be told to act
# without asking; llm_agent already loops autonomously and does not.
FENRIS_PREFACE = (
    "You have a web_browser tool that can open pages, fill in forms, and click "
    "buttons. Use it NOW, on your own, without asking for permission or "
    "confirmation -- this is an automated task with no human available to "
    "approve anything, so any request to confirm will simply fail. Make up a "
    "username, email, and password yourself. Do not narrate a plan or ask "
    "questions; just call the browser tool and do the steps. "
)


def for_llm_agent(name):
    """Instruction text for the DOM-loop agent, which registers separately."""
    return CONDITIONS.get(name, CONDITIONS["free_explore"])


def for_fenris(name, site="https://charweb.net"):
    """Same condition, phrased for Fenris: prefaced, and told to register first
    so the account step is identical across harnesses."""
    body = CONDITIONS.get(name, CONDITIONS["free_explore"])
    return (f"{FENRIS_PREFACE}Task: go to {site} and register a brand-new "
            f"account, then do this: {body}")
