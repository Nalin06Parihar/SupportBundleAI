"""Standard-library logging configuration."""

import logging


def configure_logging(log_level: int | str = logging.INFO) -> None:
    """Configure readable console logging without writing log files."""
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
