// Autonomous Drone Fire Detection System - Frontend JavaScript

const SVG_NS = 'http://www.w3.org/2000/svg';

function applyTacticalScene(scene) {
    if (!scene) return;
    if (scene.frame_width && scene.frame_height) {
        window.lastFrameDims = { w: scene.frame_width, h: scene.frame_height };
        if (window.droneDashboard) window.droneDashboard.lastFrameDims = window.lastFrameDims;
    }
    if (scene.spread) {
        const card = document.getElementById('spreadCardinal');
        const det = document.getElementById('spreadDetail');
        const vec = document.getElementById('spreadVec');
        if (card) card.textContent = scene.spread.cardinal || '—';
        if (det) det.textContent = scene.spread.label || '';
        if (vec && scene.spread.degrees != null) {
            vec.textContent = `Δ ${scene.spread.dx}, ${scene.spread.dy} px · ${scene.spread.degrees}°`;
        } else if (vec) vec.textContent = '';
    }
    if (Array.isArray(scene.priorities)) {
        const ol = document.getElementById('actionPriorityList');
        if (ol) {
            ol.innerHTML = '';
            scene.priorities.forEach((p) => {
                const li = document.createElement('li');
                const sev = (p.severity || 'medium').toLowerCase();
                li.className = `p-sev-${sev}`;
                li.innerHTML = `<strong>${p.action}</strong> — ${p.detail || ''}`;
                ol.appendChild(li);
            });
        }
    }
    if (scene.scene_graph) {
        renderTacticalGraph(scene.scene_graph);
    }
    const pm = document.getElementById('personModelBadge');
    if (pm && scene.person_model) {
        pm.textContent = scene.person_model === 'yolo' ? 'YOLO person' : scene.person_model === 'hog' ? 'HOG fallback' : '—';
    }
    const pcb = document.getElementById('peopleCountBig');
    if (pcb && scene.people_count != null) pcb.textContent = String(scene.people_count);
    const pzm = document.getElementById('personZoneMsg');
    if (pzm) {
        if (scene.person_in_fire) {
            pzm.className = 'person-zone-bad';
            pzm.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Person in / at fire zone — rescue first';
        } else {
            pzm.className = 'person-zone-ok';
            pzm.innerHTML = '<i class="fas fa-check"></i> No person in fire zone';
        }
    }

    const fireTypeEl = document.getElementById('fireTypeText');
    const spreadPredEl = document.getElementById('fireSpreadPredictionText');
    const riskLevelEl = document.getElementById('fireRiskLevelText');
    const explosionEl = document.getElementById('fireExplosionText');

    const fireType = scene.fire_type || scene.fireType || scene.fire_type_label || scene.fireTypeLabel || 'Unknown';
    if (fireTypeEl) fireTypeEl.textContent = `Fire Type: ${fireType}`;

    let predictionText = 'Spread Risk: Awaiting AI prediction (next 20 sec)';
    const spreadPrediction = scene.spread_prediction || scene.spreadPrediction || scene.prediction || scene.fire_prediction || null;
    if (typeof spreadPrediction === 'string' && spreadPrediction.trim()) {
        predictionText = `Spread Risk: ${spreadPrediction}`;
    } else if (spreadPrediction && typeof spreadPrediction === 'object') {
        const label = spreadPrediction.label || spreadPrediction.risk || spreadPrediction.level || 'HIGH';
        const windowSec = spreadPrediction.seconds || spreadPrediction.sec || spreadPrediction.window || 20;
        predictionText = `Spread Risk: ${label.toString().toUpperCase()} (next ${windowSec} sec)`;
    }
    if (spreadPredEl) spreadPredEl.textContent = predictionText;

    const riskLevel = scene.risk_level || scene.riskLevel || scene.threat_level || scene.threatLevel || 'Unknown';
    if (riskLevelEl) riskLevelEl.textContent = `Risk: ${String(riskLevel).toUpperCase()}`;

    const explosionProb = scene.explosion_probability || scene.explosionProbability || scene.explosion_risk || scene.explosionRisk || null;
    let explosionText = 'Explosion Probability: —';
    if (explosionProb != null && !Number.isNaN(Number(explosionProb))) {
        const value = Number(explosionProb);
        const formatted = value > 0 && value <= 1 ? value * 100 : value;
        explosionText = `Explosion Probability: ${Math.round(formatted * 100) / 100}%`;
    }
    if (explosionEl) explosionEl.textContent = explosionText;

    const areaBorderText = document.getElementById('areaBorderText');
    const entryPointText = document.getElementById('entryPointText');
    const bestEntryText = document.getElementById('bestEntryText');
    const safePathText = document.getElementById('safePathText');
    const fireZoneText = document.getElementById('fireZoneText');
    const personZoneText = document.getElementById('personZoneText');
    const aiCommandText = document.getElementById('aiCommandText');

    if (areaBorderText) {
        const label = scene.area_border?.label || 'NOT DETECTED';
        areaBorderText.textContent = `Area Border: ${label}`;
    }

    if (entryPointText) {
        const points = Array.isArray(scene.entry_points) ? scene.entry_points : [];
        if (points.length) {
            entryPointText.textContent = `Entry Points: ${points.map(p => `${(p.type || 'unknown').toUpperCase()} ${p.direction || ''}`.trim()).join(' · ')}`;
        } else {
            entryPointText.textContent = 'Entry Points: Not found';
        }
    }

    if (bestEntryText) {
        bestEntryText.textContent = `Best Entry: ${scene.best_entry || '—'}`;
    }

    if (safePathText) {
        safePathText.textContent = `Safe Path: ${scene.safe_path || '—'}`;
    }

    const fireTypeText = document.getElementById('fireTypeText');
    const fireSpreadText = document.getElementById('fireSpreadText');
    const trappedPersonsText = document.getElementById('trappedPersonsText');

    if (fireZoneText) {
        fireZoneText.textContent = `Fire: ${scene.fire_side || '—'}`;
    }

    if (fireTypeText) {
        fireTypeText.textContent = `Fire Type: ${scene.fire_type || '—'}`;
    }

    if (fireSpreadText) {
        fireSpreadText.textContent = `Spread: ${scene.fire_spread || '—'}`;
    }

    if (personZoneText) {
        personZoneText.textContent = `Person: ${scene.person_zone || '—'}`;
    }

    if (trappedPersonsText) {
        trappedPersonsText.textContent = `Trapped Persons: ${scene.trapped_persons ?? '—'}`;
    }

    if (aiCommandText) {
        const commands = Array.isArray(scene.ai_command) ? scene.ai_command : [scene.ai_command].filter(Boolean);
        aiCommandText.innerHTML = commands.length ? `AI Command: ${commands.join(' · ')}` : 'AI Command: —';
    }
}

