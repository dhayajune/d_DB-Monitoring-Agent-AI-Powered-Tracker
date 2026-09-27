"""DB Monitoring Agent for wind turbine installation records (workflow_details), powered by a local Ollama model.

How it works:
  1. A cheap polling loop checks MAX(id) every POLL_INTERVAL_SECONDS (no LLM work while nothing changes).
  2. When new rows appear, the local LLM is started with DB tools. It reads the new rows, investigates
     (turbine history, site overview) and raises an alert via the send_alert tool.
  3. If the LLM is unavailable (Ollama not running, model missing, ...) or never calls send_alert,
     a plain rule-based alert is raised, so new data is never missed.

    python monitor_agent.py                  # run forever + start dashboard and open it in the browser
    python monitor_agent.py --once           # check once and exit
    python monitor_agent.py --no-llm         # rule-based alerts only, no LLM calls
    python monitor_agent.py --no-dashboard   # agent only
"""
import argparse
import json
import os
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import httpx
import ollama
import pymysql

import dashboard_server
from db import monitor_connection

try:
    import winsound
except ImportError:  # not on Windows
    winsound = None

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:latest")
THINK = os.getenv("OLLAMA_THINK", "false").lower() == "true"  # reasoning mode: better analysis, slower
MAX_ROWS_PER_BATCH = 50
MAX_AGENT_STEPS = 8

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "monitor_state.json"
ALERT_LOG = BASE_DIR / "alerts.jsonl"  # one JSON alert per line; the dashboard reads this

SEVERITY_COLORS = {"INFO": "\033[96m", "WARNING": "\033[93m"}
RESET = "\033[0m"

SYSTEM_PROMPT = """You are the DB Monitoring Agent for wind turbine installation projects.
The MySQL table workflow_details stores one row per installation event: which turbine (turbine_id,
turbine_model), at which wind farm (site_name) and location, which installation_phase
(Foundation -> Tower Erection -> Nacelle Installation -> Rotor & Blade Installation ->
Electrical & Grid Connection -> Commissioning), which technician did the work, when (processed_at),
the status (OPEN = foundation stage, PROCESSING = erection/electrical work, COMPLETED = commissioned),
the work notes, and any issue found on site with the fix_applied (fix_applied empty = issue not fixed yet).

You are started whenever new rows are inserted. Work only through the tools:
1. Call get_new_records to read the new rows.
2. Optionally call get_turbine_history to see a turbine's progress, or get_site_overview for the picture
   per wind farm (turbines open / processing / commissioned, issues still waiting for a fix).
3. Finish by calling send_alert exactly once, summarising every new event: record id, turbine, site and
   location, phase, technician, time, status, and the issue and fix if any, plus one short useful observation
   (e.g. a turbine was just commissioned, or how many turbines at that site are still in progress).
   Severity: WARNING if a new record has an issue without a fix yet, otherwise INFO.
   Keep the message short: one line per event, then at most two lines.
Never invent data that the tools did not return."""


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = monitor_connection()
    with conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def to_json(rows) -> str:
    return json.dumps(rows, default=str, indent=1)


def load_last_seen_id() -> int | None:
    try:
        return json.loads(STATE_FILE.read_text())["last_seen_id"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return None


def save_state(last_id: int, llm_enabled: bool) -> None:
    """Persist the watermark plus a heartbeat the dashboard uses to show whether the agent is running."""
    STATE_FILE.write_text(json.dumps({
        "last_seen_id": last_id,
        "last_check": datetime.now().isoformat(timespec="seconds"),
        "poll_interval": POLL_INTERVAL,
        "model": MODEL if llm_enabled else None,
    }))


def current_max_id() -> int:
    return int(query("SELECT COALESCE(MAX(id), 0) AS max_id FROM workflow_details")[0]["max_id"])


def fetch_rows(since_id: int, upto_id: int) -> list[dict]:
    return query(
        "SELECT * FROM workflow_details WHERE id > %s AND id <= %s ORDER BY id LIMIT %s",
        (since_id, upto_id, MAX_ROWS_PER_BATCH),
    )


def emit_alert(severity: str, title: str, message: str, since_id: int, upto_id: int, source: str) -> None:
    """Deliver an alert: console banner, alerts.jsonl (shown on the dashboard), and a system sound for WARNING."""
    color = SEVERITY_COLORS.get(severity, "")
    bar = "=" * 70
    print(f"\n{color}{bar}\n  ALERT [{severity}] {title}\n{bar}{RESET}\n{message}\n{color}{bar}{RESET}\n", flush=True)
    alert = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "severity": severity,
        "title": title,
        "message": message,
        "first_id": since_id + 1,
        "last_id": upto_id,
        "source": source,
    }
    with ALERT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(alert) + "\n")
    if severity == "WARNING" and winsound:
        winsound.MessageBeep(winsound.MB_ICONHAND)


