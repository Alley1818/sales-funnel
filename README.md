# Sales Funnel — AI-обзвон + WhatsApp/Email догонка

## Архитектура

```
┌─────────────────────────────────────────────────────────┐
│                    ВОРОНКА ПРОДАЗ                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. ЗАГРУЗКА ЛИДОВ                                      │
│     Excel → leads.db (SQLite)                           │
│                                                         │
│  2. AI-ОБЗВОН (Technomax)                               │
│     Таргет-звонок → голосовой AI-агент                  │
│     → определяет интерес/отказ/недозвон                  │
│     → webhook → /api/call/result                        │
│                                                         │
│  3. ДОГОНКА (автоматическая)                             │
│     ├─ Заинтересован → WhatsApp КП + Email КП           │
│     ├─ Перезвонить   → повторный звонок через N часов   │
│     ├─ Отказ         → пометить, не беспокоить           │
│     └─ Недозвон      → WhatsApp fallback                 │
│                                                         │
│  4. ДАШБОРД                                             │
│     /api/stats — статистика воронки                      │
│     /api/report — текстовый отчёт                        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Компоненты

| Компонент | Файл | Описание |
|-----------|------|----------|
| БД лидов | `leads_db.py` | SQLite, импорт из Excel |
| WhatsApp | `whatsapp_client.py` | Evolution API v2 клиент |
| Email | `email_sender.py` | VK Mail SMTP |
| Движок | `funnel_engine.py` | Оркестрация воронки |
| API | `main.py` | Flask API + CLI |
| Docker | `docker-compose.yml` | Evolution API + Redis |

## Быстрый старт

```bash
# 1. Зависимости
pip install -r requirements.txt

# 2. Запуск Evolution API (WhatsApp)
docker compose up -d

# 3. Импорт базы
python main.py import "/path/to/base.xlsx"

# 4. Подключение WhatsApp (отсканировать QR)
python main.py wa-connect
python main.py wa-qr

# 5. Запуск API сервера
python main.py serve

# 6. CLI команды
python main.py report           # Статистика
python main.py batch Турагентства  # Лиды для обзвона
```

## API Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/stats` | Статистика воронки |
| GET | `/api/leads/next?industry=...&limit=10` | Следующие лиды |
| POST | `/api/call/result` | Результат звонка от Technomax |
| GET | `/api/leads/<id>` | Данные лида |
| PUT | `/api/leads/<id>/status` | Обновить статус |
| POST | `/api/whatsapp/send` | Отправить WhatsApp |
| GET | `/api/whatsapp/qr` | QR-код для WhatsApp |
| GET | `/api/report` | Текстовый отчёт |

## Webhook от Technomax

```json
POST /api/call/result
{
    "lead_id": 123,
    "result": "interested",
    "notes": "Попросил отправить КП на email"
}
```

Результаты: `interested`, `callback`, `refused`, `no_answer`, `wrong_number`, `voicemail`

## Конфигурация (.env)

```bash
# Evolution API (WhatsApp)
EVO_API_URL=http://localhost:8080
EVO_API_KEY=sales_funnel_evo_key_2026
EVO_INSTANCE=sales_funnel

# SMTP (VK Mail)
SMTP_HOST=smtp.mail.ru
SMTP_PORT=465
SMTP_USERNAME=your@mail.ru
SMTP_PASSWORD=your_app_password
SMTP_FROM_NAME=Компания
SMTP_USE_SSL=true
```
