# 🚀 Deployment Guide - Fire Detection Drone System

This guide will help you deploy your fire detection system to the cloud and make it accessible from anywhere.

---

## 📋 Pre-Deployment Checklist

- [ ] Python dependencies installed locally and tested
- [ ] Application runs without errors: `python app.py`
- [ ] All features working on localhost
- [ ] Code committed to GitHub (if using cloud services)
- [ ] Port 5000 is available on your machine

---

## 🌐 Option 1: Deploy on Render.com (EASIEST)

### Step 1: Prepare GitHub Repository
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/fire-drone-system.git
git push -u origin main
```

### Step 2: Create Render Account
1. Go to https://render.com
2. Sign up with GitHub
3. Authorize Render to access your repositories

### Step 3: Deploy Service
1. Click "New +" → "Web Service"
2. Select your `fire-drone-system` repository
3. Fill in settings:
   - **Name**: `fire-drone-system`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python app.py`
   - **Free plan** is available

4. Click "Create Web Service"
5. Wait 2-3 minutes for deployment

### Step 4: Get Your URL
- Once deployed, you'll get a URL like: `https://fire-drone-system-xyz.onrender.com`
- Share this with others to access your dashboard!

### Access Your System:
```
Main Dashboard:  https://fire-drone-system-xyz.onrender.com
Remote Device:   https://fire-drone-system-xyz.onrender.com/device/<LINK_CODE>
```

---

## 🚀 Option 2: Deploy on Railway.app

### Step 1: Create Railway Account
1. Go to https://railway.app
2. Sign up with GitHub
3. Connect your GitHub account

### Step 2: Deploy Project
1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. Choose `fire-drone-system` repository
4. Railway auto-detects Python

### Step 3: Configure Environment
1. In Project Settings:
   - Set port to 5000 (or let Railway choose)
   - No special env variables needed

### Step 4: Deploy
- Click "Deploy"
- Railway automatically detects `app.py`
- Deployment takes 1-2 minutes

### Get Your URL:
1. Go to Domains section
2. Copy the generated domain
3. Share with others!

---

## 🖥️ Option 3: Deploy on Heroku (Traditional)

### Step 1: Create Procfile
Create file named `Procfile` in root directory:
```
web: python app.py
```

### Step 2: Install Heroku CLI
```bash
# Windows (Chocolatey)
choco install heroku-cli

# Mac (Homebrew)
brew tap heroku/brew && brew install heroku

# Linux (Snap)
snap install heroku --classic
```

### Step 3: Deploy
```bash
heroku login
heroku create fire-drone-system
git push heroku main
```

### Step 4: View Logs
```bash
heroku logs --tail
```

---

## 🔧 Option 4: Deploy on Your Own Server

### Prerequisites:
- Linux server (Ubuntu 20.04 recommended)
- SSH access
- Domain name (optional)

### Step 1: SSH into Server
```bash
ssh root@your_server_ip
```

### Step 2: Install Dependencies
```bash
apt update
apt install python3 python3-pip nginx
```

### Step 3: Clone and Setup
```bash
git clone https://github.com/YOUR_USERNAME/fire-drone-system.git
cd fire-drone-system
pip install -r requirements.txt
```

### Step 4: Install & Configure Gunicorn
```bash
pip install gunicorn
# Test run
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### Step 5: Setup Systemd Service
Create `/etc/systemd/system/fire-drone.service`:
```ini
[Unit]
Description=Fire Detection Drone System
After=network.target

[Service]
User=www-data
WorkingDirectory=/root/fire-drone-system
ExecStart=/usr/bin/gunicorn -w 4 -b 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Start service:
```bash
systemctl daemon-reload
systemctl start fire-drone
systemctl enable fire-drone
```

### Step 6: Setup Nginx Reverse Proxy
Create `/etc/nginx/sites-available/fire-drone`:
```nginx
server {
    listen 80;
    server_name your_domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable:
```bash
ln -s /etc/nginx/sites-available/fire-drone /etc/nginx/sites-enabled/
nginx -t
systemctl restart nginx
```

### Step 7: Setup HTTPS (Let's Encrypt)
```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d your_domain.com
```

---

## 🔐 Security Best Practices

### 1. Environment Variables
Create `.env` file:
```
FLASK_ENV=production
SECRET_KEY=your-super-secret-key
MAX_DEVICES=10
LINK_EXPIRY=3600
```

### 2. Update app.py to Load .env:
```python
from dotenv import load_dotenv
import os

