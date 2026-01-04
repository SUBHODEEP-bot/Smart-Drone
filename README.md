# 🔥 Fire Detection Drone System - Remote Device Support

A comprehensive fire detection system with autonomous drone control and **remote device access** capabilities. Now you can access your drone's camera and GPS from any other device using a unique link!

## ✨ New Features

### Remote Device Access
- **Generate Unique Links**: Create shareable links to access camera and GPS from other devices
- **Real-time Streaming**: Live camera feed from the main dashboard or remote devices
- **GPS Tracking**: Track device location in real-time via WebSocket
- **Fire Detection Alerts**: All connected devices receive live fire detection updates

### How It Works
1. **Generate Link** on main dashboard
2. **Share the 6-character code** with another device
3. **Open the link** on mobile phone or other device
4. **Enable Camera & GPS** on remote device
5. **Monitor in real-time** from main dashboard

---

## 🚀 Installation & Setup

### Prerequisites
- Python 3.8+
- pip (Python package manager)
- Webcam (for fire detection)
- Modern web browser with camera/GPS support (for remote devices)

### Step 1: Install Dependencies

```bash
cd fire-drone-system
pip install -r requirements.txt
```

### Step 2: Run the Application

```bash
python app.py
```

The system will start on `http://localhost:5000`

### Step 3: Access the Dashboard
- **Main Dashboard**: `http://localhost:5000` (on your computer)
- **Remote Device**: Use the generated link code

---

## 📱 Using Remote Device Access

### On Main Dashboard:
1. Click **"GENERATE LINK"** button in the Drone Controls section
2. A modal will show a 6-character code (e.g., `A3K9M2`)
3. Share this link with another device

### On Remote Device (Phone/Tablet):
1. Open the full URL in browser: `http://<your-computer-ip>:5000/device/<LINK-CODE>`
2. Or navigate to: `http://localhost:5000/device/A3K9M2` (if on same network)
3. Enter a **Device Name** (e.g., "Field Phone")
4. Click **"Register Device"**
5. Click **"Start Camera"** to enable camera streaming
6. Click **"Start GPS"** to enable location tracking

### Connected Devices Panel:
- View all active devices on the main dashboard
- See real-time GPS coordinates
- Monitor camera activity status
- Track last heartbeat/update time

---

## 🔥 Fire Detection System

### Detection Features:
- **Color-based Detection**: Identifies fire by red/orange/yellow colors
- **Brightness Analysis**: Detects hot/bright areas
- **Real-time Alerts**: Immediate notification on all connected devices
- **Confidence Scoring**: Shows detection confidence percentage
- **Location Tracking**: Marks fire location on video

### Sensitivity Controls:
Adjust detection sensitivity from 10% (low) to 90% (high) to reduce false positives.

---

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

## 🌐 Network Configuration

### Local Network Access:
If you want to access from other devices on the same WiFi:

```
Main Dashboard:  http://<YOUR_COMPUTER_IP>:5000
Remote Device:   http://<YOUR_COMPUTER_IP>:5000/device/<LINK_CODE>
```

Find your computer IP:
- **Windows**: `ipconfig` → Look for IPv4 Address (usually 192.168.x.x)
- **Mac/Linux**: `ifconfig` → Look for inet address

### For Internet Access (Deployment):
Use services like **Render**, **Railway**, **Heroku**, or your own server:

```
Main Dashboard:  https://your-domain.com
Remote Device:   https://your-domain.com/device/<LINK_CODE>
```

---

## 📡 API Endpoints

### Generate Device Link
```
POST /api/generate_link
Response: {
  "success": true,
  "link": "A3K9M2",
  "device_id": "uuid",
  "access_url": "http://...",
  "expires_in_seconds": 3600
}
```

### Get Connected Devices
```
GET /api/devices
Response: {
  "devices": [...],
  "total_devices": 2
}
```

### Device Status
```
GET /api/device_status/<link_code>
Response: {
  "device_id": "uuid",
  "device_name": "Mobile Phone",
  "gps_data": {...},
  "camera_active": true,
  "fire_detected": false
}
```

---

## 🔌 WebSocket Events

### Client → Server:
- `register_device`: Register a new device
- `send_gps`: Send GPS coordinates
- `send_camera_frame`: Send camera frame data
- `request_fire_status`: Request fire detection status

### Server → Client:
- `registered`: Device successfully registered
- `fire_status`: Fire detection update
- `device_update`: Device data update

---

## ⏱️ Link Management

### Link Expiry:
- **Default Duration**: 1 hour
- **Auto Cleanup**: Expired links are automatically removed
- **Generate New**: Create new links anytime

### Why Link Expiry?
- Security: Prevent unauthorized access
- Performance: Clean up inactive devices
- Best Practice: Regenerate for sensitive operations

---

## 🐛 Troubleshooting

### Camera Not Working
- Check webcam is connected
- Allow browser camera permissions
- Try another browser (Chrome/Firefox recommended)

### GPS Not Available
- Enable location services on device
- Check browser location permissions
- Use modern browser (Chrome, Firefox, Safari)

### Can't Connect from Another Device
- Ensure both devices on same WiFi
- Check firewall settings (port 5000)
- Verify computer IP address
- Disable VPN if connected

### Link Code Not Working
- Check 1-hour expiry (regenerate new link)
- Verify correct code spelling (case-sensitive)
- Ensure Flask server is running

---

## 📊 System Status

### Monitoring:
- Real-time FPS counter
- Fire detection confidence
- Drone battery level
- GPS satellite count
- Signal strength
- System uptime

### Logs:
- Command history
- Detection history
- Connection events
- System status

---

## 🔒 Security Notes

1. **Local Network Only**: Default setup for local WiFi
2. **No Authentication**: Suitable for trusted networks
3. **Link Codes**: 6-character codes (not passwords)
4. **HTTPS Recommended**: Use HTTPS for internet deployment
5. **Firewall**: Adjust firewall rules for remote access

For production deployment, consider adding:
- User authentication
- API key validation
- Rate limiting
- HTTPS/SSL certificates

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
```

---

## 🚀 Deployment Guide

### Option 1: Render.com (Recommended)
1. Create account at render.com
2. Push code to GitHub
3. Create new "Web Service"
4. Select GitHub repository
5. Set build command: `pip install -r requirements.txt`
6. Set start command: `python app.py`
7. Deploy!

### Option 2: Railway.app
1. Sign up at railway.app
2. Connect GitHub repo
3. Select Python environment
4. Deploy automatically

### Option 3: Your Own Server
1. SSH into server
2. Clone repository
3. Install Python & dependencies
4. Run with production server:
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

---

## 📞 Support

For issues or questions:
- Check logs in terminal
- Review browser console (F12)
- Verify network connectivity
- Test on same device first

---

## 📄 License

This project is open-source and available for educational and commercial use.

---

**Happy Fire Detection! 🔥🚁**

Last Updated: January 2024
