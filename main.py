from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, desc
from contextlib import asynccontextmanager
import base64
import json
import uuid
import os
from datetime import datetime
from typing import Optional

from database import get_db, init_db, AsyncSessionLocal
from models import User, Message, Incident, AuditLog
from schemas import (
    SignupRequest, SignupResponse,
    LoginRequest, LoginResponse,
    IncidentCreate, IncidentResolve,
)
from auth import hash_password, verify_password


# ─── INFRA ──────────────────────────────────────────────────────

active_connections: dict[int, list] = {}
MEDIA_DIR = "/tmp/media"
os.makedirs(MEDIA_DIR, exist_ok=True)

ADMIN_AUTH_CODE = "GATE_ADMIN_2024"
MAX_ADMINS = 3


async def log_action(
    db: AsyncSession,
    user_id: int | None,
    action: str,
    detail: str | None = None,
    ip: str | None = None,
):
    entry = AuditLog(user_id=user_id, action=action, detail=detail, ip_address=ip)
    db.add(entry)
    await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_default_admin()
    yield


async def seed_default_admin():
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.email == "ADMIN123@GMAIL.COM")
            )
            existing = result.scalar_one_or_none()
            if not existing:
                admin = User(
                    email="ADMIN123@GMAIL.COM",
                    phone="0000000000",
                    zone="Gate A",
                    role="admin",
                    hashed_password=hash_password("Code001#"),
                )
                session.add(admin)
                await session.commit()
                print("✅ Default admin seeded: ADMIN123@GMAIL.COM")
            else:
                print("ℹ️  Default admin already exists, skipping.")
    except Exception as e:
        print(f"❌ Admin seed failed: {e}")