function renderTacticalGraph(graph) {
    const svg = document.getElementById('tacticalMapSvg');
    if (!svg || !graph) return;
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    svg.innerHTML = '';

    const size = 100;
    const padding = 8;
    const min = padding;
    const max = size - padding;
    const span = max - min;
    const tickCount = 5;
    const gridColor = '#334155';
    const labelColor = '#94a3b8';

    const border = document.createElementNS(SVG_NS, 'rect');
    border.setAttribute('x', String(min));
    border.setAttribute('y', String(min));
    border.setAttribute('width', String(span));
    border.setAttribute('height', String(span));
    border.setAttribute('fill', 'none');
    border.setAttribute('stroke', '#475569');
    border.setAttribute('stroke-width', '0.3');
    svg.appendChild(border);

    for (let i = 0; i <= tickCount; i++) {
        const frac = i / tickCount;
        const pos = min + span * frac;

        const vline = document.createElementNS(SVG_NS, 'line');
        vline.setAttribute('x1', String(pos));
        vline.setAttribute('y1', String(min));
        vline.setAttribute('x2', String(pos));
        vline.setAttribute('y2', String(max));
        vline.setAttribute('stroke', gridColor);
        vline.setAttribute('stroke-width', '0.12');
        svg.appendChild(vline);

        const hline = document.createElementNS(SVG_NS, 'line');
        hline.setAttribute('x1', String(min));
        hline.setAttribute('y1', String(pos));
        hline.setAttribute('x2', String(max));
        hline.setAttribute('y2', String(pos));
        hline.setAttribute('stroke', gridColor);
        hline.setAttribute('stroke-width', '0.12');
        svg.appendChild(hline);

        const xLabel = document.createElementNS(SVG_NS, 'text');
        xLabel.setAttribute('x', String(pos));
        xLabel.setAttribute('y', String(max + 4));
        xLabel.setAttribute('fill', labelColor);
        xLabel.setAttribute('font-size', '3');
        xLabel.setAttribute('text-anchor', 'middle');
        xLabel.textContent = frac.toFixed(1);
        svg.appendChild(xLabel);

        const yLabel = document.createElementNS(SVG_NS, 'text');
        yLabel.setAttribute('x', String(min - 1));
        yLabel.setAttribute('y', String(pos + 1.2));
        yLabel.setAttribute('fill', labelColor);
        yLabel.setAttribute('font-size', '3');
        yLabel.setAttribute('text-anchor', 'end');
        yLabel.textContent = frac.toFixed(1);
        svg.appendChild(yLabel);
    }

    const nodeMap = {};
    nodes.forEach((n) => {
        nodeMap[n.id] = n;
        const cx = min + n.x * span;
        const cy = min + n.y * span;

        const c = document.createElementNS(SVG_NS, 'circle');
        c.setAttribute('cx', String(cx));
        c.setAttribute('cy', String(cy));
        c.setAttribute('r', n.type === 'fire' ? '3.5' : '3');
        c.setAttribute('fill', n.type === 'fire' ? '#ef4444' : '#22c55e');
        c.setAttribute('stroke', n.type === 'fire' ? '#fbbf24' : '#86efac');
        c.setAttribute('stroke-width', '0.4');
        svg.appendChild(c);

        const labelX = cx + 2 > max ? cx - 2 : cx + 2;
        const labelY = cy - 4 < min ? cy + 5 : cy - 4;
        const coordY = cy + 5 > max ? cy - 2 : cy + 5;

        const t = document.createElementNS(SVG_NS, 'text');
        t.setAttribute('x', String(labelX));
        t.setAttribute('y', String(labelY));
        t.setAttribute('fill', '#e2e8f0');
        t.setAttribute('font-size', '3');
        t.textContent = (n.label || n.id).slice(0, 16);
        svg.appendChild(t);

        const coord = document.createElementNS(SVG_NS, 'text');
        coord.setAttribute('x', String(labelX));
        coord.setAttribute('y', String(coordY));
        coord.setAttribute('fill', labelColor);
        coord.setAttribute('font-size', '2.5');
        coord.textContent = `(${n.x.toFixed(2)},${n.y.toFixed(2)})`;
        svg.appendChild(coord);
    });

    edges.forEach((e) => {
        const a = nodeMap[e.from];
        const b = nodeMap[e.to];
        if (!a || !b) return;
        const line = document.createElementNS(SVG_NS, 'line');
        line.setAttribute('x1', String(min + a.x * span));
        line.setAttribute('y1', String(min + a.y * span));
        line.setAttribute('x2', String(min + b.x * span));
        line.setAttribute('y2', String(min + b.y * span));
        const col = e.risk === 'critical' ? '#f87171' : '#fbbf24';
        line.setAttribute('stroke', col);
        line.setAttribute('stroke-width', '0.35');
        svg.insertBefore(line, svg.firstChild);
    });
}

class DroneDashboard {
    constructor() {
        this.initialize();
    }

    initialize() {
        this.systemUptime = Date.now();
        this.commandHistory = [];
        this.detectionHistory = [];
        this.frameCount = 0;
        this.fps = 0;
        this.lastFpsUpdate = Date.now();
        this.consecutiveFires = 0;
        this.falsePositives = 0;
        this.trueDetections = 0;
        this.lastFrameDims = { w: 640, h: 480 };
        
        this.updateDateTime();
        this.startStatusUpdates();
        this.startDetectionHistoryUpdates();
        this.initializeEventListeners();
        this.updateUptime();
        
        // Set initial values
        this.setInitialValues();
        this.addToLog('System initialized successfully');
    }

    setInitialValues() {
        // Set initial detection history
        this.addToDetectionHistory('System started - Scanning for fire');
        
        // Set model info
        const modelInfo = document.getElementById('modelStatus');
        if (modelInfo) {
            modelInfo.textContent = 'Advanced Fire Detection Active';
        }
        
        // Update video source info
        const videoSource = document.getElementById('videoSource');
        if (videoSource) {
            const isWebcam = window.location.href.includes('localhost');
            videoSource.textContent = isWebcam ? 'Webcam' : 'RTSP Stream';
        }
    }

