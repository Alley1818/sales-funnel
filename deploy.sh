#!/bin/bash
# Sales Funnel — VPS Deployment Script
# Usage: ssh root@YOUR_VPS_IP 'bash -s' < deploy.sh
# Or: scp deploy.sh root@YOUR_VPS_IP:/ && ssh root@YOUR_VPS_IP bash /deploy.sh

set -e

echo "=========================================="
echo "  Sales Funnel — VPS Deployment"
echo "=========================================="

# 1. System update
echo "[1/8] Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

# 2. Install Docker
echo "[2/8] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
else
    echo "Docker already installed: $(docker --version)"
fi

# 3. Install Docker Compose
echo "[3/8] Installing Docker Compose..."
if ! command -v docker compose &> /dev/null; then
    apt-get install -y -qq docker-compose-plugin
else
    echo "Docker Compose already installed"
fi

# 4. Install Python 3.11+ and dependencies
echo "[4/8] Installing Python..."
apt-get install -y -qq python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx

# 5. Clone repo
echo "[5/8] Cloning repository..."
APP_DIR="/opt/sales-funnel"
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR"
    git pull origin main
else
    git clone https://github.com/Alley1818/sales-funnel.git "$APP_DIR"
    cd "$APP_DIR"
fi

# 6. Create .env files
echo "[6/8] Configuring environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "*** EDIT .env WITH YOUR REAL VALUES ***"
    echo "  nano $APP_DIR/.env"
    echo ""
fi

if [ ! -f .env.pipecat ]; then
    cp .env.pipecat.example .env.pipecat
    echo ""
    echo "*** EDIT .env.pipecat WITH YOUR REAL VALUES ***"
    echo "  nano $APP_DIR/.env.pipecat"
    echo ""
fi

# Create Python venv and install deps
python3 -m venv venv
source venv/bin/activate
pip install -q flask requests openpyxl httpx python-dotenv

# 7. Configure Nginx reverse proxy
echo "[7/8] Configuring Nginx..."
cat > /etc/nginx/sites-available/sales-funnel << 'NGINX'
server {
    listen 80;
    server_name YOUR_DOMAIN_OR_IP;

    # Flask app
    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }

    # Evolution API (WhatsApp)
    location /whatsapp/ {
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Pipecat agent
    location /pipecat/ {
        proxy_pass http://127.0.0.1:8082/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Asterisk ARI
    location /asterisk/ {
        proxy_pass http://127.0.0.1:8088/;
        proxy_set_header Host $host;
    }
}
NGINX

# Enable site
ln -sf /etc/nginx/sites-available/sales-funnel /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# 8. Create systemd service for Flask app
echo "[8/8] Creating systemd service..."
cat > /etc/systemd/system/sales-funnel.service << SERVICE
[Unit]
Description=Sales Funnel Flask App
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$APP_DIR/venv/bin/python3 -c "from main import app; app.run(host='127.0.0.1', port=5050)"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable sales-funnel
systemctl start sales-funnel

# Start Docker containers
echo ""
echo "=========================================="
echo "  Starting Docker containers..."
echo "=========================================="
cd "$APP_DIR"
docker compose up -d

# Wait for services
sleep 10

# Check status
echo ""
echo "=========================================="
echo "  STATUS"
echo "=========================================="
echo ""
echo "Flask app:"
systemctl is-active sales-funnel
echo ""
echo "Docker containers:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
echo ""
echo "Nginx:"
systemctl is-active nginx
echo ""
echo "=========================================="
echo "  DEPLOYMENT COMPLETE"
echo "=========================================="
echo ""
echo "Web UI:  http://YOUR_VPS_IP/"
echo "WhatsApp API: http://YOUR_VPS_IP:8080"
echo "Pipecat: http://YOUR_VPS_IP:8082"
echo ""
echo "NEXT STEPS:"
echo "  1. Edit .env: nano $APP_DIR/.env"
echo "  2. Edit .env.pipecat: nano $APP_DIR/.env.pipecat"
echo "  3. Restart: systemctl restart sales-funnel && docker compose restart"
echo "  4. Connect WhatsApp: open http://YOUR_VPS_IP/ → AI Агенты → Обновить QR"
echo "  5. Set up HTTPS: certbot --nginx -d YOUR_DOMAIN"
echo ""
