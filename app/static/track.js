// Track user actions and save to localStorage
const STORAGE_KEY = "userTrackingData";


// ==========================================================================
// Phase-2 adversarial telemetry layer (E3). INERT unless explicitly enabled.
//
// Threat model: track.js runs in the attacker's browser, so every field it
// reports is attacker-controlled. This layer implements that adversary so the
// claim can be measured rather than asserted.
//
// Enabled only via window.__CHARWEB_TAMPER, set by the collection driver with
// page.add_init_script() before any navigation. Deliberately NOT a URL
// parameter: that would contaminate the `url` field the task labeler reads,
// and would let an ordinary visitor trigger it.
//
// Critical invariant: this alters only what is REPORTED, never what the agent
// DOES. Task success, action sequence and real timing are untouched, so E3
// stays a pure telemetry attack and stays separable from E1 (delay injection),
// which changes behaviour. It also means the evasion tax measured here is
// exactly zero by construction -- which is the point: forging telemetry is
// free, so if attribution survives it, that is a strong result.
// ==========================================================================
const TAMPER = (typeof window !== "undefined" && window.__CHARWEB_TAMPER) || null;

// Human-plausible inter-action delays for forge mode. Lognormal-ish: most
// actions 300-900ms apart with an occasional long pause, which is roughly what
// the human literature reports and, more to the point, what an attacker would
// naively reach for.
function _humanGap() {
    const u = Math.random();
    if (u < 0.08) return 2000 + Math.random() * 6000;   // occasional think pause
    return 250 + Math.exp(Math.random() * 1.9) * 120;    // 250ms .. ~1.1s
}
let _forgedClock = Date.now();
// Rung 4: the virtual clock must SURVIVE NAVIGATION. Held in module scope it
// resets on every page load, so the first event of each new page can precede
// the last event of the previous one -- which contradicts server-assigned seq
// and is unforgeable tamper evidence. Measured: 4-7 order violations per
// session against exactly 0 for clean traffic. Persisting the clock in
// localStorage (same origin, survives navigation within the session) removes
// the last rule-detectable signature.
const _ORD_KEY = "__cw_ord";
function _ordLoad() {
    try {
        const v = JSON.parse(localStorage.getItem(_ORD_KEY) || "null");
        if (v && typeof v.realStart === "number") return v;
    } catch (e) { /* fall through to a fresh clock */ }
    return { realStart: null, forged: 0, prev: -1 };
}
function _ordSave(v) {
    try { localStorage.setItem(_ORD_KEY, JSON.stringify(v)); } catch (e) {}
}

// Human-like mouse velocity in px/s. geom_* is the most trivially forgeable
// group in the feature set: velocity is COMPUTED here and shipped as a float,
// so the attacker never has to simulate a path at all.
function _humanVelocity() {
    return 80 + Math.random() * 520;
}

