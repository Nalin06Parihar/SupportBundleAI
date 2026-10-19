"""Safe loader for YAML scenarios and their controlled evidence references."""

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.exceptions import SupportBundleError
from app.models.scenario import ScenarioDefinition


class ScenarioError(SupportBundleError):
    """A controlled scenario could not be listed, loaded, or resolved."""


class ScenarioNotFoundError(ScenarioError):
    """The requested scenario ID is not available in the controlled environment."""


class ScenarioLoader:
    """Load validated scenario definitions without leaving the controlled root."""

    def __init__(self, controlled_environment_directory: Path | str | None = None):
        root = (
            Path(controlled_environment_directory)
            if controlled_environment_directory is not None
            else Path(get_settings().controlled_environment_directory)
        )
        try:
            self._root = root.resolve(strict=True)
        except OSError:
            raise ScenarioError("The controlled scenario directory is unavailable.") from None
        if not self._root.is_dir():
            raise ScenarioError("The controlled scenario directory is unavailable.")
        self._scenario_directory = self._root / "scenarios"

    def list_scenarios(self) -> list[ScenarioDefinition]:
        """Return all valid bundled scenarios in stable ID order."""
        try:
            paths = sorted(self._scenario_directory.glob("*.yaml"))
        except OSError:
            raise ScenarioError("Controlled scenarios could not be listed.") from None

        scenarios = []
        for path in paths:
            scenarios.append(self.load_scenario(path.stem))
        return scenarios

    def load_scenario(self, scenario_id: str) -> ScenarioDefinition:
        """Load one scenario by ID and verify every evidence reference."""
        if (
            not scenario_id
            or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", scenario_id) is None
        ):
            raise ScenarioNotFoundError("The requested scenario is unavailable.")

        definition_path = self._scenario_directory / f"{scenario_id}.yaml"
        try:
            resolved_definition = definition_path.resolve(strict=True)
            if not resolved_definition.is_relative_to(self._root):
                raise ScenarioError("Scenario definition is outside the controlled directory.")
            if not resolved_definition.is_file():
                raise ScenarioNotFoundError("The requested scenario is unavailable.")
            with resolved_definition.open(encoding="utf-8") as file:
                document: Any = yaml.safe_load(file)
        except FileNotFoundError:
            raise ScenarioNotFoundError("The requested scenario is unavailable.") from None
        except yaml.YAMLError:
            raise ScenarioError("Scenario definition is not valid YAML.") from None
        except OSError:
            raise ScenarioError("Scenario definition could not be read.") from None

        try:
            scenario = ScenarioDefinition.model_validate(document)
        except ValidationError:
            raise ScenarioError("Scenario definition does not match the scenario contract.") from None

        if scenario.scenario_id != scenario_id:
            raise ScenarioError("Scenario ID does not match its definition file.")
        for reference in scenario.evidence_files:
            self.resolve_evidence_file(reference)
        return scenario

    def resolve_evidence_file(self, reference: str) -> Path:
        """Resolve an existing relative evidence file strictly under the root."""
        if (
            not reference
            or "\x00" in reference
            or PurePosixPath(reference).is_absolute()
            or PureWindowsPath(reference).is_absolute()
            or PureWindowsPath(reference).drive
            or any(part == ".." for part in reference.replace("\\", "/").split("/"))
        ):
            raise ScenarioError("Evidence file reference must be a safe relative path.")

        normalized = reference.replace("\\", "/")
        path = PurePosixPath(normalized)
        if any(part in {"", "."} for part in path.parts):
            raise ScenarioError("Evidence file reference must be a safe relative path.")

        try:
            resolved = (self._root / Path(*path.parts)).resolve(strict=True)
            if not resolved.is_relative_to(self._root) or not resolved.is_file():
                raise ScenarioError("Evidence file is not an available controlled file.")
        except FileNotFoundError:
            raise ScenarioError("A referenced controlled evidence file is unavailable.") from None
        except OSError:
            raise ScenarioError("A referenced controlled evidence file is unavailable.") from None
        return resolved
