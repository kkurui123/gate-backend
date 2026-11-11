
import os
import json
import uuid
import base64
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    UploadFile,
    File,
    WebSocket,
    WebSocketDisconnect,
    Query,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, desc

from database import get_db, init_db, AsyncSessionLocal
from models import User, Message, Incident, AuditLog
from schemas import (
    SignupRequest,
    SignupResponse,
    LoginRequest,
    LoginResponse,
    IncidentCreate,
    IncidentResolve,
)
from auth import hash_password, verify_password


# ============================================================
# CONFIGURATION
# ============================================================

active_connections: dict[int, list[WebSocket]] = {}

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/tmp/media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_AUTH_CODE = os.getenv("ADMIN_AUTH_CODE", "GATE_ADMIN_2024")
MAX_ADMINS = 3

MAX_MEDIA_SIZE = 50 * 1024 * 1024
MAX_PHOTO_SIZE = 10 * 1024 * 1024

ALLOWED_MEDIA_TYPES = {
    "image": {
        ".jpg", ".jpeg", ".png", ".gif", ".webp"
    },
    "video": {
        ".mp4", ".mov", ".avi", ".webm"
    },
    "audio": {
        ".m4a", ".mp3", ".wav", ".ogg"
    },
}


# ============================================================
# HELPERS
# ============================================================

async def log_action(
    db: AsyncSession,
    user_id: Optional[int],
    action: str,
    detail: Optional[str] = None,
    ip: Optional[str] = None,
):
    entry = AuditLog(
        user_id=user_id,
        action=action,
        detail=detail,
        ip_address=ip,
    )

    db.add(entry)
    await db.commit()


async def get_user_by_id(db: AsyncSession, user_id: int):
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def save_uploaded_file(
    file: UploadFile,
    allowed_extensions: set[str],
    max_size: int,
    prefix: str = "",
):
    original_name = Path(file.filename or "").name
    extension = Path(original_name).suffix.lower()

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file extension",
        )

    contents = await file.read(max_size + 1)

    if not contents:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty",
        )

    if len(contents) > max_size:
        raise HTTPException(
            status_code=413,
            detail="Uploaded file is too large",
        )

    file_id = f"{prefix}{uuid.uuid4().hex}{extension}"
    file_path = MEDIA_DIR / file_id

    try:
        file_path.write_bytes(contents)
    except OSError:
        raise HTTPException(
            status_code=500,
            detail="Could not save uploaded file",
        )

    return file_id


def get_media_type(filename: str):
    extension = Path(filename).suffix.lower()

    if extension in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if extension == ".png":
        return "image/png"
    if extension == ".gif":
        return "image/gif"
    if extension == ".webp":
        return "image/webp"

    if extension == ".mp4":
        return "video/mp4"
    if extension == ".mov":
        return "video/quicktime"
    if extension == ".avi":
        return "video/x-msvideo"
    if extension == ".webm":
        return "video/webm"

    if extension == ".m4a":
        return "audio/mp4"
    if extension == ".mp3":
        return "audio/mpeg"
    if extension == ".wav":
        return "audio/wav"
    if extension == ".ogg":
        return "audio/ogg"

    return "application/octet-stream"


async def cleanup_websocket(user_id: int, websocket: WebSocket):
    connections = active_connections.get(user_id)

    if connections and websocket in connections:
        connections.remove(websocket)

    if connections is not None and not connections:
        active_connections.pop(user_id, None)


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_default_admin()
    yield


async def seed_default_admin():
    """
    Retains the existing default admin for compatibility.

    Set a strong password and authorization code in your
    Render environment before production deployment.
    """
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(
                    User.email == "ADMIN123@GMAIL.COM"
                )
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

                print("Default admin account created.")
            else:
                print("Default admin already exists.")

    except Exception as exc:
        print(f"Admin seed failed: {exc}")


