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
from flask import Flask, render_template, Response, jsonify, request, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import logging
import queue
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
    logger=True,
    engineio_logger=False
)
app.logger.setLevel(logging.INFO)

# ========== CONFIGURATION ==========
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
drone_status = "IDLE"
model = None
frame_queue = queue.Queue(maxsize=10)

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

# Load YOLO fire detection model
fire_model = None
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

        for path in candidates:
            try:
                fire_model = YOLO(path)
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

def detect_fire_yolo(frame):
    """
    Advanced fire detection using YOLOv8 model with fire-specific analysis
    """
    global fire_model
    
    if fire_model is None:
        return False, 0, (0, 0), None
    
    try:
        # Run YOLO inference with optimized settings (use configured confidence)
        results = fire_model(frame, conf=CONFIDENCE_THRESHOLD, iou=0.45, imgsz=640, verbose=False)
        
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
                            fire_detected = True
                    elif previous_frame is None:
                        # First frame - allow but with lower confidence later
                        if area > max_area:
                            max_area = area
                            best_contour = contour
                            best_roi_stats = (x, y, w_box, h_box)
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

    return fire_detected, confidence, location, overlay_frame

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
    global previous_frame, fire_model, detection_history
    
    # Use provided frame or global previous_frame
    if prev_frame is None:
        prev_frame = previous_frame
    
    h, w = frame.shape[:2]
    overlay_frame = frame.copy()
    
    # Method 1: YOLO Model Detection (if available)
    yolo_detected = False
    yolo_confidence = 0
    yolo_location = (0, 0)
    yolo_boxes = []
    
    if fire_model is not None:
        yolo_detected, yolo_confidence, yolo_location, yolo_boxes = detect_fire_yolo(frame)
    
    # Method 2: Enhanced Color-based Detection
    color_detected, color_confidence, color_location, color_frame = detect_fire_color_based(frame, prev_frame)
    
    # Method 3: Simple color+motion-based smoke detection
    smoke_detected, smoke_confidence, smoke_location, smoke_frame = detect_smoke_color_based(frame, prev_frame) if 'detect_smoke_color_based' in globals() else (False, 0, (0,0), frame)
    
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
    
    # Store previous frame
    previous_frame = frame.copy()

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

@app.route('/status')
def get_status():
    return jsonify({
        'detection_status': detection_status,
        'fire_detected': fire_detected,
        'fire_confidence': fire_confidence,
        'drone_status': drone_status,
        'fire_location': fire_location if fire_detected else (0, 0),
        'system_online': True,
        'timestamp': time.time()
    })

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
        
        # Broadcast to all clients
        socketio.emit('device_update', {
            'device_id': device_id,
            'gps_data': device['gps_data']
        }, broadcast=True)

