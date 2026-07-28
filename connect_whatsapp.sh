#!/bin/bash
# Script to connect WhatsApp via Evolution API
# Usage: ./connect_whatsapp.sh [phone_number]

set -e

API_URL="http://localhost:8080"
API_KEY="sales_funnel_evo_key_2026"
INSTANCE="sales_funnel"
PHONE="${1:-}"

echo "=== WhatsApp Connection Helper ==="
echo ""

# Check if Evolution API is running
echo "1. Checking Evolution API..."
STATUS=$(curl -s "$API_URL/" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
if [ "$STATUS" != "200" ]; then
    echo "   Evolution API not running! Starting..."
    cd "$(dirname "$0")" && docker compose up -d
    sleep 10
fi
echo "   ✓ Evolution API is running"

# Delete existing instance if any
echo "2. Cleaning up existing instance..."
curl -s -X DELETE "$API_URL/instance/delete/$INSTANCE" \
  -H "apikey: $API_KEY" > /dev/null 2>&1 || true
sleep 2

# Create new instance
echo "3. Creating WhatsApp instance..."
RESULT=$(curl -s -X POST "$API_URL/instance/create" \
  -H "apikey: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"instanceName\":\"$INSTANCE\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}")
echo "   Instance created: $(echo $RESULT | python3 -c "import json,sys; print(json.load(sys.stdin).get('instance',{}).get('status',''))" 2>/dev/null)"

# Wait for QR generation
echo "4. Waiting for QR code (may take 15-30 seconds)..."
for i in $(seq 1 12); do
    sleep 5
    QR_DATA=$(curl -s "$API_URL/instance/connect/$INSTANCE" -H "apikey: $API_KEY")
    BASE64=$(echo "$QR_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('base64',''))" 2>/dev/null)
    
    if [ -n "$BASE64" ] && [ "$BASE64" != "" ]; then
        echo "   ✓ QR Code generated!"
        echo ""
        echo "5. Opening QR code in browser..."
        cat > /tmp/whatsapp_qr.html << EOF
<!DOCTYPE html>
<html>
<head><title>WhatsApp QR</title></head>
<body style="display:flex; flex-direction:column; justify-content:center; align-items:center; height:100vh; font-family:system-ui;">
    <h2>Scan this QR with WhatsApp</h2>
    <img src="$BASE64" style="width:300px; height:300px; border:2px solid #333;">
    <p style="color:#666; margin-top:20px;">WhatsApp → Settings → Linked Devices → Link a Device</p>
</body>
</html>
EOF
        open /tmp/whatsapp_qr.html
        echo ""
        echo "   Scan the QR code with your phone's WhatsApp"
        echo "   After scanning, the instance will be connected."
        echo ""
        
        # Wait for connection
        echo "6. Waiting for connection confirmation..."
        for j in $(seq 1 12); do
            sleep 5
            STATE=$(curl -s "$API_URL/instance/connectionState/$INSTANCE" \
              -H "apikey: $API_KEY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('instance',{}).get('state',''))" 2>/dev/null)
            echo "   State: $STATE"
            if [ "$STATE" = "open" ]; then
                echo ""
                echo "=== WhatsApp Connected Successfully! ==="
                exit 0
            fi
        done
        echo "   Timeout waiting for connection. Check your phone."
        exit 1
    fi
    echo "   Attempt $i/12 - QR not ready yet..."
done

echo "   QR code generation timed out."
echo "   Try accessing the manager: $API_URL/manager"
exit 1
