from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class SignupRequest(BaseModel):
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=15)
    zone: str = Field(..., pattern=r"^Gate [A-E]$")
    role: str = Field(default="guard", pattern=r"^(guard|admin|investigator)$")
    password: str = Field(..., min_length=8)
    confirm_password: str
    admin_auth_code: Optional[str] = None


class SignupResponse(BaseModel):
    id: int
    email: str
    phone: str
    zone: str
    role: str
    created_at: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    id: int
    email: str
    zone: str
    role: str


class IncidentCreate(BaseModel):
    zone: str = Field(..., pattern=r"^Gate [A-E]$")
    type: str = Field(..., min_length=2)
    location: str = Field(..., min_length=2)
    description: str = Field(..., min_length=10)
    is_urgent: bool = False


class IncidentResolve(BaseModel):
    resolution_note: str = Field(..., min_length=5)   