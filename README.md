# DB Monitoring Agent – AI-Powered Wind Turbine Installation Tracker

An AI agent running on a local Ollama model checks a MySQL database every 5 seconds for new wind turbine installation records.
When new data arrives, it looks up the turbine's history and its wind farm's progress, then raises an alert, flagging any site issue that hasn't been fixed yet.
A live dashboard shows gauges, charts and pop-up alerts for installation progress by site, phase and status, with a desktop form for entering new records.

![DB Monitoring Agent demo](DB%20Monitoring%20Agent.gif)

## How it works

```
 Insert form / your app ──INSERT──► MySQL workflow_details
                                        │
             monitor_agent.py ──poll every 5s──┤
               └─ new rows → Ollama tool calls:  get_new_records, get_turbine_history,
                                                get_site_overview, send_alert ──► alerts.jsonl
               └─ also serves the dashboard ──► http://127.0.0.1:8050 (opened automatically)
```

- A cheap polling loop checks for new rows; the LLM only runs when there is new data.
- The model works through **tool calling**: it reads the new rows, optionally checks a turbine's history or a
  site overview, and finishes by calling `send_alert`.
- If Ollama is unavailable, a rule-based alert is sent instead, so no new record is missed.
- The agent and dashboard use a **read-only** database account; all queries are parameterised.

## Tech stack

Python · MySQL 8 · Ollama (local LLM with tool calling, e.g. `qwen3.5`) · Chart.js · Tkinter

## Setup

1. **Install prerequisites**
   - [MySQL Server 8](https://dev.mysql.com/downloads/mysql/) (optionally MySQL Workbench)
   - [Ollama](https://ollama.com/download), then pull a model that supports tool calling:
     `ollama pull qwen3.5`
   - Python 3.10+

2. **Install Python packages**
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure credentials**: copy `.env.example` to `.env` and replace every `<...>` placeholder:
   - `DB_ROOT_PASSWORD`: the MySQL root password you chose when installing MySQL
   - `APP_DB_PASSWORD`, `MONITOR_DB_PASSWORD`: new passwords of your choice; `setup_db.py` creates these accounts

4. **Create the database, table and accounts**
   ```powershell
   python setup_db.py
   python simulate_inserts.py --backfill 300 --days 30   # optional: demo history for the charts
   ```

## Run

```powershell
python monitor_agent.py        # agent + dashboard (browser opens automatically)
python insert_record_gui.py    # insert form (second terminal)
```

Or double-click **`start_all.bat`** (Windows) to open both. Insert a record and watch the dashboard: a **New record**
pop-up appears within seconds, followed by the agent's **Agent alert**. You can also push scripted test data with
`python simulate_inserts.py -n 3`.

## Records

Each row in `workflow_details` is one installation event:

| Column | Example |
|---|---|
| `turbine_id`, `turbine_model` | KYR-WTG-07, 5.7 MW - 163 m rotor |
| `site_name`, `location` | Kayathar Ridge Wind Park, Thoothukudi, Tamil Nadu, India |
| `installation_phase` | Foundation → Tower Erection → Nacelle Installation → Rotor & Blade Installation → Electrical & Grid Connection → Commissioning |
| `technician`, `processed_at` | arjun.v, 2026-09-27 12:41:36 |
| `status` | **OPEN** (Foundation), **PROCESSING** (erection and electrical phases), **COMPLETED** (Commissioning) |
| `notes` | Work carried out |
| `issue`, `fix_applied` | Issue found on site and how it was fixed (`fix_applied` empty = not fixed yet) |

Wind farms, turbine models and technicians in the demo data are fictional.
Agent alerts are **Info** (new activity) or **Warning** (a new record has an issue that is not fixed yet).

## Dashboard

- **Header counters**: turbines currently Open / Processing / Completed
- **Gauges**: issue fix rate, events in the last hour, average installation time, DB response time
- **KPI tiles**: events, turbines started, phases in progress, completed, issues reported, issues fixed, agent alerts
- **Charts**: activity over time, current turbine status, installation progress by site, events & issues by phase
- **Alerts** feed with severity filter; click *Records #x–y* to jump to those rows
- **Live activity** table with search and status filter; new rows are highlighted as they arrive
- **Database overview**: MySQL version, uptime, connections, rows, turbines, wind farms, table size, agent status

## Configuration (`.env`)

| Variable | Default | |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | 5 | How often the agent checks for new rows |
| `OLLAMA_MODEL` | qwen3.5:latest | Any local model with the *tools* capability (`llama3.1`, `gemma4:12b`, ...) |
| `OLLAMA_THINK` | false | `true` = reasoning mode: better analysis, slower |
| `OLLAMA_HOST` | http://127.0.0.1:11434 | |
| `DASHBOARD_PORT` | 8050 | |

## Project structure

| File | Purpose |
|---|---|
| `monitor_agent.py` | Polling loop, Ollama tool-calling loop, alerting; starts the dashboard (`--once`, `--no-llm`, `--no-dashboard`) |
| `dashboard_server.py`, `static/dashboard.html` | Dashboard server and page (Chart.js bundled in `static/vendor/`, works offline) |
| `insert_record_gui.py` | Tkinter form for inserting records |
| `workflow.py` | Phases, statuses, sites, issue catalogue and the demo event generator |
| `simulate_inserts.py` | Scripted test data and history backfill |
| `setup_db.py`, `sql/schema.sql` | Database, table and accounts |
| `db.py` | Database connection helpers (reads `.env`) |
| `start_all.bat` | Launches the agent and the insert form on Windows |

## Notes

- On first run the agent records the current max id as a baseline and only alerts on rows inserted afterwards
  (delete `monitor_state.json` to re-baseline).
- Detection is by auto-increment `id` (inserts); updates to existing rows are not detected.
- The dashboard binds to 127.0.0.1 only and has no login. Add authentication before exposing it to other machines.
