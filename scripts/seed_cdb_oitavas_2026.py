"""Compat: seed CDB via tabela oficial CBF (não depende de lista manual)."""
from palpitaria.database import SessionLocal
from palpitaria.services.cbf_cdb_ingest import ingest_cdb_from_cbf


def main() -> None:
    db = SessionLocal()
    try:
        result = ingest_cdb_from_cbf(db, season=2026, log_callback=print)
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
