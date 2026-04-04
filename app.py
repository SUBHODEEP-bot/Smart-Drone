#!/usr/bin/env python3
"""
Autonomous Drone Fire Detection and Control System
Backend with YOLOv8 + Remote Device Support
"""

import cv2
import numpy as np
import threading
import time
import requests
import uuid
import string
import random
import os
import base64
import json
import heapq
from flask import Flask, render_template, Response, jsonify, request, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import logging
import queue
import math
from collections import deque
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Try to import YOLO for fire detection
try:
    from ultralytics import YOLO
    import torch
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    torch = None
    print("⚠️ YOLOv8 not installed. Using simple color-based detection.")

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'drone-fire-detection-secret-key-2024')
CORS(app)

# Initialize Socket.IO with proper configuration
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    ping_timeout=60,
    ping_interval=25,
    logger=os.getenv("SOCKETIO_VERBOSE", "").lower() in ("1", "true", "yes"),
    engineio_logger=False,
)
app.logger.setLevel(logging.INFO)

# ========== CONFIGURATION = ==========
RTSP_URL = 'rtsp://[MOBILE_IP]:[PORT]/live/stream'
USE_WEBCAM = os.getenv('USE_WEBCAM', 'False').lower() in ('1','true','yes')
ESP32_IP = "192.168.1.100"
ESP32_BASE_URL = f"http://{ESP32_IP}/command"

# Fire-like objects that standard YOLOv8 can detect
FIRE_RELATED_CLASSES = [
    'oven', 'toaster', 'microwave', 'fire hydrant', 
    'cup', 'bowl', 'orange', 'apple', 'banana'  # These often have fire-like colors
]
# Load thresholds from environment so they can be tuned without editing code
# `FIRE_CONFIDENCE_THRESHOLD` expected as percentage (0-100) in .env
CONFIDENCE_THRESHOLD = float(os.getenv('FIRE_CONFIDENCE_THRESHOLD', '60')) / 100.0
# Detection interval in seconds (how often detection runs)
DETECTION_INTERVAL = float(os.getenv('DETECTION_INTERVAL', '0.3'))

# Global variables
current_frame = None
frame_lock = threading.Lock()
detection_status = "Initializing..."
fire_detected = False
fire_confidence = 0
fire_location = (0, 0)
fire_source_box = None
people_detected = []
people_in_fire = False
people_detection_backend = "hog"

# Tactical scene (thermal proxy, graph, priorities) — updated each detection frame
last_scene_state = {}
latest_fire_boxes = []
latest_smoke_detected = False
latest_smoke_confidence = 0.0
latest_smoke_location = (0, 0)
fire_center_history = deque(maxlen=14)
person_center_history = deque(maxlen=12)
person_trap_threshold = 10


drone_status = "IDLE"
model = None
frame_queue = queue.Queue(maxsize=10)

# Remote camera: decode+detect off the hot path so video broadcasts stay low-latency
remote_detection_queue = queue.Queue(maxsize=1)
_remote_frame_log_counter = [0]

# Detection database (persisted as JSON)
detection_database = []
detection_db_path = os.path.join(os.getcwd(), 'fire_detections.json')

def load_detection_database():
    """Load detection history from JSON file"""
    global detection_database
    if os.path.exists(detection_db_path):
        try:
            with open(detection_db_path, 'r') as f:
                detection_database = json.load(f)
                app.logger.info(f"Loaded {len(detection_database)} historical detections")
        except Exception as e:
            app.logger.warning(f"Could not load detection database: {e}")
            detection_database = []
    else:
        detection_database = []

def save_detection_database():
    """Save current detection history to JSON file"""
    global detection_database
    try:
        with open(detection_db_path, 'w') as f:
            json.dump(detection_database, f, indent=2)
    except Exception as e:
        app.logger.error(f"Failed to save detection database: {e}")

def log_fire_detection(confidence, location, source_box, people_count, person_in_fire):
    """Log a fire detection event to database"""
    global detection_database
    
    detection_record = {
        'timestamp': datetime.now().isoformat(),
        'confidence': float(confidence),
        'location': list(location),
        'source_box': list(source_box) if source_box else None,
        'people_detected': int(people_count),
        'person_in_fire': bool(person_in_fire),
        'drone_status': drone_status
    }
    
    detection_database.append(detection_record)
    
    # Keep last 1000 detections (to avoid huge files)
    if len(detection_database) > 1000:
        detection_database = detection_database[-1000:]
    
    save_detection_database()
    app.logger.info(f"Detection logged: {confidence:.0f}% at {location}")

# Load detection database on startup
load_detection_database()

# Enhanced fire detection: temporal smoothing and motion analysis
detection_history = []  # Store recent detection results for temporal filtering
previous_frame = None  # For motion detection
motion_history = []  # Store motion vectors
MAX_HISTORY = 10  # Number of frames to analyze

# Option to ignore face regions (helps reduce false positives when people are in view)
IGNORE_FACE_REGIONS = os.getenv('IGNORE_FACE_REGIONS', 'True').lower() in ('1', 'true', 'yes')
face_cascade = None
try:
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    if face_cascade.empty():
        face_cascade = None
        app.logger.info("Face cascade not loaded (empty), will not mask faces")
    else:
        app.logger.info("Face cascade loaded, face masking enabled")
except Exception as e:
    face_cascade = None
    app.logger.debug(f"Face cascade load failed: {e}")

# Pedestrian detection (HOG) for human-in-fire analysis
person_detector = None
try:
    person_detector = cv2.HOGDescriptor()
    person_detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    app.logger.info("Pedestrian HOG detector initialized")
except Exception as e:
    person_detector = None
    app.logger.warning(f"Pedestrian detector unavailable: {e}")

# Load YOLO fire detection models (primary + auxiliary ensemble)
fire_model = None
fire_model_aux = None
if YOLO_AVAILABLE and torch is not None:
    try:
        # Try to load YOLOv8 model - prefer env override or local files
        preferred = os.getenv('MODEL_PATH', '').strip()
        candidate_files = []

        if preferred:
            candidate_files.append(preferred)

        # Add common local weights (project root)
        for fname in ('yolov11.pt', 'yolov8n.pt', 'yolov8s.pt', 'yolov8m.pt'):
            full = os.path.join(os.getcwd(), fname)
            if os.path.exists(full):
                candidate_files.append(full)
            else:
                # allow passing bare name to let ultralytics download
                candidate_files.append(fname)

        # Deduplicate while preserving order
        seen = set()
        candidates = []
        for p in candidate_files:
            if p not in seen:
                seen.add(p)
                candidates.append(p)

        primary_model_path = None
        for path in candidates:
            try:
                fire_model = YOLO(path)
                primary_model_path = path
                app.logger.info(f"✅ YOLO model loaded: {path}")
                break
            except Exception as e:
                err = str(e)
                app.logger.warning(f"YOLO load failed for {path}: {err[:200]}")
                # Try PyTorch weights_only workaround for some environments
                if 'weights_only' in err.lower() or 'weightsunpickler' in err.lower():
                    try:
                        original_load = torch.load
                        def patched_load(*a, **kw):
                            if 'weights_only' not in kw:
                                kw['weights_only'] = False
                            return original_load(*a, **kw)
                        torch.load = patched_load
                        fire_model = YOLO(path)
                        torch.load = original_load
                        app.logger.info(f"✅ YOLO loaded with patched torch.load: {path}")
                        break
                    except Exception as e2:
                        torch.load = original_load
                        app.logger.warning(f"Patched load failed for {path}: {e2}")
                        continue

        if fire_model is not None:
            # Attempt to load a second auxiliary model for ensemble if available
            for path in candidates:
                if primary_model_path is not None and path == primary_model_path:
                    continue
                if fire_model_aux is not None:
                    break
                try:
                    aux = YOLO(path)
                    # avoid same model as primary (by checking object identity)
                    if aux is not None and aux != fire_model:
                        fire_model_aux = aux
                        app.logger.info(f"✅ Auxiliary YOLO model loaded: {path}")
                        break
                except Exception as e:
                    app.logger.warning(f"Aux YOLO load failed for {path}: {e}")

        if fire_model is None:
            app.logger.warning("No YOLO model could be loaded; falling back to color-based detection")


    except Exception as e:
        app.logger.error(f"Error initializing YOLO: {e}")
        fire_model = None
else:
    print("⚠️ YOLO not available - will use enhanced color-based detection")

# If a YOLO model was loaded, log its class names so we know if 'fire' or 'smoke' exist
if fire_model is not None:
    try:
        model_names = None
        # ultralytics YOLO object may store names in different attrs depending on version
        if hasattr(fire_model, 'model') and hasattr(fire_model.model, 'names'):
            model_names = fire_model.model.names
        elif hasattr(fire_model, 'names'):
            model_names = fire_model.names

        app.logger.info(f"YOLO model class names: {model_names}")
        if model_names is not None:
            present = [c for c in ['fire', 'smoke', 'flame'] if c in model_names]
            app.logger.info(f"Fire/smoke classes present in model: {present}")
    except Exception as e:
        app.logger.debug(f"Could not list YOLO model classes: {e}")

# ========== DEVICE MANAGEMENT ==========
connected_devices = {}  # {device_id: {info, link, created_at, last_gps, last_heartbeat}}
device_links = {}  # {link_code: device_id}
LINK_EXPIRY_TIME = 3600  # 1 hour in seconds

def generate_device_link():
    """Generate a unique 6-character link code"""
    chars = string.ascii_uppercase + string.digits
    link_code = ''.join(random.choices(chars, k=6))
    device_id = str(uuid.uuid4())
    
    connected_devices[device_id] = {
        'link': link_code,
        'created_at': datetime.now(),
        'last_gps': None,
        'last_heartbeat': datetime.now(),
        'camera_data': None,
        'gps_data': None,
        'device_name': 'Unknown Device',
        'ip_address': None
    }
    device_links[link_code] = device_id
    
    return link_code, device_id

def validate_device_link(link_code):
    """Validate if link is valid and not expired"""
    if link_code not in device_links:
        return None
    
    device_id = device_links[link_code]
    device = connected_devices.get(device_id)
    
    if not device:
        return None
    
    created_at = device['created_at']
    if datetime.now() - created_at > timedelta(seconds=LINK_EXPIRY_TIME):
        return None
    
    return device_id

def cleanup_expired_links():
    """Remove expired device links"""
    while True:
        try:
            now = datetime.now()
            expired_devices = []
            
            for device_id, device in list(connected_devices.items()):
                if now - device['created_at'] > timedelta(seconds=LINK_EXPIRY_TIME):
                    expired_devices.append(device_id)
            
            for device_id in expired_devices:
                device = connected_devices[device_id]
                if device['link'] in device_links:
                    del device_links[device['link']]
                del connected_devices[device_id]
            
            time.sleep(300)  # Check every 5 minutes
        except Exception as e:
            app.logger.error(f"Link cleanup error: {e}")
            time.sleep(300)

# ========== FIRE DETECTION FUNCTIONS ==========

def box_intersection_over_union(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    if xA >= xB or yA >= yB:
        return 0.0

    interArea = (xB - xA) * (yB - yA)
    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])

    iou = interArea / float(boxAArea + boxBArea - interArea + 1e-6)
    return iou


def detect_people(frame):
    global person_detector
    if person_detector is None:
        return []

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    boxes, weights = person_detector.detectMultiScale(gray, winStride=(8, 8), padding=(8, 8), scale=1.05)
    people = []
    for (x, y, w, h), score in zip(boxes, weights):
        people.append({
            'box': (int(x), int(y), int(x + w), int(y + h)),
            'confidence': float(score)
        })

    return people


