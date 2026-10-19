"""Validated metadata describing a controlled synthetic diagnostic scenario."""

from pydantic import Field

from app.models._common import StrictModel
from app.models.plan import CollectorName


class ScenarioDefinition(StrictModel):
    """Scenario metadata and relative references to controlled evidence files."""

    scenario_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=1000)
    issue_category: str = Field(min_length=1, max_length=100)
    available_collectors: list[CollectorName] = Field(min_length=1, max_length=8)
    evidence_files: list[str] = Field(min_length=1, max_length=20)
    expected_signals: list[str] = Field(min_length=1, max_length=30)
