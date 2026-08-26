// =============================================================================
// GLOBAL STATE & CONFIGURATION
// =============================================================================
let map;
let activeRadarRing = null;
let alertCount = 0;
const nodeMarkers = {};

// Track all active threat radar rings on the map (keyed by unique logId)
const activeRadarRings = {};

// Track currently inspected log and its table row element
let currentlySelectedLog = null;
let currentlySelectedRow = null;

// Global config object, loaded dynamically from the backend
let CAMERA_NODES = {};

// Track processed alert unique keys to prevent duplicate logs (1 log per report)
const processedAlerts = new Set();

// Incident Log Array for persistence & statistics
let incidentLog = [];

// Current active history log filter states
let activeLogFilter = "all"; // "all", "human", "wildlife", "resolved"
let logSearchQuery = "";
let currentSortColumn = "timestamp";
let currentSortDirection = "desc"; // "asc", "desc"

// Calendar state variables
let currentCalYear = new Date().getFullYear();
let currentCalMonth = new Date().getMonth(); // 0-11
let selectedCalDateStr = null; // "YYYY-MM-DD"

// Main Center Station coordinate (under 7 km from Camera Trap 01)
const CENTER_STATION_COORDS = [15.606848352556517, 99.31807552572246];
let centerStationMarker = null;
const connectingLines = {};

// Camera trap status icons (active glowing red, inactive dim gray, activated online green)
let inactiveTrapIcon = null;
let activeTrapIcon = null;
let activatedTrapIcon = null;

// Heartbeat tracking for active/online status (nodeId -> timestamp)
const lastHeartbeats = {};
// Shortened to 15 seconds for interactive demo responsiveness (originally 3h10m)
const HEARTBEAT_TIMEOUT_MS = 15 * 1000;

// Track the latest RSSI values for each camera node to show on the right panel
const nodeLatestRssi = {
    "Camera-Trap-01": -88,
    "Camera-Trap-02": -96,
    "Camera-Trap-03": -85
};

// Active threat nodes set (nodeId)
const activeThreatNodes = new Set();

// =============================================================================
// SECURITY: HTML ESCAPING
// =============================================================================
// Node IDs, names and descriptions all come from sensor_config.json, which is written
// wholesale by POST /sensor-config with no server-side sanitisation. Every place that
// interpolates one of those fields (or any other server-sourced string) into an
// innerHTML template literal must escape it here first, or a camera trap named
// `<img src=x onerror=...>` runs script in every ranger's browser (FIX_PLAN.md C2).
function escapeHtml(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// =============================================================================
// 1. MAP INITIALIZATION (LEAFLET)
// =============================================================================
function initMap() {
    // Center around Huai Kha Khaeng Wildlife Sanctuary Main Station
    map = L.map('map', {
        zoomControl: true,
        attributionControl: false
    }).setView(CENTER_STATION_COORDS, 12.0);

    // Street Map (CartoDB Voyager - premium dashboard street map)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
        maxZoom: 19
    }).addTo(map);
}

function getPopupContent(nodeId, info, distanceKm) {
    const isThreat = activeThreatNodes.has(nodeId);
    const statusText = isThreat ? "THREAT ACTIVE" : "STANDBY";
    const statusClass = isThreat ? "danger" : "success";
    
    return `
        <div class="custom-leaflet-card" style="padding: 4px; font-family: 'Inter', sans-serif; min-width: 220px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <strong style="color: #3b82f6; font-size: 11px; letter-spacing: 0.05em; font-weight: 800;">${escapeHtml(nodeId)}</strong>
                <span class="status-badge-${statusClass}" style="font-size: 9px; font-weight: 800; padding: 2px 6px; border-radius: 4px; text-transform: uppercase; ${
                    isThreat
                    ? 'background-color: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.25);'
                    : 'background-color: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.25);'
                }">${statusText}</span>
            </div>
            <h4 style="font-weight: 700; font-size: 13px; color: #f3f4f6; margin-bottom: 4px; margin-top: 0;">${escapeHtml(info.name)}</h4>
            <p style="color: #9ca3af; font-size: 11px; margin-top: 4px; margin-bottom: 8px; line-height: 1.3;">${escapeHtml(info.description)}</p>
            <div style="height: 1px; background-color: rgba(255, 255, 255, 0.05); margin: 8px 0;"></div>
            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px;">
                <div style="display: flex; flex-direction: column; background-color: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 4px; padding: 4px 6px;">
                    <span style="font-size: 9px; color: #6b7280; text-transform: uppercase; font-weight: 700;">Distance</span>
                    <span style="font-size: 11px; font-weight: 600; color: #3b82f6; margin-top: 2px;">${distanceKm} km</span>
                </div>
                <div style="display: flex; flex-direction: column; background-color: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 4px; padding: 4px 6px;">
                    <span style="font-size: 9px; color: #6b7280; text-transform: uppercase; font-weight: 700;">Avg RSSI</span>
                    <span style="font-size: 11px; font-weight: 600; color: #a855f7; margin-top: 2px;">${isThreat ? '-102' : '-85'} dBm</span>
                </div>
                <div style="display: flex; flex-direction: column; background-color: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 4px; padding: 4px 6px;">
                    <span style="font-size: 9px; color: #6b7280; text-transform: uppercase; font-weight: 700;">Battery</span>
                    <span style="font-size: 11px; font-weight: 600; color: #eab308; margin-top: 2px;">82%</span>
                </div>
                <div style="display: flex; flex-direction: column; background-color: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 4px; padding: 4px 6px;">
                    <span style="font-size: 9px; color: #6b7280; text-transform: uppercase; font-weight: 700;">Status</span>
                    <span style="font-size: 11px; font-weight: 600; color: ${isThreat ? '#ef4444' : '#10b981'}; margin-top: 2px;">${isThreat ? 'Alert' : 'OK'}</span>
                </div>
            </div>
            ${isThreat ? `
                <button class="popup-view-threat-btn" data-node-id="${escapeHtml(nodeId)}" style="margin-top: 10px; width: 100%; padding: 6px 10px; background-color: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 4px; color: #f87171; font-size: 10px; font-weight: 700; cursor: pointer; transition: all 0.2s ease; font-family: 'Inter', sans-serif;" onmouseover="this.style.backgroundColor='rgba(239, 68, 68, 0.3)'; this.style.borderColor='rgba(239, 68, 68, 0.5)';" onmouseout="this.style.backgroundColor='rgba(239, 68, 68, 0.15)'; this.style.borderColor='rgba(239, 68, 68, 0.3)';">VIEW ALERT DETAILS</button>
            ` : ''}
            <div style="font-size: 9px; color: #4b5563; margin-top: 8px; text-align: center;">
                * Drag marker to relocate camera
            </div>
        </div>
    `;
}

// Called from a delegated click listener (see initPopupThreatButtonController below), not
// from an inline onclick="" attribute. nodeId therefore always arrives as a real JS string
// value read via .dataset, never re-parsed out of HTML/attribute text - the injection this
// sidesteps is that an onclick="viewActiveThreat('${nodeId}')" attribute would need BOTH
// HTML-attribute escaping AND JS-string escaping to be safe, because the browser HTML-decodes
// the attribute value before handing it to the JS parser, undoing a plain escapeHtml() pass.
function viewActiveThreat(nodeId) {
    const activeLog = incidentLog.find(l => l.nodeId === nodeId && l.action === "PATROL_DISPATCHED");
    if (activeLog) {
        const row = document.querySelector(`#log-tbody tr[data-id="${activeLog.logId}"]`);
        if (row) {
            document.querySelectorAll("#log-tbody tr").forEach(r => r.classList.remove("selected-row"));
            row.classList.add("selected-row");
            currentlySelectedLog = activeLog;
            currentlySelectedRow = row;
        }
        loadAlertIntoSidebar(activeLog, true);
    } else {
        const nodeLog = incidentLog.find(l => l.nodeId === nodeId);
        if (nodeLog) {
            const row = document.querySelector(`#log-tbody tr[data-id="${nodeLog.logId}"]`);
            if (row) {
                document.querySelectorAll("#log-tbody tr").forEach(r => r.classList.remove("selected-row"));
                row.classList.add("selected-row");
                currentlySelectedLog = nodeLog;
                currentlySelectedRow = row;
            }
            loadAlertIntoSidebar(nodeLog, true);
        }
    }
}

// Popup content is regenerated on every marker click (getPopupContent), so the button
// inside it is bound once, here, via delegation on the document rather than re-attaching
// a listener each time a popup opens.
function initPopupThreatButtonController() {
    document.addEventListener("click", (e) => {
        const btn = e.target.closest(".popup-view-threat-btn");
        if (btn) viewActiveThreat(btn.dataset.nodeId);
    });
}