def detect_people_yolo(frame):
    """YOLO/COCO person detection (class 'person') when a vision model is loaded."""
    global fire_model
    if fire_model is None:
        return []
    try:
        results = fire_model(frame, conf=0.32, iou=0.5, imgsz=640, verbose=False)
        people = []
        for result in results:
            if result.boxes is None:
                continue
            names = result.names
            for box in result.boxes:
                cls_id = int(box.cls)
                if isinstance(names, dict):
                    cls_name = str(names.get(cls_id, "")).lower()
                else:
                    cls_name = str(names[cls_id]).lower() if names is not None else ""
                if cls_name != "person":
                    continue
                conf = float(box.conf)
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                people.append({
                    "box": (x1, y1, x2, y2),
                    "confidence": conf,
                })
        return people
    except Exception as e:
        app.logger.debug(f"YOLO person detection: {e}")
        return []


def detect_people_combined(frame):
    """Prefer YOLO persons; fall back to HOG for CPU-only or missed detections."""
    global people_detection_backend
    yolo_p = detect_people_yolo(frame)
    if yolo_p:
        people_detection_backend = "yolo"
        return yolo_p
    hog_p = detect_people(frame)
    people_detection_backend = "hog" if hog_p else "none"
    return hog_p


def encode_bgr_jpeg_base64(img_bgr, quality=78):
    ret, jpeg = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ret:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode("utf-8")


def compute_pseudo_thermal_bgr(frame_bgr):
    """
    Pseudo-thermal view from visible camera (INFERNO on boosted luminance).
    Real thermal hardware would replace this pipeline.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    boosted = np.clip(gray.astype(np.float32) * 1.18, 0, 255).astype(np.uint8)
    colored = cv2.applyColorMap(boosted, cv2.COLORMAP_INFERNO)
    h, w = colored.shape[:2]
    cv2.putText(colored, "PSEUDO-THERMAL (VIS)", (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return colored


def _norm_center(box, w, h):
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2.0 / max(w, 1)
    cy = (y1 + y2) / 2.0 / max(h, 1)
    return cx, cy


def detect_area_border(frame):
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    edges = cv2.Canny(blur, 60, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_box = None
    best_area = 0

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 0.04 * w * h:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) >= 4 and area > best_area:
            x, y, bw, bh = cv2.boundingRect(approx)
            best_box = (x, y, x + bw, y + bh)
            best_area = area

    if best_box is None:
        return None, False
    return best_box, True


def approximate_direction_from_point(x, y, w, h):
    if x < w * 0.33:
        return "LEFT"
    if x > w * 0.66:
        return "RIGHT"
    if y < h * 0.33:
        return "FORWARD"
    return "BACK"


def detect_openings(frame):
    h, w = frame.shape[:2]
    openings = []

    if fire_model is not None:
        try:
            results = fire_model(frame, conf=0.25, iou=0.45, imgsz=640, verbose=False)
            for result in results:
                names = result.names
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    cls_id = int(box.cls)
                    cls_name = ""
                    if isinstance(names, dict):
                        cls_name = str(names.get(cls_id, "")).lower()
                    else:
                        cls_name = str(names[cls_id]).lower() if names is not None else ""
                    if cls_name not in ("door", "window"):
                        continue
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    cx = (x1 + x2) / 2.0
                    cy = (y1 + y2) / 2.0
                    openings.append({
                        "type": cls_name,
                        "box": [x1, y1, x2, y2],
                        "direction": approximate_direction_from_point(cx, cy, w, h),
                        "confidence": float(box.conf),
                    })
        except Exception:
            pass

    if not openings:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (7, 7), 0)
        _, thresh = cv2.threshold(blur, 80, 255, cv2.THRESH_BINARY_INV)
        kernel = np.ones((7, 7), np.uint8)
        mask = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 0.015 * w * h:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            if not (x < w * 0.25 or x + bw > w * 0.75 or y < h * 0.25 or y + bh > h * 0.75):
                continue
            direction = approximate_direction_from_point(x + bw / 2.0, y + bh / 2.0, w, h)
            opening_type = "door" if bh > bw else "window"
            openings.append({
                "type": opening_type,
                "box": [int(x), int(y), int(x + bw), int(y + bh)],
                "direction": direction,
                "confidence": min(95.0, 18.0 + (area / (w * h)) * 120.0),
            })

    return openings


def normalize_heat_value(value):
    return min(max((value - 40.0) / 140.0, 0.0), 1.0)


def heat_label(value):
    if value < 0.35:
        return "LOW"
    if value < 0.65:
        return "MEDIUM"
    return "HIGH"


def point_zone(norm_x, norm_y):
    if norm_x < 0.33:
        return "LEFT"
    if norm_x > 0.66:
        return "RIGHT"
    if norm_y < 0.33:
        return "FRONT"
    if norm_y > 0.66:
        return "BACK"
    return "CENTER"


def build_cost_grid(frame, size=5):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    h, w = blur.shape[:2]
    small = cv2.resize(blur, (size, size), interpolation=cv2.INTER_AREA)
    min_val = float(np.min(small))
    max_val = float(np.max(small))
    span = max(max_val - min_val, 1.0)
    grid = []
    for y in range(size):
        row = []
        for x in range(size):
            norm = (float(small[y, x]) - min_val) / span
            row.append(1.0 + normalize_heat_value(norm * 255.0))
        grid.append(row)
    return grid


def astar_grid(cost_grid, start, goal):
    h = len(cost_grid)
    w = len(cost_grid[0]) if h else 0
    open_heap = []
    heappush(open_heap, (0.0, start))
    gscore = {start: 0.0}
    came_from = {}

    def neighbors(node):
        x, y = node
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                yield (nx, ny)

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if current == goal:
            path = []
            while current in came_from:
                path.append(current)
                current = came_from[current]
            path.append(start)
            return list(reversed(path))

        for neighbor in neighbors(current):
            tentative_g = gscore[current] + cost_grid[neighbor[1]][neighbor[0]]
            if tentative_g < gscore.get(neighbor, float('inf')):
                came_from[neighbor] = current
                gscore[neighbor] = tentative_g
                priority = tentative_g + math.hypot(goal[0]-neighbor[0], goal[1]-neighbor[1])
                heappush(open_heap, (priority, neighbor))

    return []


def path_to_directions(path):
    if not path or len(path) < 2:
        return []
    directions = []
    for i in range(1, len(path)):
        dx = path[i][0] - path[i-1][0]
        dy = path[i][1] - path[i-1][1]
        if dx == -1 and dy == 0:
            directions.append('LEFT')
        elif dx == 1 and dy == 0:
            directions.append('RIGHT')
        elif dx == 0 and dy == -1:
            directions.append('FORWARD')
        elif dx == 0 and dy == 1:
            directions.append('BACK')
    return directions


def build_heat_grid(frame, size=20):
    h, w = frame.shape[:2]
    red_channel = frame[:, :, 2].astype(np.float32)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    merged = red_channel * 0.65 + gray * 0.35

    heat_grid = []
    min_val = float(np.min(merged))
    max_val = float(np.max(merged))
    span = max(max_val - min_val, 1.0)

    for row in range(size):
        row_vals = []
        y1 = int(row * h / size)
        y2 = int((row + 1) * h / size)
        for col in range(size):
            x1 = int(col * w / size)
            x2 = int((col + 1) * w / size)
            cell = merged[y1:y2, x1:x2]
            avg = float(np.mean(cell)) if cell.size else 0.0
            norm = min(max((avg - min_val) / span, 0.0), 1.0)
            row_vals.append(norm)
        heat_grid.append(row_vals)
    return heat_grid


def classify_fire_type(frame, box):
    if box is None:
        return 'Unknown'
    x1, y1, x2, y2 = box
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return 'Unknown'

    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return 'Unknown'

    avg_color = np.mean(crop.reshape(-1, 3), axis=0)
    blue, green, red = avg_color.tolist()
    if red > 170 and green < 130 and blue < 100:
        return 'Electrical Fire'
    if red > 150 and green > 110 and blue < 120:
        return 'Gas Fire'
    if red > 120 and green > 100 and blue > 80:
        return 'Forest Fire'
    return 'Unknown'


def estimate_fire_spread():
    if len(fire_center_history) < 2:
        return 'LOW'
    x0, y0 = fire_center_history[-2]
    x1, y1 = fire_center_history[-1]
    speed = math.hypot(x1 - x0, y1 - y0)
    if speed > 18:
        return 'HIGH'
    if speed > 7:
        return 'MEDIUM'
    return 'LOW'


def compute_person_trap_status(people):
    centers = []
    trapped = []
    for p in people or []:
        x1, y1, x2, y2 = p.get('box', (0, 0, 0, 0))
        centers.append((int((x1 + x2) / 2), int((y1 + y2) / 2)))

    if person_center_history:
        previous_centers = person_center_history[-1]
        for c in centers:
            if not previous_centers:
                continue
            distances = [math.hypot(c[0] - pc[0], c[1] - pc[1]) for pc in previous_centers]
            if distances and min(distances) < person_trap_threshold:
                trapped.append(c)

    person_center_history.append(centers)
    return trapped


def draw_entry_direction_overlay(frame, analysis):
    if not analysis:
        return frame
    h, w = frame.shape[:2]
    entry = analysis.get('entry_direction', 'UNKNOWN')
    safe_zone = analysis.get('safe_zone', 'UNKNOWN')
    risk = analysis.get('risk_level', 'UNKNOWN')
    action = ' · '.join(analysis.get('ai_command', [])) if analysis.get('ai_command') else ''

    cv2.putText(frame, f'ENTRY: {entry}', (12, h - 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.putText(frame, f'SAFE ZONE: {safe_zone}', (12, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (160, 255, 160), 2)
    cv2.putText(frame, f'ACTION: {action}', (12, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (160, 255, 160), 2)

    arrow_x = w - 160
    arrow_y = h - 70
    if entry == 'LEFT':
        cv2.arrowedLine(frame, (arrow_x + 120, arrow_y + 20), (arrow_x + 20, arrow_y + 20), (0, 255, 0), 3, tipLength=0.3)
    elif entry == 'RIGHT':
        cv2.arrowedLine(frame, (arrow_x + 20, arrow_y + 20), (arrow_x + 120, arrow_y + 20), (0, 255, 0), 3, tipLength=0.3)
    elif entry == 'FORWARD':
        cv2.arrowedLine(frame, (arrow_x + 70, arrow_y + 90), (arrow_x + 70, arrow_y + 10), (0, 255, 0), 3, tipLength=0.3)
    elif entry == 'BACK':
        cv2.arrowedLine(frame, (arrow_x + 70, arrow_y + 10), (arrow_x + 70, arrow_y + 90), (0, 255, 0), 3, tipLength=0.3)
    else:
        cv2.putText(frame, 'ENTRY UNKNOWN', (arrow_x, arrow_y + 45), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return frame


def detect_doors_windows(frame):
    """Detect doors and windows using contour analysis and heuristics."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    # Find contours
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    structures = []
    h, w = frame.shape[:2]

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 500 or area > 50000:  # Filter by size
            continue

        x, y, cw, ch = cv2.boundingRect(cnt)
        aspect_ratio = cw / float(ch) if ch > 0 else 0

        # Heuristics for doors/windows
        if 0.3 < aspect_ratio < 3.0:  # Reasonable aspect ratios
            # Check if it's rectangular enough
            perimeter = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * perimeter, True)

            if len(approx) >= 4:  # Likely rectangular
                # Classify based on position and size
                center_y = y + ch/2
                if center_y < h * 0.3:  # Top third - likely window
                    struct_type = 'WINDOW'
                    color = (255, 255, 0)  # Yellow
                elif center_y > h * 0.7:  # Bottom third - likely door
                    struct_type = 'DOOR'
                    color = (0, 255, 255)  # Cyan
                else:  # Middle - could be either
                    if aspect_ratio > 1.5:  # Wider than tall - window
                        struct_type = 'WINDOW'
                        color = (255, 255, 0)
                    else:  # Taller than wide - door
                        struct_type = 'DOOR'
                        color = (0, 255, 255)

                structures.append({
                    'type': struct_type,
                    'box': [x, y, x + cw, y + ch],
                    'center': [x + cw/2, y + ch/2],
                    'color': color
                })

    return structures


