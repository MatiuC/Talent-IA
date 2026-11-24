"""Local accounts and revocable, server-side sessions for the shared workspace.

There are no default credentials. The first account is created only from the
server itself; subsequent recruiters are created by that administrator.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from backend import database as db
from backend.schemas import email_es_valido

COOKIE_NAME = "talentia_session"
SESSION_SECONDS = 12 * 60 * 60
HASH_ROUNDS = 600_000
LOGIN_LIMIT = 8
LOGIN_WINDOW = 15 * 60
PUBLIC_PATHS = {"/api/auth/status", "/api/auth/bootstrap", "/api/auth/login"}
AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'recruiter')),
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_session_expiry ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_attempts (
    client_key TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL,
    window_start INTEGER NOT NULL
);
"""


def init_auth() -> None:
    with db.conexion() as conn:
        conn.executescript(AUTH_SCHEMA)


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, HASH_ROUNDS)
    return f"pbkdf2_sha256${HASH_ROUNDS}${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def _user(row) -> dict:
    return {key: row[key] for key in ("id", "name", "email", "role")}


def setup_required() -> bool:
    with db.conexion() as conn:
        return conn.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone() is None


def _create_user(name: str, email: str, password: str, *, bootstrap: bool) -> dict:
    encoded = _password_hash(password)
    try:
        with db.transaccion() as conn:
            if bootstrap and conn.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone():
                raise HTTPException(409, "El espacio de trabajo ya está configurado. Inicia sesión.")
            role = "admin" if bootstrap else "recruiter"
            cursor = conn.execute(
                "INSERT INTO auth_users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                (name, email, encoded, role, int(time.time())),
            )
            return {"id": cursor.lastrowid, "name": name, "email": email, "role": role}
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Ya existe una cuenta con ese correo.") from exc


def _new_session(user_id: int, old_token: str = "") -> str:
    token = secrets.token_urlsafe(48)
    now = int(time.time())
    with db.transaccion() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
        if old_token:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(old_token),))
        conn.execute(
            "INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
            (_token_hash(token), user_id, now + SESSION_SECONDS),
        )
    return token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_user(token: str) -> dict | None:
    if not token or len(token) > 200:
        return None
    with db.conexion() as conn:
        row = conn.execute(
            "SELECT u.id,u.name,u.email,u.role FROM auth_users u "
            "JOIN auth_sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>?",
            (_token_hash(token), int(time.time())),
        ).fetchone()
        return _user(row) if row else None


def _logout(token: str) -> None:
    with db.conexion() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_token_hash(token),))


def _claim_login_attempt(client_key: str) -> None:
    """Atomic database limit, shared by threads and worker processes."""
    now = int(time.time())
    with db.transaccion() as conn:
        conn.execute("DELETE FROM auth_attempts WHERE window_start <= ?", (now - LOGIN_WINDOW,))
        row = conn.execute("SELECT * FROM auth_attempts WHERE client_key=?", (client_key,)).fetchone()
        if row and row["attempts"] >= LOGIN_LIMIT:
            retry = max(1, row["window_start"] + LOGIN_WINDOW - now)
            raise HTTPException(429, "Demasiados intentos. Espera unos minutos para volver a intentar.", headers={"Retry-After": str(retry)})
        conn.execute(
            "INSERT INTO auth_attempts(client_key,attempts,window_start) VALUES(?,1,?) "
            "ON CONFLICT(client_key) DO UPDATE SET attempts=attempts+1",
            (client_key, now),
        )


def _authenticate(email: str, password: str, client: str) -> dict:
    # Limit by client and account to prevent spraying and distributed guessing.
    ip_key = "ip:" + hashlib.sha256(client.encode()).hexdigest()
    account_key = "account:" + hashlib.sha256(email.encode()).hexdigest()
    _claim_login_attempt(ip_key)
    _claim_login_attempt(account_key)
    with db.conexion() as conn:
        row = conn.execute("SELECT * FROM auth_users WHERE email=?", (email,)).fetchone()
    # A missing account still pays the same hashing cost.
    encoded = row["password_hash"] if row else f"pbkdf2_sha256${HASH_ROUNDS}${'00' * 16}${'00' * 32}"
    matches = _password_matches(password, encoded)
    if row is None or not matches:
        raise HTTPException(401, "El correo o la contraseña no son correctos.")
    with db.conexion() as conn:
        conn.execute("DELETE FROM auth_attempts WHERE client_key IN (?,?)", (ip_key, account_key))
    return _user(row)


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not email_es_valido(value):
            raise ValueError("Introduce un correo válido.")
        return value


