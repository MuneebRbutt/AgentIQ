"""Example profiles for each supported domain: starter templates and demo seed data.

Keep them valid: `tests/unit/test_profile_examples.py` loads every file.
"""

from functools import cache
from importlib import resources
from typing import Any

import yaml

from app.profiles.schema import BusinessProfileConfig, Domain


@cache
def _raw_example(domain: Domain) -> dict[str, Any]:
    text = resources.files(__package__).joinpath(f"{domain.value}.yaml").read_text("utf-8")
    data: dict[str, Any] = yaml.safe_load(text)
    return data


def load_example(domain: Domain) -> BusinessProfileConfig:
    """A fresh, validated copy of the example profile for `domain`."""
    return BusinessProfileConfig.model_validate(_raw_example(domain))
