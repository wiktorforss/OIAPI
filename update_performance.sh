#!/bin/bash
# Daily cron: auto-fill performance snapshots from Polygon cache
# Add to crontab: 0 7 * * * /root/insider-api/update_performance.sh >> /var/log/perf-update.log 2>&1

set -e

API_URL="http://localhost:8000"
ENV_FILE="/root/insider-api/.env"

# Read credentials from .env
CRON_USER=$(grep "^CRON_USER=" "$ENV_FILE" | cut -d'=' -f2)
CRON_PASS=$(grep "^CRON_PASS=" "$ENV_FILE" | cut -d'=' -f2)

if [ -z "$CRON_USER" ] || [ -z "$CRON_PASS" ]; then
  echo "[$(date)] ERROR: CRON_USER or CRON_PASS not set in .env"
  exit 1
fi

echo "[$(date)] Starting performance update..."

# Login
TOKEN=$(curl -s -X POST "$API_URL/auth/login" \
  -d "username=$CRON_USER&password=$CRON_PASS" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "[$(date)] ERROR: Failed to get auth token"
  exit 1
fi

# Run update
RESULT=$(curl -s -X POST "$API_URL/performance/update-all" \
  -H "Authorization: Bearer $TOKEN")

echo "[$(date)] $RESULT"