class AccountInput(LoginInput):
    name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=10, max_length=256)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Introduce tu nombre.")
        return value


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        return parsed.scheme == request.url.scheme and parsed.netloc.lower() == request.url.netloc.lower()
    # Script/API callers can explicitly opt in. Browsers cannot send this
    # header cross-origin without a preflight, and wildcard CORS is disabled.
    return request.headers.get("x-talentia-request") == "1" and request.headers.get("sec-fetch-site") != "cross-site"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=secure, samesite="strict", path="/")


def _admin(request: Request) -> dict:
    user = request.state.user
    if user["role"] != "admin":
        raise HTTPException(403, "Solo el administrador puede gestionar el equipo.")
    return user


def install_auth(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/auth", tags=["Acceso"])

    @app.middleware("http")
    async def protect_workspace(request: Request, call_next):
        path = request.url.path.rstrip("/")
        is_api = path == "/api" or path.startswith("/api/")
        is_docs = path in {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
        if is_api and request.method not in {"GET", "HEAD", "OPTIONS"} and not _same_origin(request):
            return JSONResponse({"detail": "La solicitud debe proceder de TalentIA."}, status_code=403)
        if (is_api and path not in PUBLIC_PATHS) or is_docs:
            user = await asyncio.to_thread(session_user, request.cookies.get(COOKIE_NAME, ""))
            if not user:
                return JSONResponse({"detail": "Inicia sesión para acceder a tu espacio de trabajo."}, status_code=401,
                                    headers={"Cache-Control": "no-store"})
            request.state.user = user
        response = await call_next(request)
        if is_api or is_docs or path == "":
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith("/assets/") and path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @router.get("/status")
    async def status():
        return {"setup_required": await asyncio.to_thread(setup_required)}

    @router.post("/bootstrap", status_code=201)
    async def bootstrap(body: AccountInput, request: Request, response: Response):
        try:
            local = ipaddress.ip_address(request.client.host if request.client else "").is_loopback
        except ValueError:
            local = False
        if not local:
            raise HTTPException(403, "Crea la primera cuenta desde localhost en el equipo servidor.")
        user = await asyncio.to_thread(_create_user, body.name, body.email, body.password, bootstrap=True)
        token = await asyncio.to_thread(_new_session, user["id"], request.cookies.get(COOKIE_NAME, ""))
        _set_cookie(response, request, token)
        return {"user": user}

    @router.post("/login")
    async def login(body: LoginInput, request: Request, response: Response):
        client = request.client.host if request.client else "unknown"
        user = await asyncio.to_thread(_authenticate, body.email, body.password, client)
        token = await asyncio.to_thread(_new_session, user["id"], request.cookies.get(COOKIE_NAME, ""))
        _set_cookie(response, request, token)
        return {"user": user}

    @router.get("/me")
    async def me(request: Request):
        return {"user": request.state.user}

    @router.post("/logout")
    async def logout(request: Request, response: Response):
        await asyncio.to_thread(_logout, request.cookies.get(COOKIE_NAME, ""))
        response.delete_cookie(COOKIE_NAME, path="/", samesite="strict", httponly=True)
        return {"ok": True}

    @router.get("/users")
    async def users(request: Request):
        _admin(request)
        def list_users():
            with db.conexion() as conn:
                return [_user(row) for row in conn.execute("SELECT id,name,email,role FROM auth_users ORDER BY id")]
        return {"users": await asyncio.to_thread(list_users)}

    @router.post("/users", status_code=201)
    async def add_user(body: AccountInput, request: Request):
        _admin(request)
        user = await asyncio.to_thread(_create_user, body.name, body.email, body.password, bootstrap=False)
        return {"user": user}

    app.include_router(router)