app = FastAPI(
    title="Gate App API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# AUTHENTICATION
# ============================================================

@app.post(
    "/api/signup",
    response_model=SignupResponse,
    status_code=201,
)
async def signup(
    request: Request,
    data: SignupRequest,
    db: AsyncSession = Depends(get_db),
):
    if data.password != data.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match",
        )

    result = await db.execute(
        select(User).where(User.email == data.email)
    )

    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Email already registered",
        )

    if data.role in ("admin", "investigator"):
        admin_result = await db.execute(
            select(User).where(
                User.role.in_(["admin", "investigator"])
            )
        )

        existing_admins = admin_result.scalars().all()

        if len(existing_admins) >= MAX_ADMINS:
            raise HTTPException(
                status_code=403,
                detail=f"Maximum of {MAX_ADMINS} privileged accounts reached",
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
        db,
        new_user.id,
        "signup",
        f"New {data.role} registered",
        request.client.host if request.client else None,
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
    result = await db.execute(
        select(User).where(User.email == data.email)
    )

    user = result.scalar_one_or_none()

    if not user or not verify_password(
        data.password,
        user.hashed_password,
    ):
        await log_action(
            db,
            None,
            "login_failed",
            f"Failed login: {data.email}",
            request.client.host if request.client else None,
        )

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    await log_action(
        db,
        user.id,
        "login",
        f"{user.role} logged in",
        request.client.host if request.client else None,
    )

    return LoginResponse(
        id=user.id,
        email=user.email,
        zone=user.zone,
        role=user.role,
    )


# ============================================================
# PUBLIC KEYS
# ============================================================

@app.put("/api/users/{user_id}/public_key")
async def upload_public_key(
    user_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    public_key = data.get("public_key")

    if not isinstance(public_key, str) or not public_key.strip():
        raise HTTPException(
            status_code=400,
            detail="A valid public key is required",
        )

    result = await db.execute(
        select(User).where(User.id == user_id)
    )

    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    user.public_key = public_key

    await db.commit()

    return {"success": True}


@app.get("/api/users/{user_id}/public_key")
async def get_public_key(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    return {"public_key": user.public_key}


# ============================================================
# USERS
# ============================================================

@app.get("/api/users")
async def get_all_users(
    search: Optional[str] = Query(None),
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
            "id": user.id,
            "email": user.email,
            "phone": user.phone,
            "zone": user.zone,
            "role": user.role,
        }
        for user in users
    ]


@app.get("/api/users/online")
async def get_online_users():
    return {
        "online_ids": list(active_connections.keys())
    }


@app.get("/api/users/{user_id}")
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

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
    user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    contents = await file.read(MAX_PHOTO_SIZE + 1)

    if not contents:
        raise HTTPException(
            status_code=400,
            detail="Uploaded photo is empty",
        )

    if len(contents) > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Photo exceeds the 10 MB limit",
        )

    user.photo = base64.b64encode(contents).decode("utf-8")

    await db.commit()

    return {"success": True}


# ============================================================
# MESSAGES
# ============================================================

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
                (Message.sender_id == user_id)
                & (Message.recipient_id == contact_id),
                (Message.sender_id == contact_id)
                & (Message.recipient_id == user_id),
            )
        )
        .order_by(Message.created_at.asc())
    )

    messages = result.scalars().all()

    return [
        {
            "id": message.id,
            "sender_id": message.sender_id,
            "recipient_id": message.recipient_id,
            "text": message.text,
            "media_url": message.media_url,
            "media_type": message.media_type,
            "created_at": str(message.created_at),
        }
        for message in messages
    ]


@app.post("/api/messages", status_code=201)
async def send_message(
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    sender_id = data.get("sender_id")
    recipient_id = data.get("recipient_id")

    if not isinstance(sender_id, int) or not isinstance(recipient_id, int):
        raise HTTPException(
            status_code=400,
            detail="Valid sender_id and recipient_id are required",
        )

    if sender_id == recipient_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot send a message to yourself",
        )

    sender = await get_user_by_id(db, sender_id)
    recipient = await get_user_by_id(db, recipient_id)

    if not sender or not recipient:
        raise HTTPException(
            status_code=404,
            detail="Sender or recipient not found",
        )

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

    await log_action(
        db,
        sender_id,
        "message_sent",
        f"To user {recipient_id}",
    )

    return {
        "success": True,
        "id": new_msg.id,
    }


# ============================================================
# MEDIA
# ============================================================

@app.post("/api/users/{user_id}/media")
async def upload_media(
    user_id: int,
    file: UploadFile = File(...),
    type: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    if type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Invalid media type",
        )

    user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    file_id = await save_uploaded_file(
        file,
        ALLOWED_MEDIA_TYPES[type],
        MAX_MEDIA_SIZE,
    )

    return {
        "success": True,
        "url": f"/media/{file_id}",
    }


@app.get("/media/{filename}")
async def serve_media(filename: str):
    # Only allow a simple filename, never a path.
    if filename != Path(filename).name:
        raise HTTPException(
            status_code=400,
            detail="Invalid filename",
        )

    file_path = MEDIA_DIR / filename

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="File not found",
        )

    return FileResponse(
        path=file_path,
        media_type=get_media_type(filename),
    )


# ============================================================
# INCIDENTS
# ============================================================

