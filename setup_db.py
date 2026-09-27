"""One-time setup: create workflow_db, the workflow_details table, sample rows and the two DB accounts.

Run as MySQL root:  python setup_db.py
"""
import os
from pathlib import Path

from db import DB_NAME, connect

SCHEMA_FILE = Path(__file__).parent / "sql" / "schema.sql"


def main() -> None:
    conn = connect("DB_ROOT_USER", "DB_ROOT_PASSWORD", database=None)
    with conn, conn.cursor() as cur:
        for statement in SCHEMA_FILE.read_text(encoding="utf-8").split(";"):
            lines = [l for l in statement.splitlines() if not l.strip().startswith("--")]
            sql = "\n".join(lines).strip()
            if sql:
                cur.execute(sql)

        accounts = [
            # (user env, password env, privileges) - the agent only ever gets SELECT.
            ("APP_DB_USER", "APP_DB_PASSWORD", "SELECT, INSERT, UPDATE"),
            ("MONITOR_DB_USER", "MONITOR_DB_PASSWORD", "SELECT"),
        ]
        for user_env, pw_env, privileges in accounts:
            user, password = os.environ[user_env], os.environ[pw_env]
            cur.execute("CREATE USER IF NOT EXISTS %s@'localhost' IDENTIFIED BY %s", (user, password))
            cur.execute("ALTER USER %s@'localhost' IDENTIFIED BY %s", (user, password))
            cur.execute(f"GRANT {privileges} ON `{DB_NAME}`.* TO %s@'localhost'", (user,))

        cur.execute(f"SELECT COUNT(*) AS n FROM `{DB_NAME}`.workflow_details")
        print(f"Setup complete. workflow_details has {cur.fetchone()['n']} rows.")


if __name__ == "__main__":
    main()
