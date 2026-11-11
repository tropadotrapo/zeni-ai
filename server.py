#!/usr/bin/env python3
"""Servidor web, autenticação e proxy seguro para a ZENI AI."""

import hashlib
import json
import math
import os
import re
import secrets
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("ZENI_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZENI_PORT", "8000"))
DATABASE_PATH = Path(os.environ.get("ZENI_DATABASE", str(ROOT / "zeni.sqlite3"))).resolve()
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
API_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = os.environ.get("ZENI_MODEL", "openai/gpt-4o-mini")
SERVER_VERSION = "zeni-chat-1"
MAX_BODY_SIZE = 1_000_000
MAX_CONVERSATION_MESSAGES = 100
MAX_MESSAGE_LENGTH = 250_000
BRASILIA_TIME_ZONE = "America/Sao_Paulo"
WINDOWS_BRASILIA_TIME_ZONE = "E. South America Standard Time"
SESSION_COOKIE = "zeni_session"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
PASSWORD_HASH_ITERATIONS = 600_000
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", f"http://{HOST}:{PORT}/auth/google/callback"
).strip()
COOKIE_SECURE = os.environ.get("ZENI_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")
WEEKDAYS_PT_BR = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
MONTHS_PT_BR = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
BASE_SYSTEM_PROMPT = (
    "Você é a ZENI, uma assistente de inteligência artificial útil, natural e confiável. "
    "Responda sempre em português brasileiro, a menos que o usuário peça outro idioma. "
    "Seja clara, inteligente e detalhada na medida certa. Não invente fatos; "
    "sinalize incertezas e peça esclarecimentos quando necessário."
)


def brasilia_now(now_utc=None):
    current_utc = now_utc or datetime.now(timezone.utc)
    if current_utc.tzinfo is None:
        raise ValueError("O horário de referência precisa incluir um fuso horário.")

    try:
        return current_utc.astimezone(ZoneInfo(BRASILIA_TIME_ZONE))
    except ZoneInfoNotFoundError as error:
        if os.name != "nt":
            raise RuntimeError(
                f"O fuso {BRASILIA_TIME_ZONE} não está disponível nesta instalação do Python."
            ) from error

        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
            ) as registry_key:
                windows_time_zone = winreg.QueryValueEx(registry_key, "TimeZoneKeyName")[0]
        except (ImportError, OSError) as registry_error:
            raise RuntimeError(
                f"Não foi possível carregar o fuso {BRASILIA_TIME_ZONE} nem confirmar o fuso do Windows."
            ) from registry_error

        if windows_time_zone != WINDOWS_BRASILIA_TIME_ZONE:
            raise RuntimeError(
                f"Dados IANA do fuso {BRASILIA_TIME_ZONE} indisponíveis e o fuso do Windows "
                f"é {windows_time_zone!r}, não Brasília."
            ) from error
        return current_utc.astimezone()


def build_system_prompt(now=None):
    current_time = now or brasilia_now()
    date_text = (
        f"{WEEKDAYS_PT_BR[current_time.weekday()]}, "
        f"{current_time.day} de {MONTHS_PT_BR[current_time.month - 1]} de {current_time.year}"
    )
    offset = current_time.strftime("%z")
    offset_text = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    return (
        f"{BASE_SYSTEM_PROMPT}\n\n"
        f"Data atual em Brasília: {date_text}.\n"
        f"Horário local atual: {current_time.strftime('%H:%M:%S')} "
        f"({BRASILIA_TIME_ZONE}, {offset_text}).\n"
        f"Timestamp local: {current_time.isoformat(timespec='seconds')}.\n"
        "Use esta data, ano e horário como fonte correta para perguntas sobre hoje ou agora. "
        "Não use a data de treinamento do modelo para responder perguntas de data e hora."
    )


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def plan_catalog():
    return {
        "free": {
            "name": "Gratuito",
            "price": "R$ 0,00",
            "price_period": "para sempre",
            "description": "Para conhecer e usar a ZENI no dia a dia.",
            "features": [
                "Até 100 mensagens por conversa",
                "Acesso ao modelo de IA padrão",
                "Histórico de conversas",
                "Recursos básicos da ZENI",
            ],
        },
        "plus": {
            "name": "Plus",
            "price": "R$ 15,99",
            "price_period": "por mês",
            "description": "Mais recursos para suas ideias avançarem.",
            "features": [
                "Até 100 mensagens por conversa",
                "Acesso a modelos de IA mais avançados",
                "Respostas mais rápidas, quando disponíveis",
                "Histórico e contexto ampliados",
                "Recursos adicionais da ZENI",
            ],
        },
        "pro": {
            "name": "Pro",
            "price": "R$ 29,99",
            "price_period": "por mês",
            "description": "Potência e prioridade para seus projetos.",
            "features": [
                "Conversas ilimitadas no histórico",
                "Acesso aos modelos mais avançados disponíveis",
                "Prioridade de processamento, quando disponível",
                "Histórico e contexto ampliados",
                "Acesso aos recursos avançados da ZENI",
            ],
        },
    }


