"""Render a safe HTML escalation summary from validated workflow data."""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIRECTORY = Path(__file__).with_name("templates")


def render_escalation_summary(context: dict[str, Any]) -> str:
    """Render structured report context with HTML autoescaping enabled."""
    environment = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIRECTORY),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    template = environment.get_template("summary.html")
    return template.render(**context)
