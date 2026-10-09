"""Pydantic schemas — the request/response contract for the SPA and tools."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field, model_validator

from core.errors import InvalidRepoUrl
from core.url import normalize_port, validate_repo_url


class DeployRequest(BaseModel):
    repo_url: str
    port_override: int | None = None

    @model_validator(mode="after")
    def _validate(self):
        # Dev-only affordance: "demo" runs the bundled demo-repo so the full
        # pipeline is exercised without a network clone (FR-1 requires HTTPS in
        # every other path).
        if self.repo_url.strip().lower() == "demo":
            if os.environ.get("PORTAL_ENV", "dev") == "prod":
                raise InvalidRepoUrl("repo_url must be an HTTPS URL.")
            self.repo_url = "demo"
        else:
            self.repo_url = validate_repo_url(self.repo_url)
        self.port_override = normalize_port(self.port_override)
        return self


class PortOverrideUpdate(BaseModel):
    port_override: int | None = None

    @model_validator(mode="after")
    def _validate(self):
        self.port_override = normalize_port(self.port_override)
        return self


class RedeployRequest(BaseModel):
    """Optional overrides for Re-deploy; both fall back to the Deployment's own
    metadata (new code from the same repo URL, port override editable)."""
    repo_url: str | None = None
    port_override: int | None = None

    @model_validator(mode="after")
    def _validate(self):
        if self.repo_url is not None:
            if self.repo_url.strip().lower() == "demo":
                if os.environ.get("PORTAL_ENV", "dev") == "prod":
                    raise InvalidRepoUrl("repo_url must be an HTTPS URL.")
                self.repo_url = "demo"
            else:
                self.repo_url = validate_repo_url(self.repo_url)
        self.port_override = normalize_port(self.port_override)
        return self


class SecretRequest(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=8192)

    @model_validator(mode="after")
    def _validate(self):
        if " " in self.key or "=" in self.key or "\n" in self.key:
            raise InvalidRepoUrl("Secret name must be a single environment name (no spaces or =).")
        return self


class OperatorConfigUpdate(BaseModel):
    quota: dict | None = None
    allow_list: list[dict] | None = None
    allow_all: bool | None = None
    egress: list | None = None
    idle_stop_seconds: int | None = None


class RollbackRequest(BaseModel):
    revision: int


class ConfirmRequest(BaseModel):
    confirm: bool = False
    reason: str | None = None