"""Outfit memory in SQL: every recommendation and every confirmed wearing, compared by *combination*.

Rules implemented here (the recommender calls these helpers):
* ``recent_signatures`` - combos recommended or worn in the last N days (skipped ones don't count against you).
* ``yesterday_signatures`` - the hard "never repeat yesterday" set.
* Saving is idempotent: the UNIQUE(outfit_date, kind, signature) index prevents duplicate rows even under races.
* Marking an outfit "worn" updates each item's usage_count / last_worn_date in the same transaction; changing your mind
  (worn -> skipped) reverses it. Only one outfit can be "worn" per day.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any, Iterable

from db import Database, now_iso
from outfit_models import Recommendation, WeatherNeeds

STATUSES = ("recommended", "worn", "skipped")


def _d(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def save_recommendation(db: Database, rec: Recommendation, outfit_date: date | str, occasion: str = "",
                        weather: WeatherNeeds | dict[str, Any] | None = None, summary: str = "") -> tuple[int, bool]:
    """Store a recommendation. Returns (outfit_id, created). Re-saving the same combination on the same date is a no-op."""
    w = weather.to_dict() if isinstance(weather, WeatherNeeds) else (weather or {})
    sig, day = rec.signature, _d(outfit_date)
    if rec.kind == "wardrobe" and not rec.item_ids:
        raise ValueError("A wardrobe outfit needs at least one wardrobe item.")
    with db.tx() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO outfits(outfit_date, kind, signature, status, favourite, occasion, weather_json, colours,
               categories, summary, created_at, updated_at) VALUES (?,?,?,'recommended',0,?,?,?,?,?,?,?)""",
            (day, rec.kind, sig, occasion, json.dumps(w), ", ".join(rec.colours), ", ".join(rec.categories), summary[:500], now_iso(), now_iso()))
        created = cur.rowcount == 1
        row = c.execute("SELECT id FROM outfits WHERE outfit_date=? AND kind=? AND signature=?", (day, rec.kind, sig)).fetchone()
        oid = int(row["id"])
        if created:
            for p in rec.all_pieces:
                c.execute("INSERT INTO outfit_items(outfit_id,item_id,role,category,colour,owned) VALUES (?,?,?,?,?,?)",
                          (oid, p.item_id, p.role, p.category, p.colour, 1 if p.owned else 0))
    return oid, created


