"""JSON API — the UI is entirely thin, this is where the state lives."""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from aiohttp import web

from . import db as dbmod
from .ingest import ingest_file


def _rows(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


async def api_health(_req: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def api_summary(req: web.Request) -> web.Response:
    """Global summary: totals per month (transfers excluded), account balances."""
    conn = dbmod.connect()
    try:
        months = _rows(conn.execute(
            "SELECT substr(op_date,1,7) AS month,"
            "  SUM(CASE WHEN amount>0 THEN amount ELSE 0 END) AS income,"
            "  SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END) AS expense,"
            "  SUM(amount) AS net,"
            "  COUNT(*) AS n"
            " FROM transactions WHERE is_transfer=0"
            " GROUP BY month ORDER BY month DESC LIMIT 24"
        ).fetchall())
        accounts = _rows(conn.execute(
            "SELECT a.id, a.bank, a.alias, a.iban, a.currency,"
            "  (SELECT balance FROM transactions t WHERE t.account_id=a.id"
            "   ORDER BY op_date DESC, id DESC LIMIT 1) AS last_balance,"
            "  (SELECT COUNT(*) FROM transactions t WHERE t.account_id=a.id) AS n_tx"
            " FROM accounts a ORDER BY a.bank, a.alias"
        ).fetchall())
        total_balance = sum((a["last_balance"] or 0) for a in accounts)
        return web.json_response({
            "months": months,
            "accounts": accounts,
            "total_balance": round(total_balance, 2),
        })
    finally:
        conn.close()


async def api_month_breakdown(req: web.Request) -> web.Response:
    """Category breakdown for one month across all accounts."""
    month = req.query.get("month") or date.today().strftime("%Y-%m")
    conn = dbmod.connect()
    try:
        cats = _rows(conn.execute(
            "SELECT c.id, c.kind, c.name, c.color, c.icon,"
            "  COUNT(t.id) AS n,"
            "  ROUND(SUM(t.amount),2) AS total"
            " FROM transactions t"
            " LEFT JOIN categories c ON c.id = t.category_id"
            " WHERE t.is_transfer=0 AND substr(t.op_date,1,7)=?"
            " GROUP BY c.id ORDER BY ABS(SUM(t.amount)) DESC",
            (month,),
        ).fetchall())
        top_merchants = _rows(conn.execute(
            "SELECT m.name,"
            "  ROUND(SUM(t.amount),2) AS total,"
            "  COUNT(*) AS n"
            " FROM transactions t JOIN merchants m ON m.id = t.merchant_id"
            " WHERE t.is_transfer=0 AND t.amount<0 AND substr(t.op_date,1,7)=?"
            " GROUP BY m.id ORDER BY SUM(t.amount) ASC LIMIT 15",
            (month,),
        ).fetchall())
        return web.json_response({"month": month, "categories": cats,
                                   "top_merchants": top_merchants})
    finally:
        conn.close()


async def api_provisions(_req: web.Request) -> web.Response:
    """Rolling 6/12-month averages per category → suggested monthly provisions."""
    conn = dbmod.connect()
    try:
        # Last 12 months (or fewer) of monthly totals per category, then avg.
        rows = _rows(conn.execute(
            "WITH monthly AS ("
            "  SELECT category_id, substr(op_date,1,7) AS m,"
            "         SUM(amount) AS total"
            "  FROM transactions WHERE is_transfer=0"
            "  GROUP BY category_id, m"
            "),"
            " last12 AS ("
            "  SELECT DISTINCT m FROM monthly ORDER BY m DESC LIMIT 12"
            "),"
            " last6 AS ("
            "  SELECT DISTINCT m FROM monthly ORDER BY m DESC LIMIT 6"
            ")"
            " SELECT c.id, c.kind, c.name, c.color, c.icon,"
            "  ROUND(AVG(CASE WHEN monthly.m IN (SELECT m FROM last6)"
            "                 THEN monthly.total END),2) AS avg_6m,"
            "  ROUND(AVG(CASE WHEN monthly.m IN (SELECT m FROM last12)"
            "                 THEN monthly.total END),2) AS avg_12m"
            " FROM monthly"
            " LEFT JOIN categories c ON c.id = monthly.category_id"
            " WHERE monthly.m IN (SELECT m FROM last12)"
            " GROUP BY c.id"
            " ORDER BY ABS(COALESCE(avg_6m,avg_12m,0)) DESC"
        ).fetchall())
        return web.json_response({"provisions": rows})
    finally:
        conn.close()


async def api_categories(_req: web.Request) -> web.Response:
    conn = dbmod.connect()
    try:
        rows = _rows(conn.execute(
            "SELECT id, kind, name, color, icon FROM categories"
            " ORDER BY kind, name"
        ).fetchall())
        return web.json_response({"categories": rows})
    finally:
        conn.close()


async def api_category_upsert(req: web.Request) -> web.Response:
    body = await req.json()
    conn = dbmod.connect()
    try:
        if body.get("id"):
            conn.execute(
                "UPDATE categories SET kind=?, name=?, color=?, icon=? WHERE id=?",
                (body["kind"], body["name"], body.get("color", "#ff6f3c"),
                 body.get("icon", "mdi:tag"), body["id"]),
            )
            return web.json_response({"id": body["id"], "updated": True})
        cur = conn.execute(
            "INSERT INTO categories(kind,name,color,icon) VALUES(?,?,?,?)",
            (body["kind"], body["name"], body.get("color", "#ff6f3c"),
             body.get("icon", "mdi:tag")),
        )
        return web.json_response({"id": cur.lastrowid, "created": True})
    finally:
        conn.close()


async def api_category_delete(req: web.Request) -> web.Response:
    cid = int(req.match_info["cid"])
    conn = dbmod.connect()
    try:
        conn.execute("DELETE FROM categories WHERE id=?", (cid,))
        return web.json_response({"deleted": cid})
    finally:
        conn.close()


async def api_transactions(req: web.Request) -> web.Response:
    account = req.query.get("account")
    month = req.query.get("month")
    category = req.query.get("category")
    show_transfers = req.query.get("transfers", "0") == "1"
    limit = int(req.query.get("limit", "500"))
    where = []
    args: list[Any] = []
    if not show_transfers:
        where.append("t.is_transfer=0")
    if account:
        where.append("t.account_id=?"); args.append(int(account))
    if month:
        where.append("substr(t.op_date,1,7)=?"); args.append(month)
    if category:
        where.append("t.category_id=?"); args.append(int(category))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = dbmod.connect()
    try:
        rows = _rows(conn.execute(
            f"SELECT t.id, t.op_date, t.concept, t.amount, t.balance,"
            f"  t.is_transfer, t.transfer_peer,"
            f"  a.alias AS account_alias, a.bank AS bank,"
            f"  c.name AS category_name, c.color AS category_color,"
            f"  c.icon AS category_icon,"
            f"  m.name AS merchant_name"
            f" FROM transactions t"
            f" JOIN accounts a ON a.id=t.account_id"
            f" LEFT JOIN categories c ON c.id=t.category_id"
            f" LEFT JOIN merchants m ON m.id=t.merchant_id"
            f" {where_sql} ORDER BY t.op_date DESC, t.id DESC LIMIT ?",
            (*args, limit),
        ).fetchall())
        return web.json_response({"transactions": rows})
    finally:
        conn.close()


async def api_tx_recategorise(req: web.Request) -> web.Response:
    tid = int(req.match_info["tid"])
    body = await req.json()
    conn = dbmod.connect()
    try:
        conn.execute(
            "UPDATE transactions SET category_id=?, merchant_id=?"
            " WHERE id=?",
            (body.get("category_id"), body.get("merchant_id"), tid),
        )
        return web.json_response({"updated": tid})
    finally:
        conn.close()


async def api_upload(req: web.Request) -> web.Response:
    reader = await req.multipart()
    tmp_path: Path | None = None
    fname = "upload.bin"
    async for part in reader:
        if part.name == "file":
            fname = part.filename or fname
            fd, tmp = tempfile.mkstemp(prefix="ha_finanzas_", suffix=Path(fname).suffix)
            tmp_path = Path(tmp)
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
    if tmp_path is None:
        return web.json_response({"error": "no file"}, status=400)
    try:
        summary = ingest_file(str(tmp_path))
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    return web.json_response({
        "file": fname,
        "bank": summary.bank,
        "account_id": summary.account_id,
        "account_alias": summary.account_alias,
        "iban": summary.iban,
        "rows_total": summary.rows_total,
        "rows_new": summary.rows_new,
        "rows_dup": summary.rows_dup,
        "transfers_paired": summary.transfers_paired,
    })


def routes(router: web.UrlDispatcher) -> None:
    router.add_get("/api/health", api_health)
    router.add_get("/api/summary", api_summary)
    router.add_get("/api/month", api_month_breakdown)
    router.add_get("/api/provisions", api_provisions)
    router.add_get("/api/categories", api_categories)
    router.add_post("/api/categories", api_category_upsert)
    router.add_delete("/api/categories/{cid}", api_category_delete)
    router.add_get("/api/transactions", api_transactions)
    router.add_post("/api/transactions/{tid}/categorise", api_tx_recategorise)
    router.add_post("/api/upload", api_upload)
