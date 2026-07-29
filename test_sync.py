#!/usr/bin/env python3
"""Test the lead sync endpoint on VPS."""
import requests
import json
import sqlite3
import sys

BASE = "http://185.4.180.241:5050"
DB_PATH = "/Users/ali/Desktop/sales_funnel/leads.db"

# Step 1: Create session and try login
session = requests.Session()

print("=== Step 1: Health check ===")
try:
    r = session.get(f"{BASE}/health", timeout=10)
    print(f"Status: {r.status_code} - {r.text[:200]}")
except Exception as e:
    print(f"FAILED: {e}")
    sys.exit(1)

# Step 2: Login - try admin first, then admin123
print("\n=== Step 2: Login ===")
auth_token = None
for password in ["admin", "admin123"]:
    print(f"Trying password: {password}")
    headers = {"Content-Type": "application/json"}
    # Add CSRF token from cookies if present
    csrf_token = session.cookies.get("csrf_token", "")
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token

    try:
        r = session.post(f"{BASE}/api/auth/login",
                         json={"username": "admin", "password": password},
                         headers=headers,
                         timeout=10)
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.text[:300]}")

        if r.status_code == 200:
            data = r.json()
            auth_token = data.get("token") or session.cookies.get("sf_token")
            print(f"  Token: {auth_token[:40]}..." if auth_token else "  No token in response")
            break
    except Exception as e:
        print(f"  Error: {e}")

if not auth_token:
    print("\n=== FAILED to get auth token ===")
    # Let's try to get a CSRF token from a page first
    print("Trying to get CSRF token from main page...")
    try:
        r = session.get(f"{BASE}/", timeout=10)
        print(f"Main page status: {r.status_code}")
        print(f"Cookies: {dict(session.cookies)}")
    except Exception as e:
        print(f"Error: {e}")
    sys.exit(1)

# Step 3: Read leads from local SQLite DB
print("\n=== Step 3: Reading leads from local DB ===")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Get all 8 leads (they're test leads)
rows = conn.execute(
    "SELECT company_name, industry, mobile, whatsapp, email FROM leads"
).fetchall()
conn.close()

leads = []
for r in rows:
    lead = {
        "company_name": r["company_name"] or "",
        "industry": r["industry"] or "",
        "mobile": r["mobile"] or "",
        "whatsapp": r["whatsapp"] or "",
        "email": r["email"] or "",
    }
    if lead["mobile"]:  # Only include leads with mobile
        leads.append(lead)

print(f"Read {len(leads)} leads with mobile numbers")
for l in leads:
    print(f"  - {l['company_name']} | mobile={l['mobile']} | industry={l['industry']}")

# Step 4: Call the sync endpoint
print(f"\n=== Step 4: Calling POST {BASE}/api/leads/sync ===")
payload = {"leads": leads}
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {auth_token}",
}
# Also add CSRF token
csrf_token = session.cookies.get("csrf_token", "")
if csrf_token:
    headers["X-CSRFToken"] = csrf_token

print(f"Payload: {json.dumps(payload, indent=2)}")

try:
    r = session.post(f"{BASE}/api/leads/sync",
                     json=payload,
                     headers=headers,
                     timeout=30)
    print(f"\nSync status: {r.status_code}")
    print(f"Sync response: {r.text}")

    if r.status_code == 200:
        result = r.json()
        print(f"\n=== SYNC RESULTS ===")
        print(f"  Inserted (new): {result.get('synced', 'N/A')}")
        print(f"  Updated:        {result.get('updated', 'N/A')}")
        if "error" in result:
            print(f"  Error: {result['error']}")
    else:
        print(f"\n=== SYNC FAILED ===")
        print(f"  Status: {r.status_code}")
        print(f"  Body: {r.text}")
except Exception as e:
    print(f"Sync request failed: {e}")

# Step 5: Verify by calling the sync AGAIN (should update, not insert)
print(f"\n=== Step 5: Re-sync same leads (should UPDATE) ===")
try:
    r2 = session.post(f"{BASE}/api/leads/sync",
                      json=payload,
                      headers=headers,
                      timeout=30)
    print(f"Re-sync status: {r2.status_code}")
    print(f"Re-sync response: {r2.text}")
    if r2.status_code == 200:
        result2 = r2.json()
        print(f"  Inserted: {result2.get('synced', 'N/A')}")
        print(f"  Updated:  {result2.get('updated', 'N/A')}")
except Exception as e:
    print(f"Re-sync failed: {e}")

print("\n=== DONE ===")
