"""Shared MySQL connection helpers. Credentials come from .env."""
import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "workflow_db")


def connect(user_env: str, password_env: str, database: str | None = DB_NAME) -> pymysql.connections.Connection:
    """Open a connection using the user/password stored in the given env vars."""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=os.environ[user_env],
        password=os.environ[password_env],
        database=database,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def monitor_connection():
    """Read-only account used by the monitoring agent."""
    return connect("MONITOR_DB_USER", "MONITOR_DB_PASSWORD")


def app_connection():
    """Read/write account used by the application (and the test data simulator)."""
    return connect("APP_DB_USER", "APP_DB_PASSWORD")