    updateDateTime() {
        const now = new Date();
        const dateStr = now.toLocaleDateString('en-US', {
            weekday: 'long',
            year: 'numeric',
            month: 'long',
            day: 'numeric'
        });
        const timeStr = now.toLocaleTimeString('en-US', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
        
        const dateTimeElement = document.getElementById('currentDateTime');
        if (dateTimeElement) {
            dateTimeElement.textContent = `${dateStr} | ${timeStr}`;
        }
        
        // Update every second
        setTimeout(() => this.updateDateTime(), 1000);
    }

    updateUptime() {
        const uptime = Date.now() - this.systemUptime;
        const hours = Math.floor(uptime / (1000 * 60 * 60));
        const minutes = Math.floor((uptime % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((uptime % (1000 * 60)) / 1000);
        
        const uptimeStr = `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        const uptimeElement = document.getElementById('systemUptime');
        if (uptimeElement) {
            uptimeElement.textContent = uptimeStr;
        }
        
        setTimeout(() => this.updateUptime(), 1000);
    }

    async startStatusUpdates() {
        while (true) {
            try {
                await this.updateSystemStatus();
                await new Promise(resolve => setTimeout(resolve, 1000)); // Update every second
            } catch (error) {
                console.error('Status update failed:', error);
                this.updateSystemStatusError();
                await new Promise(resolve => setTimeout(resolve, 5000)); // Retry after 5 seconds
            }
        }
    }

    async startDetectionHistoryUpdates() {
        while (true) {
            try {
                await this.fetchDetectionHistory();
                await new Promise(resolve => setTimeout(resolve, 5000)); // Update every 5 seconds
            } catch (error) {
                console.error('Detection history update failed:', error);
                await new Promise(resolve => setTimeout(resolve, 5000));
            }
        }
    }

    async updateSystemStatus() {
        try {
            const response = await fetch('/status');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            
            const data = await response.json();
            
            this.updateFireStatus(data);
            this.updateDroneStatus(data);
            this.updateLastUpdateTime();
            this.updateSystemHealth(data);
            this.updateFrameStats();
            if (data.spread || data.priorities || data.scene_graph || data.frame_width
                || typeof data.people_count === 'number' || data.person_model || data.area_border
                || data.entry_points || data.best_entry || data.safe_path || data.ai_command) {
                applyTacticalScene({
                    spread: data.spread,
                    priorities: data.priorities,
                    scene_graph: data.scene_graph,
                    people_count: data.people_count,
                    person_in_fire: data.person_in_fire,
                    person_model: data.person_model,
                    frame_width: data.frame_width,
                    frame_height: data.frame_height,
                    area_border: data.area_border,
                    entry_points: data.entry_points,
                    best_entry: data.best_entry,
                    safe_path: data.safe_path,
                    fire_side: data.fire_side,
                    person_zone: data.person_zone,
                    ai_command: data.ai_command,
                });
            }
            if (data.thermal_frame_data) {
                const th = document.getElementById('thermalStream');
                const thPh = document.getElementById('thermalPlaceholder');
                if (th && data.thermal_frame_data.length > 80) {
                    th.src = data.thermal_frame_data;
                    th.style.display = 'block';
                    if (thPh) thPh.style.display = 'none';
                }
            }
        } catch (error) {
            console.error('Failed to fetch status:', error);
            this.updateSystemStatusError();
        }
    }

    updateSystemStatusError() {
        const statusDot = document.querySelector('.status-dot');
        const statusText = document.querySelector('.status-text');
        
        if (statusDot) statusDot.style.backgroundColor = 'var(--danger-color)';
        if (statusText) {
            statusText.textContent = 'CONNECTION ERROR';
            statusText.style.color = 'var(--danger-color)';
        }
    }

    updateFireStatus(data) {
        // Update status elements
        const fireStatusBadge = document.getElementById('fireStatusBadge');
        const fireIcon = document.getElementById('fireIcon');
        const fireStatusText = document.getElementById('fireStatusText');
        const fireLocationText = document.getElementById('fireLocationText');
        const fireConfidenceText = document.getElementById('fireConfidenceText');
        const fireStatusCard = document.querySelector('.fire-status');
        
        // Clear all status classes
        fireStatusCard.classList.remove('fire-high-status', 'fire-medium-status', 'fire-clear-status');
        fireStatusText.classList.remove('fire-high', 'fire-medium', 'fire-low', 'fire-clear');
        fireConfidenceText.classList.remove('fire-high', 'fire-medium', 'fire-low', 'fire-clear');
        
        if (data.fire_detected) {
            const confidence = Math.round(data.fire_confidence || 0);
            
            // Determine confidence level
            if (confidence >= 80) {
                // HIGH CONFIDENCE FIRE
                this.updateHighConfidenceFire(fireStatusBadge, fireIcon, fireStatusText, 
                                            fireLocationText, fireConfidenceText, 
                                            fireStatusCard, data, confidence);
                this.consecutiveFires++;
                this.trueDetections++;
                
            } else if (confidence >= 60) {
                // MEDIUM CONFIDENCE
                this.updateMediumConfidenceFire(fireStatusBadge, fireIcon, fireStatusText,
                                              fireLocationText, fireConfidenceText,
                                              fireStatusCard, data, confidence);
                this.consecutiveFires = Math.max(0, this.consecutiveFires - 0.5);
                
            } else {
                // LOW CONFIDENCE (likely false positive)
                this.updateLowConfidenceFire(fireStatusBadge, fireIcon, fireStatusText,
                                           fireLocationText, fireConfidenceText,
                                           fireStatusCard, data, confidence);
                this.consecutiveFires = Math.max(0, this.consecutiveFires - 1);
                this.falsePositives++;
            }
            
            // Add to detection history for significant detections
            if (confidence >= 70) {
                const timestamp = new Date().toLocaleTimeString('en-US', {
                    hour12: false,
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit'
                });
                const detectionMsg = `Fire detected at ${data.fire_location[0]}, ${data.fire_location[1]} (${confidence}% confidence)`;
                
                // Avoid duplicate entries
                if (!this.detectionHistory.includes(detectionMsg)) {
                    this.addToDetectionHistory(detectionMsg);
                    this.detectionHistory.push(detectionMsg);
                    
                    // Keep only last 5 unique detections
                    if (this.detectionHistory.length > 5) {
                        this.detectionHistory.shift();
                    }
                }
            }
            
        } else {
            // NO FIRE DETECTED
            this.updateNoFireStatus(fireStatusBadge, fireIcon, fireStatusText,
                                  fireLocationText, fireConfidenceText, fireStatusCard);
            this.consecutiveFires = Math.max(0, this.consecutiveFires - 0.2);
        }
        
        // Update detection accuracy
        this.updateDetectionAccuracy();
    }

    updateHighConfidenceFire(badge, icon, statusText, locationText, confidenceText, card, data, confidence) {
        badge.innerHTML = `<i class="fas fa-fire"></i> FIRE DETECTED`;
        badge.className = 'status-badge fire-high';
        
        icon.className = 'fas fa-fire fire-icon-active';
        statusText.textContent = 'FIRE DETECTED!';
        statusText.className = 'fire-high';
        
        locationText.innerHTML = `<i class="fas fa-map-marker-alt"></i> Location: ${data.fire_location[0]}, ${data.fire_location[1]}`;
        confidenceText.innerHTML = `<i class="fas fa-chart-line"></i> Confidence: ${confidence}% (HIGH)`;
        confidenceText.className = 'fire-high';
        
        card.classList.add('fire-high-status');
        
        // Show fire source details for fire brigade
        this.showFireSourceDetails(data);
    }

    updateMediumConfidenceFire(badge, icon, statusText, locationText, confidenceText, card, data, confidence) {
        badge.innerHTML = `<i class="fas fa-exclamation-triangle"></i> SUSPECTED`;
        badge.className = 'status-badge fire-medium';
        
        icon.className = 'fas fa-exclamation-triangle';
        statusText.textContent = 'SUSPECTED FIRE';
        statusText.className = 'fire-medium';
        
        locationText.innerHTML = `<i class="fas fa-map-marker-alt"></i> Checking location...`;
        confidenceText.innerHTML = `<i class="fas fa-chart-line"></i> Confidence: ${confidence}% (MEDIUM)`;
        confidenceText.className = 'fire-medium';
        
        card.classList.add('fire-medium-status');
        
        // Show fire source details even for medium confidence
        this.showFireSourceDetails(data);
    }

    updateLowConfidenceFire(badge, icon, statusText, locationText, confidenceText, card, data, confidence) {
        badge.innerHTML = `<i class="fas fa-search"></i> CHECKING`;
        badge.className = 'status-badge fire-low';
        
        icon.className = 'fas fa-search';
        statusText.textContent = 'ANALYZING...';
        statusText.className = 'fire-low';
        
        locationText.innerHTML = `<i class="fas fa-map-marker-alt"></i> Possible false positive`;
        confidenceText.innerHTML = `<i class="fas fa-chart-line"></i> Confidence: ${confidence}% (LOW)`;
        confidenceText.className = 'fire-low';
        
        card.classList.remove('fire-high-status', 'fire-medium-status');
    }

    updateNoFireStatus(badge, icon, statusText, locationText, confidenceText, card) {
        badge.innerHTML = `<i class="fas fa-check-circle"></i> CLEAR`;
        badge.className = 'status-badge fire-clear';
        
        icon.className = 'fas fa-check-circle';
        statusText.textContent = 'NO FIRE DETECTED';
        statusText.className = 'fire-clear';
        
        locationText.innerHTML = `<i class="fas fa-map-marker-alt"></i> System scanning normally`;
        confidenceText.innerHTML = `<i class="fas fa-chart-line"></i> Status: CLEAR`;
        confidenceText.className = 'fire-clear';
        
        card.classList.add('fire-clear-status');
        
        // Hide fire source details when no fire
        const sourceDetails = document.getElementById('fireSourceDetails');
        if (sourceDetails) {
            sourceDetails.style.display = 'none';
        }
    }

    showFireSourceDetails(data) {
        const sourceDetails = document.getElementById('fireSourceDetails');
        if (!sourceDetails) return;
        
        sourceDetails.style.display = 'block';
        
        // Display fire source box coordinates
        if (data.fire_source_box) {
            const [x1, y1, x2, y2] = data.fire_source_box;
            const cx = Math.round((x1 + x2) / 2);
            const cy = Math.round((y1 + y2) / 2);
            
            document.getElementById('fireSourceBox').textContent = 
                `(${x1}, ${y1}) → (${x2}, ${y2})`;
            document.getElementById('fireSourceCenter').textContent = 
                `(${cx}, ${cy})`;
            
            // Update visual map with fire marker position
            this.updateFireLocationMap(x1, y1, x2, y2, cx, cy);
        }
        
        // Display people detection
        if (data.people_detected && Array.isArray(data.people_detected)) {
            const peopleCount = data.people_detected.length;
            const peopleAlert = document.getElementById('peopleAlert');
            const peopleCountBig = document.getElementById('peopleCountBig');
            if (peopleCountBig) peopleCountBig.textContent = peopleCount;
            
            if (peopleCount > 0) {
                document.getElementById('peopleCount').textContent = peopleCount;
                
                // Show alert if person in fire zone
                if (data.person_in_fire) {
                    peopleAlert.style.display = 'block';
                    peopleAlert.innerHTML = `
                        <i class="fas fa-exclamation-circle" style="color: #ff0000; margin-right: 0.5rem;"></i>
                        <strong style="color: #ff0000;">⚠️ PERSON IN FIRE ZONE!</strong>
                        <div style="font-size: 0.85rem; margin-top: 0.5rem; color: var(--text-secondary);">
                            <strong>IMMEDIATE ACTION REQUIRED:</strong> Detected ${peopleCount} person(s) very close to fire source!
                        </div>
                    `;
                } else {
                    peopleAlert.style.display = 'none';
                }
            } else {
                peopleAlert.style.display = 'none';
            }
        }
    }

    updateFireLocationMap(x1, y1, x2, y2, cx, cy) {
        const mapContainer = document.getElementById('fireLocationMap');
        const marker = document.getElementById('fireMarker');
        
        if (!mapContainer || !marker) return;
        
        // Calculate position as percentage of container
        const width = mapContainer.offsetWidth;
        const height = mapContainer.offsetHeight;
        
        const fw = (window.lastFrameDims && window.lastFrameDims.w) || 640;
        const fh = (window.lastFrameDims && window.lastFrameDims.h) || 480;
        const mapX = (cx / fw) * 100;
        const mapY = (cy / fh) * 100;
        
        marker.style.left = `${mapX}%`;
        marker.style.top = `${mapY}%`;
        
        // Draw grid with fire box
        const svg = document.getElementById('fireMapSvg');
        if (svg) {
            svg.innerHTML = '';
            
            // Grid lines
            const gridColor = '#333';
            for (let i = 0; i <= 5; i++) {
                const x = (i / 5) * 100;
                const y = (i / 5) * 100;
                
                // Vertical lines
                const vline = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                vline.setAttribute('x1', `${x}%`);
                vline.setAttribute('y1', '0%');
                vline.setAttribute('x2', `${x}%`);
                vline.setAttribute('y2', '100%');
                vline.setAttribute('stroke', gridColor);
                vline.setAttribute('stroke-width', '1');
                svg.appendChild(vline);
                
                // Horizontal lines
                const hline = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                hline.setAttribute('x1', '0%');
                hline.setAttribute('y1', `${y}%`);
                hline.setAttribute('x2', '100%');
                hline.setAttribute('y2', `${y}%`);
                hline.setAttribute('stroke', gridColor);
                hline.setAttribute('stroke-width', '1');
                svg.appendChild(hline);
            }
            
            // Draw fire box (if applicable)
            const fw = (window.lastFrameDims && window.lastFrameDims.w) || 640;
            const fh = (window.lastFrameDims && window.lastFrameDims.h) || 480;
            const boxX1Pct = (x1 / fw) * 100;
            const boxY1Pct = (y1 / fh) * 100;
            const boxX2Pct = (x2 / fw) * 100;
            const boxY2Pct = (y2 / fh) * 100;
            
            const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            rect.setAttribute('x', `${boxX1Pct}%`);
            rect.setAttribute('y', `${boxY1Pct}%`);
            rect.setAttribute('width', `${boxX2Pct - boxX1Pct}%`);
            rect.setAttribute('height', `${boxY2Pct - boxY1Pct}%`);
            rect.setAttribute('fill', 'none');
            rect.setAttribute('stroke', '#ff0000');
            rect.setAttribute('stroke-width', '2');
            svg.appendChild(rect);
        }
    }

    updateDetectionAccuracy() {
        const totalDetections = this.trueDetections + this.falsePositives;
        if (totalDetections > 0) {
            const accuracy = Math.round((this.trueDetections / totalDetections) * 100);
            
            // Update system info with accuracy
            const systemHealth = document.getElementById('systemHealth');
            if (systemHealth) {
                systemHealth.textContent = `${accuracy}% Accurate`;
                systemHealth.style.color = accuracy >= 80 ? 'var(--success-color)' : 
                                         accuracy >= 60 ? 'var(--warning-color)' : 
                                         'var(--danger-color)';
            }
        }
    }

    updateDroneStatus(data) {
        const droneStatusBadge = document.getElementById('droneStatusBadge');
        const batteryLevel = document.getElementById('batteryLevel');
        const batteryFill = document.getElementById('batteryFill');
        
        // Update drone status
        if (droneStatusBadge) {
            droneStatusBadge.innerHTML = `<i class="fas fa-drone"></i> ${data.drone_status || 'IDLE'}`;
            
            // Color code based on status
            if (data.drone_status && data.drone_status.includes('FIRE')) {
                droneStatusBadge.className = 'status-badge fire-high';
            } else if (data.drone_status && (data.drone_status.includes('EMERGENCY') || data.drone_status.includes('LAND'))) {
                droneStatusBadge.className = 'status-badge fire-medium';
            } else {
                droneStatusBadge.className = 'status-badge';
                droneStatusBadge.style.color = 'var(--secondary-color)';
                droneStatusBadge.style.borderColor = 'var(--secondary-color)';
            }
        }
        
        // Update battery (simulated)
        if (batteryLevel && batteryFill) {
            let battery = parseInt(batteryLevel.textContent) || 100;
            
            // Simulate battery drain based on drone activity
            if (data.drone_status === 'IDLE') {
                // Slow recharge when idle
                battery = Math.min(100, battery + 0.1);
            } else if (data.drone_status && data.drone_status.includes('FIRE')) {
                // Fast drain when moving to fire
                battery = Math.max(10, battery - 0.3);
            } else if (data.drone_status && data.drone_status !== 'IDLE') {
                // Normal drain when active
                battery = Math.max(20, battery - 0.2);
            }
            
            batteryLevel.textContent = `${Math.round(battery)}%`;
            batteryFill.style.width = `${battery}%`;
            
            // Update battery color
            if (battery > 70) {
                batteryFill.style.background = 'linear-gradient(90deg, var(--success-color), #22c55e)';
            } else if (battery > 30) {
                batteryFill.style.background = 'linear-gradient(90deg, var(--warning-color), #eab308)';
            } else {
                batteryFill.style.background = 'linear-gradient(90deg, var(--danger-color), #dc2626)';
                
                // Low battery warning
                if (battery <= 15) {
                    this.addToLog(`⚠️ Low battery: ${Math.round(battery)}% - Consider landing`);
                }
            }
        }
        
        // Update other indicators
        this.updateDroneIndicators(data);
    }

    updateDroneIndicators(data) {
        const altitude = document.getElementById('altitude');
        const signal = document.getElementById('signalStrength');
        const gps = document.getElementById('gpsStatus');
        
        if (altitude) {
            if (data.drone_status === 'IDLE') {
                altitude.textContent = '0 m';
            } else if (data.drone_status && data.drone_status.includes('FIRE')) {
                altitude.textContent = '25 m';
            } else {
                altitude.textContent = '15 m';
            }
        }
        
        if (signal) {
            // Simulate signal strength (better when idle)
            const baseSignal = data.drone_status === 'IDLE' ? 100 : 85;
            const variation = Math.sin(Date.now() / 10000) * 10;
            const signalStrength = Math.max(50, Math.min(100, baseSignal + variation));
            signal.textContent = `${Math.round(signalStrength)}%`;
        }
        
        if (gps) {
            gps.textContent = data.drone_status === 'IDLE' ? '-- Sats' : '12 Sats';
        }
    }

    updateLastUpdateTime() {
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-US', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
        
        const lastUpdate = document.getElementById('lastUpdateTime');
        if (lastUpdate) {
            lastUpdate.textContent = timeStr;
        }
        
        // Update system status indicator
        const statusDot = document.querySelector('.status-dot');
        const statusText = document.querySelector('.status-text');
        
        if (statusDot && statusText) {
            statusDot.style.backgroundColor = 'var(--success-color)';
            statusText.textContent = 'SYSTEM ACTIVE';
            statusText.style.color = 'var(--success-color)';
        }
    }

    updateSystemHealth(data) {
        const systemHealth = document.getElementById('systemHealth');
        if (systemHealth && data.system_online) {
            systemHealth.textContent = 'Healthy';
            systemHealth.style.color = 'var(--success-color)';
        }
    }

    updateFrameStats() {
        this.frameCount++;
        const now = Date.now();
        
        // Update FPS every second
        if (now - this.lastFpsUpdate >= 1000) {
            this.fps = Math.round((this.frameCount * 1000) / (now - this.lastFpsUpdate));
            this.frameCount = 0;
            this.lastFpsUpdate = now;
            
            const fpsElement = document.getElementById('fpsCounter');
            if (fpsElement) {
                fpsElement.textContent = this.fps;
                fpsElement.style.color = this.fps >= 25 ? 'var(--success-color)' : 
                                       this.fps >= 15 ? 'var(--warning-color)' : 
                                       'var(--danger-color)';
            }
            
            // Update frames processed
            const framesElement = document.getElementById('framesProcessed');
            if (framesElement) {
                const current = parseInt(framesElement.textContent) || 0;
                framesElement.textContent = (current + this.fps).toLocaleString();
            }
        }
    }

    async sendCommand(command) {
        try {
            this.addToLog(`Sending command: ${command}`);
            
            const response = await fetch(`/command/${command}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            const data = await response.json();
            
            if (response.ok && data.success) {
                this.addToLog(`✓ ${data.message}`);
                this.showCommandFeedback(command, true);
            } else {
                this.addToLog(`✗ Error: ${data.error || 'Command failed'}`);
                this.showCommandFeedback(command, false);
            }
            
        } catch (error) {
            this.addToLog(`✗ Network error: ${error.message}`);
            this.showCommandFeedback(command, false);
        }
    }

    showCommandFeedback(command, success) {
        const button = document.querySelector(`[onclick*="${command}"]`);
        if (!button) return;
        
        const originalColor = button.style.background;
        const originalText = button.innerHTML;
        
        // Visual feedback
        if (success) {
            button.style.background = 'var(--success-color)';
            button.innerHTML = button.innerHTML.replace('</i>', '</i><span style="margin-left: 5px;">✓</span>');
        } else {
            button.style.background = 'var(--danger-color)';
            button.innerHTML = button.innerHTML.replace('</i>', '</i><span style="margin-left: 5px;">✗</span>');
        }
        
        // Reset after 1 second
        setTimeout(() => {
            button.style.background = originalColor;
            button.innerHTML = originalText;
        }, 1000);
    }

    addToLog(message) {
        const logContainer = document.getElementById('commandLog');
        if (!logContainer) return;
        
        const logEntry = document.createElement('div');
        logEntry.className = 'log-entry';
        
        const timestamp = new Date().toLocaleTimeString('en-US', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
        
        // Add icon based on message type
        let icon = '📝';
        if (message.includes('✓')) icon = '✅';
        if (message.includes('✗') || message.includes('Error')) icon = '❌';
        if (message.includes('⚠️')) icon = '⚠️';
        if (message.includes('FIRE DETECTED')) icon = '🔥';
        
        logEntry.innerHTML = `<span class="log-time">[${timestamp}]</span> ${icon} ${message}`;
        
        // Color code messages
        if (message.includes('FIRE DETECTED')) {
            logEntry.style.color = 'var(--danger-color)';
            logEntry.style.fontWeight = 'bold';
        } else if (message.includes('✓')) {
            logEntry.style.color = 'var(--success-color)';
        } else if (message.includes('✗') || message.includes('Error')) {
            logEntry.style.color = 'var(--danger-color)';
        } else if (message.includes('⚠️')) {
            logEntry.style.color = 'var(--warning-color)';
        }
        
        logContainer.appendChild(logEntry);
        
        // Keep only last 10 entries
        while (logContainer.children.length > 10) {
            logContainer.removeChild(logContainer.firstChild);
        }
        
        // Auto-scroll to bottom
        logContainer.scrollTop = logContainer.scrollHeight;
    }

    addToDetectionHistory(message) {
        const historyContainer = document.getElementById('detectionHistory');
        if (!historyContainer) return;
        
        const historyItem = document.createElement('div');
        historyItem.className = 'history-item';
        
        // Extract confidence from message if present
        const confidenceMatch = message.match(/\((\d+)% confidence\)/);
        const confidence = confidenceMatch ? parseInt(confidenceMatch[1]) : 0;
        
        // Color code based on confidence
        if (confidence >= 80) {
            historyItem.style.borderLeftColor = 'var(--danger-color)';
            historyItem.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
        } else if (confidence >= 60) {
            historyItem.style.borderLeftColor = 'var(--warning-color)';
            historyItem.style.backgroundColor = 'rgba(245, 158, 11, 0.1)';
        }
        
        const timestamp = new Date().toLocaleTimeString('en-US', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit'
        });
        
        historyItem.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span>${timestamp}</span>
                ${confidence > 0 ? `<span style="font-weight: bold; color: ${confidence >= 80 ? 'var(--danger-color)' : 'var(--warning-color)'}">
                    ${confidence}%
                </span>` : ''}
            </div>
            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.25rem;">
                ${message.replace(/\(\d+% confidence\)/, '')}
            </div>
        `;
        
        historyContainer.appendChild(historyItem);
        
        // Keep only last 8 entries (including the initial one)
        while (historyContainer.children.length > 8) {
            historyContainer.removeChild(historyContainer.firstChild.nextSibling);
        }
        
        // Auto-scroll to bottom
        historyContainer.scrollTop = historyContainer.scrollHeight;
    }

    async fetchDetectionHistory() {
        // Fetch detection history from backend and update dashboard
        try {
            const response = await fetch('/api/detection_history?limit=50');
            if (!response.ok) throw new Error('Failed to fetch detection history');
            
            const data = await response.json();
            this.updateDetectionHistoryDisplay(data.detections);
        } catch (error) {
            console.error('Error fetching detection history:', error);
        }
    }

    updateDetectionHistoryDisplay(detections) {
        // Update the detection history panel with latest records from server
        const historyContainer = document.getElementById('detectionHistory');
        if (!historyContainer || !detections || detections.length === 0) return;
        
        // Keep system started message, clear rest
        const systemStarted = Array.from(historyContainer.children).find(el => 
            el.textContent.includes('System Started') || el.textContent.includes('system started')
        );
        
        historyContainer.innerHTML = '';
        if (systemStarted) historyContainer.appendChild(systemStarted);
        
        // Add detection records (most recent first)
        detections.forEach(detection => {
            const historyItem = document.createElement('div');
            historyItem.className = 'history-item';
            
            const confidence = Math.round(detection.confidence);
            const timestamp = new Date(detection.timestamp);
            const timeStr = timestamp.toLocaleTimeString('en-US', {
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
            
            // Color code based on confidence
            if (confidence >= 80) {
                historyItem.style.borderLeftColor = 'var(--danger-color)';
                historyItem.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
            } else if (confidence >= 60) {
                historyItem.style.borderLeftColor = 'var(--warning-color)';
                historyItem.style.backgroundColor = 'rgba(245, 158, 11, 0.1)';
            }
            
            const locX = detection.location[0];
            const locY = detection.location[1];
            const peopleInfo = detection.people_detected > 0 ? 
                `<strong style="color: #ff6b6b;">⚠️ ${detection.people_detected} person(s)</strong>` : 
                'No people detected';
            
            historyItem.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span><strong>${timeStr}</strong></span>
                    <span style="font-weight: bold; color: ${confidence >= 80 ? 'var(--danger-color)' : 'var(--warning-color)'}">
                        ${confidence}%
                    </span>
                </div>
                <div style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.5rem;">
                    📍 Location: (${locX}, ${locY})
                </div>
                <div style="font-size: 0.85rem; margin-top: 0.3rem;">
                    ${peopleInfo}
                </div>
            `;
            
            historyContainer.appendChild(historyItem);
        });
    }

