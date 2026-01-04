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
from flask import Flask, render_template, Response, jsonify, request, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import logging
import queue
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'drone-fire-detection-secret-key-2024')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
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
CONFIDENCE_THRESHOLD = 0.6
DETECTION_INTERVAL = 0.3

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

# ========== SIMPLE FIRE DETECTION (No YOLO download needed) ==========
def detect_fire_simple(frame):
    """
    Simple but effective fire detection using multiple techniques
    """
    h, w, _ = frame.shape
    overlay_frame = frame.copy()
    
    # Method 1: Color-based detection
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Fire color ranges (red/orange/yellow)
    lower_red1 = np.array([0, 100, 100])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 100, 100])
    upper_red2 = np.array([180, 255, 255])
    lower_orange = np.array([10, 100, 100])
    upper_orange = np.array([25, 255, 255])
    lower_yellow = np.array([25, 100, 100])
    upper_yellow = np.array([35, 255, 255])
    
    mask_red1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask_red2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask_orange = cv2.inRange(hsv, lower_orange, upper_orange)
    mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)
    
    fire_mask = cv2.bitwise_or(mask_red1, mask_red2)
    fire_mask = cv2.bitwise_or(fire_mask, mask_orange)
    fire_mask = cv2.bitwise_or(fire_mask, mask_yellow)
    
    # Clean up mask
    kernel = np.ones((5, 5), np.uint8)
    fire_mask = cv2.morphologyEx(fire_mask, cv2.MORPH_CLOSE, kernel)
    fire_mask = cv2.morphologyEx(fire_mask, cv2.MORPH_OPEN, kernel)
    
    # Method 2: Brightness detection (fire is bright)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, bright_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    
    # Combine masks
    combined_mask = cv2.bitwise_and(fire_mask, bright_mask)
    
    # If no bright fire, use just color mask
    if cv2.countNonZero(combined_mask) < 100:
        combined_mask = fire_mask
    
    # Find contours
    contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    fire_detected = False
    max_area = 0
    best_contour = None
    
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 300:  # Minimum size
            perimeter = cv2.arcLength(contour, True)
            if perimeter == 0:
                continue
            
            # Check shape (fire is not perfectly circular)
            circularity = 4 * np.pi * area / (perimeter * perimeter)
            if 0.2 < circularity < 0.9:  # Acceptable shape range
                if area > max_area:
                    max_area = area
                    best_contour = contour
                    fire_detected = True
    
    # Calculate confidence based on multiple factors
    confidence = 0
    location = (0, 0)
    
    if fire_detected and best_contour is not None:
        x, y, w_box, h_box = cv2.boundingRect(best_contour)
        location = (x + w_box//2, y + h_box//2)
        
        # Calculate confidence
        area_ratio = max_area / (w * h)
        
        # Extract ROI for analysis
        roi = frame[y:y+h_box, x:x+w_box]
        if roi.size > 0:
            roi_hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            
            # Check for fire-like properties
            avg_saturation = np.mean(roi_hsv[:,:,1])
            avg_value = np.mean(roi_hsv[:,:,2])
            std_hue = np.std(roi_hsv[:,:,0])
            
            # Confidence calculation
            color_score = min(avg_saturation / 255 * 40, 40)
            brightness_score = min(avg_value / 255 * 30, 30)
            size_score = min(area_ratio * 1000, 20)
            variation_score = min(std_hue / 50 * 10, 10)
            
            confidence = color_score + brightness_score + size_score + variation_score
            confidence = min(confidence, 100)
            
            # Draw visualization
            if confidence > 60:
                box_color = (0, 0, 255)  # Red for high confidence
                thickness = 3
            elif confidence > 40:
                box_color = (0, 165, 255)  # Orange for medium
                thickness = 2
            else:
                box_color = (0, 255, 255)  # Yellow for low
                thickness = 1
            
            cv2.rectangle(overlay_frame, (x, y), (x + w_box, y + h_box), box_color, thickness)
            cv2.circle(overlay_frame, location, 6, (0, 255, 255), -1)
            cv2.circle(overlay_frame, location, 3, (0, 0, 255), -1)
            
            # Add label
            label = f"Fire: {confidence:.0f}%"
            cv2.putText(overlay_frame, label, (x, y - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2)
            
            # Draw crosshair
            cv2.line(overlay_frame, (location[0], 0), (location[0], h), (255, 255, 0), 1)
            cv2.line(overlay_frame, (0, location[1]), (w, location[1]), (255, 255, 0), 1)
    
    # Add status bar
    cv2.rectangle(overlay_frame, (0, 0), (w, 50), (0, 0, 0), -1)
    
    if fire_detected and confidence > 60:
        status_text = f"FIRE DETECTED: {confidence:.0f}%"
        status_color = (0, 0, 255)
    elif fire_detected and confidence > 40:
        status_text = f"Checking: {confidence:.0f}%"
        status_color = (0, 165, 255)
    else:
        status_text = "Scanning - No Fire"
        status_color = (0, 255, 0)
    
    cv2.putText(overlay_frame, status_text, (10, 30),
               cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
    cv2.putText(overlay_frame, f"Drone: {drone_status}", (w - 200, 30),
               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    
    # Show detection mask in corner
    mask_display = cv2.cvtColor(combined_mask, cv2.COLOR_GRAY2BGR)
    mask_display = cv2.resize(mask_display, (w//6, h//6))
    overlay_frame[10:10+mask_display.shape[0], w-mask_display.shape[1]-10:w-10] = mask_display
    
    return fire_detected, confidence, location, overlay_frame

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
    global current_frame, detection_status, fire_detected, fire_confidence, fire_location, drone_status
    
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
                
                # Update tracking
                if fire_detected and confidence > 70:
                    consecutive_fires = min(consecutive_fires + 1, 5)
                    fire_confidence = confidence
                    fire_location = location
                    detection_status = f"FIRE: {confidence:.0f}%"
                    
                    # Only send command after 3 consecutive high-confidence detections
                    if consecutive_fires >= 3 and current_time - last_detection_time > 5:
                        app.logger.info(f"Confirmed fire! Confidence: {confidence:.0f}%")
                        success, msg = send_command_to_esp32("MOVE_TO_FIRE")
                        if success:
                            drone_status = "MOVING TO FIRE"
                            app.logger.info("Command sent to ESP32")
                else:
                    consecutive_fires = max(0, consecutive_fires - 1)
                    detection_status = "No fire"
                    fire_confidence = 0
                
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
    app.logger.info(f'Client connected: {request.sid}')
    emit('response', {'data': 'Connected to server'})

@socketio.on('register_device')
def handle_register_device(data):
    """Register a device with GPS and camera capabilities"""
    link_code = data.get('link_code')
    device_name = data.get('device_name', 'Mobile Device')
    
    device_id = validate_device_link(link_code)
    if not device_id:
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
    device_id = data.get('device_id')
    link_code = data.get('link_code')
    
    if not validate_device_link(link_code):
        return
    
    if device_id in connected_devices:
        device = connected_devices[device_id]
        frame_data = data.get('frame_data')
        device['camera_data'] = frame_data
        device['last_heartbeat'] = datetime.now()
        # Broadcast camera frame to connected dashboards (avoid heavy processing here)
        try:
            socketio.emit('device_camera', {
                'device_id': device_id,
                'device_name': device.get('device_name', 'Unknown'),
                'frame_data': frame_data
            }, broadcast=True, skip_sid=request.sid)  # Don't send back to device
            app.logger.debug(f"Broadcasted frame from device {device_id}")
        except Exception as e:
            app.logger.error(f"Failed to emit device_camera: {e}")

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
        debug=debug,
        allow_unsafe_werkzeug=True,
        use_reloader=False
    )