function applyTamper(action) {
    if (!TAMPER) return action;

    // suppress_mouse: drop every mousemove and scroll. Kills all geom_*
    // features outright -- the cheapest possible attack.
    if (TAMPER === "suppress_mouse") {
        if (action.type === "mousemove" || action.type === "scroll") return null;
        return action;
    }

    // suppress_random: drop half of everything, uniformly. Attacks action_*
    // proportions and event counts without a type-specific signature.
    if (TAMPER === "suppress_random") {
        return Math.random() < 0.5 ? null : action;
    }

    // forge_human: report human-plausible timestamps and mouse kinematics for
    // the agent's REAL actions. This is the attack that isolates client-clock
    // trust: client timing is destroyed, server_ts is untouched, so the gap
    // between the two exports is the defense's value.
    if (TAMPER === "forge_human") {
        _forgedClock += _humanGap();
        action.timestamp = new Date(_forgedClock).toISOString();
        if (action.type === "mousemove") {
            action.velocity = _humanVelocity();
            action.angle = (Math.random() * 2 - 1) * Math.PI;
        }
        if (action.type === "scroll") {
            action.scrollVelocity = _humanVelocity();
            action.scrollAcceleration = (Math.random() * 2 - 1) * 400;
        }
        return action;
    }

    // forge_human_matched: the ADAPTIVE attacker. forge_human advances its own
    // clock, so total reported duration drifts far ahead of real elapsed time
    // and the tampering is detectable by client-vs-server clock skew alone --
    // i.e. it is a strawman. This variant perturbs each real timestamp by
    // bounded noise instead: interval structure (iv_cv, iv_min, kd_cv) is
    // destroyed while total session duration stays matched to reality, so no
    // skew signal is left. Evaluate the defense against THIS one.
    if (TAMPER === "forge_human_matched") {
        const real = Date.parse(action.timestamp);
        action.timestamp = new Date(real + (Math.random() * 2 - 1) * 1500).toISOString();
        if (action.type === "mousemove") {
            action.velocity = _humanVelocity();
            action.angle = (Math.random() * 2 - 1) * Math.PI;
        }
        if (action.type === "scroll") {
            action.scrollVelocity = _humanVelocity();
            action.scrollAcceleration = (Math.random() * 2 - 1) * 400;
        }
        return action;
    }

    // forge_human_ordered: the STRONGEST attacker in this ladder.
    //
    // forge_human leaks via client-vs-server clock skew. forge_human_matched
    // closes that but reorders events relative to the server-assigned `seq`,
    // which is itself unforgeable evidence. This variant closes both: a virtual
    // clock advances by human-plausible gaps (so interval structure is
    // destroyed) but is continuously corrected toward real elapsed time (so
    // total duration stays matched) and is strictly monotonic (so it never
    // contradicts seq).
    //
    // A defense that only beats the first two rungs has not been evaluated.
    if (TAMPER === "forge_human_ordered") {
        const real = Date.parse(action.timestamp);
        const st = _ordLoad();
        if (st.realStart === null) {
            st.realStart = real; st.forged = 0;
        } else {
            const realElapsed = real - st.realStart;
            st.forged += _humanGap();
            // Pull back toward true elapsed time so the session duration a
            // defender sees matches the one the server timed.
            st.forged += (realElapsed - st.forged) * 0.3;
            if (st.forged <= st.prev) st.forged = st.prev + 1;   // monotonic
        }
        st.prev = st.forged;
        _ordSave(st);
        action.timestamp = new Date(st.realStart + st.forged).toISOString();
        if (action.type === "mousemove") {
            action.velocity = _humanVelocity();
            action.angle = (Math.random() * 2 - 1) * Math.PI;
        }
        if (action.type === "scroll") {
            action.scrollVelocity = _humanVelocity();
            action.scrollAcceleration = (Math.random() * 2 - 1) * 400;
        }
        return action;
    }

    return action;
}

// Load existing data from localStorage
function loadTrackingData() {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : [];
}

// Save actions to localStorage.
//
// The page URL is stamped here rather than in each individual listener so
// every event type gets it -- including mousemove, which carries no target
// at all and is therefore the single largest source of unlabelable events
// downstream. Stamping at *capture* time (not send time) matters: actions
// are buffered in localStorage and flushed up to 5s later, or on pagehide,
// so a batch can be transmitted from a page the user has already navigated
// away from. Deriving the URL server-side from the Referer header would
// misattribute exactly those events.
function saveToLocalStorage(action) {
    action = applyTamper(action);
    if (action === null) return;   // adversary suppressed this event
    let userActions = loadTrackingData();
    if (action.url === undefined) {
        action.url = window.location.href;
    }
    userActions.push(action);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(userActions));
}