    initializeEventListeners() {
        // Record button
        const recordBtn = document.getElementById('recordBtn');
        if (recordBtn) {
            recordBtn.addEventListener('click', () => {
                const isRecording = recordBtn.classList.toggle('recording');
                
                if (isRecording) {
                    recordBtn.innerHTML = '<i class="fas fa-square"></i> STOP';
                    recordBtn.style.background = 'var(--danger-color)';
                    this.addToLog('Started video recording');
                } else {
                    recordBtn.innerHTML = '<i class="fas fa-circle"></i> REC';
                    recordBtn.style.background = '';
                    this.addToLog('Stopped video recording');
                }
            });
        }

        // Snapshot button
        const snapshotBtn = document.getElementById('snapshotBtn');
        if (snapshotBtn) {
            snapshotBtn.addEventListener('click', () => {
                this.captureSnapshot();
            });
        }

        // Emergency stop confirmation
        const emergencyBtn = document.querySelector('.btn-emergency');
        if (emergencyBtn) {
            emergencyBtn.addEventListener('click', (e) => {
                if (confirm('⚠️ EMERGENCY STOP CONFIRMATION\n\nAre you sure you want to activate emergency stop?\n\n• All motors will immediately stop\n• Drone will fall from current altitude\n• This action cannot be undone!')) {
                    this.sendCommand('EMERGENCY_STOP');
                } else {
                    e.preventDefault();
                    this.addToLog('Emergency stop cancelled by user');
                }
            });
        }

        // Sensitivity slider
        const sensitivitySlider = document.getElementById('confidenceSlider');
        const sensitivityValue = document.getElementById('confidenceValue');
        
        if (sensitivitySlider && sensitivityValue) {
            sensitivitySlider.addEventListener('input', (e) => {
                const value = e.target.value;
                sensitivityValue.textContent = `${value}%`;
                
                // Update color based on value
                if (value >= 70) {
                    sensitivityValue.style.color = 'var(--success-color)';
                } else if (value >= 40) {
                    sensitivityValue.style.color = 'var(--warning-color)';
                } else {
                    sensitivityValue.style.color = 'var(--danger-color)';
                }
            });
            
            sensitivitySlider.addEventListener('change', async (e) => {
                const threshold = parseInt(e.target.value) / 100;
                try {
                    const response = await fetch(`/update_threshold/${threshold}`, {
                        method: 'POST'
                    });
                    const data = await response.json();
                    if (data.success) {
                        this.addToLog(`Detection sensitivity set to ${e.target.value}%`);
                    }
                } catch (error) {
                    this.addToLog(`Failed to update sensitivity: ${error.message}`);
                }
            });
        }

        // Video stream error handling
        const videoStream = document.getElementById('videoStream');
        if (videoStream) {
            videoStream.addEventListener('error', () => {
                const videoError = document.getElementById('videoError');
                const streamStatus = document.getElementById('streamStatus');
                
                if (videoError) videoError.style.display = 'block';
                if (streamStatus) {
                    streamStatus.textContent = 'Error';
                    streamStatus.style.color = 'var(--danger-color)';
                }
                
                this.addToLog('Video stream connection lost');
            });
            
            videoStream.addEventListener('load', () => {
                const videoError = document.getElementById('videoError');
                const streamStatus = document.getElementById('streamStatus');
                
                if (videoError) videoError.style.display = 'none';
                if (streamStatus) {
                    streamStatus.textContent = 'Active';
                    streamStatus.style.color = 'var(--success-color)';
                }
            });
        }
    }