function plotMarkers() {
    // Clear existing camera trap markers
    for (const marker of Object.values(nodeMarkers)) {
        map.removeLayer(marker);
    }

    // Clear existing center station marker
    if (centerStationMarker) {
        map.removeLayer(centerStationMarker);
        centerStationMarker = null;
    }

    // Clear existing connecting lines
    for (const line of Object.values(connectingLines)) {
        map.removeLayer(line);
    }

    // Initialize custom icons if not already done
    if (!inactiveTrapIcon) {
        inactiveTrapIcon = L.divIcon({
            className: 'camera-trap-container',
            html: '<div class="camera-trap-marker inactive"><div class="trap-led"></div></div>',
            iconSize: [16, 16],
            iconAnchor: [8, 8]
        });
    }
    if (!activeTrapIcon) {
        activeTrapIcon = L.divIcon({
            className: 'camera-trap-container',
            html: '<div class="camera-trap-marker active"><div class="trap-led"></div></div>',
            iconSize: [16, 16],
            iconAnchor: [8, 8]
        });
    }
    if (!activatedTrapIcon) {
        activatedTrapIcon = L.divIcon({
            className: 'camera-trap-container',
            html: '<div class="camera-trap-marker activated"><div class="trap-led"></div></div>',
            iconSize: [16, 16],
            iconAnchor: [8, 8]
        });
    }

    // Custom Icon for Main Center Station
    const centerStationIcon = L.divIcon({
        className: 'center-station-container',
        html: `
            <div class="center-station-marker">
                <div class="station-dish">
                    <svg class="station-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 2c-.83 0-1.5.67-1.5 1.5v3.1c-3.12.42-5.5 3.12-5.5 6.4 0 2.27 1.18 4.25 2.94 5.4l-1.14 2.28c-.28.56-.05 1.22.5 1.5s1.22.05 1.5-.5l1.14-2.28c.78.36 1.66.6 2.56.6v3c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5v-3c.9 0 1.78-.24 2.56-.6l1.14 2.28c.28.56.94.78 1.5.5s.78-.94.5-1.5l-1.14-2.28c1.76-1.15 2.94-3.13 2.94-5.4 0-3.28-2.38-5.98-5.5-6.4v-3.1c0-.83-.67-1.5-1.5-1.5zm0 8c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3z"/>
                    </svg>
                </div>
                <div class="station-pulse"></div>
            </div>
        `,
        iconSize: [32, 32],
        iconAnchor: [16, 16]
    });

    // Plot Main Center Station
    centerStationMarker = L.marker(CENTER_STATION_COORDS, {
        icon: centerStationIcon
    })
    .addTo(map)
    .bindPopup(`
        <div style="color: #f3f4f6; background-color: #0f172a; padding: 10px; border-radius: 8px; font-family: 'Inter', sans-serif; border: 1px solid rgba(59, 130, 246, 0.3); min-width: 180px;">
            <strong style="color: #60a5fa; font-size: 13px;">MAIN CENTER STATION</strong><br/>
            <span style="font-weight: 500; font-size: 11px; color: #9ca3af;">Central Ranger Headquarters</span>
            <p style="color: #6b7280; font-size: 11px; margin-top: 6px; line-height: 1.4; margin-bottom: 0;">Coordinates data aggregation point for all sensor traps within a 7 km radius.</p>
        </div>
    `);

    // Plot all Camera Trap Nodes from the configuration
    for (const [nodeId, info] of Object.entries(CAMERA_NODES)) {
        // Calculate distance to Main Center Station
        const distanceMeters = map.distance([info.latitude, info.longitude], CENTER_STATION_COORDS);
        const distanceKm = (distanceMeters / 1000).toFixed(2);
        console.log(`[MAP] Sensor ${nodeId} is ${distanceKm} km from Main Center Station.`);

        // Plot camera trap marker as inactive initially
        const marker = L.marker([info.latitude, info.longitude], { 
            icon: inactiveTrapIcon,
            draggable: true 
        })
        .addTo(map)
        .bindPopup(getPopupContent(nodeId, info, distanceKm));

        // Draw polyline link to Main Center Station
        const line = L.polyline([[info.latitude, info.longitude], CENTER_STATION_COORDS], {
            color: '#4b5563',
            weight: 2,
            opacity: 0.4,
            className: 'connecting-link inactive-link'
        }).addTo(map);

        line.bindTooltip(`Link: ${nodeId} to Center Station (${distanceKm} km)`, {
            permanent: false,
            direction: 'center',
            className: 'link-tooltip'
        });

        connectingLines[nodeId] = line;

        // Handle Drag End event to capture new coordinates
        marker.on('dragend', function(event) {
            const position = event.target.getLatLng();
            const lat = parseFloat(position.lat.toFixed(4));
            const lng = parseFloat(position.lng.toFixed(4));
            
            // Update local state
            CAMERA_NODES[nodeId].latitude = lat;
            CAMERA_NODES[nodeId].longitude = lng;
            
            // If the moved node is currently selected in the settings panel, update its inputs
            const selectedNode = document.getElementById("settings-node-select").value;
            if (selectedNode === nodeId) {
                document.getElementById("settings-node-lat").value = lat;
                document.getElementById("settings-node-lng").value = lng;
            }

            // Update connecting line coordinates and distance dynamically
            if (connectingLines[nodeId]) {
                connectingLines[nodeId].setLatLngs([[lat, lng], CENTER_STATION_COORDS]);
                
                const newDistanceMeters = map.distance([lat, lng], CENTER_STATION_COORDS);
                const newDistanceKm = (newDistanceMeters / 1000).toFixed(2);
                
                // Update marker popup and line tooltip
                marker.setPopupContent(getPopupContent(nodeId, CAMERA_NODES[nodeId], newDistanceKm));
                connectingLines[nodeId].bindTooltip(`Link: ${nodeId} to Center Station (${newDistanceKm} km)`, {
                    permanent: false,
                    direction: 'center',
                    className: 'link-tooltip'
                });
            }
            
            showNotification(`Camera ${nodeId} moved! Click 'Save Configuration' to save.`, "warning");
        });

        nodeMarkers[nodeId] = marker;
    }
    renderCameraStatusList();
}

function getSignalBarsHtml(rssi, isThreat) {
    if (rssi === undefined || rssi === null) {
        return `<div class="mini-signal-indicator offline" title="Offline / Not Activated">
            <div class="mini-sig-bars">
                <div class="mini-bar" style="height: 4px;"></div>
                <div class="mini-bar" style="height: 6px;"></div>
                <div class="mini-bar" style="height: 8px;"></div>
                <div class="mini-bar" style="height: 10px;"></div>
                <div class="mini-bar" style="height: 12px;"></div>
            </div>
            <span class="mini-sig-text">N/A</span>
        </div>`;
    }

    let activeBars = 0;
    let quality = "Unknown";
    const rssiVal = parseInt(rssi);

    if (rssiVal >= -75) {
        quality = "Excellent";
        activeBars = 5;
    } else if (rssiVal >= -88) {
        quality = "Good";
        activeBars = 4;
    } else if (rssiVal >= -100) {
        quality = "Moderate";
        activeBars = 3;
    } else if (rssiVal >= -108) {
        quality = "Poor";
        activeBars = 2;
    } else {
        quality = "Critical";
        activeBars = 1;
    }

    const barClass = isThreat ? "active danger" : "active";

    let barsHtml = "";
    const heights = [4, 6, 8, 10, 12];
    for (let i = 0; i < 5; i++) {
        const isActive = i < activeBars;
        barsHtml += `<div class="mini-bar ${isActive ? barClass : ''}" style="height: ${heights[i]}px;"></div>`;
    }

    return `<div class="mini-signal-indicator" title="${rssiVal} dBm (${quality})">
        <div class="mini-sig-bars">${barsHtml}</div>
        <span class="mini-sig-text">${rssiVal} dBm</span>
    </div>`;
}

function renderCameraStatusList() {
    const list = document.getElementById("camera-status-list");
    if (!list) return;
    
    list.innerHTML = "";
    const now = Date.now();
    
    // Sort keys so the list is always in order
    const sortedKeys = Object.keys(CAMERA_NODES).sort();
    
    for (const nodeId of sortedKeys) {
        const lastHb = lastHeartbeats[nodeId] || 0;
        const isOnline = (now - lastHb) < HEARTBEAT_TIMEOUT_MS;
        const isThreat = activeThreatNodes.has(nodeId);
        
        const item = document.createElement("div");
        item.className = "camera-status-item";
        item.id = `status-item-${nodeId}`;
        
        let rssiVal = null;
        
        if (isThreat) {
            rssiVal = nodeLatestRssi[nodeId] || -102;
        } else if (isOnline) {
            rssiVal = nodeLatestRssi[nodeId] || -85;
        }
        
        const signalHtml = getSignalBarsHtml(rssiVal, isThreat);
        
        item.innerHTML = `
            <span class="camera-name">${escapeHtml(nodeId)}</span>
            ${signalHtml}
        `;
        list.appendChild(item);
    }
}

// =============================================================================
// 2. WEB AUDIO API SYNTHESIZER
// =============================================================================
function playAlertChime() {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    
    try {
        const ctx = new AudioContext();
        
        // --- Note 1: Lower Frequency Start ---
        const osc1 = ctx.createOscillator();
        const gain1 = ctx.createGain();
        
        osc1.type = 'sine';
        osc1.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
        osc1.frequency.exponentialRampToValueAtTime(880.00, ctx.currentTime + 0.18); // A5
        
        gain1.gain.setValueAtTime(0.12, ctx.currentTime);
        gain1.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
        
        osc1.connect(gain1);
        gain1.connect(ctx.destination);
        
        osc1.start();
        osc1.stop(ctx.currentTime + 0.4);
        
        // --- Note 2: Higher Frequency Delay ---
        setTimeout(() => {
            const osc2 = ctx.createOscillator();
            const gain2 = ctx.createGain();
            
            osc2.type = 'sine';
            osc2.frequency.setValueAtTime(880.00, ctx.currentTime); // A5
            osc2.frequency.exponentialRampToValueAtTime(1174.66, ctx.currentTime + 0.18); // D6
            
            gain2.gain.setValueAtTime(0.12, ctx.currentTime);
            gain2.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
            
            osc2.connect(gain2);
            gain2.connect(ctx.destination);
            
            osc2.start();
            osc2.stop(ctx.currentTime + 0.4);
        }, 130);
    } catch (e) {
        console.warn("Web Audio chime blocked by browser autoplay policies:", e);
    }
}

// =============================================================================
// 3. ALERT PROCESSING ENGINE
// =============================================================================
function handleNewAlert(data, isLive = true) {
    if (!data || data.error || data.event_type === "CONFIG_UPDATE") return;

    // Deduplicate alerts: verify if we already logged this unique image/timestamp combo
    const imageName = data.image_name || "";
    const timestampStr = data.timestamp || new Date().toISOString();
    // Prefer the server-assigned log_id: it is stable across reloads, unlike image+timestamp
    const alertKey = data.log_id || `${imageName}_${timestampStr}`;
    
    if (processedAlerts.has(alertKey)) {
        console.log(`[SYSTEM] Duplicate alert event blocked for key: ${alertKey}`);
        return;
    }
    processedAlerts.add(alertKey);

    // 1. Live LoRa ARQ Transmission Progress Simulation (Premium visual feel!)
    if (isLive) {
        simulateTransmissionProgress(() => {
            finalizeAlertProcessing(data, isLive, imageName, timestampStr);
        });
    } else {
        finalizeAlertProcessing(data, isLive, imageName, timestampStr);
    }
}

function simulateTransmissionProgress(callback) {
    const progressPanel = document.getElementById("transmission-progress-panel");
    const fill = document.getElementById("progress-bar-fill");
    const valText = document.getElementById("progress-percent-val");
    
    if (!progressPanel) {
        callback();
        return;
    }
    
    progressPanel.classList.remove("hidden");
    let pct = 0;
    fill.style.width = "0%";
    valText.textContent = "0%";
    
    const interval = setInterval(() => {
        pct += Math.floor(Math.random() * 15) + 8;
        if (pct >= 100) {
            pct = 100;
            clearInterval(interval);
            setTimeout(() => {
                progressPanel.classList.add("hidden");
                callback();
            }, 400);
        }
        fill.style.width = `${pct}%`;
        valText.textContent = `${pct}%`;
    }, 120);
}

