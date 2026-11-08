from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from contextlib import asynccontextmanager
import base64
import json
import uuid
import os
from datetime import datetime

from database import get_db, init_db, AsyncSessionLocal
from models import User, Message
from schemas import SignupRequest, SignupResponse, LoginRequest, LoginResponse
from auth import hash_password, verify_password


# Track active WebSocket connections
active_connections: dict[int, list] = {}

# Media storage directory (Render uses /tmp for free tier)
MEDIA_DIR = "/tmp/media"
os.makedirs(MEDIA_DIR, exist_ok=True)


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


# ─── GET ALL USERS (for chat list + search) ─────────────────────

@app.get("/api/users")
async def get_all_users(
    search: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(User)

    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                User.email.ilike(search_term),
                User.phone.ilike(search_term),
                User.zone.ilike(search_term),
            )
        )

    result = await db.execute(query)
    users = result.scalars().all()

    return [
        {
            "id": u.id,
            "email": u.email,
            "phone": u.phone,
            "zone": u.zone,
        }
        for u in users
    ]


# ─── GET SINGLE USER ────────────────────────────────────────────

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


# ─── UPLOAD MEDIA (photos, videos, voice notes) ─────────────────

@app.post("/api/users/{user_id}/media")
async def upload_media(
    user_id: int,
    file: UploadFile = File(...),
    type: str = Query(...),
):
    if type not in ("image", "video", "audio"):
        raise HTTPException(status_code=400, detail="Invalid media type")

    file_id = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = os.path.join(MEDIA_DIR, file_id)

    contents = await file.read()
    with open(file_path, "wb") as f:
        f.write(contents)

    url = f"/media/{file_id}"

    return {"success": True, "url": url}


# ─── SERVE MEDIA FILES ──────────────────────────────────────────

from fastapi.responses import FileResponse

@app.get("/media/{filename}")
async def serve_media(filename: str):
    file_path = os.path.join(MEDIA_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    # Determine content type
    if filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
        media_type = "image/" + filename.split('.')[-1]
    elif filename.endswith(('.mp4', '.mov', '.avi', '.webm')):
        media_type = "video/" + filename.split('.')[-1]
    elif filename.endswith(('.m4a', '.mp3', '.wav', '.ogg')):
        media_type = "audio/" + filename.split('.')[-1]
    else:
        media_type = "application/octet-stream"

    return FileResponse(file_path, media_type=media_type)


# ─── GET MESSAGES BETWEEN TWO USERS ─────────────────────────────

@app.get("/api/messages/{user_id}/{contact_id}")
async def get_messages(user_id: int, contact_id: int, db: AsyncSession = Depends(get_db)):

    result = await db.execute(
        select(Message).where(
            or_(
                (Message.sender_id == user_id) & (Message.recipient_id == contact_id),
                (Message.sender_id == contact_id) & (Message.recipient_id == user_id),
            )
        ).order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()

    return [
        {
            "id": m.id,
            "sender_id": m.sender_id,
            "recipient_id": m.recipient_id,
            "text": m.text,
            "media_url": m.media_url,
            "media_type": m.media_type,
            "created_at": str(m.created_at),
        }
        for m in messages
    ]


# ─── SEND MESSAGE (REST fallback) ───────────────────────────────

@app.post("/api/messages", status_code=201)
async def send_message(data: dict, db: AsyncSession = Depends(get_db)):
    sender_id = data.get("sender_id")
    recipient_id = data.get("recipient_id")
    text = data.get("text", "")
    media_url = data.get("media_url")
    media_type = data.get("media_type")

    if not sender_id or not recipient_id:
        raise HTTPException(status_code=400, detail="sender_id and recipient_id required")

    new_msg = Message(
        sender_id=sender_id,
        recipient_id=recipient_id,
        text=text,
        media_url=media_url,
        media_type=media_type,
    )
    db.add(new_msg)
    await db.commit()
    await db.refresh(new_msg)

    return {"success": True, "id": new_msg.id}


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

            sender_id = msg.get("sender_id")
            recipient_id = msg.get("recipient_id")
            text = msg.get("text", "")
            media_url = msg.get("media_url")
            media_type = msg.get("media_type")

            # Store in DB
            async with AsyncSessionLocal() as session:
                new_msg = Message(
                    sender_id=sender_id,
                    recipient_id=recipient_id,
                    text=text,
                    media_url=media_url,
                    media_type=media_type,
                )
                session.add(new_msg)
                await session.commit()
                await session.refresh(new_msg)

            response_data = {
                "sender": str(sender_id),
                "text": text,
                "time": datetime.now().strftime("%I:%M %p"),
                "isMe": "true",
                "media_url": media_url or "",
                "media_type": media_type or "",
            }

            # Forward to recipient if online
            if recipient_id in active_connections:
                recipient_data = dict(response_data)
                recipient_data["isMe"] = "false"
                for ws in active_connections[recipient_id]:
                    await ws.send_text(json.dumps(recipient_data))

            # Echo back to sender
            await websocket.send_text(json.dumps(response_data))

    except WebSocketDisconnect:
        if user_id in active_connections:
            active_connections[user_id].remove(websocket)
            if not active_connections[user_id]:
                del active_connections[user_id]


# ─── HEALTH ─────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}   