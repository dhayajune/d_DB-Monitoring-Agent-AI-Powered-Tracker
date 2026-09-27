"""Push wind turbine installation events into the DB so you can watch the agent react.

    python simulate_inserts.py                            # move a turbine to its next phase (or start a new one)
    python simulate_inserts.py -n 5                       # 5 events
    python simulate_inserts.py --backfill 300 --days 30   # history for the dashboard charts
"""
import argparse
import random
from datetime import datetime, timedelta

from db import app_connection
from workflow import COLUMNS, INSERT_SQL, PHASES, make_event, new_turbine, next_phase


def row(event: dict, when: datetime) -> tuple:
    return tuple({**event, "processed_at": when}[c] for c in COLUMNS)


def unique_turbine(taken: set) -> dict:
    while True:
        t = new_turbine()
        if t["turbine_id"] not in taken:
            taken.add(t["turbine_id"])
            return t


def in_progress_turbines(cur) -> list[dict]:
    """Turbines whose latest event is not COMPLETED, with their latest phase."""
    cur.execute("""SELECT w.turbine_id, w.site_name, w.location, w.turbine_model, w.installation_phase
                   FROM workflow_details w
                   JOIN (SELECT turbine_id, MAX(id) AS mid FROM workflow_details GROUP BY turbine_id) t ON w.id = t.mid
                   WHERE w.status <> 'COMPLETED'""")
    return list(cur.fetchall())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=1, help="number of events to insert")
    parser.add_argument("--backfill", type=int, metavar="N", help="insert N historical events spread over --days")
    parser.add_argument("--days", type=int, default=30, help="history window for --backfill")
    args = parser.parse_args()

    conn = app_connection()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT turbine_id FROM workflow_details")
        taken = {r["turbine_id"] for r in cur.fetchall()}
        if args.backfill:
            backfill(cur, args.backfill, args.days, taken)
            return
        for _ in range(args.n):
            active = in_progress_turbines(cur)
            if active and random.random() < 0.7:
                t = random.choice(active)
                phase = next_phase(t.pop("installation_phase"))
            else:
                t, phase = unique_turbine(taken), PHASES[0]
            event = make_event(t, phase)
            cur.execute(INSERT_SQL, row(event, datetime.now()))
            issue = f" | issue: {event['issue']}" + (" (fixed)" if event["fix_applied"] else " (open)") if event["issue"] else ""
            print(f"Inserted id={cur.lastrowid}: {t['turbine_id']} {phase} -> {event['status']}{issue}")


def backfill(cur, count: int, days: int, taken: set) -> None:
    """Insert whole installation journeys (Foundation -> ... -> Commissioning) with past timestamps."""
    now = datetime.now()
    rows = []
    while len(rows) < count:
        turbine = unique_turbine(taken)
        t = now - timedelta(days=random.uniform(0, days))
        t = t.replace(hour=random.randint(7, 18), minute=random.randint(0, 59))
        for phase in PHASES:
            if t > now:
                break
            rows.append(row(make_event(turbine, phase), t))
            t += timedelta(hours=random.uniform(3, 40))  # next phase starts hours to days later
    rows.sort(key=lambda r: r[-1])
    cur.executemany(INSERT_SQL, rows)
    print(f"Backfilled {len(rows)} installation events over the last {days} days.")


if __name__ == "__main__":
    main()
