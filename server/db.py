"""SQLite by default. Postgres when DATABASE_URL starts with postgres.

SQL in the app uses '?' placeholders. adapt() turns those into '%s' for Postgres.
Read the URL inside connect(), not at import, so tests and Vercel can set it first.

Neon: use the pooled connection string (host contains "-pooler"). Each request
opens a connection and closes it, which is safe on Vercel's short-lived
functions. prepare_threshold=None is required because Neon's pooler
(PgBouncer in transaction mode) cannot keep prepared statements.
"""
from __future__ import annotations

import os
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SQLITE = os.path.join(ROOT, "data", "app.db")


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def sqlite_path() -> str:
    return os.environ.get("DRISHTI_DB", DEFAULT_SQLITE)


def dialect_of(url: str | None = None) -> str:
    url = database_url() if url is None else url
    if url.lower().startswith(("postgres://", "postgresql://")):
        return "postgres"
    return "sqlite"


def adapt(sql: str, dialect: str) -> str:
    if dialect == "postgres":
        return sql.replace("?", "%s")
    return sql


def normalize_postgres_url(url: str) -> str:
    """Accept postgres:// and require SSL unless the URL already chooses a mode."""
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if "sslmode=" not in url.split("?", 1)[-1]:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def postgres_connect_kwargs() -> dict:
    # Neon's pooled endpoint rejects named prepared statements.
    return {"prepare_threshold": None, "connect_timeout": 10}


class Conn:
    def __init__(self, raw, dialect: str):
        self.raw = raw
        self.dialect = dialect

    def execute(self, sql: str, params: tuple | list = ()):
        return self.raw.execute(adapt(sql, self.dialect), params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.raw.rollback()
        else:
            self.raw.commit()
        self.raw.close()
        return False


def _schema(dialect: str) -> list[str]:
    ledger_id = "id bigserial primary key" if dialect == "postgres" else "id integer primary key"
    return [
        """create table if not exists wallet(
            device text primary key,
            credits int not null default 0,
            used_free int not null default 0,
            email text)""",
        f"""create table if not exists ledger(
            {ledger_id},
            device text,
            ts text,
            delta int,
            reason text)""",
        """create table if not exists horoscope(
            day text, sign text, lang text, body text,
            primary key(day, sign, lang))""",
        """create table if not exists referral_codes(
            device text primary key,
            code text not null unique)""",
        """create table if not exists referral_pending(
            device text primary key,
            code text not null)""",
        """create table if not exists referral_grants(
            invitee text primary key,
            referrer text not null,
            code text not null,
            ts text not null)""",
        """create table if not exists magic_links(
            token text primary key,
            email text not null,
            device text not null,
            expires text not null,
            used int not null default 0)""",
        """create table if not exists accounts(
            email text primary key,
            created text not null)""",
        """create table if not exists sessions(
            token text primary key,
            email text not null,
            device text not null,
            created text not null)""",
        """create table if not exists profiles(
            id text primary key,
            owner text not null,
            body text not null,
            updated text not null)""",
        """create table if not exists chats(
            owner text not null,
            profile_id text not null,
            body text not null,
            updated text not null,
            primary key(owner, profile_id))""",
        """create table if not exists account_state(
            owner text primary key,
            active text,
            updated text not null)""",
        """create table if not exists play_purchases(
            token_hash text primary key,
            order_id text not null unique,
            token text,
            product_id text not null,
            package_name text not null,
            device text not null,
            state text not null,
            ts text not null,
            consumed_at text)""",
        "create index if not exists profiles_owner on profiles(owner)",
    ]


def _ensure(con: Conn):
    for stmt in _schema(con.dialect):
        con.execute(stmt)
    if con.dialect == "sqlite":
        cols = [r[1] for r in con.execute("pragma table_info(wallet)").fetchall()]
        if "email" not in cols:
            con.execute("alter table wallet add column email text")
    else:
        con.execute("alter table wallet add column if not exists email text")


def connect() -> Conn:
    url = database_url()
    if dialect_of(url) == "postgres":
        import psycopg
        raw = psycopg.connect(normalize_postgres_url(url), **postgres_connect_kwargs())
        con = Conn(raw, "postgres")
    else:
        path = sqlite_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        raw = sqlite3.connect(path)
        raw.execute("pragma busy_timeout=4000")
        con = Conn(raw, "sqlite")
    _ensure(con)
    return con
