"""SQLAlchemy declarative base and UTC timestamp mapping."""

from datetime import UTC, datetime

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM records."""


class UTCDateTime(TypeDecorator[datetime]):
    """Store timezone-aware datetimes as normalized ISO-8601 UTC strings."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: object,
    ) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Database timestamps must be timezone-aware.")
        return value.astimezone(UTC).isoformat()

    def process_result_value(
        self,
        value: str | None,
        dialect: object,
    ) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
