# HotelChat – Amapola Resort Chat Platform

## 1  Overview
HotelChat is a Flask / Flask-SocketIO application that provides:
- Web dashboard for human agents
- Real-time messaging (Socket.IO / Redis)
- AI powered responses (OpenAI)
- WhatsApp integration (Twilio)
- Celery workers for background processing
- Diagnostic & monitoring utilities

The repository already contains all code, test utilities, deployment artefacts and Phase-by-Phase documentation.  This README ties everything together and answers the most common setup questions.

## 2  Project Structure (high-level)
```
chat_server.py            # Main Flask-SocketIO application
tasks.py                  # Celery tasks (OpenAI & WhatsApp)
openai_diag_tool.py       # Stand-alone OpenAI diagnostic utility
socketio_diag_tool.py     # Stand-alone Socket.IO tester
performance_monitor.py    # Runtime metrics dashboard
staging_verification.py   # Render staging checks
production_verification.py# Render production checks
templates/                # Jinja2 & static UI
static/                   # JS / CSS
```

## 3  Prerequisites
- Python 3.9 (exact version pinned in `render.yaml`)
- PostgreSQL (or a docker instance)  
- Redis server  
- (Optional) Twilio WhatsApp sandbox  
- OpenAI account + API key

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4  Environment variables
All required keys are listed in `.env.example`.  
Create your own `.env` in the project root:

```bash
cp .env.example .env
# edit the values
```

The application automatically loads `.env` via `python-dotenv`.

Most important keys:

| Key                  | Purpose                         |
|----------------------|---------------------------------|
| OPENAI_API_KEY       | Required for AI responses       |
| DATABASE_URL         | PostgreSQL connection string    |
| REDIS_URL            | Redis for Socket.IO & Celery    |
| SECRET_KEY           | Flask session crypto            |
| TWILIO_*             | WhatsApp integration            |

> NOTE: Without a valid `OPENAI_API_KEY` the AI routes will fail.  
> You **can** still test the rest of the stack (UI, Socket.IO, DB) by leaving the key blank and setting `AI_ENABLED=0` in the DB or mocking the OpenAI client (see §7).

## 5  Running locally
```bash
# 1. Ensure PostgreSQL & Redis are running and .env is set
source .venv/bin/activate
python chat_server.py
# Dashboard → http://localhost:5000 (login with any username)
```

Celery worker (optional for WhatsApp):
```bash
celery -A tasks worker -l INFO -Q default,whatsapp --concurrency=3
```

## 6  Diagnostics & Tests
| Tool                       | Command                                   |
|----------------------------|-------------------------------------------|
| OpenAI connectivity        | `python openai_client_test.py`            |
| Advanced OpenAI diag       | `python openai_diag_tool.py --prompt "hi"`|
| Socket.IO loop-back test   | `python socketio_diag_tool.py`            |
| End-to-end integration     | `python integration_test.py --all`        |
| Performance dashboard      | Visit `/admin/dashboard` while app runs   |
| Render staging check       | `python staging_verification.py --url <url>`|
| Render production check    | `python production_verification.py --url <url>`|

## 7  Testing without real API keys
Full AI functionality requires valid keys, but you can:
1. Set `OPENAI_API_KEY=dummy` and export `MOCK_OPENAI=1` (code already checks this flag in diag tools).  
2. Override the OpenAI client inside `chat_server.py` during dev:

```python
if os.getenv("MOCK_OPENAI"):
    class _Mock:
        async def chat(self, *a, **k): return type("R",(),{"choices":[type("C",(),{"message":type("M",(),{"content":"MOCK REPLY"})})]})
    openai_client = _Mock()
```

3. Run all UI / Socket.IO flows – only AI replies are dummy.

## 8  Deployment (Render)
- Fill Render dashboard vars as per `deployment_checklist.md`
- `render.yaml` already defines `hotelchat-web` and `hotelchat-worker`
- Build command: `pip install -r requirements.txt`
- Start command: see `Procfile`

## 9  Maintenance
- Review `performance_dashboard.html` for live metrics
- Rotate logs (ConcurrentRotatingFileHandler already configured)
- Use `production_verification.py` after every deploy

## 10  FAQ
**Where is the .env file?**  
Place it in the repository root (`/Users/spencerpro/hotelchat/.env`); it is not committed.

**Can I miss anything?**  
Run the health checks: `python performance_monitor.py --interval 5` plus the verification scripts.  If all diagnostics pass, the environment is correctly configured.

**Need further help?**  
See the detailed phase documents (`technical_report.md`, `deployment_procedure.md`, etc.).

## 11  Offline / Key-less Testing
You can exercise the full stack without valid third-party keys:

1. Duplicate `.env.example` → `.env` and set dummy values  
   ```
   OPENAI_API_KEY=dummy
   MOCK_OPENAI=1          # activates mock mode in diagnostic tools
   ```
2. Start services as usual (`python chat_server.py`, Celery workers, etc.).  
   All OpenAI calls will return a static “MOCK REPLY”; Socket.IO, DB and Redis still work.

## 12  Health-Check & Diagnostics Suite
| Purpose | Command |
|---------|---------|
| Dependency / env sanity | `python performance_monitor.py --interval 1` |
| OpenAI mock / live test | `python openai_client_test.py` |
| Socket.IO round-trip    | `python socketio_diag_tool.py` |
| Full E2E (mock OK)      | `python integration_test.py --all` |
| Staging verification    | `python staging_verification.py --url <url>` |
| Prod verification       | `python production_verification.py --url <url>` |

## 13  Environment Files
`.env.example` (already in repo) lists every variable used in production.  
Create a local `.env` in the project root; it is auto-loaded via `python-dotenv`.