function finalizeAlertProcessing(data, isLive, imageName, timestampStr) {
    // Play alert chime sound if this is a live push event
    if (isLive && data.threat_type === "HUMAN_INTRUDER") {
        playAlertChime();
    }

    // Update Connection Status Badge to Connected
    updateConnectionBadge("connected");

    // Update Latest Sync Timestamp
    const now = new Date();
    const syncTimeText = document.getElementById("last-sync-time");
    if (syncTimeText) syncTimeText.textContent = now.toLocaleTimeString();

    // Extract Alert Properties
    const nodeId = data.node_id || "Camera-Trap-01";
    const threatType = data.threat_type || "HUMAN_INTRUDER";
    const confidence = (data.confidence * 100).toFixed(0) + "%";
    const rssi = data.rssi ? `${data.rssi} dBm` : "-102 dBm";
    const battery = data.battery_percent ? `${data.battery_percent}% (${data.battery_voltage}V)` : "82% (3.82V)";
    
    // Read location properties from the camera trap configuration, or fall back to alert coordinates
    const lat = CAMERA_NODES[nodeId] ? CAMERA_NODES[nodeId].latitude : (parseFloat(data.latitude) || 15.6062);
    const lng = CAMERA_NODES[nodeId] ? CAMERA_NODES[nodeId].longitude : (parseFloat(data.longitude) || 99.2158);
    const locationName = CAMERA_NODES[nodeId]?.name || data.location_name || "Huai Kha Khaeng Wildlife Sanctuary - HQ";

    // Light up the camera trap marker and its connecting line (camera activated)
    activeThreatNodes.add(nodeId);
    updateNodeOnlineState(nodeId, true);
    if (connectingLines[nodeId]) {
        connectingLines[nodeId].setStyle({
            color: '#ef4444',
            weight: 4,
            opacity: 0.9
        });
        const el = connectingLines[nodeId].getElement();
        if (el) {
            el.setAttribute('class', 'leaflet-interactive connecting-link active-link');
        }
    }

    // Update Threat Indicator Badge
    const indicator = document.getElementById("threat-indicator");
    if (indicator) {
        indicator.textContent = "CRITICAL ALERT";
        indicator.className = "threat-tag tag-active";
    }

    // Flash Sidebar Border Red
    const sidebar = document.querySelector(".sidebar-panel");
    sidebar.classList.add("threat-active");

    // Update Metadata Cards
    document.getElementById("meta-node-id").textContent = nodeId;
    document.getElementById("meta-threat-type").textContent = threatType;
    document.getElementById("meta-confidence").textContent = confidence;
    document.getElementById("meta-timestamp").textContent = new Date(timestampStr).toLocaleString();
    document.getElementById("meta-rssi").textContent = rssi;
    document.getElementById("meta-battery").textContent = battery;
    
    // Update Signal strength gauge widget
    updateSignalOverlay(nodeId, data.rssi || -102);

    // Update Captured WebP Image (Bypass cache with timestamp query)
    const alertImage = document.getElementById("alert-image");
    const placeholder = document.getElementById("no-image-placeholder");
    
    alertImage.src = `/received_images/${imageName}?t=${new Date().getTime()}`;
    alertImage.classList.remove("hidden");
    placeholder.classList.add("hidden");

    // Map View: Center and Pulse Radar Circle at received alert coordinates
    if (isLive) {
        map.setView([lat, lng], 13.5);
    }

    // Add pulsing radar circle icon on map (keyed uniquely by logId)
    // Adopt the server's id when present so POST /resolve-alert can address this exact
    // incident. A locally minted id would make the server resolve the wrong record.
    const logId = data.log_id || ("log_" + new Date().getTime() + "_" + Math.floor(Math.random() * 1000));
    
    const radarIcon = L.divIcon({
        className: 'radar-pulse-container',
        html: '<div class="radar-pulse-icon"></div>',
        iconSize: [40, 40],
        iconAnchor: [20, 20]
    });
    const ringMarker = L.marker([lat, lng], { icon: radarIcon }).addTo(map);
    activeRadarRings[logId] = ringMarker;

    const dateObj = new Date(timestampStr);
    const formattedTimestamp = dateObj.toISOString().split('T')[0] + ' ' + dateObj.toTimeString().split(' ')[0];

    // Compile log object
    const newLog = {
        logId: logId,
        timestamp: formattedTimestamp,
        nodeId: nodeId,
        threatType: threatType,
        confidence: confidence,
        locationName: locationName,
        rssi: rssi,
        action: data.action || "PATROL_DISPATCHED",
        imageName: imageName,
        latitude: lat,
        longitude: lng,
        battery: battery,
        fullDate: dateObj.toISOString().split('T')[0], // YYYY-MM-DD
        isoTimestamp: timestampStr
    };

    // Save to global list, add to table, and persist in localStorage
    incidentLog.push(newLog);
    addLogToTable(newLog, true, isLive);
    saveLogsToLocalStorage();
    updateStatsBar();

    // If currently viewing the Incident History dashboard, refresh it dynamically in real-time!
    const incidentView = document.getElementById("incident-view");
    if (incidentView && !incidentView.classList.contains("hidden")) {
        updateIncidentDashboardStats();
        renderIncidentCalendar(currentCalYear, currentCalMonth);
        if (!selectedCalDateStr) {
            renderAllIncidentHistory();
        } else {
            filterIncidentHistoryByDate(selectedCalDateStr);
        }
    }
}

function updateConnectionBadge(state) {
    const badge = document.getElementById("connection-badge");
    if (!badge) return;
    
    if (state === "connected") {
        badge.textContent = "CONNECTED";
        badge.className = "badge badge-success";
    } else if (state === "reconnecting") {
        badge.textContent = "RECONNECTING";
        badge.className = "badge badge-warning";
    } else {
        badge.textContent = "DISCONNECTED";
        badge.className = "badge badge-error";
    }
}

// Update the real-time signal strength gauge overlay
function updateSignalOverlay(nodeId, rssi) {
    const rssiVal = parseInt(rssi);
    if (!isNaN(rssiVal)) {
        nodeLatestRssi[nodeId] = rssiVal;
        renderCameraStatusList();
    }
    
    // Update top header average RSSI stat card
    const headerRssi = document.getElementById("avg-rssi-value");
    if (headerRssi) headerRssi.textContent = `${rssiVal} dBm`;
}

// =============================================================================
// 4. LOG MANAGEMENT, LOCALSTORAGE & CSV EXPORT
// =============================================================================
function loadAlertIntoSidebar(alertData, showModal = true) {
    const nodeId = alertData.nodeId || alertData.node_id || "Camera-Trap-01";
    const threatType = alertData.threatType || alertData.threat_type || "HUMAN_INTRUDER";
    const confidence = alertData.confidence;
    const timestampStr = alertData.timestamp;
    const rssi = alertData.rssi;
    const battery = alertData.battery || (alertData.battery_percent ? `${alertData.battery_percent}% (${alertData.battery_voltage}V)` : "82% (3.82V)");
    const locationName = alertData.locationName || alertData.location_name || CAMERA_NODES[nodeId]?.name || "Huai Kha Khaeng Wildlife Sanctuary - HQ";
    const imageName = alertData.imageName || alertData.image_name || "";
    
    // Update metadata cards
    document.getElementById("meta-node-id").textContent = nodeId;
    document.getElementById("meta-threat-type").textContent = threatType;
    document.getElementById("meta-confidence").textContent = confidence;
    document.getElementById("meta-timestamp").textContent = timestampStr;
    document.getElementById("meta-rssi").textContent = rssi;
    document.getElementById("meta-battery").textContent = battery;
    
    // Update image
    const alertImage = document.getElementById("alert-image");
    const placeholder = document.getElementById("no-image-placeholder");
    
    alertImage.src = `/received_images/${imageName}`;
    alertImage.classList.remove("hidden");
    placeholder.classList.add("hidden");
    
    // Update Threat Indicator Badge based on selection type
    const indicator = document.getElementById("threat-indicator");
    const sidebar = document.querySelector(".sidebar-panel");
    const btnAck = document.getElementById("btn-acknowledge");
    
    const isHuman = threatType.includes("HUMAN_INTRUDER") || threatType.includes("CRITICAL");
    if (isHuman) {
        if (alertData.action === "PATROL_DISPATCHED") {
            if (indicator) {
                indicator.textContent = "CRITICAL ALERT";
                indicator.className = "threat-tag tag-active";
            }
            sidebar.classList.add("threat-active");
            btnAck.classList.remove("hidden");
        } else {
            if (indicator) {
                indicator.textContent = "RESOLVED THREAT";
                indicator.className = "threat-tag tag-inactive";
            }
            sidebar.classList.remove("threat-active");
            btnAck.classList.add("hidden");
        }
    } else {
        if (indicator) {
            indicator.textContent = "WILDLIFE LOGGED";
            indicator.className = "threat-tag tag-inactive";
        }
        sidebar.classList.remove("threat-active");
        btnAck.classList.add("hidden");
    }
    
    // Map view: Center on the camera trap that recorded this log if showing modal/manually selected
    if (showModal) {
        const lat = parseFloat(alertData.latitude) || CAMERA_NODES[nodeId]?.latitude || 15.6062;
        const lng = parseFloat(alertData.longitude) || CAMERA_NODES[nodeId]?.longitude || 99.2158;
        map.setView([lat, lng], 13.5);
    }

    // Open the threat details popup modal
    if (showModal) {
        const threatModal = document.getElementById("threat-detail-modal");
        if (threatModal) {
            threatModal.classList.remove("hidden");
        }
    }
}