def compute_shortest_path_to_fire(heat_grid, fire_boxes, start_pos=None):
    """Compute shortest path from start to nearest fire zone."""
    if not heat_grid or not fire_boxes:
        return []

    size = len(heat_grid)
    if start_pos is None:
        start_pos = (size//2, size-1)  # Default start at bottom center

    # Find fire positions on grid
    fire_positions = []
    for fb in fire_boxes:
        if isinstance(fb, dict) and 'box' in fb:
            box = fb['box']
        else:
            box = fb

        if len(box) >= 4:
            x1, y1, x2, y2 = box
            cx = int((x1 + x2) / 2 / (640 / size))  # Assuming 640px width
            cy = int((y1 + y2) / 2 / (480 / size))  # Assuming 480px height
            cx = min(max(cx, 0), size-1)
            cy = min(max(cy, 0), size-1)
            fire_positions.append((cx, cy))

    if not fire_positions:
        return []

    # Find nearest fire
    nearest_fire = min(fire_positions, key=lambda p: abs(p[0] - start_pos[0]) + abs(p[1] - start_pos[1]))

    # Use A* to find path
    path = astar_grid(heat_grid, start_pos, nearest_fire)
    return path


def compute_rescue_path(heat_grid, person_boxes, frame_size, start_pos=None):
    """Compute a rescue path to the nearest trapped person."""
    if not heat_grid or not person_boxes:
        return []

    size = len(heat_grid)
    h, w = frame_size
    if start_pos is None:
        start_pos = (size // 2, size - 1)

    person_positions = []
    for p in person_boxes:
        if len(p) >= 4:
            x1, y1, x2, y2 = p
            cx = int(((x1 + x2) / 2) / w * size)
            cy = int(((y1 + y2) / 2) / h * size)
            cx = min(max(cx, 0), size - 1)
            cy = min(max(cy, 0), size - 1)
            person_positions.append((cx, cy))

    if not person_positions:
        return []

    nearest_person = min(person_positions, key=lambda p: abs(p[0]-start_pos[0]) + abs(p[1]-start_pos[1]))
    return astar_grid(heat_grid, start_pos, nearest_person)


def draw_structures_overlay(frame, structures):
    """Draw detected doors and windows on frame."""
    for struct in structures:
        box = struct['box']
        color = struct['color']
        struct_type = struct['type']

        x1, y1, x2, y2 = box
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        # Label
        cv2.putText(frame, struct_type, (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    return frame


def draw_shortest_path_overlay(frame, path, grid_size=20, color=(0, 255, 255), start_label='START', end_label='END', end_color=None):
    """Draw shortest path overlay on frame with start/end markers."""
    if not path:
        return frame

    h, w = frame.shape[:2]
    cell_w = w // grid_size
    cell_h = h // grid_size

    # Draw the main route line
    for i in range(len(path) - 1):
        x1 = int(path[i][0] * cell_w + cell_w / 2)
        y1 = int(path[i][1] * cell_h + cell_h / 2)
        x2 = int(path[i + 1][0] * cell_w + cell_w / 2)
        y2 = int(path[i + 1][1] * cell_h + cell_h / 2)

        cv2.line(frame, (x1, y1), (x2, y2), color, 3)
        if i % 2 == 0:
            cv2.circle(frame, (x1, y1), 5, color, -1)

    # Start marker
    x_start = int(path[0][0] * cell_w + cell_w / 2)
    y_start = int(path[0][1] * cell_h + cell_h / 2)
    cv2.circle(frame, (x_start, y_start), 10, (0, 255, 0), -1)
    cv2.putText(frame, start_label, (x_start - 20, y_start - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # End marker
    x_end = int(path[-1][0] * cell_w + cell_w / 2)
    y_end = int(path[-1][1] * cell_h + cell_h / 2)
    end_color = end_color if end_color is not None else ((0, 0, 255) if color != (0, 255, 0) else (0, 0, 255))
    cv2.circle(frame, (x_end, y_end), 12, end_color, -1)
    cv2.putText(frame, end_label, (x_end - 20, y_end - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    return frame


def compute_heat_analysis(frame, person_boxes=None, fire_box=None):
    h, w = frame.shape[:2]
    heat_grid = build_heat_grid(frame, size=20)

    # Side heat values are derived from a coarse split of the frame.
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (11, 11), 0)
    raw = {
        'LEFT': float(blur[:, :w // 2].mean()),
        'RIGHT': float(blur[:, w // 2:].mean()),
        'FORWARD': float(blur[:h // 2, :].mean()),
        'BACK': float(blur[h // 2:, :].mean()),
    }
    costs = {k: normalize_heat_value(v) for k, v in raw.items()}
    labels = {k: heat_label(v) for k, v in costs.items()}
    best_entry = min(costs, key=costs.get)
    fire_side = max(costs, key=costs.get)

    person_zone = 'CENTER'
    trapped = []
    if person_boxes:
        centers_x = [(p[0] + p[2]) / 2.0 for p in person_boxes]
        centers_y = [(p[1] + p[3]) / 2.0 for p in person_boxes]
        avg_x = sum(centers_x) / len(centers_x) / w
        avg_y = sum(centers_y) / len(centers_y) / h
        person_zone = point_zone(avg_x, avg_y)
        trapped = compute_person_trap_status([{'box': p} for p in person_boxes])

    fire_zone = 'UNKNOWN'
    fire_type = 'Unknown'
    if fire_box is not None:
        fx = (fire_box[0] + fire_box[2]) / 2.0 / w
        fy = (fire_box[1] + fire_box[3]) / 2.0 / h
        fire_zone = point_zone(fx, fy)
        fire_type = classify_fire_type(frame, fire_box)

    spread_label = estimate_fire_spread()

    coarse_grid = build_cost_grid(frame, size=5)
    start = (2, 4)
    if person_boxes:
        px = int(sum((p[0] + p[2]) / 2.0 for p in person_boxes) / len(person_boxes) / w * 5)
        py = int(sum((p[1] + p[3]) / 2.0 for p in person_boxes) / len(person_boxes) / h * 5)
        goal = (min(max(px, 0), 4), min(max(py, 0), 4))
    else:
        goal = (2, 2)

    path = astar_grid(coarse_grid, start, goal)
    directions = path_to_directions(path)
    safe_path = ' → '.join(directions[:6]) if directions else f'STAY {best_entry} SIDE'

    ai_commands = [
        f'ENTER FROM {best_entry}',
        'HOLD POSITION' if not person_boxes else 'MOVE TO PERSON LOCATION',
        f'AVOID {fire_side} SIDE',
        f'FIRE TYPE: {fire_type}',
        f'SPREAD: {spread_label}',
    ]

    shortest_path = compute_shortest_path_to_fire(coarse_grid, [fire_box] if fire_box is not None else [])
    rescue_path = compute_rescue_path(coarse_grid, person_boxes or [], (h, w)) if person_boxes else []
    rescue_directions = path_to_directions(rescue_path)
    rescue_heading = ' → '.join(rescue_directions[:6]) if rescue_directions else 'RESCUE APPROACH UNAVAILABLE'

    return {
        'heat_values': {k: round(v * 100, 1) for k, v in costs.items()},
        'heat_labels': labels,
        'heat_grid': heat_grid,
        'best_entry': f'{best_entry} SIDE',
        'safe_path': safe_path,
        'safe_zone': safe_path,
        'entry_direction': best_entry,
        'risk_level': labels.get(fire_side, 'UNKNOWN'),
        'fire_side': f'{fire_zone} SIDE ({labels.get(fire_side)})',
        'person_zone': f'{person_zone} (trapped)' if trapped else person_zone,
        'ai_command': ai_commands,
        'fire_type': fire_type,
        'fire_spread': spread_label,
        'trapped_persons': len(trapped),
        'shortest_path': shortest_path,
        'rescue_path': rescue_path,
        'rescue_directions': rescue_directions,
        'rescue_heading': rescue_heading,
    }


def compute_spread_vector():
    """Fire movement across frames (drone FOV proxy for spread direction)."""
    if len(fire_center_history) < 2:
        return {
            "dx": 0, "dy": 0, "degrees": None,
            "cardinal": "—", "label": "Collecting movement samples…",
        }
    pts = list(fire_center_history)
    x0, y0 = pts[-2]
    x1, y1 = pts[-1]
    dx, dy = float(x1 - x0), float(y1 - y0)
    mag = math.hypot(dx, dy)
    if mag < 2.0:
        return {
            "dx": dx, "dy": dy, "degrees": None,
            "cardinal": "stable", "label": "Source stable in frame (no net shift)",
        }
    deg = math.degrees(math.atan2(dy, dx))
    if deg < 0:
        deg += 360
    dirs = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
    idx = int((deg + 22.5) / 45.0) % 8
    cardinal = dirs[idx]
    return {
        "dx": round(dx, 1),
        "dy": round(dy, 1),
        "degrees": round(deg, 1),
        "cardinal": cardinal,
        "label": f"Recent shift toward {cardinal} (~{deg:.0f}° in image plane)",
    }


def compute_action_priorities(fire_ok, fire_conf, person_in_fire_z, people_count, smoke_ok, smoke_conf):
    """Ordered actions for incident command (rescue > suppress > smoke)."""
    pr = []
    r = 1
    if person_in_fire_z:
        pr.append({
            "rank": r,
            "action": "RESCUE",
            "detail": "Person inside fire zone — highest priority extraction / hose protection.",
            "severity": "critical",
        })
        r += 1
    elif people_count > 0 and fire_ok:
        pr.append({
            "rank": r,
            "action": "EVACUATE",
            "detail": f"{people_count} person(s) in scene with active fire — secure egress before direct attack.",
            "severity": "high",
        })
        r += 1
    if fire_ok and fire_conf >= 55:
        pr.append({
            "rank": r,
            "action": "SUPPRESS",
            "detail": f"Attack primary seat (confidence {fire_conf:.0f}%) — cool adjacent fuel paths.",
            "severity": "high" if fire_conf >= 75 else "medium",
        })
        r += 1
    if smoke_ok and smoke_conf >= 40:
        pr.append({
            "rank": r,
            "action": "VENTILATE",
            "detail": f"Heavy smoke signal ({smoke_conf:.0f}%) — coordinate ventilation / search line.",
            "severity": "medium",
        })
        r += 1
    if not pr:
        pr.append({
            "rank": 1,
            "action": "SCAN",
            "detail": "No confirmed hazard — continue sector scan and thermal sweep.",
            "severity": "low",
        })
    return pr


def build_scene_state(frame_bgr, hazard_active=None, hazard_conf=None, hazard_loc=None):
    """Populate last_scene_state for API + WebSocket clients."""
    global last_scene_state, people_detected, people_in_fire, people_detection_backend
    global latest_fire_boxes, latest_smoke_detected, latest_smoke_confidence, latest_smoke_location
    global fire_detected, fire_confidence, fire_location, fire_source_box

    h, w = frame_bgr.shape[:2]
    thermal = compute_pseudo_thermal_bgr(frame_bgr)
    thermal_b64 = encode_bgr_jpeg_base64(thermal)

    ha = hazard_active if hazard_active is not None else fire_detected
    hc = hazard_conf if hazard_conf is not None else fire_confidence

    spread = compute_spread_vector()
    priorities = compute_action_priorities(
        ha,
        hc,
        people_in_fire,
        len(people_detected) if people_detected else 0,
        latest_smoke_detected,
        latest_smoke_confidence,
    )

    area_border_box, area_border_detected = detect_area_border(frame_bgr)
    entry_points = detect_openings(frame_bgr)
    structures = detect_doors_windows(frame_bgr)
    heat_analysis = compute_heat_analysis(
        frame_bgr,
        [p.get('box') for p in (people_detected or []) if p.get('box')],
        fire_source_box,
    )

    nodes = []
    edges = []
    for i, fb in enumerate(latest_fire_boxes or []):
        box = fb.get("box") if isinstance(fb, dict) else fb
        if box is None or len(box) < 4:
            continue
        cx, cy = _norm_center(box, w, h)
        conf = float(fb.get("confidence", hc)) if isinstance(fb, dict) else float(hc)
        fid = f"fire{i+1}"
        nodes.append({
            "id": fid,
            "type": "fire",
            "x": round(cx, 4),
            "y": round(cy, 4),
            "label": f"Fire {conf:.0f}%",
        })
    if not nodes and ha and fire_source_box:
        box = fire_source_box
        cx, cy = _norm_center(box, w, h)
        nodes.append({
            "id": "fire1",
            "type": "fire",
            "x": round(cx, 4),
            "y": round(cy, 4),
            "label": f"Fire {hc:.0f}%",
        })

    for i, p in enumerate(people_detected or []):
        box = p.get("box")
        if not box:
            continue
        cx, cy = _norm_center(box, w, h)
        pid = f"person{i+1}"
        nodes.append({
            "id": pid,
            "type": "person",
            "x": round(cx, 4),
            "y": round(cy, 4),
            "label": f"Person {p.get('confidence', 0):.2f}",
        })
        for fn in nodes:
            if fn["type"] != "fire":
                continue
            fx, fy = fn["x"], fn["y"]
            dist = math.hypot(cx - fx, cy - fy)
            if dist < 0.42:
                risk = "critical" if people_in_fire else "high"
                edges.append({"from": pid, "to": fn["id"], "risk": risk, "distance": round(dist, 3)})

    last_scene_state = {
        "frame_width": w,
        "frame_height": h,
        "thermal_frame_data": thermal_b64,
        "spread": spread,
        "priorities": priorities,
        "scene_graph": {"nodes": nodes, "edges": edges},
        "people_count": len(people_detected or []),
        "person_in_fire": bool(people_in_fire),
        "person_model": people_detection_backend,
        "smoke_active": bool(latest_smoke_detected),
        "smoke_confidence": round(float(latest_smoke_confidence), 1),
        "hazard_active": bool(ha),
        "hazard_confidence": round(float(hc), 1),
        "area_border": {
            "box": [int(v) for v in area_border_box] if area_border_detected and area_border_box is not None else None,
            "label": "AREA BORDER DETECTED" if area_border_detected else "NOT DETECTED",
        },
        "entry_points": entry_points,
        "heat_values": heat_analysis.get('heat_values'),
        "heat_labels": heat_analysis.get('heat_labels'),
        "heat_grid": heat_analysis.get('heat_grid'),
        "best_entry": heat_analysis.get('best_entry'),
        "safe_path": heat_analysis.get('safe_path'),
        "entry_direction": heat_analysis.get('entry_direction'),
        "fire_side": heat_analysis.get('fire_side'),
        "person_zone": heat_analysis.get('person_zone'),
        "fire_type": heat_analysis.get('fire_type'),
        "fire_spread": heat_analysis.get('fire_spread'),
        "trapped_persons": heat_analysis.get('trapped_persons'),
        "shortest_path": heat_analysis.get('shortest_path'),
        "rescue_path": heat_analysis.get('rescue_path'),
        "rescue_directions": heat_analysis.get('rescue_directions'),
        "rescue_heading": heat_analysis.get('rescue_heading'),
        "structures": structures,
        "ai_command": heat_analysis.get('ai_command'),
    }


def detect_fire_yolo(frame, yolo_model=None):
    """
    Advanced fire detection using YOLOv8 model with fire-specific analysis.
    yolo_model: optional YOLO model instance to evaluate. Falls back to global fire_model.
    """
    global fire_model
    
    if yolo_model is None:
        yolo_model = fire_model

    if yolo_model is None:
        return False, 0, (0, 0), None
    
    try:
        # Run YOLO inference with optimized settings (use configured confidence)
        results = yolo_model(frame, conf=CONFIDENCE_THRESHOLD, iou=0.45, imgsz=640, verbose=False)
        
        max_confidence = 0
        best_location = (0, 0)
        fire_detected = False
        best_box = None
        fire_boxes = []
        
        h, w = frame.shape[:2]
        
        yolo_debug = []
        for result in results:
            # Analyze detections
            for box in result.boxes:
                cls_id = int(box.cls)
                cls_name = result.names[cls_id]
                conf = float(box.conf)
                
                # Get bounding box
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                
                # Analyze the detected region for fire characteristics
                roi = frame[y1:y2, x1:x2] if y2 > y1 and x2 > x1 else None
                
                if roi is not None and roi.size > 0:
                    # Convert to HSV for fire color analysis
                    roi_hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                    
                    # Check for fire colors (red, orange, yellow)
                    fire_color_mask = np.zeros(roi_hsv.shape[:2], dtype=np.uint8)
                    
                    # Red range
                    lower_red1 = np.array([0, 120, 150])
                    upper_red1 = np.array([10, 255, 255])
                    lower_red2 = np.array([170, 120, 150])
                    upper_red2 = np.array([180, 255, 255])
                    
                    # Orange range
                    lower_orange = np.array([11, 150, 180])
                    upper_orange = np.array([20, 255, 255])
                    
                    # Yellow range
                    lower_yellow = np.array([21, 150, 200])
                    upper_yellow = np.array([30, 255, 255])
                    
                    mask_red1 = cv2.inRange(roi_hsv, lower_red1, upper_red1)
                    mask_red2 = cv2.inRange(roi_hsv, lower_red2, upper_red2)
                    mask_orange = cv2.inRange(roi_hsv, lower_orange, upper_orange)
                    mask_yellow = cv2.inRange(roi_hsv, lower_yellow, upper_yellow)
                    
                    fire_color_mask = cv2.bitwise_or(mask_red1, mask_red2)
                    fire_color_mask = cv2.bitwise_or(fire_color_mask, mask_orange)
                    fire_color_mask = cv2.bitwise_or(fire_color_mask, mask_yellow)
                    
                    fire_pixel_ratio = cv2.countNonZero(fire_color_mask) / (roi.shape[0] * roi.shape[1]) if roi.size > 0 else 0
                    
                    # Check brightness (fire is bright)
                    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                    avg_brightness = np.mean(gray_roi)
                    
                    # Fire detection criteria:
                    # 1. High confidence from YOLO OR fire-like object
                    # 2. Significant fire colors in ROI (at least 30%)
                    # 3. High brightness (fire is bright)
                    
                    is_fire_like = (
                        cls_name.lower() in ['fire', 'flame', 'smoke'] or
                        fire_pixel_ratio > 0.3 or
                        (avg_brightness > 200 and fire_pixel_ratio > 0.15)
                    )
                    
                    if is_fire_like:
                        # Calculate fire confidence based on multiple factors
                        color_score = min(fire_pixel_ratio * 100, 40)
                        brightness_score = min((avg_brightness / 255) * 30, 30)
                        model_score = conf * 30
                        
                        total_confidence = color_score + brightness_score + model_score
                        total_confidence = min(total_confidence, 100)
                        
                        if total_confidence > 50:  # Minimum threshold
                            fire_boxes.append({
                                'box': (x1, y1, x2, y2),
                                'confidence': total_confidence,
                                'center': (int((x1 + x2) / 2), int((y1 + y2) / 2))
                            })
                            
                            if total_confidence > max_confidence:
                                max_confidence = total_confidence
                                best_location = (int((x1 + x2) / 2), int((y1 + y2) / 2))
                                best_box = (x1, y1, x2, y2)
                                fire_detected = True
                # Collect debug info for each detection
                try:
                    yolo_debug.append(f"{cls_name}:{conf:.2f} box=({x1},{y1},{x2},{y2})")
                except Exception:
                    pass

        if yolo_debug:
            app.logger.debug("YOLO detections: %s", "; ".join(yolo_debug))
        
        return fire_detected, max_confidence, best_location, fire_boxes
        
    except Exception as e:
        print(f"⚠️ YOLO detection error: {e}")
        return False, 0, (0, 0), None

def detect_fire_color_based(frame, previous_frame=None):
    """Enhanced fire detection using multiple techniques with motion analysis"""
    global detection_history

    h, w, _ = frame.shape
    overlay_frame = frame.copy()

    # Method 1: Improved color-based detection with tighter ranges
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # Professional fire color ranges - balanced for accuracy (reduce false positives)
    # Red range - require higher saturation to avoid false positives
    lower_red1 = np.array([0, 100, 140])  # Higher saturation threshold
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 100, 140])
    upper_red2 = np.array([180, 255, 255])

    # Orange range - fire orange is distinct
    lower_orange = np.array([11, 120, 160])  # Higher thresholds
    upper_orange = np.array([20, 255, 255])

    # Yellow range - flame tips, require brightness
    lower_yellow = np.array([21, 120, 180])  # Higher thresholds
    upper_yellow = np.array([32, 255, 255])  # Narrower range

    mask_red1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask_red2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask_orange = cv2.inRange(hsv, lower_orange, upper_orange)
    mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)

    fire_mask = cv2.bitwise_or(mask_red1, mask_red2)
    fire_mask = cv2.bitwise_or(fire_mask, mask_orange)
    fire_mask = cv2.bitwise_or(fire_mask, mask_yellow)

    # Enhanced morphological operations
    kernel_small = np.ones((3, 3), np.uint8)
    kernel_medium = np.ones((7, 7), np.uint8)
    fire_mask = cv2.morphologyEx(fire_mask, cv2.MORPH_CLOSE, kernel_medium)
    fire_mask = cv2.morphologyEx(fire_mask, cv2.MORPH_OPEN, kernel_small)

    # If enabled, detect faces and remove those regions from the fire mask
    try:
        if IGNORE_FACE_REGIONS and face_cascade is not None:
            gray_for_faces = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray_for_faces, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces) > 0:
                for (fx, fy, fw, fh) in faces:
                    pad_w = int(fw * 0.25)
                    pad_h = int(fh * 0.25)
                    x1 = max(0, fx - pad_w)
                    y1 = max(0, fy - pad_h)
                    x2 = min(w, fx + fw + pad_w)
                    y2 = min(h, fy + fh + pad_h)
                    # Zero out face region on the fire mask
                    fire_mask[y1:y2, x1:x2] = 0
                app.logger.debug(f"Face masking applied: {len(faces)} face(s) ignored from fire mask")
    except Exception as e:
        app.logger.debug(f"Face masking failed: {e}")

    # Method 2: Brightness detection (fire is bright - higher threshold to reduce false positives)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, bright_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)  # Higher threshold

    # Method 3: Motion/flicker detection
    motion_score = 0
    motion_required = True
    if previous_frame is not None:
        gray_prev = cv2.cvtColor(previous_frame, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray, gray_prev)
        _, motion_mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

        motion_in_fire = cv2.bitwise_and(motion_mask, fire_mask)
        motion_pixels = cv2.countNonZero(motion_in_fire)
        total_fire_pixels = cv2.countNonZero(fire_mask)

        if total_fire_pixels > 0:
            motion_ratio = motion_pixels / total_fire_pixels
            motion_score = min(motion_ratio * 100, 50)

            if motion_ratio < 0.15:
                motion_required = False
    else:
        motion_required = False

    # Combined mask: color AND brightness (STRICT requirement)
    combined_mask = cv2.bitwise_and(fire_mask, bright_mask)

    # STRICT: Require motion for large static regions (reduce false positives)
    combined_count = cv2.countNonZero(combined_mask)
    if not motion_required and combined_count > 0:
        # Large static region without motion = likely false positive
        if combined_count >= 200:  # Lower threshold - reject large static objects
            combined_mask = np.zeros_like(combined_mask)
            print(f"[DETECTION] ⚠️ Rejected large static region ({combined_count} pixels) - no motion")

    # Minimum size threshold - require reasonable size
    if cv2.countNonZero(combined_mask) < 80:  # Reduced from 100 to 80 for smaller flames
        combined_mask = np.zeros_like(combined_mask)

    contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    fire_detected = False
    max_area = 0
    best_contour = None
    best_roi_stats = None
    best_box = None

    for contour in contours:
        area = cv2.contourArea(contour)
        # Require minimum area - reduce false positives from noise
        if area > 100:  # Reduced from 150 to 100 for smaller flames
            perimeter = cv2.arcLength(contour, True)
            if perimeter == 0:
                continue

            circularity = 4 * np.pi * area / (perimeter * perimeter)
            if 0.15 < circularity < 0.85:
                x, y, w_box, h_box = cv2.boundingRect(contour)
                aspect_ratio = h_box / w_box if w_box > 0 else 0

                if 0.3 <= aspect_ratio <= 3.0:
                    # ULTRA STRICT: Require strong motion for ALL detections
                    # No exceptions - fire MUST flicker/move
                    if motion_required and motion_score > 15:
                        if area > max_area:
                            max_area = area
                            best_contour = contour
                            best_roi_stats = (x, y, w_box, h_box)
                            best_box = (x, y, x + w_box, y + h_box)
                            fire_detected = True
                    elif previous_frame is None:
                        # First frame - allow but with lower confidence later
                        if area > max_area:
                            max_area = area
                            best_contour = contour
                            best_roi_stats = (x, y, w_box, h_box)
                            best_box = (x, y, x + w_box, y + h_box)
                            fire_detected = True
                    else:
                        # No motion detected - reject
                        print(f"[DETECTION] ❌ Rejected: Area {area}px, Motion: {motion_score:.1f}%")

    confidence = 0
    location = (0, 0)

    if fire_detected and best_contour is not None and best_roi_stats is not None:
        x, y, w_box, h_box = best_roi_stats
        location = (x + w_box // 2, y + h_box // 2)

        area_ratio = max_area / (w * h)

        roi = frame[max(0, y):min(h, y + h_box), max(0, x):min(w, x + w_box)]
        if roi.size > 0:
            roi_hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

            avg_saturation = np.mean(roi_hsv[:, :, 1])
            avg_value = np.mean(roi_hsv[:, :, 2])
            std_hue = np.std(roi_hsv[:, :, 0])
            std_saturation = np.std(roi_hsv[:, :, 1])
            std_value = np.std(roi_hsv[:, :, 2])

            # ULTRA STRICT scoring - require high quality fire characteristics
            color_score = min(avg_saturation / 255 * 25, 25)
            brightness_score = min(avg_value / 255 * 20, 20)
            size_score = min(area_ratio * 500, 10)
            variation_score = min((std_hue / 60 + std_saturation / 100 + std_value / 100) * 4, 10)

            # Motion is CRITICAL - fire MUST flicker
            if motion_score > 25:
                motion_bonus = motion_score * 0.5  # Max 25 points
            elif motion_score > 18:
                motion_bonus = motion_score * 0.4  # Max 18 points
            elif motion_score > 12:
                motion_bonus = motion_score * 0.3  # Max 12 points
            else:
                motion_bonus = 0

            # ULTRA STRICT: Reject static objects completely
            if motion_score < 12:
                confidence = 0
                print(f"[DETECTION] ❌ REJECTED: Static object (motion: {motion_score:.1f}%)")
            elif motion_score < 18:
                base_score = (color_score + brightness_score + size_score + variation_score) * 0.4
                confidence = base_score + motion_bonus
            else:
                confidence = color_score + brightness_score + size_score + variation_score + motion_bonus

            # Additional STRICT validation
            if std_hue < 10 and std_value < 20:
                confidence *= 0.3  # Heavy penalty for uniform colors
                print(f"[DETECTION] ⚠️ Low variation: std_hue={std_hue:.1f}, std_value={std_value:.1f}")
            
            # Reject if brightness is too low
            if avg_value < 180:
                confidence *= 0.5
                print(f"[DETECTION] ⚠️ Low brightness: {avg_value:.1f}")

            confidence = min(confidence, 100)

            # Balanced threshold - require 50%+ confidence to detect fire
            if confidence < 50:
                fire_detected = False
                confidence = 0
            # ULTRA STRICT motion requirement - reject static objects completely
            elif not motion_required and motion_score < 15 and previous_frame is not None:
                # Complete rejection of static objects
                print(f"[DETECTION] ❌ REJECTED: Static object detected (motion: {motion_score:.1f}%)")
                fire_detected = False
                confidence = 0
            # Also reject if motion is too low even if motion_required is True
            elif motion_required and motion_score < 10:
                print(f"[DETECTION] ❌ REJECTED: Insufficient motion ({motion_score:.1f}%)")
                fire_detected = False
                confidence = 0
            else:
                if confidence > 75:
                    box_color = (0, 0, 255)
                    thickness = 3
                elif confidence > 60:
                    box_color = (0, 165, 255)
                    thickness = 2
                else:
                    box_color = (0, 255, 255)
                    thickness = 2

                cv2.rectangle(overlay_frame, (x, y), (x + w_box, y + h_box), box_color, thickness)
                cv2.circle(overlay_frame, location, 6, (0, 255, 255), -1)
                cv2.circle(overlay_frame, location, 3, (0, 0, 255), -1)

                label = f"Fire: {confidence:.0f}%"
                if motion_score > 10:
                    label += f" (Motion: {motion_score:.0f}%)"
                cv2.putText(overlay_frame, label, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2)

                cv2.line(overlay_frame, (location[0], 0), (location[0], h), (255, 255, 0), 1)
                cv2.line(overlay_frame, (0, location[1]), (w, location[1]), (255, 255, 0), 1)

    detection_history.append((fire_detected, confidence))
    if len(detection_history) > MAX_HISTORY:
        detection_history.pop(0)

    # Balanced temporal filtering - smooth transient noise but don't block real detections
    # Only apply if we have several frames to analyze
    if len(detection_history) >= 5:
        recent = detection_history[-5:]
        high_conf_count = sum(1 for d, c in recent if d and c > 60)
        
        # If less than 1-2 frames with >60% confidence in last 5, it's likely noise
        if high_conf_count == 0 and fire_detected:
            # Single spike of high confidence with no history = reject as noise
            fire_detected = False
            confidence = 0
            app.logger.debug("Temporal filter: noise spike rejected (no history)")
        elif high_conf_count >= 1:
            # At least 1 frame with solid confidence = accept it
            avg_conf = np.mean([c for d, c in recent if d and c > 60]) if high_conf_count > 0 else confidence
            confidence = max(confidence, avg_conf * 0.95)  # Smooth with history

    cv2.rectangle(overlay_frame, (0, 0), (w, 50), (0, 0, 0), -1)

    # Report detection at reasonable confidence thresholds
    if fire_detected and confidence > 65:
        status_text = f"🔥 FIRE DETECTED: {confidence:.0f}%"
        status_color = (0, 0, 255)
    elif fire_detected and confidence > 50:
        status_text = f"Analyzing: {confidence:.0f}%"
        status_color = (0, 165, 255)
    else:
        status_text = "Scanning - No Fire"
        status_color = (0, 255, 0)
        fire_detected = False
        confidence = 0

    cv2.putText(overlay_frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
    cv2.putText(overlay_frame, f"Drone: {drone_status}", (w - 200, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    mask_display = cv2.cvtColor(combined_mask, cv2.COLOR_GRAY2BGR)
    mask_display = cv2.resize(mask_display, (w // 6, h // 6))
    overlay_frame[10:10 + mask_display.shape[0], w - mask_display.shape[1] - 10:w - 10] = mask_display

    return fire_detected, confidence, location, overlay_frame, best_box

def detect_smoke_color_based(frame, previous_frame=None):
    """Simple smoke detection using low saturation + motion.
    Returns: smoke_detected, confidence, location, overlay_frame
    """
    h, w = frame.shape[:2]
    overlay = frame.copy()

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]

    # Smoke is low saturation, mid brightness
    mask_low_sat = cv2.inRange(sat, 0, 70)
    mask_val = cv2.inRange(val, 80, 230)
    smoke_mask = cv2.bitwise_and(mask_low_sat, mask_val)

    # Morphology to clean up
    k = np.ones((5, 5), np.uint8)
    smoke_mask = cv2.morphologyEx(smoke_mask, cv2.MORPH_OPEN, k)
    smoke_mask = cv2.morphologyEx(smoke_mask, cv2.MORPH_CLOSE, k)

    motion_score = 0
    if previous_frame is not None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_prev = cv2.cvtColor(previous_frame, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray, gray_prev)
        _, motion_mask = cv2.threshold(diff, 15, 255, cv2.THRESH_BINARY)
        motion_in_smoke = cv2.bitwise_and(motion_mask, smoke_mask)
        motion_pixels = cv2.countNonZero(motion_in_smoke)
        total_smoke = cv2.countNonZero(smoke_mask)
        if total_smoke > 0:
            motion_ratio = motion_pixels / total_smoke
            motion_score = min(motion_ratio * 100, 60)

    # Find largest smoke-like contour
    contours, _ = cv2.findContours(smoke_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    smoke_detected = False
    smoke_conf = 0
    smoke_loc = (0, 0)

    if contours:
        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area > 800:  # require reasonably large region
            x, y, bw, bh = cv2.boundingRect(largest)
            smoke_loc = (x + bw//2, y + bh//2)
            area_score = min(area / (w * h) * 1000, 60)
            motion_bonus = motion_score * 0.4
            smoke_conf = min(area_score + motion_bonus, 100)
            if smoke_conf > 30:
                smoke_detected = True
                # draw
                cv2.rectangle(overlay, (x, y), (x + bw, y + bh), (200, 200, 200), 2)
                cv2.putText(overlay, f"SMOKE: {smoke_conf:.0f}%", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200,200,200), 2)

    return smoke_detected, smoke_conf, smoke_loc, overlay

def detect_fire_hybrid(frame, prev_frame=None):
    """
    HYBRID fire detection: Combines YOLO model + Color-based + Motion analysis
    Returns: fire_detected, confidence, location, processed_frame
    """
    global previous_frame, fire_model, detection_history, fire_source_box, people_detected, people_in_fire
    global latest_fire_boxes, latest_smoke_detected, latest_smoke_confidence, latest_smoke_location
    global fire_center_history
    
    # Use provided frame or global previous_frame
    if prev_frame is None:
        prev_frame = previous_frame
    
    h, w = frame.shape[:2]

    # Method 1: YOLO Model Detection (if available)
    yolo_detected = False
    yolo_confidence = 0
    yolo_location = (0, 0)
    yolo_boxes = []

    yolo_aux_detected = False
    yolo_aux_confidence = 0
    yolo_aux_location = (0, 0)
    yolo_aux_boxes = []
    
    if fire_model is not None:
        yolo_detected, yolo_confidence, yolo_location, yolo_boxes = detect_fire_yolo(frame)

    if fire_model_aux is not None:
        yolo_aux_detected, yolo_aux_confidence, yolo_aux_location, yolo_aux_boxes = detect_fire_yolo(frame, yolo_model=fire_model_aux)

    # prefer highest-confidence YOLO result for location and source
    if yolo_aux_detected and yolo_aux_confidence > yolo_confidence:
        yolo_detected = True
        yolo_confidence = yolo_aux_confidence
        yolo_location = yolo_aux_location
        yolo_boxes = yolo_aux_boxes

    # if both models see it reliably, bump confidence
    if yolo_detected and yolo_aux_detected:
        yolo_confidence = min(100, yolo_confidence + yolo_aux_confidence * 0.2)

    # Method 2: Enhanced Color-based Detection
    color_detected, color_confidence, color_location, color_frame, color_box = detect_fire_color_based(frame, prev_frame)
    
    # Method 3: Simple color+motion-based smoke detection
    smoke_detected, smoke_confidence, smoke_location, smoke_frame = detect_smoke_color_based(frame, prev_frame) if 'detect_smoke_color_based' in globals() else (False, 0, (0,0), frame)
    latest_smoke_detected = smoke_detected
    latest_smoke_confidence = float(smoke_confidence)
    latest_smoke_location = smoke_location
    
    # Merge visualizations: use color detection frame (which has annotations) as base
    overlay_frame = color_frame.copy() if color_detected else frame.copy()
    
    # Merge smoke detection visualization
    if smoke_detected:
        overlay_frame = cv2.addWeighted(overlay_frame, 0.8, smoke_frame, 0.2, 0)
    
    # Derive a source box from YOLO or color detection
    fire_source_box = None
    if yolo_boxes:
        # pick highest confidence yolo box
        best_yolo_box = max(yolo_boxes, key=lambda b: b['confidence'])
        fire_source_box = best_yolo_box['box']
    elif color_box is not None:
        fire_source_box = color_box

    # People detection and in-fire analysis (YOLO person + HOG fallback)
    people = detect_people_combined(frame)
    people_detected = people
    person_in_fire = False

    if fire_source_box is not None and people:
        for p in people:
            iou = box_intersection_over_union(fire_source_box, p['box'])
            if iou > 0.05:
                person_in_fire = True
                break

    people_in_fire = person_in_fire

    # draw person boxes
    for p in people:
        x1, y1, x2, y2 = p['box']
        cv2.rectangle(overlay_frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
        cv2.putText(overlay_frame, f"Person: {p['confidence']:.1f}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

    if person_in_fire:
        cv2.putText(overlay_frame, "⚠️ PERSON IN FIRE ZONE", (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    # Detect and draw doors/windows on the scene
    structures = detect_doors_windows(frame)
    overlay_frame = draw_structures_overlay(overlay_frame, structures)

    # Combine results using ensemble method
    final_detected = False
    final_confidence = 0
    final_location = (0, 0)
    
    # Weighted combination
    if yolo_detected and color_detected:
        # Both methods agree - high confidence
        final_detected = True
        final_confidence = (yolo_confidence * 0.6 + color_confidence * 0.4)
        final_location = yolo_location if yolo_confidence > color_confidence else color_location
    elif yolo_detected and yolo_confidence > 70:
        # Strong YOLO detection
        final_detected = True
        final_confidence = yolo_confidence * 0.9  # Slight reduction without color confirmation
        final_location = yolo_location
    elif color_detected and color_confidence > 55:
        # Strong-enough color detection (YOLO might miss some fires)
        final_detected = True
        final_confidence = color_confidence * 0.9  # Slight reduction without YOLO confirmation
        final_location = color_location
    elif yolo_detected and color_confidence > 50:
        # YOLO detected, color partially confirms
        final_detected = True
        final_confidence = (yolo_confidence * 0.7 + color_confidence * 0.3)
        final_location = yolo_location

    # If smoke detected with high confidence, treat it as a hazard as well
    detection_method = locals().get('detection_method', None)
    if smoke_detected and smoke_confidence > 65 and (not final_detected or smoke_confidence > final_confidence):
        final_detected = True
        final_confidence = smoke_confidence * 0.9
        final_location = smoke_location
        detection_method = 'SMOKE'
    
    # Draw YOLO detections on overlay
    if yolo_boxes:
        for box_info in yolo_boxes:
            x1, y1, x2, y2 = box_info['box']
            conf = box_info['confidence']
            center = box_info['center']
            
            # Draw YOLO detection box
            if conf > 70:
                color = (0, 0, 255)  # Red for high confidence
                thickness = 3
            elif conf > 50:
                color = (0, 165, 255)  # Orange
                thickness = 2
            else:
                color = (0, 255, 255)  # Yellow
                thickness = 2
            
            cv2.rectangle(overlay_frame, (x1, y1), (x2, y2), color, thickness)
            cv2.putText(overlay_frame, f"YOLO: {conf:.0f}%", (x1, y1 - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    
    # Update status text
    cv2.rectangle(overlay_frame, (0, 0), (w, 50), (0, 0, 0), -1)
    
    if final_detected and final_confidence > 75:
        if detection_method == 'SMOKE':
            status_text = f"💨 SMOKE DETECTED: {final_confidence:.0f}%"
            status_color = (200, 200, 200)
        else:
            status_text = f"🔥 FIRE DETECTED: {final_confidence:.0f}%"
            status_color = (0, 0, 255)
            detection_method = "YOLO+Color" if yolo_detected and color_detected else ("YOLO" if yolo_detected else "Color")
            status_text += f" ({detection_method})"
    elif final_detected and final_confidence > 60:
        status_text = f"Analyzing: {final_confidence:.0f}%"
        status_color = (0, 165, 255)
    else:
        status_text = "Scanning - No Fire"
        status_color = (0, 255, 0)
        final_detected = False
        final_confidence = 0
    
    cv2.putText(overlay_frame, status_text, (10, 30),
               cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
    cv2.putText(overlay_frame, f"Drone: {drone_status}", (w - 200, 30),
               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # Draw fire source box prominently when detected
    if final_detected and fire_source_box is not None:
        x1, y1, x2, y2 = fire_source_box
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        
        # Draw thick red box for fire source
        cv2.rectangle(overlay_frame, (x1, y1), (x2, y2), (0, 0, 255), 4)
        
        # Draw large glowing crosshair pointer at center
        for radius in [50, 40, 30]:
            cv2.circle(overlay_frame, (cx, cy), radius, (0, 255, 255), 1)
        
        cv2.circle(overlay_frame, (cx, cy), 12, (0, 255, 255), 3)
        cv2.circle(overlay_frame, (cx, cy), 6, (0, 0, 255), -1)
        
        # Long crosshair lines with arrowheads
        arrow_len = 60
        cv2.line(overlay_frame, (cx - arrow_len, cy), (cx + arrow_len, cy), (0, 255, 255), 2)
        cv2.line(overlay_frame, (cx, cy - arrow_len), (cx, cy + arrow_len), (0, 255, 255), 2)
        
        # Arrow tips
        for dx, dy in [(arrow_len, 0), (-arrow_len, 0), (0, arrow_len), (0, -arrow_len)]:
            tip_x = cx + dx
            tip_y = cy + dy
            cv2.circle(overlay_frame, (tip_x, tip_y), 3, (0, 255, 0), -1)
        
        # Add coordinates label (large, clear)
        cv2.rectangle(overlay_frame, (x1 - 5, y1 - 50), (x2 + 5, y1 - 10), (0, 0, 0), -1)
        cv2.putText(overlay_frame, f"FIRE DETECTED", (x1, y1 - 35),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        
        # Exact fire center coordinates (for fire brigade dispatch)
        cv2.rectangle(overlay_frame, (cx - 100, cy - 40), (cx + 100, cy - 5), (0, 0, 128), -1)
        cv2.putText(overlay_frame, f"X:{cx} Y:{cy}", (cx - 90, cy - 20),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    # Draw smoke detection area
    if smoke_detected and smoke_confidence > 30:
        sx, sy = smoke_location
        # Draw smoke indicator
        cv2.circle(overlay_frame, (sx, sy), 15, (200, 200, 200), 3)
        cv2.circle(overlay_frame, (sx, sy), 10, (180, 180, 180), 2)
        cv2.putText(overlay_frame, f"SMOKE: {smoke_confidence:.0f}% at ({sx},{sy})", 
                   (sx - 80, sy - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)

    # Draw alert box with grid lines if person in fire
    if person_in_fire and final_detected:
        cv2.rectangle(overlay_frame, (0, 0), (w, h), (0, 0, 255), 5)
        for i in range(5):
            y_line = (h // 5) * i
            cv2.line(overlay_frame, (0, y_line), (w, y_line), (0, 0, 128), 1)
            x_line = (w // 5) * i
            cv2.line(overlay_frame, (x_line, 0), (x_line, h), (0, 0, 128), 1)

    # Keep best source for the hazard box
    if final_detected:
        if fire_source_box is not None:
            fire_source_box = fire_source_box
        else:
            fire_source_box = (
                max(0, int(final_location[0] - 20)),
                max(0, int(final_location[1] - 20)),
                min(w, int(final_location[0] + 20)),
                min(h, int(final_location[1] + 20))
            )
    else:
        fire_source_box = None

    # Multi-point fire sources for tactical graph
    latest_fire_boxes = []
    if yolo_boxes:
        for b in yolo_boxes:
            latest_fire_boxes.append({
                "box": b["box"],
                "confidence": float(b["confidence"]),
            })
    elif final_detected and fire_source_box is not None:
        latest_fire_boxes.append({
            "box": fire_source_box,
            "confidence": float(final_confidence),
        })

    if final_detected and final_location and final_location != (0, 0):
        fire_center_history.append((int(final_location[0]), int(final_location[1])))

    # Store previous frame
    previous_frame = frame.copy()

    try:
        build_scene_state(frame, final_detected, final_confidence, final_location)
    except Exception as e:
        app.logger.debug(f"build_scene_state: {e}")

    # Draw the computed shortest path to fire and rescue path on the final overlay.
    try:
        overlay_frame = draw_shortest_path_overlay(
            overlay_frame,
            last_scene_state.get('shortest_path', []),
            grid_size=20,
            color=(0, 255, 255),
            start_label='DRONE',
            end_label='FIRE',
            end_color=(0, 0, 255),
        )
        overlay_frame = draw_shortest_path_overlay(
            overlay_frame,
            last_scene_state.get('rescue_path', []),
            grid_size=20,
            color=(0, 255, 0),
            start_label='DRONE',
            end_label='PERSON',
            end_color=(0, 128, 255),
        )
        overlay_frame = draw_entry_direction_overlay(overlay_frame, last_scene_state)
        overlay_frame = draw_structures_overlay(overlay_frame, last_scene_state.get('structures', []))
    except Exception as e:
        app.logger.debug(f"draw_shortest_path_overlay: {e}")

    # Save debug image when hazard confirmed for offline inspection
    try:
        if final_detected and final_confidence >= 50:
            debug_dir = os.path.join(os.getcwd(), 'detection_debug')
            os.makedirs(debug_dir, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            label = detection_method if detection_method is not None else 'HAZARD'
            fname = f"{ts}_{label}_{int(final_confidence)}.jpg"
            path = os.path.join(debug_dir, fname)
            cv2.imwrite(path, overlay_frame)
            app.logger.info(f"Saved debug detection image: {path}")
            
            # Log detection to database for fire brigade historical records
            if final_confidence >= 65:  # Only save for high-confidence detections
                log_fire_detection(final_confidence, final_location, fire_source_box, 
                                 len(people_detected), people_in_fire)
    except Exception as e:
        app.logger.debug(f"Debug save failed: {e}")

    return final_detected, final_confidence, final_location, overlay_frame

def detect_fire_simple(frame):
    """
    Enhanced fire detection wrapper with hybrid approach
    Returns: fire_detected, confidence, location, processed_frame
    """
    return detect_fire_hybrid(frame, previous_frame)

# ========== ESP32 COMMUNICATION ==========
def send_command_to_esp32(command):
    try:
        # Simulation mode
        time.sleep(0.2)
        responses = {
            "TAKE_OFF": "Drone taking off",
            "LAND": "Drone landing",
            "EMERGENCY_STOP": "Emergency stop activated",
            "MOVE_TO_FIRE": "Moving to fire location",
            "RETURN_HOME": "Returning to home",
            "IDLE": "Entering idle mode"
        }
        return True, responses.get(command, f"Command '{command}' executed")
    except Exception as e:
        return False, str(e)

# ========== VIDEO CAPTURE THREAD ==========
def video_capture_thread():
    global current_frame, detection_status, fire_detected, fire_confidence, fire_location, drone_status, previous_frame
    
    app.logger.info("Starting video capture...")
    cap = cv2.VideoCapture(0)  # Always use webcam for simplicity
    
    if not cap.isOpened():
        app.logger.error("Cannot open webcam")
        while True:
            test_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(test_frame, "WEBCAM NOT FOUND", (150, 240),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.putText(test_frame, "Connect webcam and restart", (100, 280),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            with frame_lock:
                current_frame = test_frame
            time.sleep(0.033)
        return
    
    # Set camera properties
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    
    app.logger.info("Webcam started successfully")
    
    frame_count = 0
    last_detection_time = 0
    consecutive_fires = 0
    
    while True:
        try:
            ret, frame = cap.read()
            frame_count += 1
            
            if not ret:
                app.logger.warning("Frame read failed")
                time.sleep(0.1)
                continue
            
            # Mirror frame (webcam is usually mirrored)
            frame = cv2.flip(frame, 1)
            
            # Run detection
            current_time = time.time()
            if current_time - last_detection_time > DETECTION_INTERVAL:
                fire_detected, confidence, location, processed_frame = detect_fire_simple(frame)
                
                # Adjusted tracking for better fire detection (including small flames)
                if fire_detected and confidence > 55:  # Lowered from 75% to 55%
                    consecutive_fires = min(consecutive_fires + 1, 5)
                    fire_confidence = confidence
                    fire_location = location
                    detection_status = f"🔥 FIRE: {confidence:.0f}%"
                    
                    # Send command after 3 consecutive detections (was 4)
                    if consecutive_fires >= 3 and current_time - last_detection_time > 5:
                        app.logger.info(f"Confirmed fire! Confidence: {confidence:.0f}%")
                        success, msg = send_command_to_esp32("MOVE_TO_FIRE")
                        if success:
                            drone_status = "MOVING TO FIRE"
                            app.logger.info("Command sent to ESP32")
                elif fire_detected and confidence > 40:
                    # Medium confidence - keep tracking
                    consecutive_fires = max(0, consecutive_fires - 0.2)
                    fire_confidence = confidence
                    fire_location = location
                    detection_status = f"Analyzing: {confidence:.0f}%"
                elif fire_detected and confidence > 25:
                    # Low but detected - still report
                    consecutive_fires = max(0, consecutive_fires - 0.5)
                    fire_confidence = confidence
                    fire_location = location
                    detection_status = f"Checking: {confidence:.0f}%"
                else:
                    # No fire detected
                    consecutive_fires = max(0, consecutive_fires - 1)
                    detection_status = "No fire"
                    fire_confidence = 0
                    fire_detected = False
                
                last_detection_time = current_time
                
                with frame_lock:
                    current_frame = processed_frame
            
            # Log occasionally
            if frame_count % 100 == 0:
                app.logger.debug(f"Frame {frame_count}: {detection_status}")
            
            time.sleep(0.01)
            
        except Exception as e:
            app.logger.error(f"Video error: {e}")
            time.sleep(1)
    
    cap.release()

# ========== FLASK ROUTES ==========
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    def generate():
        while True:
            with frame_lock:
                if current_frame is None:
                    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(placeholder, "STARTING...", (220, 240),
                               cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    ret, jpeg = cv2.imencode('.jpg', placeholder)
                else:
                    ret, jpeg = cv2.imencode('.jpg', current_frame)
            
            if ret:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + 
                       jpeg.tobytes() + b'\r\n')
            
            time.sleep(0.033)
    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

def serialize_people_detected(people):
    out = []
    for p in people or []:
        b = p.get('box', (0, 0, 0, 0))
        out.append({
            'box': [int(x) for x in b],
            'confidence': float(p.get('confidence', 0)),
        })
    return out


@app.route('/status')
def get_status():
    fb = fire_source_box
    if fb is not None:
        fb = [int(fb[0]), int(fb[1]), int(fb[2]), int(fb[3])]
    payload = {
        'detection_status': detection_status,
        'fire_detected': fire_detected,
        'fire_confidence': fire_confidence,
        'drone_status': drone_status,
        'fire_location': list(fire_location) if fire_detected else [0, 0],
        'fire_source_box': fb if fire_detected and fire_source_box is not None else None,
        'people_detected': serialize_people_detected(people_detected),
        'person_in_fire': people_in_fire,
        'person_model': people_detection_backend,
        'system_online': True,
        'timestamp': time.time(),
    }
    if last_scene_state:
        payload['spread'] = last_scene_state.get('spread')
        payload['priorities'] = last_scene_state.get('priorities')
        payload['scene_graph'] = last_scene_state.get('scene_graph')
        payload['people_count'] = last_scene_state.get('people_count')
        payload['smoke_active'] = last_scene_state.get('smoke_active')
        payload['smoke_confidence'] = last_scene_state.get('smoke_confidence')
        payload['frame_width'] = last_scene_state.get('frame_width')
        payload['frame_height'] = last_scene_state.get('frame_height')
        payload['area_border'] = last_scene_state.get('area_border')
        payload['entry_points'] = last_scene_state.get('entry_points')
        payload['best_entry'] = last_scene_state.get('best_entry')
        payload['safe_path'] = last_scene_state.get('safe_path')
        payload['fire_side'] = last_scene_state.get('fire_side')
        payload['person_zone'] = last_scene_state.get('person_zone')
        payload['fire_type'] = last_scene_state.get('fire_type')
        payload['fire_spread'] = last_scene_state.get('fire_spread')
        payload['trapped_persons'] = last_scene_state.get('trapped_persons')
        payload['shortest_path'] = last_scene_state.get('shortest_path')
        payload['structures'] = last_scene_state.get('structures')
        payload['ai_command'] = last_scene_state.get('ai_command')
    return jsonify(payload)

@app.route('/ai-data')
def get_ai_data():
    if not last_scene_state:
        return jsonify({'error': 'Scene data unavailable'}), 404
    return jsonify({
        'best_entry': last_scene_state.get('best_entry'),
        'safe_path': last_scene_state.get('safe_path'),
        'fire_side': last_scene_state.get('fire_side'),
        'fire_type': last_scene_state.get('fire_type'),
        'fire_spread': last_scene_state.get('fire_spread'),
        'people_count': last_scene_state.get('people_count'),
        'trapped_persons': last_scene_state.get('trapped_persons'),
        'shortest_path': last_scene_state.get('shortest_path'),
        'structures': last_scene_state.get('structures'),
        'ai_command': last_scene_state.get('ai_command'),
        'timestamp': time.time(),
    })

@app.route('/api/detection_history', methods=['GET'])
def get_detection_history():
    """Get historical fire detection records for fire brigade"""
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    # Return most recent detections first
    recent_detections = detection_database[-limit-offset:-offset if offset else None]
    recent_detections.reverse()
    
    return jsonify({
        'total_detections': len(detection_database),
        'returned': len(recent_detections),
        'detections': recent_detections,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/detection_history/clear', methods=['POST'])
def clear_detection_history():
    """Clear detection history (admin only)"""
    global detection_database
    detection_database = []
    save_detection_database()
    
    return jsonify({
        'success': True,
        'message': 'Detection history cleared'
    })

@app.route('/api/detection_history/download', methods=['GET'])
def download_detection_history():
    """Download detection history as JSON for fire brigade records"""
    try:
        return jsonify({
            'timestamp': datetime.now().isoformat(),
            'total_records': len(detection_database),
            'detections': detection_database
        }), 200, {
            'Content-Disposition': 'attachment;filename=fire_detections.json'
        }
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/command/<action>', methods=['POST'])
def send_command(action):
    global drone_status
    
    valid_commands = ['TAKE_OFF', 'LAND', 'EMERGENCY_STOP', 
                      'MOVE_TO_FIRE', 'RETURN_HOME', 'IDLE']
    
    if action not in valid_commands:
        return jsonify({'error': 'Invalid command'}), 400
    
    try:
        success, message = send_command_to_esp32(action)
        
        if success:
            drone_status = action.replace('_', ' ').title()
            return jsonify({
                'success': True,
                'message': message,
                'drone_status': drone_status
            })
        else:
            return jsonify({'error': message}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Test endpoints
@app.route('/test_detection', methods=['POST'])
def test_detection():
    global fire_detected, fire_confidence, detection_status
    fire_detected = True
    fire_confidence = 85
    detection_status = "TEST FIRE"
    return jsonify({
        'success': True,
        'message': 'Test detection activated',
        'confidence': fire_confidence
    })

@app.route('/reset_detection', methods=['POST'])
def reset_detection():
    global fire_detected, fire_confidence, detection_status
    fire_detected = False
    fire_confidence = 0
    detection_status = "Scanning"
    return jsonify({
        'success': True,
        'message': 'Detection reset'
    })

# ========== NEW: DEVICE LINK MANAGEMENT ==========
@app.route('/api/generate_link', methods=['POST'])
def generate_link():
    """Generate a unique link for remote device access"""
    try:
        link_code, device_id = generate_device_link()
        return jsonify({
            'success': True,
            'link': link_code,
            'device_id': device_id,
            'access_url': f"http://{request.host}/device/{link_code}",
            'expires_in_seconds': LINK_EXPIRY_TIME
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Get list of all connected devices"""
    devices_list = []
    for device_id, device in connected_devices.items():
        devices_list.append({
            'device_id': device_id,
            'link': device['link'],
            'device_name': device['device_name'],
            'ip_address': device['ip_address'],
            'last_heartbeat': device['last_heartbeat'].isoformat(),
            'gps_data': device['gps_data'],
            'camera_active': device['camera_data'] is not None
        })
    
    return jsonify({
        'devices': devices_list,
        'total_devices': len(devices_list)
    })

@app.route('/api/test-frame')
def test_frame():
    """Test endpoint to send a test frame to dashboard (for debugging)"""
    print("[TEST] Sending test frame to all clients...")
    # Create a simple test image
    test_img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(test_img, "TEST FRAME", (150, 240),
               cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 255, 0), 3)
    
    ret, jpeg = cv2.imencode('.jpg', test_img)
    frame_data = 'data:image/jpeg;base64,' + str(base64.b64encode(jpeg.tobytes()).decode('utf-8'))
    
    # Use socketio.emit() from server context
    socketio.emit('device_camera', {
        'device_id': 'test-device',
        'device_name': 'TEST FRAME',
        'frame_data': frame_data,
        'timestamp': datetime.now().isoformat()
    }, namespace='/')
    
    print(f"[TEST] ✓ Test frame sent to all clients ({len(frame_data)/1024:.1f}KB)")
    return jsonify({'message': 'Test frame sent to all connected clients'})

@app.route('/device/<link_code>')
def device_interface(link_code):
    """Remote device interface - serves camera/GPS client"""
    device_id = validate_device_link(link_code)
    if not device_id:
        return render_template('device_error.html', message="Invalid or expired link")
    
    session['device_id'] = device_id
    session['link_code'] = link_code
    return render_template('device.html', link_code=link_code)

@app.route('/api/device_status/<link_code>')
def device_status(link_code):
    """Get current status of device"""
    device_id = validate_device_link(link_code)
    if not device_id:
        return jsonify({'error': 'Invalid link'}), 401
    
    device = connected_devices[device_id]
    return jsonify({
        'device_id': device_id,
        'device_name': device['device_name'],
        'gps_data': device['gps_data'],
        'camera_active': device['camera_data'] is not None,
        'fire_detected': fire_detected,
        'fire_confidence': fire_confidence,
        'fire_location': fire_location if fire_detected else None
    })


def _enqueue_remote_frame_for_detection(base64_str, device_id, device_name):
    """Keep only the newest frame — YOLO/hybrid work stays off the Socket.IO hot path."""
    item = (base64_str, device_id, device_name)
    try:
        remote_detection_queue.put_nowait(item)
    except queue.Full:
        try:
            remote_detection_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            remote_detection_queue.put_nowait(item)
        except queue.Full:
            pass


def remote_detection_worker():
    """Decode + run hybrid detection; emit tactical refresh when done (video already live)."""
    global fire_detected, fire_confidence, fire_location, detection_status
    while True:
        try:
            base64_str, device_id, dev_name = remote_detection_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            frame_bytes = base64.b64decode(base64_str)
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None or frame.shape[0] == 0:
                continue
            fd, fc, floc, _ = detect_fire_simple(frame)
            fire_detected = bool(fd)
            fire_confidence = float(fc)
            fire_location = (int(floc[0]), int(floc[1])) if floc else (0, 0)
            if fire_detected:
                if latest_smoke_detected and latest_smoke_confidence > 35:
                    detection_status = f"🔥💨 HAZARD — {dev_name}"
                else:
                    detection_status = f"🔥 FIRE — {dev_name}"
            else:
                fire_confidence = 0
                fire_location = (0, 0)
                detection_status = f"Scanning — {dev_name}"
        except Exception as e:
            app.logger.debug("Remote detection: %s", e)
            continue

        try:
            pl = {'device_id': device_id, 'timestamp': datetime.now().isoformat()}
            if last_scene_state:
                pl['thermal_frame_data'] = last_scene_state.get('thermal_frame_data')
                pl['scene'] = {
                    'spread': last_scene_state.get('spread'),
                    'priorities': last_scene_state.get('priorities'),
                    'scene_graph': last_scene_state.get('scene_graph'),
                    'people_count': last_scene_state.get('people_count'),
                    'person_in_fire': last_scene_state.get('person_in_fire'),
                    'person_model': last_scene_state.get('person_model'),
                    'smoke_active': last_scene_state.get('smoke_active'),
                    'smoke_confidence': last_scene_state.get('smoke_confidence'),
                    'frame_width': last_scene_state.get('frame_width'),
                    'frame_height': last_scene_state.get('frame_height'),
                }
            with app.app_context():
                socketio.emit('scene_update', pl, namespace='/')
        except Exception as e:
            app.logger.debug("scene_update: %s", e)


# ========== NEW: SOCKETIO HANDLERS FOR REAL-TIME DATA ==========
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    app.logger.info(f'✅ Client connected: {request.sid}')
    print(f"[CONNECT] Client {request.sid} connected")
    emit('response', {'data': 'Connected to server'})

@socketio.on('join_dashboard')
def handle_join_dashboard():
    """Join dashboard to receive device frames"""
    join_room('dashboard')
    app.logger.info(f'✅ Client {request.sid} joined dashboard room')
    print(f"[DASHBOARD] Client {request.sid} joined dashboard room")
    emit('status', {'message': 'Joined dashboard room', 'sid': request.sid})

@socketio.on('register_device')
def handle_register_device(data):
    """Register a device with GPS and camera capabilities"""
    link_code = data.get('link_code')
    device_name = data.get('device_name', 'Mobile Device')
    
    device_id = validate_device_link(link_code)
    if not device_id:
        print(f"[REGISTER] ❌ Invalid link: {link_code}")
        emit('error', {'message': 'Invalid link'})
        return
    
    device = connected_devices[device_id]
    device['device_name'] = device_name
    device['ip_address'] = request.remote_addr
    device['last_heartbeat'] = datetime.now()
    
    join_room(device_id)
    emit('registered', {
        'device_id': device_id,
        'message': f'Device {device_name} registered successfully'
    })
    
    print(f"[REGISTER] ✅ Device '{device_name}' registered (ID: {device_id[:8]}...)")
    app.logger.info(f'Device {device_name} registered with ID: {device_id}')

@socketio.on('send_gps')
def handle_gps_data(data):
    """Receive GPS data from remote device"""
    device_id = data.get('device_id')
    link_code = data.get('link_code')
    
    if not validate_device_link(link_code):
        emit('error', {'message': 'Invalid link'})
        return
    
    if device_id in connected_devices:
        device = connected_devices[device_id]
        device['gps_data'] = {
            'latitude': data.get('latitude'),
            'longitude': data.get('longitude'),
            'accuracy': data.get('accuracy'),
            'timestamp': datetime.now().isoformat()
        }
        device['last_heartbeat'] = datetime.now()
        
        # Broadcast to all clients (Flask-SocketIO 5+: no broadcast= kwarg)
        socketio.emit('device_update', {
            'device_id': device_id,
            'gps_data': device['gps_data']
        }, namespace='/')

@socketio.on('send_camera_frame')
def handle_camera_frame(data):
    """Receive camera frame — broadcast video immediately; detection runs in background."""
    device_id = data.get('device_id')
    link_code = data.get('link_code')

    validated_device_id = validate_device_link(link_code)
    if not validated_device_id:
        return

    if device_id not in connected_devices:
        return

    device = connected_devices[device_id]
    frame_data = data.get('frame_data')
    device['camera_data'] = frame_data
    device['last_heartbeat'] = datetime.now()

    if isinstance(frame_data, str) and frame_data.startswith('data:image'):
        base64_str = frame_data.split(',', 1)[1]
    else:
        base64_str = frame_data

    # 1) Low-latency video: broadcast first (stale tactical/thermal until worker catches up)
    try:
        payload = {
            'device_id': device_id,
            'device_name': device.get('device_name', 'Unknown'),
            'frame_data': frame_data,
            'timestamp': datetime.now().isoformat(),
        }
        if last_scene_state:
            payload['thermal_frame_data'] = last_scene_state.get('thermal_frame_data')
            payload['scene'] = {
                'spread': last_scene_state.get('spread'),
                'priorities': last_scene_state.get('priorities'),
                'scene_graph': last_scene_state.get('scene_graph'),
                'people_count': last_scene_state.get('people_count'),
                'person_in_fire': last_scene_state.get('person_in_fire'),
                'person_model': last_scene_state.get('person_model'),
                'smoke_active': last_scene_state.get('smoke_active'),
                'smoke_confidence': last_scene_state.get('smoke_confidence'),
                'frame_width': last_scene_state.get('frame_width'),
                'frame_height': last_scene_state.get('frame_height'),
            }
        socketio.emit('device_camera', payload, namespace='/')
    except Exception as e:
        app.logger.error("device_camera emit failed: %s", e)

    # 2) Heavy detection off the hot path (queue size 1 = always newest frame)
    _enqueue_remote_frame_for_detection(base64_str, device_id, device.get('device_name', 'Unknown'))

    _remote_frame_log_counter[0] += 1
    if _remote_frame_log_counter[0] % 120 == 0:
        app.logger.info(
            "Remote stream frames in: %d (~%.0f KB last)",
            _remote_frame_log_counter[0],
            len(frame_data) / 1024 if frame_data else 0,
        )

@socketio.on('request_fire_status')
def handle_fire_status_request(data):
    """Send current fire detection status to device"""
    device_id = data.get('device_id')
    link_code = data.get('link_code')
    
    if not validate_device_link(link_code):
        return
    
    emit('fire_status', {
        'fire_detected': fire_detected,
        'fire_confidence': fire_confidence,
        'fire_location': fire_location if fire_detected else None,
        'drone_status': drone_status,
        'timestamp': datetime.now().isoformat()
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnect"""
    app.logger.info(f'Client disconnected: {request.sid}')

# ========== MAIN ==========
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    app.logger.info("=" * 60)
    app.logger.info("🔥 FIRE DETECTION DRONE SYSTEM WITH REMOTE DEVICE SUPPORT 🔥")
    app.logger.info("=" * 60)
    
    # Start video thread only if server webcam is enabled
    if USE_WEBCAM:
        video_thread = threading.Thread(target=video_capture_thread, daemon=True)
        video_thread.start()
        app.logger.info("Webcam capture thread started")
    else:
        app.logger.info("Webcam disabled (USE_WEBCAM=False). Using remote device streams only.")
    
    # Start device link cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_expired_links, daemon=True)
    cleanup_thread.start()

    remote_det_thread = threading.Thread(target=remote_detection_worker, daemon=True, name='remote-detection')
    remote_det_thread.start()
    app.logger.info("Remote detection worker started (stream latency optimized)")

    time.sleep(2)
    
    app.logger.info(f"ESP32 IP: {ESP32_IP}")
    if USE_WEBCAM:
        app.logger.info("Using webcam for video")
    else:
        app.logger.info("Server webcam disabled; expecting remote device streams")
    
    # Get port from environment or default to 5000
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    
    app.logger.info(f"Server: http://{host}:{port}")
    app.logger.info("Remote Device Support: Enabled")
    app.logger.info("=" * 60)
    
    # Run on all network interfaces with SocketIO
    socketio.run(
        app,
        host=host,
        port=port,
        debug=False,
        use_reloader=False,
        log_output=True
    )