    captureSnapshot() {
        const video = document.getElementById('videoStream');
        if (!video || video.style.display === 'none') {
            this.addToLog('✗ Cannot capture snapshot: No video stream');
            return;
        }
        
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        
        canvas.width = video.videoWidth || 640;
        canvas.height = video.videoHeight || 480;
        
        try {
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            
            // Add timestamp and info
            const now = new Date();
            ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            ctx.fillRect(0, canvas.height - 40, canvas.width, 40);
            
            ctx.fillStyle = 'white';
            ctx.font = '14px Arial';
            ctx.fillText(`Fire Detection System - ${now.toLocaleString()}`, 10, canvas.height - 20);
            
            // Get current status
            const statusText = document.getElementById('fireStatusText').textContent;
            ctx.fillText(`Status: ${statusText}`, 10, canvas.height - 5);
            
            // Convert to data URL and download
            const imageData = canvas.toDataURL('image/png');
            
            // Create download link
            const link = document.createElement('a');
            const filename = `fire_detection_${now.toISOString().replace(/[:.]/g, '-')}.png`;
            link.download = filename;
            link.href = imageData;
            
            // Trigger download
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            this.addToLog(`Snapshot saved: ${filename}`);
            
        } catch (error) {
            this.addToLog('✗ Failed to capture snapshot');
            console.error('Snapshot error:', error);
        }
    }
}

