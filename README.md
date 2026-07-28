# 🔥 Fire Detection Drone System - Remote Device Support

A comprehensive fire detection system with autonomous drone control and **remote device access** capabilities. Now you can access your drone's camera and GPS from any other device using a unique link!

## ✨ New Features

### Remote Device Access
- **Generate Unique Links**: Create shareable links to access camera and GPS from other devices
- **Real-time Streaming**: Live camera feed from the main dashboard or remote devices
- **GPS Tracking**: Track device location in real-time via WebSocket
- **Fire Detection Alerts**: All connected devices receive live fire detection updates


## 🚀 Installation & Setup

### Prerequisites
- Python 3.8+
- pip (Python package manager)
- Webcam (for fire detection)
- Modern web browser with camera/GPS support (for remote devices)



## 🔥 Fire Detection System

### Detection Features:
- **Color-based Detection**: Identifies fire by red/orange/yellow colors
- **Brightness Analysis**: Detects hot/bright areas
- **Real-time Alerts**: Immediate notification on all connected devices
- **Confidence Scoring**: Shows detection confidence percentage
- **Location Tracking**: Marks fire location on video


## 🎮 Drone Control Commands

| Command | Description |
|---------|------------|
| **TAKE OFF** | Drone takes flight |
| **LAND** | Drone lands safely |
| **MOVE TO FIRE** | Autonomous fire approach |
| **RETURN HOME** | Returns to starting position |
| **EMERGENCY STOP** | Immediate stop (safety) |
| **IDLE MODE** | Standby mode |

---




## 📝 Project Structure

```
fire-drone-system/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── static/
│   ├── script.js         # Dashboard JavaScript
│   └── style.css         # Styling
└── templates/
    ├── index.html        # Main dashboard
    ├── device.html       # Remote device interface
    └── device_error.html # Error page
