from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from contextlib import asynccontextmanager

from database import get_db, init_db
from models import User
from schemas import SignupRequest, SignupResponse, LoginRequest, LoginResponse
from auth import hash_password, verify_password


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Gate App API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── SIGNUP ─────────────────────────────────────────────────────

@app.post("/api/signup", response_model=SignupResponse, status_code=201)
async def signup(data: SignupRequest, db: AsyncSession = Depends(get_db)):

    if data.password != data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    new_user = User(
        email=data.email,
        phone=data.phone,
        zone=data.zone,
        hashed_password=hash_password(data.password),
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return SignupResponse(
        id=new_user.id,
        email=new_user.email,
        phone=new_user.phone,
        zone=new_user.zone,
        created_at=str(new_user.created_at),
    )


# ─── LOGIN ──────────────────────────────────────────────────────

@app.post("/api/login", response_model=LoginResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):

    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return LoginResponse(id=user.id, email=user.email, zone=user.zone)


# ─── GET USER BY ID ─────────────────────────────────────────────

@app.get("/api/users/{user_id}")
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {"id": user.id, "email": user.email, "phone": user.phone, "zone": user.zone}


# ─── HEALTH ─────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}   