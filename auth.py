
from passlib.context import CryptContext
from passlib.exc import UnknownHashError, InvalidHashError


pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)


def hash_password(password: str) -> str:
    """
    Hash a user's password using bcrypt.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")

    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password exceeds bcrypt's 72-byte limit.")

    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """
    Verify a plain password against its stored bcrypt hash.
    """
    if not isinstance(plain, str) or not isinstance(hashed, str):
        return False

    if not plain or not hashed:
        return False

    if len(plain.encode("utf-8")) > 72:
        return False

    try:
        return pwd_context.verify(plain, hashed)

    except (UnknownHashError, InvalidHashError, ValueError, TypeError):
        return False
