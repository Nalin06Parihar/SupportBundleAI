"""Shared transaction helpers for session-per-operation repositories."""

from contextlib import AbstractContextManager

from sqlalchemy.orm import Session, sessionmaker

from app.database.session import session_scope


class BaseRepository:
    """Repository that opens and commits one database session per operation."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _session(self) -> AbstractContextManager[Session]:
        return session_scope(self._session_factory)
