"""Youzan administration request schemas."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class YouzanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prepare_login: StrictBool = True
    interactive_verification: StrictBool = False
    account: str = Field(min_length=1, max_length=160)
    password: str = Field(default="", max_length=256)
    kdt_id: str | None = Field(default=None, max_length=24, pattern=r"^[0-9]*$")

    @field_validator('password', mode='before')
    @classmethod
    def empty_password(cls, value):
        return '' if value is None else value

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
    kind: Literal["sid", "csrf", "shops", "all", "connect"]


class SendVerification(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    kind: Literal['send']


class CodeVerification(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    kind: Literal['code']
    code: str = Field(pattern=r'^[0-9]{6}$')


class PointerSample(BaseModel):
    model_config = ConfigDict(extra='forbid')
    x: int = Field(strict=True, ge=-500, le=500)
    y: int = Field(strict=True, ge=-400, le=400)
    ms: int = Field(strict=True, ge=0, le=10000)


class SliderVerification(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    kind: Literal['slider']
    points: list[PointerSample] = Field(min_length=2, max_length=160)


VerificationInput = Annotated[SendVerification | CodeVerification | SliderVerification, Field(discriminator='kind')]


class RenewalInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=True, strict=True)
    interval_days: float = Field(default=2, gt=0, allow_inf_nan=False, strict=True)


class RenewalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sid: RenewalInterval = Field(default_factory=RenewalInterval)
    csrf: RenewalInterval = Field(default_factory=RenewalInterval)
