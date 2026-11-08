from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=15)
    zone: str = Field(..., pattern=r"^Gate [A-E]$")
    password: str = Field(..., min_length=8)
    confirm_password: str


class SignupResponse(BaseModel):
    id: int
    email: str
    phone: str
    zone: str
    created_at: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    id: int
    email: str
    zone: str   