function resolveDetection(log, tr) {
    const logId = log.logId || log.timestamp;
    
    // Persist resolution state to the server
    fetch("/resolve-alert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ log_id: logId })
    })
    .then(response => {
        if (!response.ok) {
            console.error("[SERVER] Failed to mark alert as resolved on backend");
        }
    })
    .catch(err => {
        console.error("[SERVER] Error sending resolve request:", err);
    });
    
    // Remove its specific pulsing radar ring from the map
    if (activeRadarRings[logId]) {
        map.removeLayer(activeRadarRings[logId]);
        delete activeRadarRings[logId];
    }
    
    // Update the log object action state
    log.action = "PATROL_RESOLVED";
    
    // Sync matching log in our master array
    const masterLog = incidentLog.find(l => l.logId === logId || l.timestamp === log.timestamp);
    if (masterLog) masterLog.action = "PATROL_RESOLVED";
    saveLogsToLocalStorage();
    
    // Deactivate the camera trap marker and its connecting line (turn off the light)
    const nodeId = log.nodeId || "Camera-Trap-01";
    activeThreatNodes.delete(nodeId);
    
    // Check if the camera trap is currently online/activated (within last 3h10m heartbeat)
    const now = Date.now();
    const lastHb = lastHeartbeats[nodeId] || 0;
    const isOnline = (now - lastHb) < HEARTBEAT_TIMEOUT_MS;
    updateNodeOnlineState(nodeId, isOnline);
    
    if (connectingLines[nodeId]) {
        connectingLines[nodeId].setStyle({
            color: '#4b5563',
            weight: 2,
            opacity: 0.4
        });
        const el = connectingLines[nodeId].getElement();
        if (el) {
            el.setAttribute('class', 'leaflet-interactive connecting-link inactive-link');
        }
    }
    
    // Update the log row's action column in the table
    if (tr) {
        const actionCell = tr.querySelector(".action-status");
        if (actionCell) {
            actionCell.textContent = "PATROL_RESOLVED";
            actionCell.style.color = "#9ca3af";
        }
        // Remove the resolve button from the row if it exists
        const resolveBtn = tr.querySelector(".table-resolve-btn");
        if (resolveBtn) {
            resolveBtn.remove();
        }
    }
    
    // If this is the currently selected log in the sidebar, update sidebar UI
    if (currentlySelectedLog && (currentlySelectedLog.logId === logId || currentlySelectedLog.timestamp === log.timestamp)) {
        const sidebar = document.querySelector(".sidebar-panel");
        sidebar.classList.remove("threat-active");
        
        const indicator = document.getElementById("threat-indicator");
        if (indicator) {
            indicator.textContent = "RESOLVED THREAT";
            indicator.className = "threat-tag tag-inactive";
        }
        
        const btnAck = document.getElementById("btn-acknowledge");
        btnAck.classList.add("hidden");
    }
    
    showNotification("Incident resolved and archived successfully.", "success");
    updateStatsBar();
    applyLogFilters();
    
    // Close the threat details popup modal after resolving
    const threatModal = document.getElementById("threat-detail-modal");
    if (threatModal) {
        threatModal.classList.add("hidden");
    }
}

function addLogToTable(log, insertAtTop = false, showModal = false) {
    const tbody = document.getElementById("log-tbody");
    if (!tbody) return;
    
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", log.logId);
    
    const isHuman = log.threatType.includes("HUMAN_INTRUDER") || log.threatType.includes("CRITICAL");
    if (isHuman) {
        tr.className = "critical-row";
    }

    const threatBadge = isHuman
        ? `<span class="badge badge-error">CRITICAL</span>`
        : `<span class="badge badge-success">${escapeHtml(log.threatType)}</span>`;

    tr.innerHTML = `
        <td style="font-weight: 500;">${escapeHtml(log.timestamp)}</td>
        <td style="font-weight: 700; color: #3b82f6;">${escapeHtml(log.nodeId)}</td>
        <td>${threatBadge}</td>
        <td style="font-weight: 600;">${escapeHtml(log.confidence)}</td>
        <td style="color: #9ca3af;">${escapeHtml(log.locationName)}</td>
        <td style="font-family: monospace;">${escapeHtml(log.rssi)}</td>
        <td class="action-cell">
            <span class="action-status" style="font-size: 11px; font-weight: 700; color: ${isHuman && log.action === 'PATROL_DISPATCHED' ? '#f87171' : '#9ca3af'};">
                ${escapeHtml(log.action)}
            </span>
            ${isHuman && log.action === 'PATROL_DISPATCHED' ? `
                <button class="table-resolve-btn" title="Mark as Resolved">Resolve</button>
            ` : ''}
        </td>
    `;

    // Add click handler to the Resolve button if it exists
    const resolveBtn = tr.querySelector(".table-resolve-btn");
    if (resolveBtn) {
        resolveBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            // Select the row silently without opening the modal
            document.querySelectorAll("#log-tbody tr").forEach(r => r.classList.remove("selected-row"));
            tr.classList.add("selected-row");
            currentlySelectedLog = log;
            currentlySelectedRow = tr;
            loadAlertIntoSidebar(log, false); // Load data silently, do not show modal
            
            resolveDetection(log, tr);
        });
    }

    // Make the row clickable
    tr.addEventListener("click", () => {
        document.querySelectorAll("#log-tbody tr").forEach(r => r.classList.remove("selected-row"));
        tr.classList.add("selected-row");
        
        currentlySelectedLog = log;
        currentlySelectedRow = tr;
        
        loadAlertIntoSidebar(log, true); // Open the popup modal on explicit click
    });

    if (insertAtTop) {
        tbody.insertBefore(tr, tbody.firstChild);
        // Select the row in the table, and show/hide popup based on showModal
        document.querySelectorAll("#log-tbody tr").forEach(r => r.classList.remove("selected-row"));
        tr.classList.add("selected-row");
        currentlySelectedLog = log;
        currentlySelectedRow = tr;
        loadAlertIntoSidebar(log, showModal); // Load metadata into modal DOM and show/hide
    } else {
        tbody.appendChild(tr);
    }

    alertCount = incidentLog.length;
    applyLogFilters();
}

function saveLogsToLocalStorage() {
    localStorage.setItem("ap_incident_log_v2", JSON.stringify(incidentLog));
}

function serverRecordToLog(rec) {
    const dateObj = new Date(rec.timestamp);
    return {
        logId: rec.log_id,
        timestamp: dateObj.toISOString().split("T")[0] + " " + dateObj.toTimeString().split(" ")[0],
        nodeId: rec.node_id,
        threatType: rec.threat_type || "HUMAN_INTRUDER",
        confidence: typeof rec.confidence === "number"
            ? (rec.confidence * 100).toFixed(0) + "%"
            : (rec.confidence || "-"),
        locationName: rec.location_name || "Unknown",
        rssi: (rec.rssi !== undefined ? rec.rssi : -102) + " dBm",
        action: rec.action || "PATROL_DISPATCHED",
        imageName: rec.image_name,
        latitude: rec.latitude,
        longitude: rec.longitude,
        battery: `${rec.battery_percent}% (${rec.battery_voltage}V)`,
        fullDate: dateObj.toISOString().split("T")[0],
        isoTimestamp: rec.timestamp
    };
}

function renderIncidentLog() {
    const tbody = document.getElementById("log-tbody");
    if (tbody) tbody.innerHTML = "";

    incidentLog.forEach(log => {
        processedAlerts.add(log.logId);
        addLogToTable(log);
    });

    alertCount = incidentLog.length;

    if (incidentLog.length > 0 && tbody) {
        const firstTr = tbody.querySelector("tr");
        if (firstTr) {
            document.querySelectorAll("#log-tbody tr").forEach(r => r.classList.remove("selected-row"));
            firstTr.classList.add("selected-row");
            currentlySelectedLog = incidentLog[0];
            currentlySelectedRow = firstTr;
            loadAlertIntoSidebar(incidentLog[0], false);
        }
    }
    applyLogFilters();
}

// Incident history now comes from the server (FIX_PLAN.md B2). It used to live only in
// this browser's localStorage, so it was invisible from any other machine and gone for
// good the moment site data was cleared. localStorage is kept purely as an offline cache.
function loadIncidentHistory() {
    return fetch("/incidents")
        .then(response => {
            if (!response.ok) throw new Error("HTTP " + response.status);
            return response.json();
        })
        .then(data => {
            incidentLog = (data.incidents || []).map(serverRecordToLog).reverse();
            renderIncidentLog();
            saveLogsToLocalStorage();
            console.log(`[SYSTEM] Loaded ${incidentLog.length} incident(s) from server.`);
        })
        .catch(err => {
            console.warn("[SYSTEM] Server history unavailable, using local cache:", err);
            loadLogsFromLocalStorage();
        });
}

function loadLogsFromLocalStorage() {
    const stored = localStorage.getItem("ap_incident_log_v2");
    if (!stored) {
        incidentLog = [];
        renderIncidentLog();
        return;
    }
    try {
        incidentLog = JSON.parse(stored);
        renderIncidentLog();
        console.log("[SYSTEM] Restored incident history from local cache:", incidentLog.length);
    } catch (e) {
        console.error("[SYSTEM] Local cache unreadable, starting empty:", e);
        incidentLog = [];
        renderIncidentLog();
    }
}

function updateStatsBar() {
    // 1. Calculate stats from incidentLog array
    const total = incidentLog.length;
    const activeThreatsCount = incidentLog.filter(l => l.action === "PATROL_DISPATCHED").length;
    const resolvedThreatsCount = incidentLog.filter(l => l.action === "PATROL_RESOLVED").length;
    
    const resolveRate = total > 0 ? Math.round((resolvedThreatsCount / total) * 100) + "%" : "100%";
    
    // 2. Update Top Stats Cards HTML
    const alertsTodayEl = document.getElementById("alerts-today-count");
    if (alertsTodayEl) alertsTodayEl.textContent = total;
    
    const patrolsEl = document.getElementById("patrols-dispatched-count");
    if (patrolsEl) patrolsEl.textContent = activeThreatsCount;
    
    // 3. Update Threat Statistics Panel HTML (Anti-Poaching metrics)
    const statTotalEl = document.getElementById("stat-total-alerts");
    if (statTotalEl) statTotalEl.textContent = total;
    
    const statActiveEl = document.getElementById("stat-active-alerts");
    if (statActiveEl) statActiveEl.textContent = activeThreatsCount;
    
    const statResolvedEl = document.getElementById("stat-resolved-alerts");
    if (statResolvedEl) statResolvedEl.textContent = resolvedThreatsCount;
    
    const statRateEl = document.getElementById("stat-resolve-rate");
    if (statRateEl) statRateEl.textContent = resolveRate;
    
    // Update battery status based on latest node readings
    const batteryEl = document.getElementById("avg-battery-value");
    if (batteryEl && incidentLog.length > 0) {
        batteryEl.textContent = incidentLog[0].battery.split(" (")[1]?.replace(")", "") || "3.82 V";
    }
}

