"""File ingestion: dedup, categorise, insert, then run transfer matching."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import db as dbmod
from . import parsers
from .categorize import categorise


TRANSFER_WINDOW_DAYS = int(os.environ.get("HA_FINANZAS_TRANSFER_WINDOW_DAYS", "3"))
TRANSFER_AMOUNT_TOL = float(os.environ.get("HA_FINANZAS_TRANSFER_AMOUNT_TOLERANCE", "0.01"))


@dataclass
class ImportSummary:
    import_id: int
    file_name: str
    bank: str
    account_id: int
    account_alias: Optional[str]
    iban: str
    rows_total: int
    rows_new: int
    rows_dup: int
    transfers_paired: int


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_concept(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _tx_hash(account_id: int, op_date: date, amount: float, concept: str) -> str:
    payload = f"{account_id}|{op_date.isoformat()}|{amount:.2f}|{_norm_concept(concept)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _ensure_account(conn: sqlite3.Connection, bank: str, iban: str,
                    alias: Optional[str], holder: Optional[str],
                    currency: str) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE iban=?", (iban,)).fetchone()
    if row:
        # keep alias/holder fresh if they were empty before
        conn.execute(
            "UPDATE accounts SET alias=COALESCE(alias,?), holder=COALESCE(holder,?) "
            "WHERE id=?",
            (alias, holder, row["id"]),
        )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO accounts(bank, iban, alias, holder, currency) VALUES(?,?,?,?,?)",
        (bank, iban, alias, holder, currency),
    )
    return cur.lastrowid


def _match_transfers(conn: sqlite3.Connection) -> int:
    """Pair transactions across different accounts with opposite amounts.

    Runs after each import so newly ingested rows can find peers among the
    previously imported set. Only pairs rows that are still unpaired.
    """
    win = TRANSFER_WINDOW_DAYS
    tol = TRANSFER_AMOUNT_TOL

    unpaired = conn.execute(
        "SELECT id, account_id, op_date, amount, is_transfer "
        "FROM transactions WHERE transfer_peer IS NULL "
        "ORDER BY op_date ASC, id ASC"
    ).fetchall()

    seen: dict[int, sqlite3.Row] = {r["id"]: r for r in unpaired}
    pairs = 0

    for r in unpaired:
        if r["id"] not in seen:
            continue  # already paired in a previous iteration of this loop
        d = date.fromisoformat(r["op_date"])
        d_lo = (d - timedelta(days=win)).isoformat()
        d_hi = (d + timedelta(days=win)).isoformat()
        candidates = conn.execute(
            "SELECT id, account_id, op_date, amount "
            "FROM transactions "
            "WHERE transfer_peer IS NULL AND id!=? AND account_id!=? "
            "  AND op_date BETWEEN ? AND ? "
            "  AND ABS(amount + ?) <= ?",
            (r["id"], r["account_id"], d_lo, d_hi, r["amount"], tol),
        ).fetchall()
        if not candidates:
            continue
        # Prefer the closest date match, then oldest id for determinism.
        candidates.sort(key=lambda c: (
            abs((date.fromisoformat(c["op_date"]) - d).days),
            c["id"],
        ))
        peer = candidates[0]
        if peer["id"] not in seen:
            continue
        conn.execute(
            "UPDATE transactions SET is_transfer=1, transfer_peer=? WHERE id=?",
            (peer["id"], r["id"]),
        )
        conn.execute(
            "UPDATE transactions SET is_transfer=1, transfer_peer=? WHERE id=?",
            (r["id"], peer["id"]),
        )
        seen.pop(r["id"], None)
        seen.pop(peer["id"], None)
        pairs += 1

    return pairs


def ingest_file(path: str) -> ImportSummary:
    dbmod.init_db()
    stmt = parsers.detect_and_parse(path)
    file_sha1 = _sha1(path)
    file_name = Path(path).name

    conn = dbmod.connect()
    try:
        # Reject exact-duplicate files early.
        dup = conn.execute(
            "SELECT id FROM imports WHERE file_sha1=?", (file_sha1,)
        ).fetchone()
        if dup:
            return ImportSummary(
                import_id=dup["id"], file_name=file_name, bank=stmt.bank,
                account_id=0, account_alias=stmt.alias, iban=stmt.iban,
                rows_total=len(stmt.transactions), rows_new=0,
                rows_dup=len(stmt.transactions), transfers_paired=0,
            )

        account_id = _ensure_account(
            conn, stmt.bank, stmt.iban, stmt.alias, stmt.holder, stmt.currency,
        )

        cur = conn.execute(
            "INSERT INTO imports(file_name, file_sha1, bank, account_id, rows_total) "
            "VALUES(?,?,?,?,?)",
            (file_name, file_sha1, stmt.bank, account_id, len(stmt.transactions)),
        )
        import_id = cur.lastrowid

        new = 0
        dup = 0
        for t in stmt.transactions:
            h = _tx_hash(account_id, t.op_date, t.amount, t.concept)
            exists = conn.execute(
                "SELECT 1 FROM transactions WHERE dedup_hash=?", (h,)
            ).fetchone()
            if exists:
                dup += 1
                continue
            cat = categorise(conn, t.concept, t.amount)
            conn.execute(
                "INSERT INTO transactions("
                " account_id, op_date, value_date, concept, amount, balance,"
                " category_id, merchant_id, is_transfer, import_id, dedup_hash"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    account_id, t.op_date.isoformat(),
                    t.value_date.isoformat() if t.value_date else None,
                    t.concept, t.amount, t.balance,
                    cat.category_id, cat.merchant_id, int(cat.is_transfer),
                    import_id, h,
                ),
            )
            new += 1

        conn.execute(
            "UPDATE imports SET rows_new=?, rows_dup=? WHERE id=?",
            (new, dup, import_id),
        )

        paired = _match_transfers(conn)

        return ImportSummary(
            import_id=import_id, file_name=file_name, bank=stmt.bank,
            account_id=account_id, account_alias=stmt.alias, iban=stmt.iban,
            rows_total=len(stmt.transactions), rows_new=new, rows_dup=dup,
            transfers_paired=paired,
        )
    finally:
        conn.close()