def get_outfit(db: Database, outfit_id: int) -> dict[str, Any] | None:
    with db.read() as c:
        row = c.execute("SELECT * FROM outfits WHERE id=?", (outfit_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["items"] = [dict(r) for r in c.execute("SELECT * FROM outfit_items WHERE outfit_id=? ORDER BY id", (outfit_id,))]
    out["weather"] = json.loads(out.pop("weather_json") or "{}")
    return out


def outfits_on(db: Database, day: date | str, statuses: Iterable[str] | None = None, kind: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT id FROM outfits WHERE outfit_date=?", [_d(day)]
    if statuses:
        st = list(statuses)
        q += f" AND status IN ({','.join('?' * len(st))})"
        args += st
    if kind:
        q += " AND kind=?"
        args.append(kind)
    with db.read() as c:
        ids = [r["id"] for r in c.execute(q + " ORDER BY id", args)]
    return [o for o in (get_outfit(db, i) for i in ids) if o]


def list_history(db: Database, limit: int = 30, favourites_only: bool = False) -> list[dict[str, Any]]:
    q = "SELECT id FROM outfits" + (" WHERE favourite=1" if favourites_only else "") + " ORDER BY outfit_date DESC, id DESC LIMIT ?"
    with db.read() as c:
        ids = [r["id"] for r in c.execute(q, (limit,))]
    return [o for o in (get_outfit(db, i) for i in ids) if o]


def recent_signatures(db: Database, today: date, days: int, kind: str = "wardrobe") -> dict[str, dict[str, str]]:
    """signature -> {"last_date", "status"} for outfits dated in [today-days, today-1] that weren't skipped."""
    lo, hi = _d(today - timedelta(days=max(0, days))), _d(today - timedelta(days=1))
    out: dict[str, dict[str, str]] = {}
    with db.read() as c:
        for r in c.execute("SELECT signature, outfit_date, status FROM outfits WHERE kind=? AND outfit_date BETWEEN ? AND ? "
                           "AND status != 'skipped' ORDER BY outfit_date", (kind, lo, hi)):
            out[r["signature"]] = {"last_date": r["outfit_date"], "status": r["status"]}
    return out


def yesterday_signatures(db: Database, today: date, kind: str = "wardrobe") -> set[str]:
    y = _d(today - timedelta(days=1))
    with db.read() as c:
        return {r["signature"] for r in c.execute(
            "SELECT signature FROM outfits WHERE kind=? AND outfit_date=? AND status IN ('worn','recommended')", (kind, y))}


def today_signatures(db: Database, today: date, kind: str = "wardrobe") -> set[str]:
    """Combos already shown/worn today (used by 'suggest another' so you don't get the same one again)."""
    with db.read() as c:
        return {r["signature"] for r in c.execute("SELECT signature FROM outfits WHERE kind=? AND outfit_date=?", (kind, _d(today)))}


def item_last_worn(db: Database, before: date, days: int) -> dict[int, str]:
    """item_id -> most recent date it was actually WORN within the window (reuse explanations only use real wear)."""
    lo, hi = _d(before - timedelta(days=max(0, days))), _d(before - timedelta(days=1))
    out: dict[int, str] = {}
    with db.read() as c:
        for r in c.execute("""SELECT oi.item_id AS item_id, MAX(o.outfit_date) AS d FROM outfit_items oi JOIN outfits o ON o.id=oi.outfit_id
                              WHERE o.status='worn' AND oi.item_id IS NOT NULL AND o.outfit_date BETWEEN ? AND ? GROUP BY oi.item_id""", (lo, hi)):
            out[int(r["item_id"])] = r["d"]
    return out


# ------------------------------------------------------------------ status changes
def _recompute_item_usage(c: sqlite3.Connection, item_id: int) -> None:
    row = c.execute("""SELECT COUNT(DISTINCT o.id) AS n, MAX(o.outfit_date) AS d FROM outfit_items oi JOIN outfits o ON o.id=oi.outfit_id
                       WHERE oi.item_id=? AND o.status='worn'""", (item_id,)).fetchone()
    c.execute("UPDATE clothing_items SET usage_count=?, last_worn_date=? WHERE id=?", (row["n"], row["d"], item_id))


def set_status(db: Database, outfit_id: int, status: str) -> dict[str, Any]:
    """'worn' | 'skipped' | 'recommended'. Idempotent; keeps item usage statistics consistent in one transaction."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with db.tx() as c:
        row = c.execute("SELECT * FROM outfits WHERE id=?", (outfit_id,)).fetchone()
        if not row:
            raise LookupError("That outfit no longer exists.")
        touched: set[int] = set()
        if status == "worn":  # one worn outfit per day: demote any other
            for other in c.execute("SELECT id FROM outfits WHERE outfit_date=? AND status='worn' AND id != ?", (row["outfit_date"], outfit_id)).fetchall():
                c.execute("UPDATE outfits SET status='recommended', updated_at=? WHERE id=?", (now_iso(), other["id"]))
                touched |= {r["item_id"] for r in c.execute("SELECT item_id FROM outfit_items WHERE outfit_id=? AND item_id IS NOT NULL", (other["id"],))}
        if row["status"] != status:
            c.execute("UPDATE outfits SET status=?, updated_at=? WHERE id=?", (status, now_iso(), outfit_id))
        touched |= {r["item_id"] for r in c.execute("SELECT item_id FROM outfit_items WHERE outfit_id=? AND item_id IS NOT NULL", (outfit_id,))}
        for iid in touched:
            _recompute_item_usage(c, int(iid))
    return get_outfit(db, outfit_id)  # type: ignore[return-value]


def set_favourite(db: Database, outfit_id: int, value: bool = True) -> None:
    with db.tx() as c:
        if c.execute("UPDATE outfits SET favourite=?, updated_at=? WHERE id=?", (1 if value else 0, now_iso(), outfit_id)).rowcount == 0:
            raise LookupError("That outfit no longer exists.")


def set_feedback(db: Database, outfit_id: int, text: str) -> None:
    with db.tx() as c:
        if c.execute("UPDATE outfits SET feedback=?, updated_at=? WHERE id=?", (text.strip()[:500], now_iso(), outfit_id)).rowcount == 0:
            raise LookupError("That outfit no longer exists.")


def clear_history(db: Database) -> int:
    """Privacy control: delete all stored outfit history; item usage statistics are reset too."""
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM outfits").fetchone()["n"]
        c.execute("DELETE FROM outfits")
        c.execute("UPDATE clothing_items SET usage_count=0, last_worn_date=NULL")
    return int(n)