def connect_database():
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


@contextmanager
def database_connection():
    connection = connect_database()
    try:
        yield connection
        if connection.in_transaction:
            connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with database_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                google_sub TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL,
                name TEXT NOT NULL,
                picture TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_expiration ON sessions(expires_at);
            CREATE TABLE IF NOT EXISTS local_credentials (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                email_normalized TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                nonce TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_plans (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                plan TEXT NOT NULL DEFAULT 'free',
                status TEXT NOT NULL DEFAULT 'active',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS conversations_by_user
                ON conversations(user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_by_conversation
                ON messages(conversation_id, id);
            CREATE TABLE IF NOT EXISTS subscription_requests (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan TEXT NOT NULL CHECK (plan IN ('monthly', 'annual')),
                price TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plan_requests (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan TEXT NOT NULL CHECK (plan IN ('plus', 'pro')),
                price TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'cancelled')),
                created_at TEXT NOT NULL
            );

            """
        )
        connection.execute(
            "UPDATE user_plans SET plan = 'plus' WHERE plan = 'monthly'"
        )
        connection.execute(
            "UPDATE user_plans SET plan = 'pro' WHERE plan = 'annual'"
        )


def session_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email):
    normalized = email.strip().casefold()
    if len(normalized) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise ValueError("Informe um endereço de e-mail válido.")
    return normalized


def hash_password(password, salt=None):
    salt_bytes = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, PASSWORD_HASH_ITERATIONS
    )
    return salt_bytes.hex(), password_hash.hex()


def validate_password(password):
    if not isinstance(password, str):
        raise ValueError("Informe uma senha.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"A senha deve ter pelo menos {PASSWORD_MIN_LENGTH} caracteres.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"A senha deve ter no máximo {PASSWORD_MAX_LENGTH} caracteres.")


def account_payload(user):
    catalog = plan_catalog()
    with database_connection() as connection:
        plan_row = connection.execute(
            "SELECT plan, status FROM user_plans WHERE user_id = ?",
            (user["id"],),
        ).fetchone()
        credential_row = connection.execute(
            "SELECT 1 FROM local_credentials WHERE user_id = ?", (user["id"],)
        ).fetchone()
    active_plan = plan_row["plan"] if plan_row else "free"
    active_plan = {"monthly": "plus", "annual": "pro"}.get(active_plan, active_plan)
    if active_plan not in catalog:
        active_plan = "free"
    return {
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "picture": user["picture"],
        },
        "plan": {
            "id": active_plan,
            "name": (
                "ZENI Free"
                if active_plan == "free"
                else f"ZENI {catalog[active_plan]['name']}"
            ),
            "status": plan_row["status"] if plan_row else "active",
        },
        "plans": catalog,
        "has_password": credential_row is not None,
        "auth_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
    }


def redact_api_key(message, api_key):
    return message.replace(api_key, "[redacted]") if api_key else message


class ZeniHandler(BaseHTTPRequestHandler):
    server_version = "ZENI/1.0"

    def log_message(self, fmt, *args):
        print(f"[ZENI] {self.address_string()} - {fmt % args}")

    def send_json(self, status, value, cookie_header=None):
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-ZENI-Server-Version", SERVER_VERSION)
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location, cookie_header=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-ZENI-Server-Version", SERVER_VERSION)
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.end_headers()

    def set_session_cookie(self, token, max_age=SESSION_TTL_SECONDS):
        secure = "; Secure" if COOKIE_SECURE else ""
        return (
            f"{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; "
            f"SameSite=Lax{secure}"
        )

    def get_session_user(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except (CookieError, ValueError):
            return None
        cookie = cookies.get(SESSION_COOKIE)
        if cookie is None or not cookie.value:
            return None
        token_digest = session_hash(cookie.value)
        now = datetime.now(timezone.utc).timestamp()
        with database_connection() as connection:
            row = connection.execute(
                """
                SELECT users.id, users.name, users.email, users.picture
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (token_digest, now),
            ).fetchone()
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return row

    def require_user(self):
        user = self.get_session_user()
        if user is None:
            self.send_json(401, {"error": "Entre na sua conta para continuar."})
            return None
        return user

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/auth/google/start":
            self.start_google_login()
            return
        if path == "/auth/google/callback":
            self.finish_google_login(parsed.query)
            return
        if path in ("/login", "/login.html"):
            self.serve_file("login.html", "text/html; charset=utf-8")
            return
        if path in ("/api/status", "/api/version"):
            self.send_json(
                200,
                {
                    "provider": "OpenRouter",
                    "version": SERVER_VERSION,
                    "base_url": API_BASE_URL,
                    "configured": bool(API_KEY),
                    "model": MODEL,
                    "auth_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
                    "local_auth_configured": True,
                },
            )
            return
        if path == "/api/me":
            user = self.get_session_user()
            if user is None:
                try:
                    plans = plan_catalog()
                except RuntimeError as error:
                    self.send_json(500, {"error": str(error)})
                    return
                self.send_json(
                    200,
                    {
                        "authenticated": False,
                        "auth_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
                        "local_auth_configured": True,
                        "plans": plans,
                    },
                )
                return
            try:
                self.send_json(200, {"authenticated": True, **account_payload(user)})
            except RuntimeError as error:
                self.send_json(500, {"error": str(error)})
            return
        if path == "/api/conversations":
            user = self.require_user()
            if user is None:
                return
            self.send_json(200, {"conversations": self.load_conversations(user["id"])})
            return
        if path == "/":
            self.serve_file("index.html", "text/html; charset=utf-8")
            return
        if path in ("/plans", "/plans.html"):
            self.serve_file("plans.html", "text/html; charset=utf-8")
            return
        if path in ("/styles.css", "/app.js", "/plans.js", "/auth.js"):
            if path.endswith(".css"):
                mime_type = "text/css; charset=utf-8"
            else:
                mime_type = "text/javascript; charset=utf-8"
            self.serve_file(path.lstrip("/"), mime_type)
            return
        self.send_json(404, {"error": "Página não encontrada."})

    def serve_file(self, name, content_type):
        path = (ROOT / name).resolve()
        if path.parent != ROOT or not path.is_file():
            self.send_json(404, {"error": "Arquivo não encontrado."})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-ZENI-Server-Version", SERVER_VERSION)
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        user = self.require_user()
        if user is None:
            return
        path = urlparse(self.path).path
        prefix = "/api/conversations/"
        if not path.startswith(prefix):
            self.send_json(404, {"error": "Página não encontrada."})
            return
        conversation_id = path[len(prefix):]
        try:
            conversation_id = str(uuid.UUID(conversation_id))
        except (ValueError, AttributeError):
            self.send_json(400, {"error": "Identificador de conversa inválido."})
            return
        with database_connection() as connection:
            result = connection.execute(
                "DELETE FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user["id"]),
            )
        if result.rowcount == 0:
            self.send_json(404, {"error": "Conversa não encontrada."})
            return
        self.send_json(200, {"deleted": True})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/auth/logout":
            self.logout()
            return
        if path not in (
            "/auth/login",
            "/auth/register",
            "/auth/password",
            "/api/chat",
        ):
            self.send_json(404, {"error": "Página não encontrada."})
            return
        payload = self.read_json_body()
        if payload is None:
            return
        if path == "/auth/login":
            self.login_with_password(payload)
            return
        if path == "/auth/register":
            self.register_with_password(payload)
            return
        if path == "/auth/password":
            user = self.require_user()
            if user is not None:
                self.set_account_password(user, payload)
            return
        if path == "/api/chat":
            user = self.get_session_user()
            if user is None:
                self.chat_guest(payload)
            else:
                self.chat(user, payload)
            return
        user = self.require_user()
        if user is None:
            return

    def create_session(self, user_id):
        token = secrets.token_urlsafe(48)
        expires_at = datetime.now(timezone.utc).timestamp() + SESSION_TTL_SECONDS
        with database_connection() as connection:
            connection.execute(
                "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (session_hash(token), user_id, expires_at),
            )
        return token

    def login_with_password(self, payload):
        try:
            email = normalize_email(payload.get("email", ""))
            password = payload.get("password")
            if not isinstance(password, str) or not password or len(password) > PASSWORD_MAX_LENGTH:
                raise ValueError("E-mail ou senha inválidos.")
        except (AttributeError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
            return

        with database_connection() as connection:
            row = connection.execute(
                """
                SELECT users.id, users.name, users.email, users.picture,
                       local_credentials.password_salt, local_credentials.password_hash
                FROM local_credentials
                JOIN users ON users.id = local_credentials.user_id
                WHERE local_credentials.email_normalized = ?
                """,
                (email,),
            ).fetchone()
        if row is None:
            hash_password(password, bytes(16))
            self.send_json(401, {"error": "E-mail ou senha inválidos."})
            return

        _, candidate_hash = hash_password(password, bytes.fromhex(row["password_salt"]))
        if not secrets.compare_digest(candidate_hash, row["password_hash"]):
            self.send_json(401, {"error": "E-mail ou senha inválidos."})
            return
        token = self.create_session(row["id"])
        self.send_json(
            200,
            {
                "authenticated": True,
                "user": {
                    "id": row["id"],
                    "name": row["name"],
                    "email": row["email"],
                    "picture": row["picture"],
                },
            },
            self.set_session_cookie(token),
        )

    def register_with_password(self, payload):
        try:
            email = normalize_email(payload.get("email", ""))
            name = payload.get("name", "").strip()
            password = payload.get("password")
            validate_password(password)
            if not name or len(name) > 80:
                raise ValueError("Informe um nome com até 80 caracteres.")
        except (AttributeError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
            return

        with database_connection() as connection:
            existing_user = connection.execute(
                "SELECT email FROM users WHERE lower(email) = ?", (email,)
            ).fetchone()
            if existing_user is not None:
                self.send_json(
                    409,
                    {
                        "error": (
                            "Este e-mail já está associado a uma conta. "
                            "Entre nela para definir ou usar uma senha."
                        )
                    },
                )
                return

        user_id = str(uuid.uuid4())
        now = utc_timestamp()
        password_salt, password_digest = hash_password(password)
        try:
            with database_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO users(id, google_sub, email, name, picture, created_at)
                    VALUES (?, ?, ?, ?, '', ?)
                    """,
                    (user_id, f"local:{user_id}", email, name, now),
                )
                connection.execute(
                    """
                    INSERT INTO local_credentials(
                        user_id, email_normalized, password_salt, password_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, email, password_salt, password_digest, now),
                )
                connection.execute(
                    """
                    INSERT INTO user_plans(user_id, plan, status, updated_at)
                    VALUES (?, 'free', 'active', ?)
                    """,
                    (user_id, now),
                )
            token = self.create_session(user_id)
        except sqlite3.IntegrityError:
            self.send_json(409, {"error": "Já existe uma conta com este e-mail."})
            return
        self.send_json(
            201,
            {"authenticated": True, "user": {"id": user_id, "name": name, "email": email}},
            self.set_session_cookie(token),
        )

    def set_account_password(self, user, payload):
        try:
            password = payload.get("password")
            validate_password(password)
            email = normalize_email(user["email"])
        except (AttributeError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
            return

        password_salt, password_digest = hash_password(password)
        try:
            with database_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO local_credentials(
                        user_id, email_normalized, password_salt, password_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (user["id"], email, password_salt, password_digest, utc_timestamp()),
                )
        except sqlite3.IntegrityError:
            self.send_json(
                409,
                {"error": "Este e-mail já está vinculado a outro acesso por senha."},
            )
            return
        self.send_json(200, {"password_configured": True})

    def read_json_body(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Tamanho da requisição inválido."})
            return None
        if content_length < 1 or content_length > MAX_BODY_SIZE:
            self.send_json(413, {"error": "A mensagem está vazia ou excede o limite de 1 MB."})
            return None
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "O corpo da requisição precisa ser um JSON válido."})
            return None
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "O corpo da requisição precisa ser um objeto JSON."})
            return None
        return payload

    def start_google_login(self):
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            self.redirect("/?auth_error=google_not_configured")
            return
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).timestamp()
        try:
            with database_connection() as connection:
                connection.execute("DELETE FROM oauth_states WHERE created_at < ?", (now - 600,))
                connection.execute(
                    "INSERT INTO oauth_states(state, nonce, created_at) VALUES (?, ?, ?)",
                    (state, nonce, now),
                )
        except sqlite3.Error as error:
            print(f"[ZENI] Falha ao preparar o login Google: {error}")
            self.redirect("/?auth_error=google_start_failed")
            return
        query = urllib.parse.urlencode(
            {
                "client_id": GOOGLE_CLIENT_ID,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "prompt": "select_account",
            }
        )
        self.redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")

    def finish_google_login(self, query_string):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        if params.get("error") or not state or not code:
            self.redirect("/?auth_error=google_login_cancelled")
            return
        now = datetime.now(timezone.utc).timestamp()
        with database_connection() as connection:
            state_row = connection.execute(
                "SELECT nonce, created_at FROM oauth_states WHERE state = ?", (state,)
            ).fetchone()
            connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        if state_row is None or now - state_row["created_at"] > 600:
            self.redirect("/?auth_error=oauth_state_invalid")
            return
        try:
            token_request = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=urllib.parse.urlencode(
                    {
                        "code": code,
                        "client_id": GOOGLE_CLIENT_ID,
                        "client_secret": GOOGLE_CLIENT_SECRET,
                        "redirect_uri": GOOGLE_REDIRECT_URI,
                        "grant_type": "authorization_code",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(token_request, timeout=20) as response:
                token_data = json.loads(response.read())
            id_token = token_data.get("id_token")
            if not isinstance(id_token, str) or not id_token:
                raise ValueError("O Google não retornou um token de identidade.")
            tokeninfo_url = (
                "https://oauth2.googleapis.com/tokeninfo?"
                + urllib.parse.urlencode({"id_token": id_token})
            )
            with urllib.request.urlopen(tokeninfo_url, timeout=20) as response:
                identity = json.loads(response.read())
            self.validate_google_identity(identity, state_row["nonce"])
            user_id = self.upsert_google_user(identity)
            token = secrets.token_urlsafe(48)
            expires_at = now + SESSION_TTL_SECONDS
            with database_connection() as connection:
                connection.execute(
                    "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                    (session_hash(token), user_id, expires_at),
                )
            self.redirect("/", self.set_session_cookie(token))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
            print(f"[ZENI] Falha na autenticação Google: {error}")
            self.redirect("/?auth_error=google_auth_failed")
        except sqlite3.Error as error:
            print(f"[ZENI] Falha ao salvar sessão de usuário: {error}")
            self.redirect("/?auth_error=account_storage_failed")

    def validate_google_identity(self, identity, expected_nonce):
        issuer = identity.get("iss")
        if issuer not in ("accounts.google.com", "https://accounts.google.com"):
            raise ValueError("Emissor do token Google inválido.")
        if identity.get("aud") != GOOGLE_CLIENT_ID:
            raise ValueError("O token Google foi emitido para outro cliente.")
        if identity.get("nonce") != expected_nonce:
            raise ValueError("Nonce do token Google inválido.")
        try:
            expiration = float(identity.get("exp", "0"))
        except (TypeError, ValueError) as error:
            raise ValueError("A validade do token Google é inválida.") from error
        if not math.isfinite(expiration) or expiration <= datetime.now(timezone.utc).timestamp():
            raise ValueError("O token Google expirou.")
        email_verified = identity.get("email_verified")
        if email_verified is not True and email_verified != "true":
            raise ValueError("O e-mail da conta Google não foi verificado.")
        if not identity.get("sub") or not identity.get("email"):
            raise ValueError("O Google não retornou a identidade necessária.")

    def upsert_google_user(self, identity):
        now = utc_timestamp()
        user_id = str(uuid.uuid4())
        with database_connection() as connection:
            connection.execute(
                """
                INSERT INTO users(id, google_sub, email, name, picture, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(google_sub) DO UPDATE SET
                    email = excluded.email, name = excluded.name, picture = excluded.picture
                """,
                (
                    user_id,
                    identity["sub"],
                    identity["email"],
                    identity.get("name") or identity["email"],
                    identity.get("picture") or "",
                    now,
                ),
            )
            user = connection.execute(
                "SELECT id FROM users WHERE google_sub = ?", (identity["sub"],)
            ).fetchone()
            connection.execute(
                """
                INSERT OR IGNORE INTO user_plans(user_id, plan, status, updated_at)
                VALUES (?, 'free', 'active', ?)
                """,
                (user["id"], now),
            )
        return user["id"]

    def logout(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            cookies = SimpleCookie()
        cookie = cookies.get(SESSION_COOKIE)
        if cookie is not None and cookie.value:
            with database_connection() as connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (session_hash(cookie.value),)
                )
        self.redirect("/", self.set_session_cookie("", max_age=0))

    def load_conversations(self, user_id):
        with database_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM conversations WHERE user_id = ?
                ORDER BY updated_at DESC
                """,
                (user_id,),
            ).fetchall()
            result = []
            for row in rows:
                messages = connection.execute(
                    """
                    SELECT role, content, created_at
                    FROM messages WHERE conversation_id = ? AND user_id = ?
                    ORDER BY id
                    """,
                    (row["id"], user_id),
                ).fetchall()
                result.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "createdAt": row["created_at"],
                        "updatedAt": row["updated_at"],
                        "messages": [
                            {"role": message["role"], "content": message["content"]}
                            for message in messages
                        ],
                    }
                )
        return result

    def chat_guest(self, payload):
        if not API_KEY:
            self.send_json(
                503,
                {
                    "error": (
                        "A IA não está configurada. Defina OPENROUTER_API_KEY no ambiente "
                        "do servidor e reinicie a aplicação."
                    )
                },
            )
            return
        content = payload.get("message")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_MESSAGE_LENGTH:
            self.send_json(400, {"error": "A mensagem está vazia ou excede o limite de 250 KB."})
            return
        history = payload.get("history", [])
        if not isinstance(history, list):
            self.send_json(400, {"error": "O histórico da conversa é inválido."})
            return
        if len(history) > MAX_CONVERSATION_MESSAGES - 2:
            self.send_json(
                409,
                {
                    "error": "Esta conversa chegou a 100 mensagens. Uma nova conversa será iniciada.",
                    "conversation_full": True,
                },
            )
            return
        for message in history:
            if (
                not isinstance(message, dict)
                or message.get("role") not in ("user", "assistant")
                or not isinstance(message.get("content"), str)
                or len(message["content"]) > MAX_MESSAGE_LENGTH
            ):
                self.send_json(400, {"error": "O histórico da conversa contém uma mensagem inválida."})
                return

        try:
            system_prompt = build_system_prompt()
        except (RuntimeError, ValueError) as error:
            self.send_json(
                500,
                {"error": f"Não foi possível determinar a configuração da conversa: {error}"},
            )
            return

        normalized = [{"role": "system", "content": system_prompt}]
        normalized.extend(
            {"role": message["role"], "content": message["content"]}
            for message in history
        )
        normalized.append({"role": "user", "content": content.strip()})
        try:
            reply = request_openrouter(normalized)
        except OpenRouterError as error:
            self.send_json(error.status, {"error": str(error)})
            return
        self.send_json(
            200,
            {
                "reply": reply,
                "model": MODEL,
                "title": make_title(content),
            },
        )

    def chat(self, user, payload):
        if not API_KEY:
            self.send_json(
                503,
                {
                    "error": (
                        "A IA não está configurada. Defina OPENROUTER_API_KEY no ambiente "
                        "do servidor e reinicie a aplicação."
                    )
                },
            )
            return
        content = payload.get("message")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_MESSAGE_LENGTH:
            self.send_json(
                400,
                {"error": "A mensagem está vazia ou excede o limite de 250 KB."},
            )
            return
        conversation_id = payload.get("conversation_id")
        if conversation_id:
            try:
                conversation_id = str(uuid.UUID(conversation_id))
            except (ValueError, AttributeError, TypeError):
                self.send_json(400, {"error": "Identificador de conversa inválido."})
                return
        try:
            system_prompt = build_system_prompt()
        except (RuntimeError, ValueError) as error:
            self.send_json(
                500,
                {"error": f"Não foi possível determinar a configuração da conversa: {error}"},
            )
            return

        now = utc_timestamp()
        try:
            with database_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if conversation_id:
                    conversation = connection.execute(
                        """
                        SELECT id, title FROM conversations
                        WHERE id = ? AND user_id = ?
                        """,
                        (conversation_id, user["id"]),
                    ).fetchone()
                    if conversation is None:
                        connection.rollback()
                        self.send_json(404, {"error": "Conversa não encontrada nesta conta."})
                        return
                    existing = connection.execute(
                        """
                        SELECT role, content FROM messages
                        WHERE conversation_id = ? AND user_id = ?
                        ORDER BY id
                        """,
                        (conversation_id, user["id"]),
                    ).fetchall()
                    title = conversation["title"]
                else:
                    existing = []
                    title = make_title(content)
                    conversation_id = str(uuid.uuid4())
                if len(existing) >= MAX_CONVERSATION_MESSAGES - 1:
                    connection.rollback()
                    self.send_json(
                        409,
                        {
                            "error": "Esta conversa chegou a 100 mensagens. Uma nova conversa será iniciada.",
                            "conversation_full": True,
                        },
                    )
                    return
                if not existing:
                    connection.execute(
                        """
                        INSERT INTO conversations(id, user_id, title, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (conversation_id, user["id"], title, now, now),
                    )
                connection.execute(
                    """
                    INSERT INTO messages(conversation_id, user_id, role, content, created_at)
                    VALUES (?, ?, 'user', ?, ?)
                    """,
                    (conversation_id, user["id"], content.strip(), now),
                )
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
                    (now, conversation_id, user["id"]),
                )
                normalized = [{"role": "system", "content": system_prompt}]
                normalized.extend(
                    {"role": message["role"], "content": message["content"]}
                    for message in existing[-(MAX_CONVERSATION_MESSAGES - 2):]
                )
                normalized.append({"role": "user", "content": content.strip()})
                connection.commit()
        except sqlite3.Error as error:
            print(f"[ZENI] Falha ao salvar mensagem: {error}")
            self.send_json(500, {"error": "Não foi possível salvar a mensagem da conta."})
            return

        try:
            reply = request_openrouter(normalized)
        except OpenRouterError as error:
            self.send_json(
                error.status,
                {
                    "error": str(error),
                    "conversation_id": conversation_id,
                    "title": title,
                },
            )
            return
        try:
            with database_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO messages(conversation_id, user_id, role, content, created_at)
                    VALUES (?, ?, 'assistant', ?, ?)
                    """,
                    (conversation_id, user["id"], reply, utc_timestamp()),
                )
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
                    (utc_timestamp(), conversation_id, user["id"]),
                )
        except sqlite3.Error as error:
            print(f"[ZENI] Resposta recebida, mas falhou ao persistir: {error}")
            self.send_json(
                500,
                {
                    "error": "A resposta foi recebida, mas não foi possível salvá-la no histórico.",
                    "conversation_id": conversation_id,
                },
            )
            return
        self.send_json(
            200,
            {
                "reply": reply,
                "model": MODEL,
                "conversation_id": conversation_id,
                "title": title,
            },
        )


class OpenRouterError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def request_openrouter(messages):
    request_payload = json_bytes(
        {"model": MODEL, "messages": messages, "temperature": 0.7}
    )
    request = urllib.request.Request(
        f"{API_BASE_URL}/chat/completions",
        data=request_payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": f"http://{HOST}:{PORT}",
            "X-Title": "ZENI AI",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read())
        reply = result["choices"][0]["message"]["content"]
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("O provedor retornou uma resposta vazia.")
        return reply
    except urllib.error.HTTPError as error:
        detail = ""
        response_body = b""
        try:
            response_body = error.read()
            provider_error = json.loads(response_body).get("error", {})
            if isinstance(provider_error, dict):
                detail = provider_error.get("message", "")
                error_code = provider_error.get("code")
                if error_code and str(error_code) not in detail:
                    detail = (
                        f"{detail} (provider code {error_code})"
                        if detail
                        else f"provider code {error_code}"
                    )
            elif isinstance(provider_error, str):
                detail = provider_error
        except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
            detail = response_body.decode("utf-8", "replace").strip()
        detail = redact_api_key(str(detail), API_KEY)
        request_id = (error.headers or {}).get("X-Request-Id")
        request_reference = f" (request id: {request_id})" if request_id else ""
        message = (
            f"OpenRouter HTTP {error.code}: {detail[:1000]}"
            if detail
            else f"OpenRouter HTTP {error.code}"
        )
        print(f"[ZENI] {message}{request_reference}")
        raise OpenRouterError(502, f"{message}{request_reference}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        detail = redact_api_key(str(reason), API_KEY)
        message = f"Falha de conexão com OpenRouter: {detail}"
        print(f"[ZENI] {message}")
        raise OpenRouterError(502, message) from error
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as error:
        message = f"Resposta inválida do OpenRouter: {redact_api_key(str(error), API_KEY)}"
        print(f"[ZENI] {message}")
        raise OpenRouterError(502, message) from error


def make_title(text):
    normalized = " ".join(text.split())
    return (normalized[:36].rstrip() + "…") if len(normalized) > 36 else normalized or "Nova conversa"


class ThreadedServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


if __name__ == "__main__":
    initialize_database()
    if not secrets.compare_digest(HOST, "127.0.0.1") and HOST not in ("localhost", "::1"):
        print("[ZENI] Atenção: o servidor está acessível na rede. Proteja-o com HTTPS antes de expô-lo.")
    try:
        server = ThreadedServer((HOST, PORT), ZeniHandler)
    except OSError as error:
        if getattr(error, "winerror", None) == 10048 or error.errno in (48, 98, 10048):
            raise SystemExit(
                f"A porta {PORT} já está ocupada. Há outro servidor usando este endereço; "
                "confirme e encerre a instância antiga da ZENI antes de iniciar uma nova."
            ) from error
        raise
    print(f"ZENI AI {SERVER_VERSION} disponível em http://{HOST}:{PORT}")
    print(f"OpenRouter API key configurada: {'sim' if API_KEY else 'não'}")
    print(f"Google OAuth configurado: {'sim' if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET else 'não'}")
    print(f"OpenRouter: modelo {MODEL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando ZENI AI...")
    finally:
        server.server_close()
