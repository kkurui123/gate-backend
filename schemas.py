
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, model_validator


# --------------------------------------------------
# SIGNUP
# --------------------------------------------------

class SignupRequest(BaseModel):
    email: EmailStr

    phone: str = Field(
        ...,
        min_length=10,
        max_length=15,
    )

    zone: str = Field(
        ...,
        pattern=r"^Gate [A-E]$",
    )

    role: str = Field(
        default="guard",
        pattern=r"^(guard|admin|investigator)$",
    )

    password: str = Field(
        ...,
        min_length=8,
    )

    confirm_password: str = Field(
        ...,
        min_length=8,
    )

    admin_auth_code: Optional[str] = None

    @model_validator(mode="after")
    def validate_password_confirmation(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")

        return self


class SignupResponse(BaseModel):
    id: int
    email: str
    phone: str
    zone: str
    role: str
    created_at: str


# --------------------------------------------------
# LOGIN
# --------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr

    password: str = Field(
        ...,
        min_length=1,
    )


class LoginResponse(BaseModel):
    id: int
    email: str
    zone: str
    role: str


# --------------------------------------------------
# INCIDENT REPORTING
# --------------------------------------------------

class IncidentCreate(BaseModel):
    zone: str = Field(
        ...,
        pattern=r"^Gate [A-E]$",
    )

    type: str = Field(
        ...,
        min_length=2,
        max_length=50,
    )

    location: str = Field(
        ...,
        min_length=2,
        max_length=255,
    )

    description: str = Field(
        ...,
        min_length=10,
    )

    is_urgent: bool = False


# --------------------------------------------------
# INCIDENT RESOLUTION
# --------------------------------------------------

class IncidentResolve(BaseModel):
    resolution_note: str = Field(
        ...,
        min_length=5,
    )
