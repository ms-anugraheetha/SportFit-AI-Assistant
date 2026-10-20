"""Check the configured PostgreSQL database end to end (works for Neon, local Docker, anything).

Run from the backend folder:   python -m scripts.check_db
Uses DATABASE_URL from the environment or backend/.env. Prints the host and database name but never the password.
It changes nothing permanent: the write test runs on a temporary table inside a transaction that is rolled back.
Exit code 0 = connection and a real write/read round trip work; 1 = something failed.
"""

import sys

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db import get_engine


def main() -> int:
    settings = get_settings()
    if not settings.database_url:
        print("[FAIL] DATABASE_URL is not set (environment or backend/.env).")
        return 1
    url = make_url(settings.sqlalchemy_url)
    print(f"Target: host={url.host} port={url.port or 5432} database={url.database} user={url.username}")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
            print(f"[PASS] connected: {version.split(',')[0]}")
            ssl = conn.execute(text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")).scalar()
            print(f"[{'PASS' if ssl else 'WARN'}] TLS in use: {bool(ssl)}")
        with engine.connect() as probe:
            tx = probe.begin()
            probe.execute(text("CREATE TEMP TABLE _sportfit_probe (n int)"))
            probe.execute(text("INSERT INTO _sportfit_probe VALUES (42)"))
            got = probe.execute(text("SELECT n FROM _sportfit_probe")).scalar_one()
            tx.rollback()
        print(f"[{'PASS' if got == 42 else 'FAIL'}] write/read round trip on a temporary table (rolled back)")
        if got != 42:
            return 1
        tables = set(inspect(engine).get_table_names())
        needed = {"service_requests", "status_history", "staff_sessions", "alembic_version"}
        missing = sorted(needed - tables)
        if missing:
            print(f"[WARN] tables not created yet: {', '.join(missing)}  -> run: python -m alembic upgrade head")
        else:
            with engine.connect() as conn:
                rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                count = conn.execute(text("SELECT count(*) FROM service_requests")).scalar()
            print(f"[PASS] schema present (migration {rev}); service_requests rows: {count}")
    except Exception as exc:  # report the type only: messages can contain connection details
        print(f"[FAIL] {type(exc).__name__}: could not complete the database check.")
        return 1
    print("Database check finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