app = FastAPI(title="Gate App API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── AUTH ───────────────────────────────────────────────────────

@app.post("/api/signup", response_model=SignupResponse, status_code=201)
async def signup(
    request: Request,
    data: SignupRequest,
    db: AsyncSession = Depends(get_db),
):
    if data.password != data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    # ── Role restrictions ──
    if data.role in ("admin", "investigator"):
        admin_count_result = await db.execute(
            select(User).where(User.role.in_(["admin", "investigator"]))
        )
        existing_admins = admin_count_result.scalars().all()

        if len(existing_admins) >= MAX_ADMINS:
            raise HTTPException(
                status_code=403,
                detail=f"Maximum of {MAX_ADMINS} admin/investigator accounts reached",
            )

        if data.admin_auth_code != ADMIN_AUTH_CODE:
            raise HTTPException(
                status_code=403,
                detail="Invalid admin authorization code",
            )

    new_user = User(
        email=data.email,
        phone=data.phone,
        zone=data.zone,
        role=data.role,
        hashed_password=hash_password(data.password),
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    await log_action(
        db, new_user.id, "signup", f"New {data.role} registered",
        str(request.client.host) if request.client else None,
    )

    return SignupResponse(
        id=new_user.id,
        email=new_user.email,
        phone=new_user.phone,
        zone=new_user.zone,
        role=new_user.role,
        created_at=str(new_user.created_at),
    )


@app.post("/api/login", response_model=LoginResponse)
async def login(
    request: Request,
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        await log_action(
            db, None, "login_failed", f"Failed login: {data.email}",
            str(request.client.host) if request.client else None,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await log_action(
        db, user.id, "login", f"{user.role} logged in",
        str(request.client.host) if request.client else None,
    )

    return LoginResponse(id=user.id, email=user.email, zone=user.zone, role=user.role)


# ─── PUBLIC KEYS ────────────────────────────────────────────────

@app.put("/api/users/{user_id}/public_key")
async def upload_public_key(
    user_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.public_key = data.get("public_key")
    await db.commit()
    return {"success": True}


@app.get("/api/users/{user_id}/public_key")
async def get_public_key(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"public_key": user.public_key}


# ─── USERS ──────────────────────────────────────────────────────

@app.get("/api/users")
async def get_all_users(
    search: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(User)
    if search:
        term = f"%{search}%"
        query = query.where(
            or_(
                User.email.ilike(term),
                User.phone.ilike(term),
                User.zone.ilike(term),
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
            "role": u.role,
        }
        for u in users
    ]


@app.get("/api/users/online")
async def get_online_users():
    return {"online_ids": list(active_connections.keys())}


@app.get("/api/users/{user_id}")
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user.id,
        "email": user.email,
        "phone": user.phone,
        "zone": user.zone,
        "role": user.role,
        "photo": user.photo,
    }


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
    user.photo = base64.b64encode(contents).decode("utf-8")
    await db.commit()
    return {"success": True}


# ─── MESSAGES ───────────────────────────────────────────────────

@app.get("/api/messages/{user_id}/{contact_id}")
async def get_messages(
    user_id: int,
    contact_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Message)
        .where(
            or_(
                (Message.sender_id == user_id) & (Message.recipient_id == contact_id),
                (Message.sender_id == contact_id) & (Message.recipient_id == user_id),
            )
        )
        .order_by(Message.created_at.asc())
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


@app.post("/api/messages", status_code=201)
async def send_message(
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    sender_id = data.get("sender_id")
    recipient_id = data.get("recipient_id")
    if not sender_id or not recipient_id:
        raise HTTPException(status_code=400, detail="sender_id and recipient_id required")

    new_msg = Message(
        sender_id=sender_id,
        recipient_id=recipient_id,
        text=data.get("text", ""),
        media_url=data.get("media_url"),
        media_type=data.get("media_type"),
    )
    db.add(new_msg)
    await db.commit()
    await db.refresh(new_msg)

    await log_action(db, sender_id, "message_sent", f"To user {recipient_id}")

    return {"success": True, "id": new_msg.id}


# ─── MEDIA ──────────────────────────────────────────────────────

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
    return {"success": True, "url": f"/media/{file_id}"}


@app.get("/media/{filename}")
async def serve_media(filename: str):
    file_path = os.path.join(MEDIA_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    if filename.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        media_type = "image/" + filename.split(".")[-1]
    elif filename.endswith((".mp4", ".mov", ".avi", ".webm")):
        media_type = "video/" + filename.split(".")[-1]
    elif filename.endswith((".m4a", ".mp3", ".wav", ".ogg")):
        media_type = "audio/" + filename.split(".")[-1]
    else:
        media_type = "application/octet-stream"
    return FileResponse(file_path, media_type=media_type)


# ─── INCIDENTS ──────────────────────────────────────────────────

@app.post("/api/incidents", status_code=201)
async def create_incident(
    request: Request,
    data: IncidentCreate,
    reporter_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == reporter_id))
    reporter = result.scalar_one_or_none()
    if not reporter:
        raise HTTPException(status_code=404, detail="Reporter not found")

    new_incident = Incident(
        reporter_id=reporter_id,
        zone=data.zone,
        type=data.type,
        location=data.location,
        description=data.description,
        is_urgent=data.is_urgent,
    )
    db.add(new_incident)
    await db.commit()
    await db.refresh(new_incident)

    await log_action(
        db, reporter_id, "incident_reported",
        f"{data.type} at {data.location} (urgent={data.is_urgent})",
        str(request.client.host) if request.client else None,
    )

    return {
        "id": new_incident.id,
        "status": new_incident.status,
        "created_at": str(new_incident.created_at),
    }


@app.get("/api/incidents")
async def list_incidents(
    status: str = Query(None),
    zone: str = Query(None),
    urgent_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    query = select(Incident).order_by(desc(Incident.created_at))
    if status:
        query = query.where(Incident.status == status)
    if zone:
        query = query.where(Incident.zone == zone)
    if urgent_only:
        query = query.where(Incident.is_urgent == True)

    result = await db.execute(query)
    incidents = result.scalars().all()

    out = []
    for inc in incidents:
        r = await db.execute(select(User).where(User.id == inc.reporter_id))
        reporter = r.scalar_one_or_none()
        out.append({
            "id": inc.id,
            "reporter_id": inc.reporter_id,
            "reporter_email": reporter.email if reporter else "unknown",
            "zone": inc.zone,
            "type": inc.type,
            "location": inc.location,
            "description": inc.description,
            "is_urgent": inc.is_urgent,
            "status": inc.status,
            "photo_url": inc.photo_url,
            "resolved_by": inc.resolved_by,
            "resolution_note": inc.resolution_note,
            "created_at": str(inc.created_at),
            "resolved_at": str(inc.resolved_at) if inc.resolved_at else None,
        })
    return out


@app.get("/api/incidents/{incident_id}")
async def get_incident(
    incident_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    inc = result.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    r = await db.execute(select(User).where(User.id == inc.reporter_id))
    reporter = r.scalar_one_or_none()

    return {
        "id": inc.id,
        "reporter_id": inc.reporter_id,
        "reporter_email": reporter.email if reporter else "unknown",
        "zone": inc.zone,
        "type": inc.type,
        "location": inc.location,
        "description": inc.description,
        "is_urgent": inc.is_urgent,
        "status": inc.status,
        "photo_url": inc.photo_url,
        "resolved_by": inc.resolved_by,
        "resolution_note": inc.resolution_note,
        "created_at": str(inc.created_at),
        "resolved_at": str(inc.resolved_at) if inc.resolved_at else None,
    }


@app.post("/api/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: int,
    data: IncidentResolve,
    resolver_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    inc = result.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    if inc.status != "reported":
        raise HTTPException(status_code=400, detail="Already resolved")

    inc.status = "resolved"
    inc.resolved_by = resolver_id
    inc.resolved_at = datetime.utcnow()
    inc.resolution_note = data.resolution_note
    await db.commit()

    await log_action(
        db, resolver_id, "incident_resolved",
        f"Incident #{incident_id}: {data.resolution_note}",
    )

    return {"success": True, "status": "resolved"}


@app.post("/api/incidents/{incident_id}/photo")
async def upload_incident_photo(
    incident_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    inc = result.scalar_one_or_none()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    file_id = f"incident_{incident_id}_{uuid.uuid4().hex}.jpg"
    file_path = os.path.join(MEDIA_DIR, file_id)
    contents = await file.read()
    with open(file_path, "wb") as f:
        f.write(contents)

    inc.photo_url = f"/media/{file_id}"
    await db.commit()
    return {"success": True, "url": f"/media/{file_id}"}


# ─── FORENSIC EXTRACTION ────────────────────────────────────────

@app.get("/api/forensic/{user_id}")
async def forensic_extract(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    msg_result = await db.execute(
        select(Message)
        .where(or_(Message.sender_id == user_id, Message.recipient_id == user_id))
        .order_by(Message.created_at.asc())
    )
    messages = msg_result.scalars().all()

    inc_result = await db.execute(
        select(Incident)
        .where(Incident.reporter_id == user_id)
        .order_by(desc(Incident.created_at))
    )
    incidents = inc_result.scalars().all()

    audit_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.user_id == user_id)
        .order_by(desc(AuditLog.created_at))
        .limit(100)
    )
    logs = audit_result.scalars().all()

    conversations = {}
    for m in messages:
        other_id = m.recipient_id if m.sender_id == user_id else m.sender_id
        if other_id not in conversations:
            conversations[other_id] = []
        conversations[other_id].append({
            "id": m.id,
            "sender_id": m.sender_id,
            "recipient_id": m.recipient_id,
            "text": m.text,
            "media_url": m.media_url,
            "media_type": m.media_type,
            "created_at": str(m.created_at),
        })

    contact_names = {}
    for cid in conversations.keys():
        r = await db.execute(select(User).where(User.id == cid))
        cu = r.scalar_one_or_none()
        contact_names[cid] = cu.email if cu else f"User #{cid}"

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "phone": user.phone,
            "zone": user.zone,
            "role": user.role,
            "created_at": str(user.created_at),
        },
        "conversations": [
            {
                "contact_id": cid,
                "contact_email": contact_names.get(cid, "unknown"),
                "message_count": len(msgs),
                "messages": msgs,
            }
            for cid, msgs in conversations.items()
        ],
        "incidents": [
            {
                "id": i.id,
                "type": i.type,
                "location": i.location,
                "description": i.description,
                "is_urgent": i.is_urgent,
                "status": i.status,
                "created_at": str(i.created_at),
                "resolution_note": i.resolution_note,
            }
            for i in incidents
        ],
        "audit_logs": [
            {
                "id": l.id,
                "action": l.action,
                "detail": l.detail,
                "created_at": str(l.created_at),
            }
            for l in logs
        ],
    }


# ─── AUDIT LOGS ─────────────────────────────────────────────────

@app.get("/api/audit-logs")
async def list_audit_logs(
    user_id: int = Query(None),
    action: str = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    query = select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if action:
        query = query.where(AuditLog.action == action)

    result = await db.execute(query)
    logs = result.scalars().all()

    out = []
    for l in logs:
        email = None
        if l.user_id:
            r = await db.execute(select(User).where(User.id == l.user_id))
            u = r.scalar_one_or_none()
            email = u.email if u else None
        out.append({
            "id": l.id,
            "user_id": l.user_id,
            "user_email": email,
            "action": l.action,
            "detail": l.detail,
            "ip_address": l.ip_address,
            "created_at": str(l.created_at),
        })
    return out


# ─── WEBSOCKET ──────────────────────────────────────────────────

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

                await log_action(session, sender_id, "message_sent", f"To user {recipient_id}")

            response_data = {
                "sender": str(sender_id),
                "text": text,
                "time": datetime.now().strftime("%I:%M %p"),
                "isMe": "true",
                "media_url": media_url or "",
                "media_type": media_type or "",
            }

            if recipient_id in active_connections:
                recipient_data = dict(response_data)
                recipient_data["isMe"] = "false"
                for ws in active_connections[recipient_id]:
                    await ws.send_text(json.dumps(recipient_data))

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