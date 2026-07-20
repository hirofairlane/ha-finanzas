"""HA Finanzas — aiohttp app.

Serves the comic-styled UI under `/` and a small JSON API under `/api/*`.
Runs a background watcher that ingests any file dropped into the inbox dir.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web

from . import db as dbmod
from .api import routes as api_routes
from .ingest import ingest_file


LOG = logging.getLogger("ha_finanzas")

STATIC_DIR = Path(__file__).resolve().parent / "static"
INBOX_DIR = Path(os.environ.get("HA_FINANZAS_INBOX_DIR", "/share/ha_finanzas/inbox"))
ARCHIVE_DIR = Path(os.environ.get("HA_FINANZAS_ARCHIVE_DIR", "/share/ha_finanzas/archive"))
WATCH_ENABLED = os.environ.get("HA_FINANZAS_WATCH_SHARE_DIR", "true").lower() == "true"

# Add-on Ingress hits port 8123 by default (see config.yaml).
PORT = int(os.environ.get("HA_FINANZAS_PORT", "8123"))


async def _index(_req: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


async def _watch_inbox() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    LOG.info("Watching %s for new statement files", INBOX_DIR)
    seen: set[str] = set()
    while True:
        try:
            for p in sorted(INBOX_DIR.iterdir()):
                if not p.is_file() or p.name.startswith("."):
                    continue
                if p.name in seen:
                    continue
                seen.add(p.name)
                LOG.info("Ingesting %s", p)
                try:
                    summary = await asyncio.to_thread(ingest_file, str(p))
                    LOG.info(
                        "Ingested %s: total=%d new=%d dup=%d transfers=%d",
                        p.name, summary.rows_total, summary.rows_new,
                        summary.rows_dup, summary.transfers_paired,
                    )
                    dest = ARCHIVE_DIR / p.name
                    if dest.exists():
                        dest = ARCHIVE_DIR / f"{p.stem}.{summary.import_id}{p.suffix}"
                    p.rename(dest)
                except Exception:
                    LOG.exception("Failed to ingest %s", p)
        except Exception:
            LOG.exception("watcher loop error")
        await asyncio.sleep(10)


async def _on_startup(app: web.Application) -> None:
    dbmod.init_db()
    if WATCH_ENABLED:
        app["watcher"] = asyncio.create_task(_watch_inbox())


async def _on_cleanup(app: web.Application) -> None:
    task = app.get("watcher")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def build_app() -> web.Application:
    app = web.Application(client_max_size=32 * 1024 * 1024)
    app.router.add_get("/", _index)
    app.router.add_static("/static/", str(STATIC_DIR), show_index=False)
    api_routes(app.router)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main() -> None:
    log_level = os.environ.get("HA_FINANZAS_LOG_LEVEL", "info").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    web.run_app(build_app(), host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
