#!/bin/bash
# ================================================================
#  TG Monitor Pro - Automated Installer for Linux / aaPanel
#  Tested on: Ubuntu 20.04 / 22.04 / CentOS 7+
# ================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
head() { echo -e "\n${CYAN}${BOLD}══ $1 ══${NC}"; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════╗"
echo "║     TG Monitor Pro  •  Auto Installer    ║"
echo "║         v3.0  Python Edition             ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ── 1. System requirements ──────────────────────────────────────
head "1. System Check"
command -v python3 >/dev/null 2>&1 || err "Python3 not found. Install with: apt install python3"
PYVER=$(python3 --version | awk '{print $2}')
log "Python $PYVER detected"
command -v pip3 >/dev/null 2>&1 || { apt-get install -y python3-pip 2>/dev/null || yum install -y python3-pip; }
log "pip3 available"

# ── 2. PostgreSQL ────────────────────────────────────────────────
head "2. PostgreSQL Setup"
if ! command -v psql >/dev/null 2>&1; then
  warn "PostgreSQL not installed. Installing..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y postgresql postgresql-contrib
  else
    yum install -y postgresql-server postgresql-contrib && postgresql-setup initdb
  fi
  systemctl enable postgresql && systemctl start postgresql
fi
log "PostgreSQL ready"

# Create database and user
PG_DB="tgmonitor"
PG_USER="tgmonitor"
PG_PASS=$(openssl rand -hex 16)

sudo -u postgres psql -c "CREATE USER $PG_USER WITH PASSWORD '$PG_PASS';" 2>/dev/null || warn "User may already exist"
sudo -u postgres psql -c "CREATE DATABASE $PG_DB OWNER $PG_USER;" 2>/dev/null || warn "Database may already exist"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $PG_DB TO $PG_USER;" 2>/dev/null

DB_URL="postgresql://$PG_USER:$PG_PASS@127.0.0.1:5432/$PG_DB"
log "Database created: $PG_DB"

# ── 3. Dependencies ──────────────────────────────────────────────
head "3. Installing Python Dependencies"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/tgbot"
pip3 install -r requirements.txt -q
pip3 install PySocks -q
log "All dependencies installed"

# ── 4. Environment File ──────────────────────────────────────────
head "4. Configuration"

if [ ! -f "$SCRIPT_DIR/.env" ]; then
  echo ""
  echo -e "${YELLOW}Please provide the following configuration:${NC}"
  read -p "  BOT_TOKEN (from @BotFather): " BOT_TOKEN
  read -p "  ADMIN_TELEGRAM_ID (your Telegram ID): " ADMIN_ID
  SESSION_SECRET=$(openssl rand -hex 32)

  cat > "$SCRIPT_DIR/.env" << EOF
# TG Monitor Pro - Environment Configuration
BOT_TOKEN=$BOT_TOKEN
ADMIN_TELEGRAM_ID=$ADMIN_ID
DATABASE_URL=$DB_URL
SESSION_SECRET=$SESSION_SECRET
EOF
  log ".env file created"
else
  warn ".env already exists, skipping creation"
  # Ensure DATABASE_URL is set
  if ! grep -q "DATABASE_URL" "$SCRIPT_DIR/.env"; then
    echo "DATABASE_URL=$DB_URL" >> "$SCRIPT_DIR/.env"
    log "DATABASE_URL added to .env"
  fi
fi

# ── 5. Database Schema ───────────────────────────────────────────
head "5. Initializing Database"
cd "$SCRIPT_DIR"
export $(grep -v '^#' .env | xargs) 2>/dev/null
python3 -c "
import asyncio, sys
sys.path.insert(0, '.')
from tgbot.app.core.init_db import init_database
asyncio.run(init_database())
print('Database initialized successfully')
"
log "Database schema applied"

# ── 6. Systemd Service ───────────────────────────────────────────
head "6. Setting up System Service"
PYTHON_BIN=$(which python3)
INSTALL_DIR="$SCRIPT_DIR"

cat > /etc/systemd/system/tgmonitor.service << EOF
[Unit]
Description=TG Monitor Pro Bot Service
After=network.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR/tgbot
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$PYTHON_BIN $INSTALL_DIR/tgbot/run.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/tgmonitor.log
StandardError=append:/var/log/tgmonitor.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tgmonitor
systemctl start tgmonitor
log "Service tgmonitor started"

# ── 7. Nginx Reverse Proxy (Optional) ───────────────────────────
head "7. Nginx Setup (Optional)"
if command -v nginx >/dev/null 2>&1; then
  DOMAIN=""
  read -p "  Enter your domain (leave blank to skip nginx config): " DOMAIN
  if [ -n "$DOMAIN" ]; then
    cat > /etc/nginx/sites-available/tgmonitor << EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_read_timeout 300s;
    }
}
EOF
    ln -sf /etc/nginx/sites-available/tgmonitor /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx
    log "Nginx configured for $DOMAIN"
    warn "Run: certbot --nginx -d $DOMAIN  (to enable HTTPS)"
  fi
fi

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "╔══════════════════════════════════════════╗"
echo "║        Installation Complete! ✓          ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  🌐 Admin Panel:  ${CYAN}http://YOUR_SERVER_IP:8000/admin/${NC}"
echo -e "  📋 Default Login: ${YELLOW}admin / admin123${NC}"
echo -e "  📝 Service Logs:  ${CYAN}journalctl -u tgmonitor -f${NC}"
echo -e "  🗄️  DB URL:        ${CYAN}$DB_URL${NC}"
echo ""
echo -e "${YELLOW}Security reminder: Change the admin password immediately after first login!${NC}"
echo ""