load_dotenv()
app.secret_key = os.getenv('SECRET_KEY')
```

### 3. Add Rate Limiting
```bash
pip install Flask-Limiter
```

### 4. Use HTTPS Only
- Redirect HTTP to HTTPS
- Use security headers
- Enable HSTS

### 5. Database Authentication
For production, add user authentication:
```bash
pip install Flask-SQLAlchemy Flask-Login
```

---

## 📊 Monitoring Deployment

### Render.com
- View logs in dashboard
- Monitor resource usage
- Set up alerts

### Railway
- Real-time logs
- Resource graphs
- Error tracking

### Your Server
```bash
# View service logs
journalctl -u fire-drone -f

# Monitor system
htop

# Check disk space
df -h
```

---

## 🐛 Common Deployment Issues

### Issue: Port Already in Use
```bash
# Find process using port 5000
lsof -i :5000

# Kill process
kill -9 <PID>
```

### Issue: Module Not Found
```bash
# Ensure requirements.txt is installed
pip install -r requirements.txt

# Check installed packages
pip list
```

### Issue: WebSocket Connection Failed
- Ensure Flask-SocketIO is installed
- Check firewall allows WebSocket connections
- Verify allow_unsafe_werkzeug=True in socketio.run()

### Issue: Camera/GPS Not Working
- Remote devices need HTTPS (not HTTP)
- Allow browser permissions for camera/location
- Check CORS settings

---

## 🔄 Continuous Deployment

### GitHub Actions (Auto-Deploy on Push)
Create `.github/workflows/deploy.yml`:
```yaml
name: Deploy to Render

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Deploy to Render
        run: |
          curl https://api.render.com/deploy/srv-xxx?key=${{ secrets.RENDER_DEPLOY_KEY }}
```

---

## 📈 Scaling

### For More Devices:
1. Increase Python workers: `-w 8` in Gunicorn
2. Use load balancer
3. Add Redis for session management
4. Use separate database

### For More Traffic:
1. Add Nginx caching
2. CDN for static files
3. Database indexing
4. API rate limiting

---

## 💰 Cost Estimates

| Platform | Free Tier | Cost (Hobby) |
|----------|-----------|-------------|
| **Render** | Yes (512MB RAM) | $7/month |
| **Railway** | $5/month credit | $5+ |
| **Heroku** | Deprecated | $7+ |
| **AWS** | 1 year free | Varies |
| **Your Server** | Initial setup | $5-20/month |

---

## ✅ Post-Deployment Checklist

- [ ] Application accessible via URL
- [ ] Main dashboard loads
- [ ] Can generate device links
- [ ] Remote device interface works
- [ ] Camera streaming functional
- [ ] GPS tracking operational
- [ ] Fire detection working
- [ ] No errors in logs
- [ ] HTTPS enabled (if applicable)
- [ ] Domain configured properly

---

## 🚨 Emergency Response

### If System Goes Down:
1. Check service status: `systemctl status fire-drone`
2. View logs: `journalctl -u fire-drone -n 50`
3. Restart service: `systemctl restart fire-drone`
4. Check disk space: `df -h`
5. Monitor memory: `free -h`

---

## 📞 Support Resources

- **Render Docs**: https://render.com/docs
- **Railway Docs**: https://docs.railway.app
- **Flask Docs**: https://flask.palletsprojects.com
- **SocketIO Docs**: https://python-socketio.readthedocs.io

---

## 📝 Next Steps

After deployment:
1. Share the link with team members
2. Test from different devices
3. Monitor system performance
4. Plan for scaling if needed
5. Regular backups of code

---

**🎉 Congratulations! Your Fire Detection System is now online and accessible worldwide!**

For updates and improvements, keep monitoring logs and user feedback.

Last Updated: January 2024