// Initialize dashboard when page loads
document.addEventListener('DOMContentLoaded', () => {
    window.droneDashboard = new DroneDashboard();
    
    // Make sendCommand available globally for button onclick handlers
    window.sendCommand = (command) => {
        window.droneDashboard.sendCommand(command);
    };
});

// Add additional CSS for new elements
const additionalStyles = document.createElement('style');
additionalStyles.textContent = `
    .confidence-value {
        font-family: 'Orbitron', sans-serif;
        font-weight: bold;
        min-width: 40px;
        text-align: center;
    }
    
    .slider {
        width: 100%;
        height: 6px;
        border-radius: 3px;
        background: linear-gradient(90deg, var(--success-color), var(--warning-color), var(--danger-color));
        outline: none;
        -webkit-appearance: none;
    }
    
    .slider::-webkit-slider-thumb {
        -webkit-appearance: none;
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: var(--text-primary);
        cursor: pointer;
        border: 2px solid var(--card-bg);
    }
    
    .slider::-moz-range-thumb {
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: var(--text-primary);
        cursor: pointer;
        border: 2px solid var(--card-bg);
    }
    
    .model-info {
        padding: 0.75rem;
        background: rgba(74, 144, 226, 0.1);
        border-radius: 6px;
        border: 1px solid var(--secondary-color);
        margin-bottom: 1rem;
        font-size: 0.9rem;
    }
    
    .model-info i {
        color: var(--secondary-color);
        margin-right: 0.5rem;
    }
    
    #videoError {
        display: none;
    }
    
    .log-time {
        color: var(--text-secondary);
        font-size: 0.8rem;
        margin-right: 0.5rem;
    }
    
    .device-item {
        background: rgba(0, 255, 136, 0.05);
        border: 1px solid rgba(0, 255, 136, 0.3);
        padding: 10px;
        border-radius: 4px;
        margin-bottom: 8px;
        font-size: 0.9rem;
    }
    
    .device-item-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 5px;
    }
    
    .device-status-dot {
        display: inline-block;
        width: 8px;
        height: 8px;
        background: #00ff88;
        border-radius: 50%;
        margin-right: 5px;
        animation: pulse-dot 1s infinite;
    }
    
    @keyframes pulse-dot {
        0%, 100% {
            opacity: 1;
        }
        50% {
            opacity: 0.5;
        }
    }
`;
document.head.appendChild(additionalStyles);