@app.post("/api/incidents", status_code=201)
async def create_incident(
    request: Request,
    data: IncidentCreate,
    reporter_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    reporter = await get_user_by_id(db, reporter_id)

    if not reporter:
        raise HTTPException(
            status_code=404,
            detail="Reporter not found",
        )

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
        db,
        reporter_id,
        "incident_reported",
        f"{data.type} at {data.location} (urgent={data.is_urgent})",
        request.client.host if request.client else None,
    )

    return {
        "id": new_incident.id,
        "status": new_incident.status,
        "created_at": str(new_incident.created_at),
    }


@app.get("/api/incidents")
async def list_incidents(
    status: Optional[str] = Query(None),
    zone: Optional[str] = Query(None),
    urgent_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    query = select(Incident).order_by(
        desc(Incident.created_at)
    )

    if status:
        query = query.where(Incident.status == status)

    if zone:
        query = query.where(Incident.zone == zone)

    if urgent_only:
        query = query.where(Incident.is_urgent.is_(True))

    result = await db.execute(query)
    incidents = result.scalars().all()

    out = []

    for incident in incidents:
        reporter = await get_user_by_id(
            db,
            incident.reporter_id,
        )

        out.append({
            "id": incident.id,
            "reporter_id": incident.reporter_id,
            "reporter_email": reporter.email if reporter else "unknown",
            "zone": incident.zone,
            "type": incident.type,
            "location": incident.location,
            "description": incident.description,
            "is_urgent": incident.is_urgent,
            "status": incident.status,
            "photo_url": incident.photo_url,
            "resolved_by": incident.resolved_by,
            "resolution_note": incident.resolution_note,
            "created_at": str(incident.created_at),
            "resolved_at": (
                str(incident.resolved_at)
                if incident.resolved_at else None
            ),
        })

    return out


@app.get("/api/incidents/{incident_id}")
async def get_incident(
    incident_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )

    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    reporter = await get_user_by_id(
        db,
        incident.reporter_id,
    )

    return {
        "id": incident.id,
        "reporter_id": incident.reporter_id,
        "reporter_email": reporter.email if reporter else "unknown",
        "zone": incident.zone,
        "type": incident.type,
        "location": incident.location,
        "description": incident.description,
        "is_urgent": incident.is_urgent,
        "status": incident.status,
        "photo_url": incident.photo_url,
        "resolved_by": incident.resolved_by,
        "resolution_note": incident.resolution_note,
        "created_at": str(incident.created_at),
        "resolved_at": (
            str(incident.resolved_at)
            if incident.resolved_at else None
        ),
    }


@app.post("/api/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: int,
    data: IncidentResolve,
    resolver_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    incident_result = await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )

    incident = incident_result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    if incident.status != "reported":
        raise HTTPException(
            status_code=400,
            detail="Already resolved",
        )

    resolver = await get_user_by_id(db, resolver_id)

    if not resolver:
        raise HTTPException(
            status_code=404,
            detail="Resolver not found",
        )

    incident.status = "resolved"
    incident.resolved_by = resolver_id
    incident.resolved_at = datetime.utcnow()
    incident.resolution_note = data.resolution_note

    await db.commit()

    await log_action(
        db,
        resolver_id,
        "incident_resolved",
        f"Incident #{incident_id}: {data.resolution_note}",
    )

    return {
        "success": True,
        "status": "resolved",
    }


@app.post("/api/incidents/{incident_id}/photo")
async def upload_incident_photo(
    incident_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )

    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    file_id = await save_uploaded_file(
        file,
        ALLOWED_MEDIA_TYPES["image"],
        MAX_PHOTO_SIZE,
        prefix=f"incident_{incident_id}_",
    )

    incident.photo_url = f"/media/{file_id}"

    await db.commit()

    return {
        "success": True,
        "url": f"/media/{file_id}",
    }


# ============================================================
# FORENSIC EXTRACTION
# ============================================================

@app.get("/api/forensic/{user_id}")
async def forensic_extract(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    msg_result = await db.execute(
        select(Message)
        .where(
            or_(
                Message.sender_id == user_id,
                Message.recipient_id == user_id,
            )
        )
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

    for message in messages:
        other_id = (
            message.recipient_id
            if message.sender_id == user_id
            else message.sender_id
        )

        if other_id not in conversations:
            conversations[other_id] = []

        conversations[other_id].append({
            "id": message.id,
            "sender_id": message.sender_id,
            "recipient_id": message.recipient_id,
            "text": message.text,
            "media_url": message.media_url,
            "media_type": message.media_type,
            "created_at": str(message.created_at),
        })

    contact_names = {}

    for contact_id in conversations:
        contact = await get_user_by_id(db, contact_id)

        contact_names[contact_id] = (
            contact.email if contact else f"User #{contact_id}"
        )

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
                "contact_id": contact_id,
                "contact_email": contact_names.get(contact_id, "unknown"),
                "message_count": len(contact_messages),
                "messages": contact_messages,
            }
            for contact_id, contact_messages in conversations.items()
        ],
        "incidents": [
            {
                "id": incident.id,
                "type": incident.type,
                "location": incident.location,
                "description": incident.description,
                "is_urgent": incident.is_urgent,
                "status": incident.status,
                "created_at": str(incident.created_at),
                "resolution_note": incident.resolution_note,
            }
            for incident in incidents
        ],
        "audit_logs": [
            {
                "id": log.id,
                "action": log.action,
                "detail": log.detail,
                "created_at": str(log.created_at),
            }
            for log in logs
        ],
    }