// Export logs to CSV file (Feature D)
function exportLogsToCSV() {
    if (incidentLog.length === 0) {
        showNotification("No incident logs available to export.", "warning");
        return;
    }
    
    let csvContent = "data:text/csv;charset=utf-8,";
    csvContent += "Timestamp,Trap ID,Alert Type,Confidence,Location Name,RSSI,Status,Battery\n";
    
    incidentLog.forEach(log => {
        const cleanLocName = log.locationName.replace(/,/g, " "); // remove commas
        const cleanType = log.threatType.replace(/,/g, " ");
        const row = `"${log.timestamp}","${log.nodeId}","${cleanType}","${log.confidence}","${cleanLocName}","${log.rssi}","${log.action}","${log.battery}"`;
        csvContent += row + "\n";
    });
    
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `ap_incident_history_${new Date().toISOString().slice(0,10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    showNotification("Incident history exported to CSV successfully!", "success");
}

// =============================================================================
// 4.6 SORTABLE TABLE HEADERS (UI Improvement)
// =============================================================================
function sortTable(column) {
    if (currentSortColumn === column) {
        currentSortDirection = currentSortDirection === "asc" ? "desc" : "asc";
    } else {
        currentSortColumn = column;
        currentSortDirection = "asc";
    }
    
    // Update header arrows
    document.querySelectorAll(".sortable-header").forEach(th => {
        const colName = th.getAttribute("data-sort");
        const arrow = th.querySelector(".sort-arrow");
        if (colName === column) {
            arrow.textContent = currentSortDirection === "asc" ? " ▲" : " ▼";
        } else {
            arrow.textContent = "";
        }
    });
    
    // Perform sort on global log array
    incidentLog.sort((a, b) => {
        let valA = a[column] || "";
        let valB = b[column] || "";
        
        // Handle numeric conversion for confidence
        if (column === "confidence") {
            valA = parseInt(valA.replace("%", "")) || 0;
            valB = parseInt(valB.replace("%", "")) || 0;
        }
        
        if (valA < valB) return currentSortDirection === "asc" ? -1 : 1;
        if (valA > valB) return currentSortDirection === "asc" ? 1 : -1;
        return 0;
    });
    
    // Repopulate table
    const tbody = document.getElementById("log-tbody");
    if (tbody) {
        tbody.innerHTML = "";
        incidentLog.forEach(log => addLogToTable(log));
    }
}

function initSortableHeaders() {
    document.querySelectorAll(".sortable-header").forEach(th => {
        th.addEventListener("click", () => {
            const col = th.getAttribute("data-sort");
            sortTable(col);
        });
    });
}

// =============================================================================
// 4.7 INCIDENT LOG FILTERS (TABS & TEXT SEARCH)
// =============================================================================
function applyLogFilters() {
    const rows = document.querySelectorAll("#log-tbody tr");
    let visibleCount = 0;
    
    rows.forEach(tr => {
        const logId = tr.getAttribute("data-id");
        const log = incidentLog.find(l => l.logId === logId);
        if (!log) return;
        
        const timestamp = log.timestamp.toLowerCase();
        const trapId = log.nodeId.toLowerCase();
        const alertType = log.threatType.toLowerCase();
        const locationName = log.locationName.toLowerCase();
        const action = log.action.toLowerCase();
        
        // 1. Evaluate Tab Filter
        let matchesTab = false;
        if (activeLogFilter === "all") {
            matchesTab = true;
        } else if (activeLogFilter === "active") {
            matchesTab = action.includes("patrol_dispatched");
        } else if (activeLogFilter === "resolved") {
            matchesTab = action.includes("patrol_resolved");
        }
        
        // 2. Evaluate Search Query
        let matchesSearch = false;
        if (logSearchQuery === "") {
            matchesSearch = true;
        } else {
            matchesSearch = trapId.includes(logSearchQuery) || 
                            locationName.includes(logSearchQuery) || 
                            alertType.includes(logSearchQuery) ||
                            timestamp.includes(logSearchQuery) ||
                            action.includes(logSearchQuery);
        }
        
        // Combine filters
        if (matchesTab && matchesSearch) {
            tr.style.display = "";
            visibleCount++;
        } else {
            tr.style.display = "none";
        }
    });
    
    // Update count badge to reflect filtered count
    const badge = document.getElementById("log-count");
    if (badge) {
        if (activeLogFilter === "all" && logSearchQuery === "") {
            badge.textContent = `${alertCount} Logs`;
        } else {
            badge.textContent = `${visibleCount} of ${alertCount} Logs`;
        }
    }
}

function initFilterController() {
    // 1. Tab buttons click handlers
    const tabs = document.querySelectorAll(".log-tab");
    tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            tabs.forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            activeLogFilter = tab.getAttribute("data-filter");
            applyLogFilters();
        });
    });
    
    // 2. Search input handler
    const searchInput = document.getElementById("log-search-input");
    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            logSearchQuery = e.target.value.toLowerCase().trim();
            applyLogFilters();
        });
    }
}

function initLogCollapseController() {
    const logHeader = document.getElementById("log-panel-header");
    const logPanel = document.querySelector(".log-panel");
    const collapseIcon = document.getElementById("log-collapse-icon");
    
    if (logHeader && logPanel) {
        // If it is collapsed by default on load, rotate the icon initially
        if (logPanel.classList.contains("collapsed") && collapseIcon) {
            collapseIcon.style.transform = "rotate(-90deg)";
        }
        
        logHeader.addEventListener("click", () => {
            const isCollapsed = logPanel.classList.toggle("collapsed");
            if (collapseIcon) {
                collapseIcon.style.transform = isCollapsed ? "rotate(-90deg)" : "rotate(0deg)";
            }
            setTimeout(() => {
                map.invalidateSize();
            }, 310);
        });
    }
}

// =============================================================================
// 5. SETTINGS DRAWER CONTROLLER
// =============================================================================
function initSettingsController() {
    const drawer = document.getElementById("settings-drawer");
    const btnOpen = document.getElementById("btn-settings");
    const btnOpenNav = document.getElementById("btn-settings-nav");
    const btnClose = document.getElementById("btn-close-settings");
    const btnSave = document.getElementById("btn-save-settings");
    const selectNode = document.getElementById("settings-node-select");

    // Open Drawer from top button
    if (btnOpen) {
        btnOpen.addEventListener("click", () => {
            drawer.classList.add("active");
            populateSettingsForm(selectNode.value);
        });
    }

    // Open Drawer from sidebar link
    if (btnOpenNav) {
        btnOpenNav.addEventListener("click", (e) => {
            e.preventDefault();
            drawer.classList.add("active");
            populateSettingsForm(selectNode.value);
        });
    }

    // Close Drawer
    if (btnClose) {
        btnClose.addEventListener("click", () => {
            drawer.classList.remove("active");
        });
    }

    // Dropdown change
    if (selectNode) {
        selectNode.addEventListener("change", (e) => {
            populateSettingsForm(e.target.value);
        });
    }

    // Dynamic field listeners
    const inputs = ["settings-node-name", "settings-node-lat", "settings-node-lng", "settings-node-desc"];
    inputs.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener("input", () => {
                updateLocalNodeFromForm();
            });
        }
    });

    // Save Configuration
    if (btnSave) {
        btnSave.addEventListener("click", () => {
            saveConfigurationToServer();
        });
    }
}

function populateSettingsForm(nodeId) {
    const info = CAMERA_NODES[nodeId];
    if (!info) return;

    document.getElementById("settings-node-name").value = info.name || "";
    document.getElementById("settings-node-lat").value = info.latitude || "";
    document.getElementById("settings-node-lng").value = info.longitude || "";
    document.getElementById("settings-node-desc").value = info.description || "";
}

function updateLocalNodeFromForm() {
    const nodeId = document.getElementById("settings-node-select").value;
    if (!CAMERA_NODES[nodeId]) return;

    const lat = parseFloat(document.getElementById("settings-node-lat").value) || 0;
    const lng = parseFloat(document.getElementById("settings-node-lng").value) || 0;
    const name = document.getElementById("settings-node-name").value;
    const desc = document.getElementById("settings-node-desc").value;

    // Update state
    CAMERA_NODES[nodeId].latitude = lat;
    CAMERA_NODES[nodeId].longitude = lng;
    CAMERA_NODES[nodeId].name = name;
    CAMERA_NODES[nodeId].description = desc;

    // Update marker on the map
    if (nodeMarkers[nodeId]) {
        nodeMarkers[nodeId].setLatLng([lat, lng]);
        
        const distanceMeters = map.distance([lat, lng], CENTER_STATION_COORDS);
        const distanceKm = (distanceMeters / 1000).toFixed(2);
        
        nodeMarkers[nodeId].setPopupContent(getPopupContent(nodeId, CAMERA_NODES[nodeId], distanceKm));
        
        if (connectingLines[nodeId]) {
            connectingLines[nodeId].setLatLngs([[lat, lng], CENTER_STATION_COORDS]);
            connectingLines[nodeId].bindTooltip(`Link: ${nodeId} to Center Station (${distanceKm} km)`, {
                permanent: false,
                direction: 'center',
                className: 'link-tooltip'
            });
        }
    }
}

function saveConfigurationToServer() {
    console.log("[SETTINGS] Saving configuration to server:", CAMERA_NODES);

    fetch("/sensor-config", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(CAMERA_NODES)
    })
    .then(response => {
        if (response.ok) {
            return response.json();
        }
        throw new Error("Failed to save camera configuration");
    })
    .then(data => {
        showNotification("Camera coordinates and names saved successfully!", "success");
        updateStatsBar();
    })
    .catch(err => {
        console.error(err);
        showNotification("Failed to save camera configuration.", "error");
    });
}

// Notification Banner Manager
function showNotification(message, type = "success") {
    const banner = document.getElementById("notification-banner");
    const msgEl = document.getElementById("notification-message");
    if (!banner || !msgEl) return;
    
    msgEl.textContent = message;
    
    if (type === "success") {
        banner.style.backgroundColor = "rgba(16, 185, 129, 0.95)";
    } else if (type === "warning") {
        banner.style.backgroundColor = "rgba(245, 158, 11, 0.95)";
    } else if (type === "error") {
        banner.style.backgroundColor = "rgba(239, 68, 68, 0.95)";
    }
    
    banner.classList.remove("hidden");
    
    setTimeout(() => {
        banner.classList.add("hidden");
    }, 3500);
}

// =============================================================================
// 6. SERVER CONNECTION & INITIALIZATION
// =============================================================================
function loadSensorConfig() {
    return fetch("/sensor-config")
        .then(response => {
            if (response.ok) {
                return response.json();
            }
            throw new Error("Failed to load camera config");
        })
        .then(data => {
            CAMERA_NODES = data;
            console.log("[SYSTEM] Loaded camera config successfully:", CAMERA_NODES);
            plotMarkers();
        })
        .catch(err => {
            console.error("[SYSTEM] Error loading camera configuration:", err);
            showNotification("Failed to load camera locations from server.", "error");
        });
}

function updateNodeOnlineState(nodeId, isOnline) {
    // If the node item doesn't exist in the list yet, let's re-render the list!
    if (!document.getElementById(`status-item-${nodeId}`)) {
        renderCameraStatusList();
    }

    // Update the floating panel badge status
    // CSS.escape guards against a node ID containing selector-breaking characters
    // (e.g. a space or quote from a maliciously edited sensor_config.json) throwing
    // an exception here and aborting the status update for every other camera trap.
    const badge = document.querySelector(`#status-item-${CSS.escape(nodeId)} .camera-status-badge`);
    if (badge) {
        if (activeThreatNodes.has(nodeId)) {
            badge.textContent = "THREAT ACTIVE";
            badge.className = "camera-status-badge badge-error";
        } else if (isOnline) {
            badge.textContent = "ACTIVATED";
            badge.className = "camera-status-badge badge-online";
        } else {
            badge.textContent = "NOT ACTIVATED";
            badge.className = "camera-status-badge badge-offline";
        }
    }
    
    // Update the map marker icon based on active threat status and online status
    if (nodeMarkers[nodeId]) {
        if (activeThreatNodes.has(nodeId)) {
            nodeMarkers[nodeId].setIcon(activeTrapIcon);
        } else if (isOnline) {
            nodeMarkers[nodeId].setIcon(activatedTrapIcon);
        } else {
            nodeMarkers[nodeId].setIcon(inactiveTrapIcon);
        }
    }
}

