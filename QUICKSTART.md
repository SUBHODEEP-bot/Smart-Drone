# 🚀 Quick Start Guide

Get your Fire Detection System running in 5 minutes!

## 1️⃣ Install Dependencies

```bash
cd fire-drone-system
pip install -r requirements.txt
```

⏱️ **Takes ~2 minutes** (depending on internet speed)

---

## 2️⃣ Run the Application

```bash
python app.py
```

You should see:
```
🔥 FIRE DETECTION DRONE SYSTEM WITH REMOTE DEVICE SUPPORT 🔥
ESP32 IP: 192.168.1.100
Using webcam for video
Dashboard: http://localhost:5000
Remote Device Support: Enabled
```

---

## 3️⃣ Open Dashboard

Open your web browser and go to:
```
http://localhost:5000
```

You'll see:
- 📹 Live video feed from your webcam
- 🔥 Fire detection status
- 🎮 Drone control buttons
- 📱 Connected devices panel

---

## 4️⃣ Test Fire Detection

Click **"FIRE DETECTION"** section and:
1. Point at something orange/red
2. Watch the detection box appear
3. See confidence percentage update

---

## 5️⃣ Generate Remote Device Link

1. Click **"GENERATE LINK"** button (in Drone Controls)
2. You'll get a 6-character code (e.g., `A3K9M2`)
3. Copy the full URL

Example:
```
http://localhost:5000/device/A3K9M2
```

---

## 6️⃣ Access from Another Device

### Option A: Same WiFi Network

1. Find your computer's IP address:
   - **Windows**: Open CMD, type `ipconfig`, look for IPv4 (like `192.168.x.x`)
   - **Mac/Linux**: Open Terminal, type `ifconfig`

2. On another device (phone/tablet):
   ```
   http://<YOUR_IP>:5000/device/A3K9M2
   ```

   Example:
   ```
   http://192.168.1.100:5000/device/A3K9M2
   ```

### Option B: Internet Access

Deploy using Render.com (see DEPLOYMENT.md) and share the full URL!

---

## 7️⃣ Use Remote Device

On the remote device:
1. ✅ Enter device name
2. 📱 Click "Register Device"
3. 🎥 Click "Start Camera"
4. 📍 Click "Start GPS"
5. 👀 Watch fire detection updates in real-time

---

## 🎯 What You Can Do

### Main Dashboard
- ✅ See live camera feed
- ✅ View fire detection status
- ✅ Control drone (TAKE OFF, LAND, etc.)
- ✅ Adjust detection sensitivity
- ✅ View connected devices
- ✅ Check system status

### Remote Device
- ✅ Stream camera from another device
- ✅ Share GPS location
- ✅ Receive fire alerts
- ✅ Monitor drone status

---

## 🔥 Fire Detection Guide

### What It Detects:
- Red/orange flames 🔴
- Hot bright areas ☀️
- Fire-like colors 🟠

### Adjust Sensitivity:
- **Low**: Less false alarms, might miss small fires
- **High**: Catches more fires, more false alarms
- 💡 Default is 60% (balanced)

### How Confidence Works:
- `10%-40%`: Suspicious, might be false alarm
- `40%-60%`: Checking, monitoring closely
- `60%-100%`: HIGH CONFIDENCE, ALERT!

---

## 📱 Remote Device Requirements

✅ **Must Have:**
- Modern web browser (Chrome, Firefox, Safari)
- WiFi connection (same as main computer)
- Camera enabled
- Location/GPS enabled

✅ **Recommended:**
- Good WiFi signal
- Recent smartphone (iPhone 6+ or Android 5+)
- Enough battery

---

## 🐛 Troubleshooting

### Camera Not Showing?
```bash
# Check if webcam is connected
# Restart browser and refresh page
# Try a different browser
```

### Can't Connect from Another Device?
```bash
# Check IP address: ipconfig (Windows) or ifconfig (Mac/Linux)
# Make sure both on same WiFi
# Check Windows Firewall allows port 5000
# Try http:// not https://
```

### Fire Detection Not Working?
```bash
# Wait 2 seconds for system to initialize
# Point camera at bright colors
# Adjust sensitivity slider
# Check lighting conditions
```

### Link Doesn't Work?
```bash
# Check 1-hour expiry (regenerate new link)
# Verify correct 6-character code
# Ensure Flask server still running
# Check browser console for errors (F12)
```

---

## 📊 Performance Tips

**For Smooth Operation:**
- Use 5GHz WiFi if available
- Close other bandwidth-heavy apps
- Keep devices near router
- Ensure good lighting for camera
- Use modern browser (Chrome recommended)

---

## 🚀 Ready to Deploy Online?

When you want to make it accessible from anywhere:

1. Push code to GitHub
2. Deploy on Render.com (easiest)
3. Share the domain link
4. No more localhost!

👉 See **DEPLOYMENT.md** for full instructions

---

## 🎓 Learning Resources

### Understanding the Code:
- `app.py` - Backend logic
- `templates/index.html` - Main dashboard
- `templates/device.html` - Remote device interface
- `static/script.js` - JavaScript interactions

### Customization:
- Change detection colors in `detect_fire_simple()`
- Adjust link expiry time: `LINK_EXPIRY_TIME`
- Modify UI colors in CSS
- Add more features!

---

## 💡 Pro Tips

1. **Keep Browser Tab Active**: Helps with GPS accuracy
2. **Use High Brightness**: Better fire detection
3. **Test First**: Test on same device before remote
4. **Monitor Devices**: Watch for inactive devices
5. **Regenerate Links**: For security, create new links regularly

---

## 🎉 You're Ready!

```
✅ System Running
✅ Dashboard Working
✅ Fire Detection Active
✅ Remote Access Ready

Your fire detection system is live! 🚁🔥
```

---

## 📞 Need Help?

Check logs for errors:
```bash
# Look at terminal output
# Or check browser console (F12 → Console tab)
```

Common issues:
- **ModuleNotFoundError**: Run `pip install -r requirements.txt` again
- **Port 5000 in use**: Change port in `app.run()` or kill process
- **No camera**: Check webcam is connected and allowed

---

## 🚀 Next Steps

1. ✅ Explore all features on main dashboard
2. ✅ Test remote device on another phone
3. ✅ Experiment with fire detection
4. ✅ Adjust sensitivity to your needs
5. ✅ When ready → Deploy online (see DEPLOYMENT.md)

---

**Happy monitoring! 🔥🚁**

Last Updated: January 2024
