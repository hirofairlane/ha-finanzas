"""SQLite schema and connection helpers for HA Finanzas."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = os.environ.get("HA_FINANZAS_DB_PATH", "/data/finanzas.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bank         TEXT    NOT NULL,
    iban         TEXT    NOT NULL UNIQUE,
    alias        TEXT,
    holder       TEXT,
    currency     TEXT    NOT NULL DEFAULT 'EUR',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL CHECK (kind IN ('gasto','ingreso')),
    name         TEXT    NOT NULL,
    color        TEXT    NOT NULL DEFAULT '#ff6f3c',
    icon         TEXT    NOT NULL DEFAULT 'mdi:tag',
    UNIQUE(kind, name)
);

CREATE TABLE IF NOT EXISTS merchants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL
);

-- Rules: match on `concepto` (regex, case-insensitive) → assign category+merchant.
-- Higher priority runs first. Rules can also be marked as transfer heuristics.
CREATE TABLE IF NOT EXISTS rules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern      TEXT    NOT NULL,
    category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    merchant_id  INTEGER REFERENCES merchants(id) ON DELETE SET NULL,
    is_transfer  INTEGER NOT NULL DEFAULT 0,
    priority     INTEGER NOT NULL DEFAULT 100,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS imports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name    TEXT    NOT NULL,
    file_sha1    TEXT    NOT NULL,
    bank         TEXT,
    account_id   INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    rows_total   INTEGER NOT NULL DEFAULT 0,
    rows_new     INTEGER NOT NULL DEFAULT 0,
    rows_dup     INTEGER NOT NULL DEFAULT 0,
    imported_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(file_sha1)
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    op_date       TEXT    NOT NULL,   -- YYYY-MM-DD (fecha operación)
    value_date    TEXT,               -- YYYY-MM-DD (fecha valor)
    concept       TEXT    NOT NULL,
    amount        REAL    NOT NULL,   -- negativo=gasto, positivo=ingreso
    balance       REAL,
    category_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    merchant_id   INTEGER REFERENCES merchants(id) ON DELETE SET NULL,
    is_transfer   INTEGER NOT NULL DEFAULT 0,
    transfer_peer INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    import_id     INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    dedup_hash    TEXT    NOT NULL UNIQUE,
    notes         TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tx_op_date ON transactions(op_date);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_tx_month ON transactions(substr(op_date,1,7));
"""

# Seed a small starter taxonomy so the UI is never empty.
SEED_CATEGORIES = [
    ("gasto",   "Supermercado",        "#4caf50", "mdi:cart"),
    ("gasto",   "Restauración",        "#ff9800", "mdi:silverware-fork-knife"),
    ("gasto",   "Combustible",         "#795548", "mdi:gas-station"),
    ("gasto",   "Hogar / Suministros", "#607d8b", "mdi:home-lightning-bolt"),
    ("gasto",   "Impuestos",           "#e53935", "mdi:bank-minus"),
    ("gasto",   "Ocio",                "#9c27b0", "mdi:party-popper"),
    ("gasto",   "Salud / Farmacia",    "#f06292", "mdi:medical-bag"),
    ("gasto",   "Suscripciones",       "#3f51b5", "mdi:repeat"),
    ("gasto",   "Compras online",      "#ff6f3c", "mdi:package-variant"),
    ("gasto",   "Seguros",             "#00838f", "mdi:shield-check"),
    ("gasto",   "Comisiones",          "#616161", "mdi:currency-eur-off"),
    ("gasto",   "Sin categorizar",     "#bdbdbd", "mdi:help-circle"),
    ("ingreso", "Nómina",              "#2e7d32", "mdi:cash-plus"),
    ("ingreso", "Devoluciones",        "#8bc34a", "mdi:cash-refund"),
    ("ingreso", "Otros ingresos",      "#9ccc65", "mdi:cash"),
    ("ingreso", "Sin categorizar",     "#bdbdbd", "mdi:help-circle"),
]

# Default rules matching common patterns from Openbank + Bizum + transfers.
SEED_RULES = [
    # transfers first (highest priority)
    (r"transferencia\s+(inmediata\s+)?(a\s+favor\s+de|de|entrante|saliente)", None, None, 1, 10),
    (r"traspaso\s+", None, None, 1, 10),
    # merchants (concept keyword, category name)
    (r"mercadona",       "Supermercado",     "Mercadona",     0, 50),
    (r"\bal[dt]i\b",     "Supermercado",     "Aldi",          0, 50),
    (r"\blidl\b",        "Supermercado",     "Lidl",          0, 50),
    (r"carref",          "Supermercado",     "Carrefour",     0, 50),
    (r"costco",          "Supermercado",     "Costco",        0, 50),
    (r"plenergy|repsol|cepsa|bp\s|galp",       "Combustible", None, 0, 50),
    (r"farmacia",        "Salud / Farmacia", None,            0, 50),
    (r"netflix|spotify|hbo|disney\+?|prime video|youtube premium|anthropic|openai|chatgpt|claude",
                         "Suscripciones",    None,            0, 40),
    (r"amazon",          "Compras online",   "Amazon",        0, 60),
    (r"kinepolis|cines|cinesa|yelmo",         "Ocio", None,   0, 50),
    (r"linea directa|mapfre|mutua|axa|allianz|zurich",
                         "Seguros",          None,            0, 50),
    (r"ayuntamiento|impuest|hacienda|agencia tributaria",
                         "Impuestos",        None,            0, 50),
    (r"iberdrola|endesa|naturgy|repsol\s+luz|totalenergies|holaluz",
                         "Hogar / Suministros", None,         0, 50),
    (r"movistar|vodafone|orange|masmovil|yoigo|digi",
                         "Hogar / Suministros", None,         0, 50),
    (r"comision",        "Comisiones",       None,            0, 40),
    (r"n[oó]mina|salario|abono\s+n[oó]mina", "Nómina", None,  0, 40),
    (r"taco bell|burger king|mcdonald|kfc|dominos|telepizza|rincon de|restaur",
                         "Restauración",     None,            0, 50),
    (r"bizum",           "Sin categorizar",  None,            0, 30),
]


def connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        cur = conn.cursor()
        yield cur
    finally:
        conn.close()


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # Seed categories if empty
        n = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if n == 0:
            conn.executemany(
                "INSERT INTO categories(kind,name,color,icon) VALUES(?,?,?,?)",
                SEED_CATEGORIES,
            )
        n = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
        if n == 0:
            for pattern, cat_name, merchant_name, is_transfer, priority in SEED_RULES:
                cat_id = None
                if cat_name:
                    row = conn.execute(
                        "SELECT id FROM categories WHERE name=? LIMIT 1", (cat_name,)
                    ).fetchone()
                    cat_id = row["id"] if row else None
                merchant_id = None
                if merchant_name:
                    conn.execute(
                        "INSERT OR IGNORE INTO merchants(name, category_id) VALUES(?,?)",
                        (merchant_name, cat_id),
                    )
                    merchant_id = conn.execute(
                        "SELECT id FROM merchants WHERE name=?", (merchant_name,)
                    ).fetchone()["id"]
                conn.execute(
                    "INSERT INTO rules(pattern,category_id,merchant_id,is_transfer,priority) "
                    "VALUES(?,?,?,?,?)",
                    (pattern, cat_id, merchant_id, is_transfer, priority),
                )
    finally:
        conn.close()
