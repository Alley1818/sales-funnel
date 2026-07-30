#!/bin/bash
# setup_wa_webhook.sh — Configure WhatsApp webhook in Evolution API
# Run on VPS: bash scripts/setup_wa_webhook.sh
set -e

EVO_URL="${EVO_API_URL:-http://localhost:8080}"
EVO_KEY="${AUTHENTICATION_API_KEY:-sales_funnel_evo_key_2026}"
INSTANCE="${EVO_INSTANCE:-sales_funnel}"
WEBHOOK_URL="${WA_WEBHOOK_URL:-http://flask-app:5050/api/wa/webhook}"
WEBHOOK_SECRET="${WA_WEBHOOK_SECRET:-}"

echo "=== WhatsApp Webhook Setup ==="
echo "Evolution API: $EVO_URL"
echo "Instance: $INSTANCE"
echo "Webhook URL: $WEBHOOK_URL"
echo ""

# 1. Check Evolution API is running
echo "[1/4] Checking Evolution API..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$EVO_URL/" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "000" ]; then
    echo "ERROR: Evolution API not reachable at $EVO_URL"
    echo "Make sure docker compose is running: docker compose up -d"
    exit 1
fi
echo "  OK (HTTP $HTTP_CODE)"

# 2. Check/create instance
echo "[2/4] Checking instance '$INSTANCE'..."
INSTANCES=$(curl -s "$EVO_URL/instance/fetchInstances" \
    -H "apikey: $EVO_KEY" 2>/dev/null)

if echo "$INSTANCES" | grep -q "\"instanceName\":\"$INSTANCE\"" 2>/dev/null; then
    echo "  Instance exists"
else
    echo "  Creating instance..."
    RESULT=$(curl -s -X POST "$EVO_URL/instance/create" \
        -H "apikey: $EVO_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"instanceName\":\"$INSTANCE\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}")
    echo "  Result: $RESULT"
fi

# 3. Configure webhook
echo "[3/4] Configuring webhook..."
WEBHOOK_PAYLOAD="{\"webhook\":{\"enabled\":true,\"url\":\"$WEBHOOK_URL\",\"events\":[\"MESSAGES_UPSERT\",\"CONNECTION_UPDATE\"]}}"
if [ -n "$WEBHOOK_SECRET" ]; then
    WEBHOOK_PAYLOAD="{\"webhook\":{\"enabled\":true,\"url\":\"$WEBHOOK_URL\",\"events\":[\"MESSAGES_UPSERT\",\"CONNECTION_UPDATE\"],\"headers\":{\"X-Webhook-Secret\":\"$WEBHOOK_SECRET\"}}}"
fi

RESULT=$(curl -s -X POST "$EVO_URL/webhook/set/$INSTANCE" \
    -H "apikey: $EVO_KEY" \
    -H "Content-Type: application/json" \
    -d "$WEBHOOK_PAYLOAD")
echo "  Result: $RESULT"

# 4. Check connection state
echo "[4/4] Checking WhatsApp connection state..."
STATE=$(curl -s "$EVO_URL/instance/connectionState/$INSTANCE" \
    -H "apikey: $EVO_KEY" 2>/dev/null)
echo "  $STATE"

# Check if connected
if echo "$STATE" | grep -q '"state":"open"'; then
    echo ""
    echo "=== WhatsApp CONNECTED ==="
    echo "Everything is set up and working!"
else
    echo ""
    echo "=== WhatsApp NOT CONNECTED ==="
    echo "You need to scan a QR code to connect WhatsApp."
    echo ""
    echo "Option 1: Via web UI"
    echo "  Open: http://YOUR_VPS_IP:8080/manager"
    echo "  Login with API key: $EVO_KEY"
    echo "  Click Connect on the '$INSTANCE' instance"
    echo ""
    echo "Option 2: Via API"
    echo "  curl '$EVO_URL/instance/connect/$INSTANCE' -H 'apikey: $EVO_KEY'"
    echo "  (returns QR code base64)"
fi
