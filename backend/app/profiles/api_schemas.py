"""Request and response shapes of the profile API."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.profiles.schema import BusinessProfileConfig, Domain


class ProfileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    domain: Domain
    version: int
    updated_at: datetime


class ProfileOut(ProfileSummary):
    created_at: datetime
    config: BusinessProfileConfig


class ProfileUpdate(BaseModel):
    # The version the edit started from; the save is refused if it's no longer current.
    version: int = Field(ge=1)
    config: BusinessProfileConfig


class ProfileVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    created_at: datetime
    saved_by: uuid.UUID | None


class FieldError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ValidationReport(BaseModel):
    valid: bool
    errors: list[FieldError]


class TemplateSummary(BaseModel):
    domain: Domain
    name: str


def field_errors(errors: list[Any]) -> list[FieldError]:
    """Pydantic errors in the same shape the API uses for 422 responses."""
    return [
        FieldError(loc=list(err.get("loc", ())), msg=err.get("msg", ""), type=err.get("type", ""))
        for err in errors
    ]
