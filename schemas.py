from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


class SignupRequest(BaseModel):
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=15)
    zone: str = Field(..., pattern=r"^Gate [A-E]$")
    role: str = Field(default="guard", pattern=r"^(guard|admin|investigator)$")
    password: str = Field(..., min_length=8)
    confirm_password: str


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


class IncidentResponse(BaseModel):
    id: int
    reporter_id: int
    reporter_email: str
    zone: str
    type: str
    location: str
    description: str
    is_urgent: bool
    status: str
    photo_url: Optional[str] = None
    resolved_by: Optional[int] = None
    resolution_note: Optional[str] = None
    created_at: str
    resolved_at: Optional[str] = None


class IncidentResolve(BaseModel):
    resolution_note: str = Field(..., min_length=5)


class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int]
    user_email: Optional[str]
    action: str
    detail: Optional[str]
    ip_address: Optional[str]
    created_at: str   