def fallback_alert(since_id: int, upto_id: int, reason: str) -> None:
    """Plain alert built without the LLM, used when the agent can't run."""
    rows = fetch_rows(since_id, upto_id)
    lines = []
    for r in rows:
        line = (f"#{r['id']} {r['processed_at']} | {r['turbine_id']} at {r['site_name']} ({r['location']}) | "
                f"{r['installation_phase']} by {r['technician']} -> {r['status']}")
        if r["issue"]:
            line += f" | issue: {r['issue']}" + (f" - fixed: {r['fix_applied']}" if r["fix_applied"] else " - not fixed yet")
        lines.append(line)
    severity = "WARNING" if any(r["issue"] and not r["fix_applied"] for r in rows) else "INFO"
    emit_alert(severity, f"{upto_id - since_id} new installation record(s)", "\n".join(lines),
               since_id, upto_id, source=f"rule-based ({reason})")


def build_tools(since_id: int, upto_id: int, sent_alerts: list) -> dict:
    """Tools for one batch of new rows. All DB access is read-only and parameterised.

    Ollama builds each tool's JSON schema from the function signature and Google-style docstring.
    """

    def get_new_records() -> str:
        """Return the newly inserted workflow_details rows that triggered this run.

        Returns:
            JSON list of the new rows.
        """
        return to_json(fetch_rows(since_id, upto_id))

    def get_turbine_history(turbine_id: str) -> str:
        """Return every installation event recorded for one turbine, oldest first.

        Args:
            turbine_id: Turbine tag, e.g. KYR-WTG-07.

        Returns:
            JSON list of the turbine's events.
        """
        return to_json(query(
            "SELECT * FROM workflow_details WHERE turbine_id = %s ORDER BY processed_at, id LIMIT 100",
            (str(turbine_id),),
        ))

    def get_site_overview(site_name: str = "") -> str:
        """Return, per wind farm, how many turbines are open / processing / commissioned right now and how many
        issues are still waiting for a fix.

        Args:
            site_name: Limit to one wind farm (exact name), or empty for all sites.

        Returns:
            JSON list with one object per site.
        """
        latest = ("SELECT w.* FROM workflow_details w JOIN (SELECT turbine_id, MAX(id) AS mid "
                  "FROM workflow_details GROUP BY turbine_id) t ON w.id = t.mid")
        where, params = ("WHERE l.site_name = %s", (str(site_name),)) if site_name else ("", ())
        return to_json(query(
            f"""SELECT l.site_name, l.location,
                       SUM(l.status = 'OPEN') AS turbines_open,
                       SUM(l.status = 'PROCESSING') AS turbines_processing,
                       SUM(l.status = 'COMPLETED') AS turbines_commissioned,
                       (SELECT COUNT(*) FROM workflow_details i WHERE i.site_name = l.site_name
                          AND i.issue IS NOT NULL AND i.fix_applied IS NULL) AS issues_not_fixed
                FROM ({latest}) l {where} GROUP BY l.site_name, l.location ORDER BY l.site_name""",
            params,
        ))

    def send_alert(severity: str, title: str, message: str) -> str:
        """Raise an alert to the operations team. Call this exactly once, at the end.

        Args:
            severity: WARNING if a new record has an issue that is not fixed yet, otherwise INFO.
            title: One-line headline.
            message: Alert body, one line per new event plus a short observation.

        Returns:
            Delivery confirmation.
        """
        severity = str(severity).strip().upper()
        if severity not in SEVERITY_COLORS:
            return "Error: severity must be INFO or WARNING."
        if sent_alerts:
            return "Alert already sent for this batch."
        emit_alert(severity, str(title), str(message), since_id, upto_id, source=f"ollama:{MODEL}")
        sent_alerts.append(severity)
        return "Alert delivered."

    return {f.__name__: f for f in (get_new_records, get_turbine_history, get_site_overview, send_alert)}