// Emit one synthetic "pageload" event per page as the zero point for
// time-to-first-action (first real interaction minus this timestamp). Without
// it there is no page-boundary marker, so decision latency -- a core feature
// and a human/agent discriminator -- cannot be computed site-side. `url` is
// stamped by saveToLocalStorage, matching every other event.
(function emitPageLoad() {
    function fire() {
        saveToLocalStorage({
            type: "pageload",
            timestamp: new Date().toISOString(),
            target: "document"
        });
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", fire, { once: true });
    } else {
        fire();  // script loaded after DOMContentLoaded already fired
    }
})();

// Track clicks
document.addEventListener("click", function(event) {
    const action = {
        type: "click",
        timestamp: new Date().toISOString(),
        target: event.target.id || event.target.tagName,
        x: event.clientX,
        y: event.clientY
    };
    saveToLocalStorage(action);
    console.log("Click tracked:", action);
});

// Track keyboard input
document.addEventListener("keydown", function(event) {
    const el = event.target;
    const type = (el.type || "").toLowerCase();
    const isSensitive = type === "password" ||
        el.autocomplete === "current-password" ||
        el.autocomplete === "new-password" ||
        el.hasAttribute("data-no-track");

    if (isSensitive) {
        return; // never log keystrokes from password or explicitly excluded fields
    }

    const action = {
        type: "keydown",
        timestamp: new Date().toISOString(),
        key: event.key,
        target: el.id || el.tagName
    };
    saveToLocalStorage(action);
    console.log("Key press tracked:", action);
});

// Track mouse scroll
let lastScrollTime = null;
let lastScrollY = window.scrollY || window.pageYOffset;
let lastScrollVelocity = 0;

document.addEventListener("wheel", function(event) {
    const now = Date.now();
    const currentScrollY = window.scrollY || window.pageYOffset;
    const deltaTime = lastScrollTime ? (now - lastScrollTime) / 1000 : 0;
    const velocity = deltaTime > 0 ? (currentScrollY - lastScrollY) / deltaTime : 0;
    const acceleration = deltaTime > 0 ? (velocity - lastScrollVelocity) / deltaTime : 0;

    const action = {
        type: "scroll",
        timestamp: new Date().toISOString(),
        target: event.target.id || event.target.tagName,
        direction: event.deltaY > 0 ? "down" : "up",
        deltaX: event.deltaX,
        deltaY: event.deltaY,
        deltaZ: event.deltaZ,
        scrollX: window.scrollX || window.pageXOffset,
        scrollY: currentScrollY,
        scrollVelocity: velocity,
        scrollAcceleration: acceleration
    };

    lastScrollTime = now;
    lastScrollY = currentScrollY;
    lastScrollVelocity = velocity;

    saveToLocalStorage(action);
    console.log("Scroll tracked:", action);
});

// Track mouse movement (sampled, not every pixel-level event)
let lastMouseX = null;
let lastMouseY = null;
let lastMouseTime = null;
let latestMousePos = null;

document.addEventListener("mousemove", function(event) {
    latestMousePos = { x: event.clientX, y: event.clientY };
});

setInterval(function() {
    if (!latestMousePos) return;

    const now = Date.now();
    const { x, y } = latestMousePos;

    let velocity = 0;
    let angle = null;
    if (lastMouseTime !== null) {
        const dt = (now - lastMouseTime) / 1000;
        const dx = x - lastMouseX;
        const dy = y - lastMouseY;
        const distance = Math.sqrt(dx * dx + dy * dy);
        velocity = dt > 0 ? distance / dt : 0;
        angle = Math.atan2(dy, dx);
    }

    const action = {
        type: "mousemove",
        timestamp: new Date().toISOString(),
        x: x,
        y: y,
        velocity: velocity,
        angle: angle
    };

    lastMouseX = x;
    lastMouseY = y;
    lastMouseTime = now;
    latestMousePos = null;

    saveToLocalStorage(action);
}, 100);

// Get all tracked data from localStorage
function getTrackedData() {
    return loadTrackingData();
}

// Clear all tracked data
function clearTrackedData() {
    localStorage.removeItem(STORAGE_KEY);
    console.log("All tracking data cleared");
}