// Periodically check for heartbeat timeouts (every 2 seconds)
setInterval(() => {
    const now = Date.now();
    for (const nodeId of Object.keys(CAMERA_NODES)) {
        const lastHb = lastHeartbeats[nodeId] || 0;
        const isOnline = (now - lastHb) < HEARTBEAT_TIMEOUT_MS;
        updateNodeOnlineState(nodeId, isOnline);
    }
}, 2000);

function connectToSSE() {
    updateConnectionBadge("reconnecting");
    const eventSource = new EventSource("/events");

    eventSource.onopen = function() {
        console.log("[SSE] Connection established successfully.");
        updateConnectionBadge("connected");
    };

    eventSource.onmessage = function(event) {
        try {
            const alertData = JSON.parse(event.data);

            if (alertData.event_type === "HEARTBEAT") {
                const hbNode = alertData.node_id;
                lastHeartbeats[hbNode] = Date.now();
                updateNodeOnlineState(hbNode, true);
                return;
            }
            
            if (alertData.event_type === "INCIDENT_RESOLVED") {
                // Another operator resolved this one; mirror it without re-POSTing
                const log = incidentLog.find(l => l.logId === alertData.log_id);
                if (log && log.action !== "PATROL_RESOLVED") {
                    log.action = "PATROL_RESOLVED";
                    const row = document.querySelector(`#log-tbody tr[data-id="${alertData.log_id}"]`);
                    if (row) {
                        const cell = row.querySelector(".action-status");
                        if (cell) { cell.textContent = "PATROL_RESOLVED"; cell.style.color = "#9ca3af"; }
                        const btn = row.querySelector(".table-resolve-btn");
                        if (btn) btn.remove();
                    }
                    saveLogsToLocalStorage();
                    updateStatsBar();
                    applyLogFilters();
                }
                return;
            }

            if (alertData.event_type === "CONFIG_UPDATE") {
                console.log("[SSE] Configuration update received from server:", alertData.sensors);
                CAMERA_NODES = alertData.sensors;
                plotMarkers();
                
                const drawer = document.getElementById("settings-drawer");
                if (drawer.classList.contains("active")) {
                    const selectedNode = document.getElementById("settings-node-select").value;
                    populateSettingsForm(selectedNode);
                }
                showNotification("Map coordinates synchronized with server.", "success");
                return;
            }
            
            console.log("[SSE] New real-time alert received:", alertData);
            handleNewAlert(alertData, true);
        } catch (e) {
            console.error("[SSE] Failed to parse event data:", e);
        }
    };

    eventSource.onerror = function(err) {
        console.error("[SSE] Connection error occurred. Retrying in 5 seconds...");
        updateConnectionBadge("disconnected");
        eventSource.close();
        setTimeout(connectToSSE, 5000);
    };
}

function initAcknowledgeController() {
    const btnAck = document.getElementById("btn-acknowledge");
    if (btnAck) {
        btnAck.addEventListener("click", () => {
            if (currentlySelectedLog && currentlySelectedLog.action === "PATROL_DISPATCHED") {
                resolveDetection(currentlySelectedLog, currentlySelectedRow);
            }
        });
    }
}

function openCameraTrapsModal() {
    // Navigate directly to the Camera Traps SPA view by simulating sidebar click
    const navTraps = document.getElementById("nav-traps");
    if (navTraps) navTraps.click();
}

function renderCameraTrapsModal() {
    const listContainer = document.getElementById("modal-traps-list");
    if (!listContainer) return;
    
    listContainer.innerHTML = "";
    const now = Date.now();
    
    // Sort keys so they are listed in order
    const sortedKeys = Object.keys(CAMERA_NODES).sort();
    
    for (const nodeId of sortedKeys) {
        const info = CAMERA_NODES[nodeId];
        const lastHb = lastHeartbeats[nodeId] || 0;
        const isOnline = (now - lastHb) < HEARTBEAT_TIMEOUT_MS;
        const isThreat = activeThreatNodes.has(nodeId);
        
        let statusText = "OFFLINE";
        let statusClass = "badge-offline";
        if (isThreat) {
            statusText = "THREAT ACTIVE";
            statusClass = "badge-error";
        } else if (isOnline) {
            statusText = "STANDBY";
            statusClass = "badge-online";
        }
        
        const distanceMeters = map.distance([info.latitude, info.longitude], CENTER_STATION_COORDS);
        const distanceKm = (distanceMeters / 1000).toFixed(2);
        
        const card = document.createElement("div");
        card.className = "modal-trap-card";
        card.innerHTML = `
            <div class="trap-card-header">
                <span class="trap-card-title">${escapeHtml(nodeId)}</span>
                <span class="badge ${statusClass}">${statusText}</span>
            </div>
            <div class="trap-card-location" style="font-size: 0.8rem; font-weight: 700; color: #f3f4f6; margin-top: 2px;">${escapeHtml(info.name)}</div>
            <div class="trap-card-desc" style="font-size: 0.72rem; color: #9ca3af; line-height: 1.3; margin-top: 4px;">${escapeHtml(info.description)}</div>
            <div class="trap-card-grid" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 8px;">
                <div class="trap-card-stat" style="background-color: rgba(8, 12, 20, 0.4); border: 1px solid rgba(255, 255, 255, 0.03); border-radius: 4px; padding: 6px; display: flex; flex-direction: column;">
                    <span class="trap-card-stat-label" style="font-size: 0.55rem; color: #6b7280; text-transform: uppercase; font-weight: 700;">RSSI</span>
                    <span class="trap-card-stat-val" style="font-size: 0.72rem; font-weight: 600; color: #a855f7; margin-top: 2px;">${isThreat ? '-102' : (isOnline ? '-85' : 'N/A')} dBm</span>
                </div>
                <div class="trap-card-stat" style="background-color: rgba(8, 12, 20, 0.4); border: 1px solid rgba(255, 255, 255, 0.03); border-radius: 4px; padding: 6px; display: flex; flex-direction: column;">
                    <span class="trap-card-stat-label" style="font-size: 0.55rem; color: #6b7280; text-transform: uppercase; font-weight: 700;">Battery</span>
                    <span class="trap-card-stat-val" style="font-size: 0.72rem; font-weight: 600; color: #eab308; margin-top: 2px;">${isOnline ? '82% (3.82V)' : 'N/A'}</span>
                </div>
                <div class="trap-card-stat" style="background-color: rgba(8, 12, 20, 0.4); border: 1px solid rgba(255, 255, 255, 0.03); border-radius: 4px; padding: 6px; display: flex; flex-direction: column;">
                    <span class="trap-card-stat-label" style="font-size: 0.55rem; color: #6b7280; text-transform: uppercase; font-weight: 700;">Distance</span>
                    <span class="trap-card-stat-val" style="font-size: 0.72rem; font-weight: 600; color: #3b82f6; margin-top: 2px;">${distanceKm} km</span>
                </div>
            </div>
            <div class="trap-card-actions" style="display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px; flex-wrap: wrap;">
                <button class="modal-action-btn view-on-map-btn" data-id="${escapeHtml(nodeId)}" style="padding: 4px 8px; font-size: 0.68rem;">🗺️ View</button>
                <button class="modal-action-btn config-btn" data-id="${escapeHtml(nodeId)}" style="padding: 4px 8px; font-size: 0.68rem;">⚙️ Config</button>
            </div>
        `;
        
        // Bind Actions
        card.querySelector(".view-on-map-btn").addEventListener("click", () => {
            closeCameraTrapsModal();
            
            if (nodeMarkers[nodeId] && map) {
                map.setView([info.latitude, info.longitude], 14);
                nodeMarkers[nodeId].openPopup();
                showNotification(`Centered map on ${nodeId}.`, "success");
            }
        });
        
        card.querySelector(".config-btn").addEventListener("click", () => {
            // Show config view inside the modal
            document.getElementById("config-mode").value = "edit";
            document.getElementById("config-modal-title").textContent = `CONFIGURE CAMERA TRAP: ${nodeId}`;
            
            const idInput = document.getElementById("config-node-id");
            idInput.value = nodeId;
            idInput.disabled = true; // Read-only in edit mode
            
            document.getElementById("config-node-name").value = info.name || "";
            document.getElementById("config-node-lat").value = info.latitude || "";
            document.getElementById("config-node-lng").value = info.longitude || "";
            document.getElementById("config-node-desc").value = info.description || "";
            
            document.getElementById("modal-view-registry").classList.add("hidden");
            document.getElementById("modal-view-config").classList.remove("hidden");
        });

        listContainer.appendChild(card);
    }
}