@socketio.on('send_camera_frame')
def handle_camera_frame(data):
    """Receive camera frame from remote device"""
    global fire_detected, fire_confidence, fire_location, detection_status
    
    device_id = data.get('device_id')
    link_code = data.get('link_code')
    
    validated_device_id = validate_device_link(link_code)
    if not validated_device_id:
        print(f"[FRAME] ❌ Invalid link_code: {link_code}")
        return
    
    if device_id in connected_devices:
        device = connected_devices[device_id]
        frame_data = data.get('frame_data')
        device['camera_data'] = frame_data
        device['last_heartbeat'] = datetime.now()
        
        frame_size_kb = len(frame_data) / 1024 if frame_data else 0
        print(f"[FRAME] 📥 Received {frame_size_kb:.1f}KB from {device.get('device_name')} ({device_id[:8]}...)")
        
        # Decode and process frame for fire detection
        try:
            # Remove data URL prefix and decode base64
            if isinstance(frame_data, str) and frame_data.startswith('data:image'):
                base64_str = frame_data.split(',')[1]
            else:
                base64_str = frame_data
            
            frame_bytes = base64.b64decode(base64_str)
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if frame is not None and frame.shape[0] > 0:
                # Run professional fire and smoke detection
                fire_detected_result, fire_conf, fire_loc, _ = detect_fire_simple(frame)
                smoke_detected_result, smoke_conf, smoke_loc, _ = detect_smoke_color_based(frame)
                
                # ULTRA STRICT PROFESSIONAL THRESHOLDS - eliminate false positives
                # Require VERY high confidence AND multiple confirmations
                final_fire_detected = False
                final_confidence = 0
                final_location = (0, 0)
                detection_type = ""
                
                # Fire detection - require 80%+ confidence to eliminate false positives
                if fire_detected_result and fire_conf > 80:
                    final_fire_detected = True
                    final_confidence = fire_conf
                    final_location = fire_loc
                    detection_type = "FIRE"
                    print(f"[DETECTION] 🔥 FIRE DETECTED on {device.get('device_name')}! Confidence: {fire_conf:.1f}%")
                elif fire_detected_result and fire_conf > 75:
                    # Very high confidence - check for smoke confirmation
                    if smoke_detected_result and smoke_conf > 50:
                        final_fire_detected = True
                        final_confidence = min(100, fire_conf + smoke_conf * 0.2)
                        final_location = fire_loc
                        detection_type = "FIRE+SMOKE"
                        print(f"[DETECTION] 🔥 FIRE+SMOKE detected! Fire: {fire_conf:.1f}%, Smoke: {smoke_conf:.1f}%")
                    else:
                        # No smoke - still high confidence fire
                        final_fire_detected = True
                        final_confidence = fire_conf
                        final_location = fire_loc
                        detection_type = "FIRE"
                        print(f"[DETECTION] 🔥 FIRE DETECTED (high confidence): {fire_conf:.1f}%")
                elif fire_detected_result and fire_conf > 70:
                    # High confidence - require smoke confirmation
                    if smoke_detected_result and smoke_conf > 45:
                        final_fire_detected = True
                        final_confidence = min(100, fire_conf * 0.9 + smoke_conf * 0.2)
                        final_location = fire_loc
                        detection_type = "FIRE+SMOKE"
                        print(f"[DETECTION] 🔥 FIRE+SMOKE (confirmed): Fire: {fire_conf:.1f}%, Smoke: {smoke_conf:.1f}%")
                    else:
                        # No smoke confirmation - reject to avoid false positive
                        print(f"[DETECTION] ❌ Rejected: Fire {fire_conf:.1f}% but no smoke confirmation")
                        final_fire_detected = False
                
                # Smoke detection - require 60%+ confidence
                if not final_fire_detected and smoke_detected_result and smoke_conf > 60:
                    final_fire_detected = True
                    final_confidence = smoke_conf
                    final_location = smoke_loc
                    detection_type = "SMOKE"
                    print(f"[DETECTION] 💨 SMOKE DETECTED on {device.get('device_name')}! Confidence: {smoke_conf:.1f}%")
                
                # Update global status
                if final_fire_detected:
                    fire_detected = True
                    fire_confidence = final_confidence
                    fire_location = final_location
                    if detection_type == "FIRE":
                        detection_status = f"🔥 FIRE DETECTED - {device.get('device_name')}"
                    elif detection_type == "SMOKE":
                        detection_status = f"💨 SMOKE DETECTED - {device.get('device_name')}"
                    elif detection_type == "FIRE+SMOKE":
                        detection_status = f"🔥💨 FIRE+SMOKE - {device.get('device_name')}"
                    else:
                        detection_status = f"Analyzing... {final_confidence:.0f}%"
                else:
                    fire_detected = False
                    fire_confidence = 0
                    fire_location = (0, 0)
                    detection_status = "Monitoring..."
            
        except Exception as e:
            print(f"[FRAME] ⚠️ Error processing frame: {e}")
        
        # Broadcast camera frame to all clients
        try:
            emit('device_camera', {
                'device_id': device_id,
                'device_name': device.get('device_name', 'Unknown'),
                'frame_data': frame_data,
                'timestamp': datetime.now().isoformat()
            }, broadcast=True)
            
            print(f"[FRAME] 📤 ✓ Broadcasted frame to all clients")
        except Exception as e:
            print(f"[FRAME] ❌ Failed to broadcast: {e}")
            app.logger.error(f"Failed to emit device_camera: {e}")
    else:
        print(f"[FRAME] ❌ Device {device_id} not found")

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