import jwt
from django.conf import settings


class AuthenticationError(Exception):
    pass


def authenticate_request(request) -> dict:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthenticationError("Access token missing")

    secret = settings.JWT_ACCESS_SECRET
    if not secret:
        raise AuthenticationError("JWT authentication is not configured")

    token = header[7:].strip()
    if not token:
        raise AuthenticationError("Access token missing")

    try:
        payload = jwt.decode(token, secret, algorithms=settings.JWT_ALGORITHMS)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired token") from exc

    try:
        principal_id = int(payload["id"])
        username = str(payload.get("username", ""))
        role = str(payload["role"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationError("Invalid token profile") from exc

    if role == "user":
        principal_type = "user"
    elif role in {"admin", "superadmin", "moderator"}:
        principal_type = "admin"
    else:
        raise AuthenticationError("Unsupported token role")

    return {
        "id": principal_id,
        "username": username,
        "role": role,
        "principal_type": principal_type,
    }