// Display tracked data on the page
function displayTrackedData() {
    const data = loadTrackingData();
    const container = document.getElementById("tracking-display");

    if (!container) {
        console.warn("No element with id 'tracking-display' found on page");
        return;
    }

    if (data.length === 0) {
        container.innerHTML = "<p>No tracking data yet</p>";
        return;
    }

    let html = `<h3>Tracked Actions (${data.length})</h3>`;
    html += "<table style='border-collapse: collapse; width: 100%;'>";
    html += "<tr style='background: #f0f0f0;'><th style='border: 1px solid #ddd; padding: 8px;'>Type</th><th style='border: 1px solid #ddd; padding: 8px;'>Target</th><th style='border: 1px solid #ddd; padding: 8px;'>Time</th><th style='border: 1px solid #ddd; padding: 8px;'>Details</th></tr>";

    data.forEach((action, index) => {
        const details = action.key || `${action.x}, ${action.y}` || action.direction || "";
        html += `<tr style='${index % 2 === 0 ? "background: #fff;" : "background: #f9f9f9;"}'><td style='border: 1px solid #ddd; padding: 8px;'>${action.type}</td><td style='border: 1px solid #ddd; padding: 8px;'>${action.target}</td><td style='border: 1px solid #ddd; padding: 8px; font-size: 12px;'>${new Date(action.timestamp).toLocaleTimeString()}</td><td style='border: 1px solid #ddd; padding: 8px;'>${details}</td></tr>`;
    });

    html += "</table>";
    html += `<p style='margin-top: 10px;'><button onclick='clearTrackedData(); displayTrackedData()'>Clear Data</button></p>`;

    container.innerHTML = html;
}

// Send tracked actions to server
let sendInProgress = false;
let consecutiveFailures = 0;

function sendTrackingData() {
    if (sendInProgress) return;
    const userActions = loadTrackingData();
    if (userActions.length > 0) {
        sendInProgress = true;
        fetch("/api/track", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(userActions)
        }).then(response => {
            if (response.ok) {
                clearTrackedData();
                consecutiveFailures = 0;
            } else {
                consecutiveFailures++;
            }
        }).catch(err => {
            console.error("Tracking error:", err);
            consecutiveFailures++;
        }).finally(() => { sendInProgress = false; });
    }
}

// Best-effort send for when the page is closing/hiding. Use fetch(keepalive),
// NOT sendBeacon: the collection harness attaches the X-Harness / X-Model
// attribution headers to the browser context, and those ride along on fetch
// requests but are STRIPPED from sendBeacon. With a beacon, a fast-navigating
// agent whose events all flush on page-hide was recorded unlabelled (None/None).
// keepalive still guarantees dispatch during page teardown, and (unlike a plain
// fetch) it also carries the auth cookie + custom headers.
function sendTrackingDataViaBeacon() {
    const userActions = loadTrackingData();
    if (userActions.length === 0) return;
    try {
        fetch("/api/track", {
            method: "POST",
            credentials: "same-origin",
            keepalive: true,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(userActions)
        });
        clearTrackedData();
    } catch (e) {
        if (navigator.sendBeacon) {
            const blob = new Blob([JSON.stringify(userActions)], { type: "application/json" });
            navigator.sendBeacon("/api/track", blob);
            clearTrackedData();
        }
    }
}

// Flush periodically while the page is open
setInterval(sendTrackingData, 5000);

// Retry sooner after a failure instead of waiting the full interval,
// so a brief connectivity blip doesn't compound data loss.
setInterval(function() {
    if (consecutiveFailures > 0 && consecutiveFailures < 5) {
        sendTrackingData();
    }
}, 3000);

// Flush whenever the tab is hidden/closed/navigated away from
document.addEventListener("visibilitychange", function() {
    if (document.visibilityState === "hidden") {
        sendTrackingDataViaBeacon();
    }
});
window.addEventListener("pagehide", sendTrackingDataViaBeacon);