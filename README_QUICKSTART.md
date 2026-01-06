# 🔥 Fire Detection System — Quick Start

## Install Dependencies

```powershell
python -m pip install -r requirements.txt
```

## Configure Environment

Copy `.env.example` to `.env` and set key variables:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

Edit `.env` with your settings:
- `USE_WEBCAM=True` — use local webcam  
- `FIRE_CONFIDENCE_THRESHOLD=60` — detection confidence (0-100, default 60%)  
- `DETECTION_INTERVAL=0.3` — frames per second interval
- `IGNORE_FACE_REGIONS=True` — mask faces to reduce false positives  
- `MODEL_PATH=` — (optional) path to custom YOLOv8 weights

## Run Server

```powershell
# Navigate to project folder
cd fire-drone-system

# Start the server
python app.py
```

Expected output:
```
========== FIRE DETECTION DRONE SYSTEM ==========
Server: http://0.0.0.0:5000
Webcam: Enabled
YOLO: Loaded (if available)
```

## Access Dashboard

- **Main UI**: http://localhost:5000
- **Live Video Stream**: http://localhost:5000/video_feed

## Remote Mobile Device Support

Generate a temporary device link for smartphone/tablet camera:

```powershell
curl -X POST http://localhost:5000/api/generate_link
```

Response example:
```json
{
  "link": "ABC123",
  "access_url": "http://192.168.1.X:5000/device/ABC123"
}
```

Visit the `access_url` from your phone browser to stream camera + GPS data.

## Test Detection

```powershell
# Trigger test fire alert
curl -X POST http://localhost:5000/test_detection

# Reset detection state
curl -X POST http://localhost:5000/reset_detection
```

## Detection Tuning

Adjust `.env` values to balance false positives vs sensitivity:

| Setting | Effect |
|---------|--------|
| `FIRE_CONFIDENCE_THRESHOLD=50` | Lower = more sensitive (more false positives) |
| `FIRE_CONFIDENCE_THRESHOLD=80` | Higher = stricter (may miss weak flames) |
| `IGNORE_FACE_REGIONS=False` | Disable face masking if not needed |

## What Gets Detected

- **Color-based**: Red, orange, yellow flames
- **Motion**: Flickering/moving fire (less static false positives)  
- **Brightness**: Bright, hot regions
- **YOLO (if available)**: Standard object classes + fire-like patterns

## Logs

Server logs appear in console. Fire detections are printed:
```
[DETECTION] 🔥 FIRE DETECTED on device_name! Confidence: 85.2%
```

Frames with detections saved to `detection_debug/` folder for review.

---

**Need help?** Check the main [README.md](README.md) for full documentation.
