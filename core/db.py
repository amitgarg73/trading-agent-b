"""
Supabase client for Strategy B.
All tables are prefixed b_ to coexist with Strategy A.
"""
from __future__ import annotations
from supabase import create_client, Client
from config.settings import SUPABASE_URL, SUPABASE_KEY

_client: Client | None = None


def _get() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def select(table: str, filters: dict | None = None,
           order: str | None = None, limit: int | None = None) -> list[dict]:
    q = _get().table(table).select("*")
    for k, v in (filters or {}).items():
        q = q.is_(k, "null") if v is None else q.eq(k, v)
    if order:
        q = q.order(order, desc=True)
    if limit:
        q = q.limit(limit)
    return q.execute().data or []


def insert(table: str, row: dict) -> dict:
    return _get().table(table).insert(row).execute().data[0]


def upsert(table: str, row: dict, on_conflict: str = "id") -> dict:
    return _get().table(table).upsert(row, on_conflict=on_conflict).execute().data[0]


def update(table: str, filters: dict, values: dict) -> list[dict]:
    q = _get().table(table).update(values)
    for k, v in filters.items():
        q = q.eq(k, v)
    return q.execute().data or []


def delete(table: str, filters: dict) -> list[dict]:
    q = _get().table(table).delete()
    for k, v in filters.items():
        q = q.eq(k, v)
    return q.execute().data or []


def rpc(fn: str, params: dict | None = None) -> list[dict]:
    return _get().rpc(fn, params or {}).execute().data or []
