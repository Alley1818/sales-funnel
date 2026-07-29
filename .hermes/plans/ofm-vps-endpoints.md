# ОФМ AI Agent — VPS Webhook Endpoints (PENDING)

**Status:** maybe — waiting for VPS access
**Created:** 2026-07-29

## Context

The ОФМ AI agent on Technomax has been upgraded with 4 API_REQUEST tools.
The agent config is live, but the VPS endpoints don't exist yet (port 5051 not listening).

Agent ID: `76538e66-00d0-4d9d-8731-4400be9eba6b`
VPS: 185.4.180.241 (SSH via `~/.ssh/github_actions` as `ubuntu`)
Target port: 5051

## What's Already Done (agent config)

- Prompt: contact number 8 776 120 07 00 + tool usage instructions
- Temperature: 0.2 (was 0.3)
- Max output tokens: 1500 (was 1000)
- Time context: enabled
- User away handler: ON (5s silence, 2 pings)
- User long away handler: ON (30s silence)
- Static messages: greeting + "Алло? Вы на линии?" sequence
- Context variables: phone, email, contact_name, callback_datetime, interest_level
- Dialog results: tightened format

## What Needs to Be Built

Create a Flask service on VPS port 5051 with 4 endpoints:

### 1. POST /send-whatsapp
```json
Input:  {"phone": "+770..."}
Action: Send materials via Evolution API (localhost:8080)
Output: {"success": true, "message": "Отправлено в WhatsApp"}
```
- Evo API is in Docker container `evolution_api` on port 8080
- Need to discover instance name and API key first
- Send a pre-defined message with link to presentation/materials

### 2. POST /send-email
```json
Input:  {"phone": "+770...", "email": "dir@company.kz"}
Action: Send email with materials (presentation, case study, pricing)
Output: {"success": true, "message": "Отправлено на email"}
```
- Need SMTP credentials (or use a transactional email service)
- Email content: Technomax presentation + voice robot demo link

### 3. POST /create-deal
```json
Input:  {"phone": "+770...", "contact_name": "Иван", "interest_level": "высокий", "notes": "callback завтра 14:00"}
Action: Create deal in Bitrix24 CRM
Output: {"success": true, "message": "Сделка создана"}
```
- Bitrix24: b24-0ucvgg.bitrix24.kz
- Credentials: a.zeinolla@technomax.io / jAxrob-kukbo4-cyrxog
- Create deal with contact info, interest level, notes

### 4. POST /schedule-callback
```json
Input:  {"phone": "+770...", "callback_datetime": "завтра в 14:00", "contact_name": "Иван"}
Action: Create Bitrix24 task for callback
Output: {"success": true, "message": "Звонок запланирован"}
```
- Create a Bitrix24 task with deadline = callback time
- Assign to sales team

## Discovery Needed (before building)

```bash
# SSH into VPS
ssh -i ~/.ssh/github_actions ubuntu@185.4.180.241

# Get Evo API instance + key
docker exec evolution_api env | grep -iE 'API_KEY|INSTANCE|AUTHENTICATION'

# Check Bitrix24 webhook availability
curl -s 'https://b24-0ucvgg.bitrix24.kz/rest/1/.../crm.deal.list'

# Check if SMTP is configured anywhere
docker exec sales_funnel_app env | grep -iE 'MAIL|SMTP|EMAIL'

# Check sales_funnel Flask app structure
docker exec sales_funnel_app ls /app/
```

## VPS Status

- SSH key: `~/.ssh/github_actions` (user: `ubuntu`)
- Last check: 2026-07-29 — VPS was unreachable (network timeout)
- Earlier in session: first SSH worked, then timed out
- Services running: sales_funnel (:5050), evo (:8080), pipecat (:8082), asterisk
