"""Rule-based categorisation + merchant extraction.

Given a raw ``concept`` string from any bank, resolve:
  - category_id (may be None → "Sin categorizar")
  - merchant_id (may be None; auto-created if we can extract a clean name)
  - is_transfer flag

Extraction heuristics for the merchant look for the substring "COMPRA EN <X>,"
which is Openbank's canonical shape ("Google pay: COMPRA EN MERCADONA
MONTEBURGOS, CON LA TARJETA..."), and Bizum ("BIZUM A FAVOR DE <X>").
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class Categorisation:
    category_id: Optional[int]
    merchant_id: Optional[int]
    is_transfer: bool


_COMPRA_RE = re.compile(
    r"compra\s+en\s+(?:linea\s+)?(.+?)(?:,|\s+con\s+la\s+tarjeta|\s+el\s+\d{4}-\d{2}-\d{2}|$)",
    re.IGNORECASE,
)
_BIZUM_RE = re.compile(r"bizum\s+(?:a\s+favor\s+de|de)\s+(.+?)(?:concepto|$)", re.IGNORECASE)
_RECIBO_RE = re.compile(r"recibo\s+(.+?)(?:\s+n[º°o]\s+recibo|$)", re.IGNORECASE)
_TRANSF_TO_RE = re.compile(r"transferencia\s+(?:inmediata\s+)?a\s+favor\s+de\s+(.+?)(?:concepto|$)",
                            re.IGNORECASE)
_TRANSF_FROM_RE = re.compile(r"transferencia\s+(?:inmediata\s+)?de\s+(.+?)(?:concepto|$)",
                              re.IGNORECASE)

_JUNK_PREFIXES = (
    "google pay:", "apple pay:", "samsung pay:", "pago con movil:",
)


def _normalise_merchant(raw: str) -> str:
    s = raw.strip().strip(".,;:")
    # collapse consecutive spaces, upper case first letters for readability
    s = re.sub(r"\s+", " ", s)
    # drop trailing card numbers or dates that leak through
    s = re.sub(r"\s*\b\d{4,}\b.*$", "", s).strip()
    # title case looks better in the UI without being ugly on acronyms
    return s.title() if s.isupper() else s


def _extract_merchant(concept: str) -> Optional[str]:
    text = concept
    for prefix in _JUNK_PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    for regex in (_COMPRA_RE, _BIZUM_RE, _TRANSF_TO_RE, _TRANSF_FROM_RE, _RECIBO_RE):
        m = regex.search(text)
        if m:
            candidate = _normalise_merchant(m.group(1))
            if candidate and len(candidate) >= 2:
                return candidate
    return None


def _get_or_create_merchant(conn: sqlite3.Connection, name: str,
                             category_id: Optional[int]) -> int:
    row = conn.execute("SELECT id FROM merchants WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO merchants(name, category_id) VALUES(?,?)",
        (name, category_id),
    )
    return cur.lastrowid


def _uncategorised(conn: sqlite3.Connection, kind: str) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM categories WHERE kind=? AND name='Sin categorizar' LIMIT 1",
        (kind,),
    ).fetchone()
    return row["id"] if row else None


def categorise(conn: sqlite3.Connection, concept: str, amount: float) -> Categorisation:
    kind = "ingreso" if amount > 0 else "gasto"

    rules = conn.execute(
        "SELECT id, pattern, category_id, merchant_id, is_transfer "
        "FROM rules WHERE active=1 ORDER BY priority ASC, id ASC"
    ).fetchall()

    matched_cat: Optional[int] = None
    matched_merchant: Optional[int] = None
    is_transfer = False

    for r in rules:
        try:
            if re.search(r["pattern"], concept, re.IGNORECASE):
                if r["is_transfer"]:
                    is_transfer = True
                    break
                if matched_cat is None and r["category_id"] is not None:
                    matched_cat = r["category_id"]
                if matched_merchant is None and r["merchant_id"] is not None:
                    matched_merchant = r["merchant_id"]
                if matched_cat and matched_merchant:
                    break
        except re.error:
            continue

    if is_transfer:
        return Categorisation(category_id=None, merchant_id=None, is_transfer=True)

    # Auto-extract merchant from concept if the rules didn't nail one.
    if matched_merchant is None:
        m_name = _extract_merchant(concept)
        if m_name:
            matched_merchant = _get_or_create_merchant(conn, m_name, matched_cat)
            # If merchant already had a category assigned, inherit it.
            if matched_cat is None:
                row = conn.execute(
                    "SELECT category_id FROM merchants WHERE id=?", (matched_merchant,),
                ).fetchone()
                if row and row["category_id"]:
                    matched_cat = row["category_id"]

    if matched_cat is None:
        matched_cat = _uncategorised(conn, kind)

    return Categorisation(category_id=matched_cat, merchant_id=matched_merchant,
                          is_transfer=False)
