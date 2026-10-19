"""Models describing a user's reported technical issue."""

import re

from pydantic import Field, field_validator

from app.models._common import StrictModel


class IssueRequest(StrictModel):
    """Validated, non-sensitive technical issue submitted by a user."""

    title: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=10, max_length=5000)
    application_name: str = Field(min_length=2, max_length=150)
    symptoms: list[str] = Field(default_factory=list, max_length=20)
    scenario_id: str | None = Field(default=None, max_length=100)

    @field_validator("symptoms")
    @classmethod
    def normalize_symptoms(cls, symptoms: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for symptom in symptoms:
            symptom = symptom.strip()
            if len(symptom) > 300:
                raise ValueError("Symptoms must not exceed 300 characters.")
            if symptom and symptom not in seen:
                seen.add(symptom)
                normalized.append(symptom)
        return normalized

    @field_validator("scenario_id", mode="before")
    @classmethod
    def normalize_scenario_id(cls, scenario_id: object) -> object:
        if isinstance(scenario_id, str):
            scenario_id = scenario_id.strip()
            return scenario_id or None
        return scenario_id

    @field_validator("scenario_id")
    @classmethod
    def validate_scenario_id(cls, scenario_id: str | None) -> str | None:
        if scenario_id is not None and re.fullmatch(
            r"[A-Za-z0-9_-]{1,100}", scenario_id
        ) is None:
            raise ValueError("Scenario ID contains unsupported characters.")
        return scenario_id
