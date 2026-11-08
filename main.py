from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from contextlib import asynccontextmanager
import base64
import json
from datetime import datetime

from database import get_db, init_db, AsyncSessionLocal
from models import User, Message
from schemas import SignupRequest, SignupResponse, LoginRequest, LoginResponse
from auth import hash_password, verify_password


# Track active WebSocket connections
active_connections: dict[int, list] = {}


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


# ─── GET USER ───────────────────────────────────────────────────

@app.get("/api/users/{user_id}")
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": user.id,
        "email": user.email,
        "phone": user.phone,
        "zone": user.zone,
        "photo": user.photo,
    }


# ─── UPLOAD PHOTO ───────────────────────────────────────────────

@app.post("/api/users/{user_id}/photo")
async def upload_photo(
    user_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    contents = await file.read()
    photo_b64 = base64.b64encode(contents).decode("utf-8")

    user.photo = photo_b64
    await db.commit()

    return {"success": True, "message": "Photo updated"}


# ─── GET MESSAGES ───────────────────────────────────────────────

@app.get("/api/messages/{user_id}/{contact_id}")
async def get_messages(user_id: int, contact_id: int, db: AsyncSession = Depends(get_db)):

    result = await db.execute(
        select(Message).where(
            (Message.sender_id == user_id) & (Message.recipient_id == contact_id)
        ) | (
            (Message.sender_id == contact_id) & (Message.recipient_id == user_id)
        )
    )
    messages = result.scalars().all()

    return [
        {
            "id": m.id,
            "sender_id": m.sender_id,
            "recipient_id": m.recipient_id,
            "text": m.text,
            "created_at": str(m.created_at),
        }
        for m in messages
    ]


# ─── WEBSOCKET CHAT ─────────────────────────────────────────────

@app.websocket("/ws/chat/{user_id}/{contact_id}")
async def chat_websocket(websocket: WebSocket, user_id: int, contact_id: int):
    await websocket.accept()

    if user_id not in active_connections:
        active_connections[user_id] = []
    active_connections[user_id].append(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            sender_id = msg['sender_id']
            recipient_id = msg['recipient_id']
            text = msg['text']

            # Store in DB
            async with AsyncSessionLocal() as session:
                new_msg = Message(
                    sender_id=sender_id,
                    recipient_id=recipient_id,
                    text=text,
                )
                session.add(new_msg)
                await session.commit()

            # Forward to recipient if online
            if recipient_id in active_connections:
                for ws in active_connections[recipient_id]:
                    await ws.send_text(json.dumps({
                        'sender': str(sender_id),
                        'text': text,
                        'time': datetime.now().strftime('%I:%M %p'),
                        'isMe': 'false',
                    }))

            # Echo back to sender
            await websocket.send_text(json.dumps({
                'sender': str(sender_id),
                'text': text,
                'time': datetime.now().strftime('%I:%M %p'),
                'isMe': 'true',
            }))

    except WebSocketDisconnect:
        active_connections[user_id].remove(websocket)
        if not active_connections[user_id]:
            del active_connections[user_id]


# ─── HEALTH ─────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}   