function closeCameraTrapsModal() {
    // Return to Map View by simulating sidebar click
    const navDashboard = document.getElementById("nav-dashboard");
    if (navDashboard) navDashboard.click();
}

function triggerSimulation(nodeId, threatType) {
    showNotification(`Simulating camera transmission for ${nodeId}...`, "warning");
    closeCameraTrapsModal();
    
    // Center map on the target sensor and open its popup to prepare for the alert
    const info = CAMERA_NODES[nodeId];
    if (info && map && nodeMarkers[nodeId]) {
        map.setView([info.latitude, info.longitude], 14);
        nodeMarkers[nodeId].openPopup();
    }
    
    fetch("/simulate-alert", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            node_id: nodeId,
            threat_type: threatType
        })
    })
    .then(response => {
        if (response.ok) {
            return response.json();
        }
        throw new Error("Failed to trigger simulation");
    })
    .then(data => {
        showNotification(`Simulation event triggered on ${nodeId}!`, "success");
    })
    .catch(err => {
        console.error(err);
        showNotification("Failed to trigger simulated alert.", "error");
    });
}

function getNextCameraTrapId() {
    let maxNum = 0;
    for (const key of Object.keys(CAMERA_NODES)) {
        const match = key.match(/Camera-Trap-(\d+)/i);
        if (match) {
            const num = parseInt(match[1], 10);
            if (num > maxNum) {
                maxNum = num;
            }
        }
    }
    const nextNum = maxNum + 1;
    return `Camera-Trap-${nextNum.toString().padStart(2, '0')}`;
}

function saveModalConfiguration() {
    const mode = document.getElementById("config-mode").value;
    const nodeId = document.getElementById("config-node-id").value.trim();
    const name = document.getElementById("config-node-name").value.trim();
    const latVal = document.getElementById("config-node-lat").value.trim();
    const lngVal = document.getElementById("config-node-lng").value.trim();
    const desc = document.getElementById("config-node-desc").value.trim();
    
    if (!nodeId) {
        showNotification("Camera Trap ID is required.", "error");
        return;
    }
    
    if (mode === "add" && CAMERA_NODES[nodeId]) {
        showNotification(`A camera with ID "${nodeId}" already exists.`, "error");
        return;
    }
    
    const lat = parseFloat(latVal);
    const lng = parseFloat(lngVal);
    
    if (isNaN(lat) || isNaN(lng)) {
        showNotification("Latitude and Longitude must be valid numbers.", "error");
        return;
    }
    
    // Add/Update in local state
    if (!CAMERA_NODES[nodeId]) {
        CAMERA_NODES[nodeId] = {};
    }
    CAMERA_NODES[nodeId].name = name || nodeId;
    CAMERA_NODES[nodeId].latitude = lat;
    CAMERA_NODES[nodeId].longitude = lng;
    CAMERA_NODES[nodeId].description = desc || "";
    
    console.log("[SETTINGS] Saving modal configuration to server:", CAMERA_NODES);
    
    fetch("/sensor-config", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(CAMERA_NODES)
    })
    .then(response => {
        if (response.ok) {
            return response.json();
        }
        throw new Error("Failed to save camera configuration");
    })
    .then(data => {
        showNotification(`Camera "${nodeId}" saved successfully!`, "success");
        // Return to registry view
        document.getElementById("modal-view-config").classList.add("hidden");
        document.getElementById("modal-view-registry").classList.remove("hidden");
        
        // Refresh registry list
        renderCameraTrapsModal();
    })
    .catch(err => {
        console.error(err);
        showNotification("Failed to save camera configuration.", "error");
    });
}

function initCameraTrapsModalController() {
    const modal = document.getElementById("camera-traps-modal");
    
    // Close button on registry view
    const btnCloseRegistry = document.getElementById("btn-close-traps-modal");
    if (btnCloseRegistry) {
        btnCloseRegistry.addEventListener("click", () => {
            closeCameraTrapsModal();
        });
    }
    
    // Close button on config view
    const btnCloseConfig = document.getElementById("btn-close-config-modal");
    if (btnCloseConfig) {
        btnCloseConfig.addEventListener("click", () => {
            closeCameraTrapsModal();
        });
    }
    
    // Backdrop click close
    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) {
                closeCameraTrapsModal();
            }
        });
    }
    
    // Back button in config form
    const btnBack = document.getElementById("btn-back-to-registry");
    if (btnBack) {
        btnBack.addEventListener("click", () => {
            document.getElementById("modal-view-config").classList.add("hidden");
            document.getElementById("modal-view-registry").classList.remove("hidden");
            renderCameraTrapsModal();
        });
    }
    
    // Add Camera button in registry view
    const btnAddSensor = document.getElementById("btn-add-sensor");
    if (btnAddSensor) {
        btnAddSensor.addEventListener("click", () => {
            document.getElementById("config-mode").value = "add";
            document.getElementById("config-modal-title").textContent = "ADD NEW CAMERA TRAP";
            
            const idInput = document.getElementById("config-node-id");
            idInput.value = getNextCameraTrapId();
            idInput.disabled = false; // Enabled in add mode!
            
            document.getElementById("config-node-name").value = "";
            document.getElementById("config-node-lat").value = "";
            document.getElementById("config-node-lng").value = "";
            document.getElementById("config-node-desc").value = "";
            
            document.getElementById("modal-view-registry").classList.add("hidden");
            document.getElementById("modal-view-config").classList.remove("hidden");
        });
    }
    
    // Save button in config form
    const btnSaveModalConfig = document.getElementById("btn-save-modal-config");
    if (btnSaveModalConfig) {
        btnSaveModalConfig.addEventListener("click", () => {
            saveModalConfiguration();
        });
    }
}

function initThreatDetailModalController() {
    const modal = document.getElementById("threat-detail-modal");
    const btnClose = document.getElementById("btn-close-threat-modal");
    
    if (btnClose) {
        btnClose.addEventListener("click", () => {
            if (modal) modal.classList.add("hidden");
        });
    }
    
    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) {
                modal.classList.add("hidden");
            }
        });
    }
}

// =============================================================================
// INCIDENT HISTORY DASHBOARD & CALENDAR ENGINE
// =============================================================================
function initIncidentHistoryDashboard() {
    const btnBack = document.getElementById("btn-back-to-dashboard");
    const btnPrev = document.getElementById("btn-prev-month");
    const btnNext = document.getElementById("btn-next-month");
    const btnClearFilter = document.getElementById("btn-clear-date-filter");

    if (btnBack) {
        btnBack.addEventListener("click", (e) => {
            e.preventDefault();
            const navDashboard = document.getElementById("nav-dashboard");
            if (navDashboard) navDashboard.click();
        });
    }

    if (btnPrev) {
        btnPrev.addEventListener("click", () => {
            currentCalMonth--;
            if (currentCalMonth < 0) {
                currentCalMonth = 11;
                currentCalYear--;
            }
            renderIncidentCalendar(currentCalYear, currentCalMonth);
        });
    }

    if (btnNext) {
        btnNext.addEventListener("click", () => {
            currentCalMonth++;
            if (currentCalMonth > 11) {
                currentCalMonth = 0;
                currentCalYear++;
            }
            renderIncidentCalendar(currentCalYear, currentCalMonth);
        });
    }

    if (btnClearFilter) {
        btnClearFilter.addEventListener("click", () => {
            selectedCalDateStr = null;
            document.querySelectorAll(".calendar-day").forEach(d => d.classList.remove("selected"));
            renderAllIncidentHistory();
        });
    }
}

function updateIncidentDashboardStats() {
    const totalEl = document.getElementById("inc-stat-total");
    const criticalEl = document.getElementById("inc-stat-critical");
    const wildlifeEl = document.getElementById("inc-stat-wildlife");
    const rateEl = document.getElementById("inc-stat-rate");

    if (!totalEl) return;

    const total = incidentLog.length;
    const critical = incidentLog.filter(l => l.threatType.includes("HUMAN_INTRUDER") || l.threatType.includes("CRITICAL")).length;
    const wildlife = total - critical;
    
    const resolvedCount = incidentLog.filter(l => l.action === "PATROL_RESOLVED").length;
    const rate = total > 0 ? Math.round((resolvedCount / total) * 100) + "%" : "100%";

    totalEl.textContent = total;
    criticalEl.textContent = critical;
    wildlifeEl.textContent = wildlife;
    rateEl.textContent = rate;
}

