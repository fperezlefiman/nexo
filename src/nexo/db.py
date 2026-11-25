"""Postgres access: one connection per query, explicit commits, and no credentials in the code.

The connection string comes from the environment or from a 600-mode file. It is never a default
value: a default is how the password ended up committed to a public repository (ADR 0020).
"""

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SCHEMA = Path(__file__).parent / "schema.sql"
SECRET_FILE = Path(
    os.environ.get("NEXO_DATABASE_URL_FILE", str(Path.home() / ".config" / "nexo" / "database_url"))
)
CONNECT_TIMEOUT = 10


def database_url() -> str:
    """Environment first, then the secret file. Raised, never guessed."""
    url = os.environ.get("NEXO_DATABASE_URL", "").strip()
    if url:
        return url
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    raise RuntimeError(
        f"NEXO_DATABASE_URL is not set and the secret file {SECRET_FILE} does not exist"
    )


def connect() -> psycopg.Connection:
    # shortcut: one connection per query, no pool. At one operator that is a few connects per
    # message; add psycopg_pool when concurrent workers make it matter.
    return psycopg.connect(
        database_url(), row_factory=dict_row, autocommit=False, connect_timeout=CONNECT_TIMEOUT
    )


def migrate() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA.read_text())
    print("schema applied")


def kv_get(conn, key: str, default: str = "") -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM kv WHERE key = %s", (key,))
        row = cur.fetchone()
    return row["value"] if row else default


def kv_set(conn, key: str, value: str | int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO kv (key, value, updated_at) VALUES (%s, %s, now())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
            (key, str(value)),
        )
    conn.commit()
