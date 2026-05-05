"""Loads YAML process templates at startup."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_loaded_templates: dict[str, dict[str, Any]] = {}


def load_templates() -> None:
    """Load all YAML templates from the templates/ directory."""
    for path in TEMPLATES_DIR.glob("*.yaml"):
        with path.open() as fh:
            data = yaml.safe_load(fh)
        _loaded_templates[path.stem] = data
        logger.info("Loaded template '%s' from %s", path.stem, path)


def get_template(name: str) -> dict[str, Any] | None:
    return _loaded_templates.get(name)


def all_templates() -> dict[str, dict[str, Any]]:
    return dict(_loaded_templates)
