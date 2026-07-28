# Graph Report - .  (2026-07-28)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 465 nodes · 902 edges · 23 communities (20 shown, 3 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 56 edges (avg confidence: 0.66)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `bf617d3f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_basic.py
- funnel_features.py
- advanced_features.py
- main.py
- SimplePipeline
- WhatsAppClient
- main.py
- funnel_engine.py
- leads_db.py
- agent_sync.py
- TechnomaxClient
- telegram_notifier.py
- PipecatClient
- FunnelEngine
- technomax_agent.py
- db_conn.py
- load_config
- manifest.json
- db_extended.py
- entrypoint.sh
- connect_whatsapp.sh
- deploy.sh

## God Nodes (most connected - your core abstractions)
1. `get_conn()` - 36 edges
2. `register_feature_routes()` - 28 edges
3. `get_conn()` - 25 edges
4. `WhatsAppClient` - 25 edges
5. `register_advanced_routes()` - 24 edges
6. `FunnelEngine` - 20 edges
7. `PipecatClient` - 15 edges
8. `EmailSender` - 12 edges
9. `FunnelConfig` - 12 edges
10. `get_pooled_conn()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `test_advanced_imports()` --indirect_call--> `analyze_sentiment()`  [INFERRED]
  tests/test_basic.py → advanced_features.py
- `test_auto_score()` --calls--> `auto_score_lead()`  [EXTRACTED]
  tests/test_basic.py → advanced_features.py
- `FunnelEngine` --uses--> `EmailConfig`  [INFERRED]
  funnel_engine.py → email_sender.py
- `FunnelEngine` --uses--> `EmailResult`  [INFERRED]
  funnel_engine.py → email_sender.py
- `FunnelEngine` --uses--> `EmailSender`  [INFERRED]
  funnel_engine.py → email_sender.py

## Import Cycles
- None detected.

## Communities (23 total, 3 thin omitted)

### Community 0 - "test_basic.py"
Cohesion: 0.06
Nodes (43): analyze_sentiment(), Keyword-based sentiment analysis with negation handling., Flask, Leads API endpoints for the new UI: list, search, export, detail., register_leads_routes(), before_request_hook(), check_rate(), check_session() (+35 more)

### Community 1 - "funnel_features.py"
Cohesion: 0.12
Nodes (47): Flask, API endpoints for all sales funnel features. Mounted on the main Flask app., Register all feature API routes on the Flask app., register_feature_routes(), add_dnc(), add_leads_to_campaign(), can_send(), create_ab_test() (+39 more)

### Community 2 - "advanced_features.py"
Cohesion: 0.11
Nodes (44): Flask, API endpoints for advanced features: RAG, sentiment, scoring, callbacks, transfe, Register all advanced feature routes., register_advanced_routes(), add_document(), authenticate_user(), auto_score_lead(), batch_score_leads() (+36 more)

### Community 3 - "main.py"
Cohesion: 0.10
Nodes (23): BackgroundTasks, BaseModel, FastAPI, AsteriskARI, CallRequest, CallStatus, create_call(), get_call() (+15 more)

### Community 4 - "SimplePipeline"
Cohesion: 0.09
Nodes (22): media_websocket(), STT (faster-whisper) -> LLM (OpenRouter) -> TTS (edge-tts) pipeline.     All fre, STT: audio bytes -> text., LLM: user text -> response text., TTS: text -> MP3 audio bytes., Full pipeline: audio -> text -> LLM response -> audio.         Returns (user_tex, WebSocket endpoint for Asterisk audio streaming.     Asterisk connects here when, SimplePipeline (+14 more)

### Community 5 - "WhatsAppClient"
Cohesion: 0.08
Nodes (19): Send WhatsApp message manually., Get WhatsApp connection state., Get WhatsApp QR code for connecting., Create/initialize WhatsApp instance., Get QR code as base64 image for embedding in UI., send_whatsapp(), whatsapp_connect(), whatsapp_qr() (+11 more)

### Community 6 - "main.py"
Cohesion: 0.10
Nodes (23): call_result(), cli(), get_engine(), health(), next_leads(), pipecat_status(), Sales Funnel — main entry point. Provides a simple API server for Technomax webh, Get next batch of leads to call. (+15 more)

### Community 7 - "funnel_engine.py"
Cohesion: 0.15
Nodes (17): build_kp_html(), EmailConfig, EmailResult, EmailSender, Email sender for sales funnel — VK Mail (mail.ru) SMTP., Build commercial proposal HTML., Send emails via VK Mail SMTP., CallResult (+9 more)

### Community 8 - "leads_db.py"
Cohesion: 0.11
Nodes (22): Generate a text funnel report., get_industry_stats(), get_leads_by_industry(), get_leads_by_status(), get_stats(), import_excel(), init_db(), Lead (+14 more)

### Community 9 - "agent_sync.py"
Cohesion: 0.14
Nodes (22): build_agent_prompt(), get_conn(), get_conversation_history(), get_lead_context(), get_next_action(), init_sync_tables(), log_message(), Unified AI Agent — syncs voice calls and WhatsApp conversations per lead. Single (+14 more)

### Community 10 - "TechnomaxClient"
Cohesion: 0.19
Nodes (8): Technomax API client — fetch call data, statuses, bot info. Uses the platform RE, Get available call result statuses., Get all data needed for the dashboard., Client for Technomax platform REST API., Get or refresh JWT token., List autocall (voice bot) tasks., Get autocall task details with call stats., TechnomaxClient

### Community 11 - "telegram_notifier.py"
Cohesion: 0.18
Nodes (17): Send a test Telegram notification., test_notification(), disable_auto_send(), enable_auto_send(), _load_config(), notify_send(), Telegram notifier — sends notifications to Ali's Telegram when messages are sent, Enable automatic sending. (+9 more)

### Community 12 - "PipecatClient"
Cohesion: 0.13
Nodes (10): CallResult, PipecatClient, PipecatConfig, Pipecat Agent client — trigger AI calls from the sales funnel., Client for triggering AI voice calls via Pipecat agent., Check if Pipecat agent is running., Start a single AI call., Get call status and result. (+2 more)

### Community 13 - "FunnelEngine"
Cohesion: 0.15
Nodes (9): FunnelEngine, Connection, Process a call result and trigger follow-up actions.         Returns summary of, Run a batch of calls for new leads.         Returns list of leads to call (for t, Get leads scheduled for callback., Start AI calls via Pipecat agent for new leads.         Returns list of call res, Send WhatsApp КП to a lead., Send email КП to a lead. (+1 more)

### Community 14 - "technomax_agent.py"
Cohesion: 0.22
Nodes (13): create_technomax_agent(), Create AI agent on Technomax platform., authenticate(), _build_prompt(), create_sales_agent(), get_agent(), _headers(), list_agents() (+5 more)

### Community 15 - "db_conn.py"
Cohesion: 0.22
Nodes (12): close_conn(), execute(), execute_one(), execute_write(), get_conn(), Connection, Single connection manager for all modules. Every module should use get_conn() fr, Get a single shared connection (thread-safe via lock). (+4 more)

### Community 16 - "load_config"
Cohesion: 0.20
Nodes (11): get_notifications(), get_smtp_config(), load_config(), Get SMTP configuration (password masked)., Save SMTP configuration., Load config from file., Get notification config., Update notification config. (+3 more)

### Community 17 - "manifest.json"
Cohesion: 0.22
Nodes (8): background_color, description, display, icons, name, short_name, start_url, theme_color

### Community 18 - "db_extended.py"
Cohesion: 0.40
Nodes (5): get_conn(), init_extended_tables(), Connection, Extended database schema for all sales funnel features., Create all extended tables.

## Knowledge Gaps
- **12 isolated node(s):** `entrypoint.sh script`, `connect_whatsapp.sh script`, `deploy.sh script`, `Lead`, `name` (+7 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `WhatsAppClient` connect `WhatsAppClient` to `leads_db.py`, `FunnelEngine`, `main.py`, `funnel_engine.py`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `PipecatClient` connect `PipecatClient` to `FunnelEngine`, `main.py`, `funnel_engine.py`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Why does `FunnelEngine` connect `FunnelEngine` to `WhatsAppClient`, `main.py`, `funnel_engine.py`, `leads_db.py`, `PipecatClient`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `WhatsAppClient` (e.g. with `CallResult` and `FunnelConfig`) actually correct?**
  _`WhatsAppClient` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `entrypoint.sh script`, `connect_whatsapp.sh script`, `deploy.sh script` to the rest of the system?**
  _12 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_basic.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06359189378057302 - nodes in this community are weakly interconnected._
- **Should `funnel_features.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11734693877551021 - nodes in this community are weakly interconnected._