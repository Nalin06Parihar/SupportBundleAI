"""SQLAlchemy engine construction and SQLite connection safeguards."""

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool
from typing import Any


def create_database_engine(database_url: str) -> Engine:
    """Create a synchronous engine without logging connection details."""
    url = make_url(database_url)
    options: dict[str, Any] = {
        "pool_pre_ping": True,
        "hide_parameters": True,
    }

    if url.drivername.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url.database in {None, "", ":memory:"}:
            options["poolclass"] = StaticPool

    engine = create_engine(url, **options)
    if url.drivername.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(
            dbapi_connection: Any,
            _record: object,
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def initialize_database_engine(engine: Engine) -> None:
    """Verify connectivity and SQLite foreign-key enforcement without creating tables."""
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            enabled = connection.scalar(text("PRAGMA foreign_keys"))
            if enabled != 1:
                raise RuntimeError("SQLite foreign-key enforcement is unavailable.")