# ============================================================
# AUDIT LOGS
# ============================================================

@app.get("/api/audit-logs")
async def list_audit_logs(
    user_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    query = select(AuditLog).order_by(
        desc(AuditLog.created_at)
    ).limit(limit)

    if user_id is not None:
        query = query.where(AuditLog.user_id == user_id)

    if action:
        query = query.where(AuditLog.action == action)

    result = await db.execute(query)
    logs = result.scalars().all()

    out = []

    for log in logs:
        email = None

        if log.user_id:
            user = await get_user_by_id(db, log.user_id)
            email = user.email if user else None

        out.append({
            "id": log.id,
            "user_id": log.user_id,
            "user_email": email,
            "action": log.action,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": str(log.created_at),
        })

    return out


# ============================================================
# WEBSOCKET CHAT
# ============================================================

@app.websocket("/ws/chat/{user_id}/{contact_id}")
async def chat_websocket(
    websocket: WebSocket,
    user_id: int,
    contact_id: int,
):
    await websocket.accept()

    # Confirm that both accounts exist.
    try:
        async with AsyncSessionLocal() as session:
            sender = await get_user_by_id(session, user_id)
            recipient = await get_user_by_id(session, contact_id)

            if not sender or not recipient:
                await websocket.close(code=1008)
                return

    except Exception as exc:
        print(f"WebSocket initialization failed: {exc}")
        await websocket.close(code=1011)
        return

    active_connections.setdefault(user_id, []).append(websocket)

    try:
        while True:
            raw_data = await websocket.receive_text()

            try:
                msg = json.loads(raw_data)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "error": "Invalid JSON message",
                }))
                continue

            if not isinstance(msg, dict):
                await websocket.send_text(json.dumps({
                    "error": "Invalid message format",
                }))
                continue

            sender_id = msg.get("sender_id")
            recipient_id = msg.get("recipient_id")

            # Do not allow a connection to impersonate another sender
            # or send to a different contact.
            if sender_id != user_id or recipient_id != contact_id:
                await websocket.send_text(json.dumps({
                    "error": "Message sender or recipient does not match connection",
                }))
                continue

            text = msg.get("text", "")
            media_url = msg.get("media_url")
            media_type = msg.get("media_type")

            if not isinstance(text, str):
                await websocket.send_text(json.dumps({
                    "error": "Message text must be a string",
                }))
                continue

            try:
                async with AsyncSessionLocal() as session:
                    new_msg = Message(
                        sender_id=user_id,
                        recipient_id=contact_id,
                        text=text,
                        media_url=media_url,
                        media_type=media_type,
                    )

                    session.add(new_msg)
                    await session.commit()
                    await session.refresh(new_msg)

                    await log_action(
                        session,
                        user_id,
                        "message_sent",
                        f"To user {contact_id}",
                    )

            except Exception as exc:
                print(f"WebSocket message save failed: {exc}")

                await websocket.send_text(json.dumps({
                    "error": "Message could not be saved",
                }))
                continue

            response_data = {
                "sender": str(user_id),
                "text": text,
                "time": datetime.now().strftime("%I:%M %p"),
                "isMe": "true",
                "media_url": media_url or "",
                "media_type": media_type or "",
            }

            # Send to recipient's active connections.
            recipient_connections = list(
                active_connections.get(contact_id, [])
            )

            recipient_data = dict(response_data)
            recipient_data["isMe"] = "false"

            for connection in recipient_connections:
                try:
                    await connection.send_text(
                        json.dumps(recipient_data)
                    )
                except Exception as exc:
                    print(f"Recipient WebSocket delivery failed: {exc}")

            # Confirm to sender.
            await websocket.send_text(
                json.dumps(response_data)
            )

    except WebSocketDisconnect:
        print(f"User {user_id} disconnected from chat.")

    except Exception as exc:
        print(f"WebSocket error for user {user_id}: {exc}")

    finally:
        await cleanup_websocket(user_id, websocket)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "Gate App API",
    }
