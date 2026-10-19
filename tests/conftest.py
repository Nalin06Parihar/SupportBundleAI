"""Isolated SQLite fixtures for persistence tests."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.base import Base
from app.database.engine import create_database_engine, initialize_database_engine
from app.database.session import create_session_factory


@pytest.fixture
def database_engine(tmp_path) -> Iterator[Engine]:
    database_path = tmp_path / "supportbundle-test.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database_engine(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(database_engine)