function renderIncidentCalendar(year, month) {
    const months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ];

    const monthYearText = document.getElementById("calendar-month-year");
    if (monthYearText) {
        monthYearText.textContent = `${months[month]} ${year}`;
    }

    const container = document.getElementById("calendar-days-container");
    if (!container) return;
    container.innerHTML = "";

    // Calculation of calendar days grid
    const firstDayIndex = new Date(year, month, 1).getDay();
    const totalDays = new Date(year, month + 1, 0).getDate();
    const prevTotalDays = new Date(year, month, 0).getDate();

    // 1. Offset days of the previous month
    for (let i = firstDayIndex; i > 0; i--) {
        const dayDiv = document.createElement("div");
        dayDiv.className = "calendar-day day-empty";
        dayDiv.innerHTML = `<span class="calendar-day-num">${prevTotalDays - i + 1}</span>`;
        container.appendChild(dayDiv);
    }

    // 2. Days of the current month
    for (let day = 1; day <= totalDays; day++) {
        const dayDiv = document.createElement("div");
        dayDiv.className = "calendar-day";
        
        // Formatting date string as YYYY-MM-DD
        const formattedMonth = String(month + 1).padStart(2, '0');
        const formattedDay = String(day).padStart(2, '0');
        const dateStr = `${year}-${formattedMonth}-${formattedDay}`;

        if (selectedCalDateStr === dateStr) {
            dayDiv.classList.add("selected");
        }

        // Count threats/incidents on this date
        const dailyIncidents = incidentLog.filter(l => {
            if (l.fullDate) return l.fullDate === dateStr;
            // Fallback parsing date from timestamp/iso if fullDate is missing
            if (l.isoTimestamp) return l.isoTimestamp.startsWith(dateStr);
            return false;
        });

        const dayNumSpan = `<span class="calendar-day-num">${day}</span>`;

        if (dailyIncidents.length > 0) {
            dayDiv.classList.add("day-incident");
            
            // Critical vs Wildlife classification
            const criticalCount = dailyIncidents.filter(l => l.threatType.includes("HUMAN_INTRUDER") || l.threatType.includes("CRITICAL")).length;
            const pluralSuffix = dailyIncidents.length > 1 ? "s" : "";
            
            let statusHtml = "";
            if (criticalCount > 0) {
                statusHtml = `<span class="calendar-day-status-incident">🚨 ${dailyIncidents.length} Case${pluralSuffix}</span>`;
                // Add flashing pulse dot for active threats
                const pulseDot = document.createElement("div");
                pulseDot.className = "incident-pulse-dot";
                dayDiv.appendChild(pulseDot);
            } else {
                statusHtml = `<span class="calendar-day-status-incident" style="color: #3b82f6; text-shadow: 0 0 6px rgba(59, 130, 246, 0.3);">🐾 ${dailyIncidents.length} Case${pluralSuffix}</span>`;
            }
            
            dayDiv.innerHTML = `${dayNumSpan}${statusHtml}`;
        } else {
            dayDiv.classList.add("day-normal");
            dayDiv.innerHTML = `${dayNumSpan}<span class="calendar-day-status-normal">✓ Normal</span>`;
        }

        dayDiv.addEventListener("click", () => {
            document.querySelectorAll(".calendar-day").forEach(d => d.classList.remove("selected"));
            dayDiv.classList.add("selected");
            selectedCalDateStr = dateStr;
            filterIncidentHistoryByDate(dateStr);
        });

        container.appendChild(dayDiv);
    }
}

function renderIncidentHistoryTable(logs) {
    const tbody = document.getElementById("inc-history-tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    if (logs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--color-text-muted); padding: 20px;">No incidents recorded for this period.</td></tr>`;
        return;
    }

    logs.forEach(log => {
        const tr = document.createElement("tr");
        
        const isHuman = log.threatType.includes("HUMAN_INTRUDER") || log.threatType.includes("CRITICAL");
        if (isHuman) {
            tr.className = "critical-row";
        }

        const threatBadge = isHuman
            ? `<span class="badge badge-error">CRITICAL</span>`
            : `<span class="badge badge-success">${escapeHtml(log.threatType)}</span>`;

        tr.innerHTML = `
            <td style="font-weight: 500;">${escapeHtml(log.timestamp)}</td>
            <td style="font-weight: 700; color: #3b82f6;">${escapeHtml(log.nodeId)}</td>
            <td>${threatBadge}</td>
            <td style="font-weight: 600;">${escapeHtml(log.confidence)}</td>
            <td style="color: #9ca3af;">${escapeHtml(log.locationName)}</td>
            <td style="font-weight: 700; font-size: 11px; color: ${log.action === 'PATROL_RESOLVED' ? '#9ca3af' : '#f87171'};">${escapeHtml(log.action)}</td>
        `;
        
        tbody.appendChild(tr);
    });
}

function filterIncidentHistoryByDate(dateStr) {
    const filtered = incidentLog.filter(l => {
        if (l.fullDate) return l.fullDate === dateStr;
        if (l.isoTimestamp) return l.isoTimestamp.startsWith(dateStr);
        return false;
    });

    const parsedDate = new Date(dateStr);
    const dateOptions = { year: 'numeric', month: 'long', day: 'numeric' };
    const dateFormatted = parsedDate.toLocaleDateString('en-US', dateOptions);

    document.getElementById("selected-date-title").textContent = `Incidents on ${dateFormatted}`;
    document.getElementById("btn-clear-date-filter").classList.remove("hidden");

    renderIncidentHistoryTable(filtered);
}

function renderAllIncidentHistory() {
    document.getElementById("selected-date-title").textContent = "All Historical Incidents";
    document.getElementById("btn-clear-date-filter").classList.add("hidden");
    
    // Sort all logs descending by timestamp
    const sortedLogs = [...incidentLog].reverse();
    renderIncidentHistoryTable(sortedLogs);
}

function initNavigationController() {
    const navDashboard = document.getElementById("nav-dashboard");
    const navTraps = document.getElementById("nav-traps");
    const navLogs = document.getElementById("nav-logs");
    const navStats = document.getElementById("nav-stats");
    const navSettings = document.getElementById("btn-settings-nav");
    
    const navItems = [navDashboard, navTraps, navLogs, navStats, navSettings];
    
    function setActiveNavItem(activeItem) {
        navItems.forEach(item => {
            if (item) item.classList.remove("active");
        });
        if (activeItem) activeItem.classList.add("active");
    }
    
    if (navDashboard) {
        navDashboard.addEventListener("click", (e) => {
            e.preventDefault();
            setActiveNavItem(navDashboard);
            
            // Show dashboard and hide other views
            document.getElementById("dashboard-view").classList.remove("hidden");
            document.getElementById("incident-view").classList.add("hidden");
            document.getElementById("camera-traps-view").classList.add("hidden");
            
            // Recalculate map boundaries/size
            if (map) {
                setTimeout(() => { map.invalidateSize(); }, 50);
            }
            
            window.scrollTo({ top: 0, behavior: "smooth" });
            showNotification("Viewing primary operational Map.", "success");
        });
    }
    
    if (navTraps) {
        navTraps.addEventListener("click", (e) => {
            e.preventDefault();
            setActiveNavItem(navTraps);
            
            // Hide other views and show Camera Traps view
            document.getElementById("dashboard-view").classList.add("hidden");
            document.getElementById("incident-view").classList.add("hidden");
            document.getElementById("camera-traps-view").classList.remove("hidden");
            
            // Show registry view and hide config form view inside Camera Traps view by default
            document.getElementById("modal-view-config").classList.add("hidden");
            document.getElementById("modal-view-registry").classList.remove("hidden");
            
            // Populate registry list dynamically
            renderCameraTrapsModal();
            
            window.scrollTo({ top: 0, behavior: "smooth" });
            showNotification("Viewing Camera Trap management and registry.", "success");
        });
    }
    
    if (navLogs) {
        navLogs.addEventListener("click", (e) => {
            e.preventDefault();
            setActiveNavItem(navLogs);
            
            // Hide other views and show incident history view
            document.getElementById("dashboard-view").classList.add("hidden");
            document.getElementById("incident-view").classList.remove("hidden");
            document.getElementById("camera-traps-view").classList.add("hidden");
            
            // Render the calendar and load logs
            updateIncidentDashboardStats();
            renderIncidentCalendar(currentCalYear, currentCalMonth);
            renderAllIncidentHistory();
            
            window.scrollTo({ top: 0, behavior: "smooth" });
            showNotification("Viewing historical incident logs and operational calendar.", "success");
        });
    }
    
    if (navStats) {
        navStats.addEventListener("click", (e) => {
            e.preventDefault();
            setActiveNavItem(navStats);
            
            // Highlight and scroll to threat statistics widget in sidebar
            const statsWidget = document.querySelector(".threat-stats-panel-new");
            if (statsWidget) {
                statsWidget.scrollIntoView({ behavior: "smooth", block: "center" });
                statsWidget.style.outline = "2px solid var(--color-purple)";
                statsWidget.style.borderRadius = "8px";
                setTimeout(() => {
                    statsWidget.style.outline = "none";
                }, 1200);
            }
            showNotification("Highlighted threat summary statistics.", "success");
        });
    }
}

// On Page Load Initialization
window.addEventListener('DOMContentLoaded', () => {
    // 1. Initialize empty map
    initMap();
    
    // 2. Load sensor configuration from backend, then plot markers
    loadSensorConfig().then(() => {
        // 3. Incident history comes from the server, with localStorage as offline fallback
        loadIncidentHistory().then(() => {
            updateStatsBar();
        });
    });

    // 5. Connect to Real-time event stream
    connectToSSE();
    
    // 6. Bind UI settings drawer controllers
    initSettingsController();
    
    // 7. Initialize administrator acknowledgment controller
    initAcknowledgeController();

    // 7b. Delegated handler for the "VIEW ALERT DETAILS" button inside map popups
    initPopupThreatButtonController();

    // 8. Fetch active/online cameras to set initial LED states
    fetch("/active-cameras")
        .then(response => response.ok ? response.json() : { active_nodes: [] })
        .then(data => {
            if (data.active_nodes) {
                for (const nodeId of data.active_nodes) {
                    lastHeartbeats[nodeId] = Date.now();
                    updateNodeOnlineState(nodeId, true);
                }
            }
        })
        .catch(err => console.log("[SYSTEM] Error fetching active cameras:", err));

    // 9. Bind Camera Status Panel Toggle Controllers
    const statusPanel = document.getElementById("camera-status-panel");
    const btnHideStatus = document.getElementById("btn-hide-status");
    const btnShowStatus = document.getElementById("btn-show-status");
    
    if (btnHideStatus && btnShowStatus) {
        btnHideStatus.addEventListener("click", () => {
            statusPanel.classList.add("hidden");
            btnShowStatus.classList.remove("hidden");
        });
        
        btnShowStatus.addEventListener("click", () => {
            statusPanel.classList.remove("hidden");
            btnShowStatus.classList.add("hidden");
        });
    }
    
    // 10. Initialize history log filter controller
    initFilterController();
    
    // 11. Initialize history log collapse controller
    initLogCollapseController();
    
    // 12. Bind CSV Export button
    const btnExport = document.getElementById("btn-export-csv");
    if (btnExport) {
        btnExport.addEventListener("click", () => {
            exportLogsToCSV();
        });
    }
    
    // 13. Bind Sortable Headers
    initSortableHeaders();
    
    // 14. Initialize Left Sidebar Navigation Controller
    initNavigationController();
    
    // 15. Initialize Camera Traps Modal Controller
    initCameraTrapsModalController();
    
    // 16. Initialize Threat Detail Modal Controller
    initThreatDetailModalController();
    
    // 17. Initialize Incident History Dashboard & Calendar Controller
    initIncidentHistoryDashboard();
});
