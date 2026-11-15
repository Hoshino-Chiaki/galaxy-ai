"""Youzan administration request schemas."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class YouzanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str = Field(min_length=1, max_length=160)
    password: str = Field(default="", max_length=256)
    kdt_id: str | None = Field(default=None, max_length=24, pattern=r"^[0-9]*$")

    @field_validator("account")
    @classmethod
    def account_value(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid account")
        return value


class ShopInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kdt_id: str = Field(min_length=1, max_length=24, pattern=r"^[0-9]+$")


class RefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["sid", "csrf", "shops", "all"]


class RenewalInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=True, strict=True)
    interval_days: float = Field(default=2, gt=0, allow_inf_nan=False, strict=True)


class RenewalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sid: RenewalInterval = Field(default_factory=RenewalInterval)
    csrf: RenewalInterval = Field(default_factory=RenewalInterval)