def run_agent(client: ollama.Client, think: bool | None, since_id: int, upto_id: int) -> None:
    """Manual tool-calling loop: ask the model, run the tools it calls, feed results back, repeat."""
    sent_alerts: list[str] = []
    tools = build_tools(since_id, upto_id, sent_alerts)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{upto_id - since_id} new record(s) were inserted into workflow_details "
                                    f"(ids {since_id + 1}..{upto_id}). Investigate and send the alert."},
    ]
    nudged = False
    for _ in range(MAX_AGENT_STEPS):
        response = client.chat(model=MODEL, messages=messages, tools=list(tools.values()), think=think,
                               options={"temperature": 0.2})
        reply = response.message
        messages.append(reply)

        if not reply.tool_calls:
            if sent_alerts or nudged:
                break
            # Small local models sometimes answer in prose instead of calling the tool - remind once.
            nudged = True
            messages.append({"role": "user", "content": "Do not answer in text. Call the send_alert tool now."})
            continue

        for call in reply.tool_calls:
            name, args = call.function.name, dict(call.function.arguments or {})
            log(f"  agent -> {name}({json.dumps(args, default=str)[:120]})")
            fn = tools.get(name)
            try:
                result = fn(**args) if fn else f"Error: unknown tool '{name}'."
            except (TypeError, ValueError) as e:
                result = f"Error: bad arguments for {name}: {e}"
            messages.append({"role": "tool", "content": result, "tool_name": name})

        if sent_alerts:
            break

    if not sent_alerts:
        fallback_alert(since_id, upto_id, f"{MODEL} did not send an alert")


def connect_llm() -> tuple[ollama.Client, bool | None] | None:
    """Return (client, think flag) if Ollama is up and the model is installed, else None."""
    client = ollama.Client(host=OLLAMA_HOST, timeout=300)
    try:
        info = client.show(MODEL)
    except ollama.ResponseError as e:
        log(f"WARNING: model '{MODEL}' not available in Ollama ({e.error}). Run: ollama pull {MODEL}")
        return None
    except (ConnectionError, httpx.HTTPError):
        log(f"WARNING: Ollama is not reachable at {OLLAMA_HOST}. Start the Ollama app.")
        return None
    capabilities = info.capabilities or []
    if "tools" not in capabilities:
        log(f"WARNING: model '{MODEL}' does not support tool calling. Pick another OLLAMA_MODEL.")
        return None
    # Only send the think flag to models that support it; others reject it.
    return client, (THINK if "thinking" in capabilities else None)


def check_once(llm, last_seen: int) -> int:
    """Check for new rows, alert on them, and return the new last-seen id."""
    max_id = current_max_id()
    if max_id <= last_seen:
        return last_seen

    upto_id = min(max_id, last_seen + MAX_ROWS_PER_BATCH)
    log(f"New data detected: ids {last_seen + 1}..{upto_id}")
    if llm is None:
        fallback_alert(last_seen, upto_id, "LLM disabled")
    else:
        try:
            run_agent(*llm, last_seen, upto_id)
        except ollama.ResponseError as e:
            fallback_alert(last_seen, upto_id, f"Ollama error: {e.error}")
        except (ConnectionError, httpx.HTTPError):
            fallback_alert(last_seen, upto_id, "Ollama not reachable")

    return upto_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor workflow_details and alert on new rows.")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--no-llm", action="store_true", help="rule-based alerts only, don't call the LLM")
    parser.add_argument("--no-dashboard", action="store_true", help="don't start the dashboard / open the browser")
    args = parser.parse_args()

    if not (args.once or args.no_dashboard):
        url = dashboard_server.start_in_background()
        log(f"Dashboard: {url}")
        webbrowser.open(url)

    llm = None if args.no_llm else connect_llm()

    last_seen = load_last_seen_id()
    if last_seen is None:
        last_seen = current_max_id()
        save_state(last_seen, llm is not None)
        log(f"First run: baseline set at id {last_seen}; only rows inserted from now on will alert.")

    log(f"Monitoring workflow_details every {POLL_INTERVAL}s (LLM: {'off' if llm is None else 'ollama/' + MODEL}). Ctrl+C to stop.")
    try:
        while True:
            try:
                last_seen = check_once(llm, last_seen)
                save_state(last_seen, llm is not None)
            except pymysql.MySQLError as e:
                log(f"Database error: {e} - retrying in {POLL_INTERVAL}s")
            if args.once:
                break
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
