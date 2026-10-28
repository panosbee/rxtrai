#!/bin/bash
# RxTrail Deployment Script
# Usage: bash deploy.sh
set -e

SERVER="hetzner"
REMOTE_DIR="/var/www/rxtrail"
SERVICE="rxtrail"

echo "=== RxTrail Deploy ==="
echo ""

# 1. Test locally first
echo "[1/6] Running local tests..."
cd "$(dirname "$0")"
python -m pytest backend/tests/test_state_machine.py backend/tests/test_closed_silence.py -q 2>&1 | tail -3
echo ""

# 2. Create remote directory
echo "[2/6] Setting up server directory..."
ssh $SERVER "mkdir -p $REMOTE_DIR $REMOTE_DIR/uploads $REMOTE_DIR/frontend/icons"
echo "  Done."
echo ""

# 3. Upload code
echo "[3/6] Uploading backend..."
rsync -avz --delete \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='uploads/*' \
    backend/ ${SERVER}:$REMOTE_DIR/backend/
echo ""

echo "[4/6] Uploading frontend..."
rsync -avz --delete \
    frontend/ ${SERVER}:$REMOTE_DIR/frontend/
echo ""

# 4. Upload config files
echo "[5/6] Uploading service & nginx config..."
scp rxtrail.service ${SERVER}:/etc/systemd/system/rxtrail.service
scp nginx-rxtrail.conf ${SERVER}:/etc/nginx/sites-available/rxtrail
echo "  Done."
echo ""

# 5. Server-side setup
echo "[6/6] Running server setup..."
ssh $SERVER "bash -s" << 'REMOTE_SCRIPT'
set -e

# Install Python dependencies
cd /var/www/rxtrail
pip install -r backend/requirements.txt 2>/dev/null || pip3 install -r backend/requirements.txt

# Enable Nginx site
ln -sf /etc/nginx/sites-available/rxtrail /etc/nginx/sites-enabled/rxtrail
nginx -t && systemctl reload nginx

# Enable and start service
systemctl daemon-reload
systemctl enable rxtrail.service
systemctl restart rxtrail.service

# SSL (if not already set up)
if [ ! -f /etc/letsencrypt/live/rxtrail.top/fullchain.pem ]; then
    echo "Getting SSL certificate for rxtrail.top..."
    certbot --nginx -d rxtrail.top -d www.rxtrail.top --non-interactive --agree-tos --email admin@medresearch-ai.org
fi

echo ""
echo "=== Done! ==="
echo "Check: systemctl status rxtrail"
echo "Visit: https://rxtrail.top"
REMOTE_SCRIPT

echo ""
echo "✅ Deployment complete!"
