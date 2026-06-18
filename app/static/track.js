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
    const action = {
        type: "keydown",
        timestamp: new Date().toISOString(),
        key: event.key,
        target: event.target.id || event.target.tagName
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
function sendTrackingData() {
    const userActions = loadTrackingData();
    if (userActions.length > 0) {
        fetch("/api/track", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(userActions)
        }).catch(err => console.error("Tracking error:", err));
    }
}