// ========== NEW: DEVICE LINK MANAGEMENT ==========
async function generateDeviceLink() {
    try {
        const response = await fetch('/api/generate_link', {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (data.success) {
            const linkCode = data.link;
            const accessUrl = data.access_url;
            
            // Show link in popup
            showLinkPopup(linkCode, accessUrl);

            // Log to dashboard if available
            try {
                if (window && window.droneDashboard && typeof window.droneDashboard.addToLog === 'function') {
                    window.droneDashboard.addToLog(`Device link generated: ${linkCode}`);
                }
            } catch (e) {
                console.warn('Dashboard log unavailable:', e);
            }
        } else {
            alert('Error: ' + (data.error || 'Could not generate link'));
        }
    } catch (error) {
        console.error('Error generating link:', error);
        alert('Failed to generate link: ' + error.message);
    }
}

function showLinkPopup(linkCode, accessUrl) {
    const modal = document.createElement('div');
    modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.7);
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 9999;
    `;
    
    const content = document.createElement('div');
    content.style.cssText = `
        background: var(--bg-darker);
        border: 2px solid var(--success-color);
        border-radius: 12px;
        padding: 30px;
        max-width: 500px;
        text-align: center;
        color: var(--text-primary);
    `;
    
    content.innerHTML = `
        <h2 style="color: var(--success-color); margin-bottom: 20px;">
            <i class="fas fa-check-circle"></i> Device Link Generated
        </h2>
        <p style="color: var(--text-secondary); margin-bottom: 15px;">
            Share this link with another device to access camera and GPS:
        </p>
        <div style="background: rgba(0, 255, 136, 0.1); border: 1px solid var(--success-color); border-radius: 8px; padding: 15px; margin: 20px 0;">
            <p style="color: #999; font-size: 0.85rem; margin-bottom: 10px;">DEVICE LINK CODE:</p>
            <p style="font-family: 'Orbitron'; font-size: 2rem; color: var(--success-color); letter-spacing: 2px; margin: 0;">
                ${linkCode}
            </p>
        </div>
        <p style="color: var(--text-secondary); font-size: 0.9rem; margin: 15px 0;">
            Full URL:
        </p>
        <input type="text" value="${accessUrl}" readonly style="
            width: 100%;
            padding: 10px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            border-radius: 4px;
            font-family: monospace;
            margin-bottom: 20px;
        " id="linkInput">
        <div style="display: flex; gap: 10px; justify-content: center;">
            <button onclick="copyToClipboard('linkInput'); this.textContent = 'Copied!'" style="
                padding: 10px 20px;
                background: rgba(0, 255, 136, 0.2);
                border: 1px solid var(--success-color);
                color: var(--success-color);
                border-radius: 4px;
                cursor: pointer;
                font-family: 'Roboto';
            ">
                <i class="fas fa-copy"></i> Copy URL
            </button>
            <button onclick="this.parentElement.parentElement.parentElement.remove()" style="
                padding: 10px 20px;
                background: rgba(255, 68, 68, 0.2);
                border: 1px solid var(--primary-color);
                color: var(--primary-color);
                border-radius: 4px;
                cursor: pointer;
                font-family: 'Roboto';
            ">
                <i class="fas fa-times"></i> Close
            </button>
        </div>
        <p style="color: var(--text-secondary); font-size: 0.8rem; margin-top: 15px; border-top: 1px solid var(--border-color); padding-top: 15px;">
            ⏱️ Link expires in 1 hour. Generate a new link if needed.
        </p>
    `;
    
    modal.appendChild(content);
    document.body.appendChild(modal);
    
    modal.onclick = (e) => {
        if (e.target === modal) modal.remove();
    };
}

function copyToClipboard(elementId) {
    const element = document.getElementById(elementId);
    element.select();
    document.execCommand('copy');
}

async function downloadDetectionHistory() {
    // Download all detection history records as JSON file
    try {
        const response = await fetch('/api/detection_history/download');
        const data = await response.json();
        
        // Create downloadable JSON file
        const jsonString = JSON.stringify(data, null, 2);
        const blob = new Blob([jsonString], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `fire_detections_${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
        
        // Log to dashboard
        if (window && window.droneDashboard && typeof window.droneDashboard.addToLog === 'function') {
            window.droneDashboard.addToLog(`✓ Downloaded ${data.total_records} detection records`);
        }
    } catch (error) {
        console.error('Error downloading detection history:', error);
        if (window && window.droneDashboard && typeof window.droneDashboard.addToLog === 'function') {
            window.droneDashboard.addToLog(`✗ Failed to download detection history: ${error.message}`);
        }
    }
}

async function updateConnectedDevices() {
    try {
        const response = await fetch('/api/devices');
        const data = await response.json();
        
        const devicesList = document.getElementById('devicesList');
        const deviceCount = document.getElementById('deviceCount');
        
        if (data.devices.length === 0) {
            devicesList.innerHTML = `
                <p style="color: var(--text-secondary); text-align: center; padding: 20px 0;">
                    <i class="fas fa-inbox"></i> No devices connected
                </p>
            `;
            deviceCount.textContent = '0 devices';
        } else {
            devicesList.innerHTML = data.devices.map(device => `
                <div class="device-item">
                    <div class="device-item-header">
                        <span>
                            <span class="device-status-dot"></span>
                            <strong>${device.device_name}</strong>
                        </span>
                        <span style="color: var(--text-secondary); font-size: 0.8rem;">
                            ${device.ip_address}
                        </span>
                    </div>
                    <div style="font-size: 0.85rem; color: var(--text-secondary);">
                        <div>Link: <span style="color: var(--success-color); font-family: 'Orbitron';">${device.link}</span></div>
                        ${device.camera_active ? '<div><i class="fas fa-video" style="color: var(--success-color);"></i> Camera: Active</div>' : ''}
                        ${device.gps_data ? `<div><i class="fas fa-location-dot" style="color: var(--success-color);"></i> GPS: ${device.gps_data.latitude.toFixed(4)}, ${device.gps_data.longitude.toFixed(4)}</div>` : ''}
                    </div>
                </div>
            `).join('');
            
            deviceCount.textContent = `${data.total_devices} device${data.total_devices !== 1 ? 's' : ''}`;
        }
    } catch (error) {
        console.error('Error updating devices:', error);
    }
}

// Update device list every 5 seconds
setInterval(updateConnectedDevices, 5000);

// Initial update
updateConnectedDevices();

// ========== Dashboard Socket.IO: Receive camera frames from devices ==========
window.dashboardSocket = null;
let frameCounter = 0;

console.log('Initializing Socket.IO dashboard listener...');

const socket = io({
    transports: ['websocket', 'polling'],
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    reconnectionAttempts: 10
});

window.dashboardSocket = socket;

socket.on('connect', () => {
    console.log('✅ Dashboard connected to Socket.IO server');
    console.log('   SID:', socket.id);
});

socket.on('device_camera', (data) => {
    frameCounter++;
    try {
        const img = document.getElementById('videoStream');
        const placeholder = document.getElementById('videoPlaceholder');

        if (!img) {
            return;
        }

        if (data && data.frame_data && data.frame_data.length > 100) {
            img.src = data.frame_data;
            img.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';

            if (frameCounter % 120 === 0) {
                console.log(`Stream ${frameCounter} frames (~${(data.frame_data.length / 1024).toFixed(0)} KB)`);
            }
        }

        const th = document.getElementById('thermalStream');
        const thPh = document.getElementById('thermalPlaceholder');
        if (data.thermal_frame_data && data.thermal_frame_data.length > 80 && th) {
            th.src = data.thermal_frame_data;
            th.style.display = 'block';
            if (thPh) thPh.style.display = 'none';
        }
        if (data.scene) {
            applyTacticalScene({
                spread: data.scene.spread,
                priorities: data.scene.priorities,
                scene_graph: data.scene.scene_graph,
                people_count: data.scene.people_count,
                person_in_fire: data.scene.person_in_fire,
                person_model: data.scene.person_model,
                frame_width: data.scene.frame_width,
                frame_height: data.scene.frame_height,
                area_border: data.scene.area_border,
                entry_points: data.scene.entry_points,
                best_entry: data.scene.best_entry,
                safe_path: data.scene.safe_path,
                fire_side: data.scene.fire_side,
                fire_type: data.scene.fire_type,
                fire_spread: data.scene.fire_spread,
                person_zone: data.scene.person_zone,
                trapped_persons: data.scene.trapped_persons,
                ai_command: data.scene.ai_command,
            });
        }
    } catch (e) {
        console.error('device_camera:', e);
    }
});

socket.on('scene_update', (data) => {
    try {
        const th = document.getElementById('thermalStream');
        const thPh = document.getElementById('thermalPlaceholder');
        if (data.thermal_frame_data && data.thermal_frame_data.length > 80 && th) {
            th.src = data.thermal_frame_data;
            th.style.display = 'block';
            if (thPh) thPh.style.display = 'none';
        }
        if (data.scene) {
            applyTacticalScene({
                spread: data.scene.spread,
                priorities: data.scene.priorities,
                scene_graph: data.scene.scene_graph,
                people_count: data.scene.people_count,
                person_in_fire: data.scene.person_in_fire,
                person_model: data.scene.person_model,
                frame_width: data.scene.frame_width,
                frame_height: data.scene.frame_height,
                area_border: data.scene.area_border,
                entry_points: data.scene.entry_points,
                best_entry: data.scene.best_entry,
                safe_path: data.scene.safe_path,
                fire_side: data.scene.fire_side,
                fire_type: data.scene.fire_type,
                fire_spread: data.scene.fire_spread,
                person_zone: data.scene.person_zone,
                trapped_persons: data.scene.trapped_persons,
                ai_command: data.scene.ai_command,
            });
        }
    } catch (e) {
        console.error('scene_update:', e);
    }
});

socket.on('status', (data) => {
    console.log('Status received:', data);
});

socket.on('device_update', (data) => {
    console.log('Device update:', data);
    updateConnectedDevices();
});

socket.on('disconnect', () => {
    console.log('❌ Disconnected from server');
});

socket.on('error', (error) => {
    console.error('⚠️ Socket.IO error:', error);
});

socket.onAny((event, ...args) => {
    if (event.startsWith('ping') || event === 'device_camera' || event === 'scene_update') {
        return;
    }
    console.log(`[EVENT] ${event}:`, args[0]);
});