from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from palpitaria.config import settings
from palpitaria.database import init_db
from palpitaria.services.ai_tracker import backfill_from_fixture_reports, resolve_pending_recommendations
from palpitaria.services.ledger import close_past_months, migrate_branch_sides
from palpitaria.services.odds_service import update_competition_odds  # noqa: F401 — re-exportado: agents/tools/sync.py e scripts/sync_brazil.py importam `update_competition_odds` daqui.

from palpitaria.routers import admin, auth, chat, ciclos, home, ia_historico, ledger, pages, system

if settings.secret_key_error:
    raise RuntimeError(settings.secret_key_error)

app = FastAPI(title="Palpitaria FC", description="Leitura fundamentada para mercados de gols")
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(auth.router)
app.include_router(home.router)
app.include_router(ledger.router)
app.include_router(ia_historico.router)
app.include_router(pages.router)
app.include_router(chat.router)
app.include_router(ciclos.router)
app.include_router(admin.router)
app.include_router(system.router)


@app.on_event("startup")
def on_startup() -> None:
    from palpitaria.routers.home import LOG_BUFFER, PIPELINE_CANCEL, PIPELINE_STATE

    LOG_BUFFER.clear()
    PIPELINE_STATE.update(
        active=False, running=False, done=False, error=None, comp=None, cancelled=False
    )
    PIPELINE_CANCEL.clear()

    if settings.database_config_error:
        print(f"AVISO: {settings.database_config_error}", flush=True)
        return
    try:
        init_db()
        from palpitaria.database import SessionLocal

        db = SessionLocal()
        try:
            migrate_branch_sides(db)
            closed = close_past_months(db)
            if closed:
                print(f"Ledger: {len(closed)} fechamento(s) mensal(is) consolidado(s).", flush=True)
            backfilled = backfill_from_fixture_reports(db)
            if backfilled:
                print(f"IA tracker: {backfilled} recomendação(ões) importadas.", flush=True)
            resolved = resolve_pending_recommendations(db)
            if resolved:
                print(f"IA tracker: {resolved} recomendação(ões) resolvidas.", flush=True)
        finally:
            db.close()
    except Exception as exc:
        msg = str(exc).lower()
        if "translate host" in msg or "getaddrinfo" in msg or "ipv6" in msg or "unreachable" in msg:
            print(
                "\nAVISO: não foi possível conectar ao Supabase no startup.\n"
                "Causa provável: DATABASE_URL usa db.PROJECT.supabase.co (só IPv6).\n"
                "Solução: no Supabase Dashboard → Database → Connection pooling → Session,\n"
                "copie a URL do pooler (aws-*-REGION.pooler.supabase.com) para DATABASE_URL.\n",
                flush=True,
            )
        print(f"AVISO: startup do banco falhou ({exc!r}) — app sobe em modo degradado.", flush=True)


def run() -> None:
    import uvicorn

    uvicorn.run("palpitaria.main:app", host="127.0.0.1", port=8000, reload=settings.debug)
