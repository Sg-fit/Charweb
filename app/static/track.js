// Track user actions and save to localStorage
const STORAGE_KEY = "userTrackingData";

// Load existing data from localStorage
function loadTrackingData() {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : [];
}

// Save actions to localStorage
function saveToLocalStorage(action) {
    let userActions = loadTrackingData();
    userActions.push(action);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(userActions));
}

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

// Best-effort send for when the page is closing/hiding. sendBeacon is
// fire-and-forget (no response to confirm success), but the browser
// guarantees it gets dispatched even as the page tears down, which a
// normal fetch() does not guarantee.
function sendTrackingDataViaBeacon() {
    const userActions = loadTrackingData();
    if (userActions.length === 0) return;
    if (navigator.sendBeacon) {
        const blob = new Blob([JSON.stringify(userActions)], { type: "application/json" });
        navigator.sendBeacon("/api/track", blob);
        clearTrackedData();
    } else {
        sendTrackingData();
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