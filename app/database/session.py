"""SQLAlchemy session factory, transaction scope, and FastAPI dependency."""

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from fastapi import Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a non-expiring synchronous session factory for an engine."""
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@contextmanager
def session_scope(
    factory: sessionmaker[Session],
) -> Iterator[Session]:
    """Provide one transactional session, committing on success and rolling back on error."""
    session = factory()
    try:
        with session.begin():
            yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Yield a session from the engine initialized by the application lifespan."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise RuntimeError("Database session factory is not initialized.")
    session = factory()
    try:
        yield session
    finally:
        session.close()
