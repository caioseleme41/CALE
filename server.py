from __future__ import annotations

import hashlib
import hmac
import base64
import binascii
import json
import mimetypes
import os
import re
import secrets
import smtplib
import ssl
import traceback
import uuid
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import mysql.connector
    from mysql.connector import IntegrityError
except ImportError as exc:
    raise SystemExit("Dependência ausente. Instale com: pip install -r requirements.txt") from exc


ROOT = Path(__file__).resolve().parent
SESSION_COOKIE = "siap_session"
SESSION_DAYS = 14
PASSWORD_ITERATIONS = 310_000
MAX_BODY_SIZE = 1_000_000
COMMON_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1",
    "password123", "qwerty123", "qwertyuiop", "admin123", "welcome123",
    "senha123", "abc12345", "letmein123", "iloveyou", "11111111",
    "00000000", "changeme",
}


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
DB_HOST = os.getenv("SIAP_DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("SIAP_DB_PORT", "3306"))
DB_USER = os.getenv("SIAP_DB_USER", "siap_app")
DB_PASSWORD = os.getenv("SIAP_DB_PASSWORD", "")
DB_NAME = os.getenv("SIAP_DB_NAME", "siap_db")
APP_HOST = os.getenv("SIAP_APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("SIAP_APP_PORT", "8000"))
SMTP_HOST = os.getenv("SIAP_SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SIAP_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SIAP_SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SIAP_SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SIAP_SMTP_FROM", "").strip() or SMTP_USERNAME
SMTP_USE_SSL = os.getenv("SIAP_SMTP_USE_SSL", "false").strip().lower() == "true"
SMTP_STARTTLS = os.getenv("SIAP_SMTP_STARTTLS", "true").strip().lower() == "true"
EMAIL_CODE_TTL_MINUTES = 15
EMAIL_CODE_RESEND_SECONDS = 60
EMAIL_CODE_MAX_ATTEMPTS = 5

if not re.fullmatch(r"[A-Za-z0-9_]+", DB_NAME):
    raise SystemExit("SIAP_DB_NAME aceita apenas letras, números e underscore.")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def db_connect(include_database: bool = True):
    config = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "connection_timeout": 5,
    }
    if include_database:
        config["database"] = DB_NAME
    return mysql.connector.connect(**config)


def initialize_database() -> None:
    connection = db_connect()
    try:
        cursor = connection.cursor()
        cursor.execute("SHOW TABLES")
        existing = {row[0] for row in cursor.fetchall()}
        missing = {"users", "tasks", "sessions"} - existing
        if missing:
            raise SystemExit(
                "O banco está acessível, mas falta o esquema do CALÊ. Execute schema.sql "
                "no MySQL antes de iniciar o servidor."
            )
        cursor.execute("SHOW COLUMNS FROM users")
        user_columns = {row[0] for row in cursor.fetchall()}
        missing_profile = {
            "phone",
            "sms_alerts",
            "avatar_data",
            "email_verified",
            "verification_code_salt",
            "verification_code_hash",
            "verification_expires_at",
            "verification_sent_at",
            "verification_attempts",
        } - user_columns
        if missing_profile:
            raise SystemExit(
                "O banco do CALÊ ainda não tem os campos de perfil. Execute "
                "migration_email_verification.sql com uma conta administradora do MySQL."
            )
    finally:
        connection.close()


def password_digest(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PASSWORD_ITERATIONS
    ).hex()


def is_common_password(password: str) -> bool:
    return (
        password.strip().casefold() in COMMON_PASSWORDS
        or (len(password) >= 8 and len(set(password)) == 1)
    )


def public_user(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "email": row["email"],
        "phone": row.get("phone") or "",
        "smsAlerts": bool(row.get("sms_alerts", False)),
        "avatar": row.get("avatar_data"),
        "hasPassword": bool(row.get("password_hash")),
    }


def normalize_avatar(data: object) -> str | None:
    if data is None:
        return None
    if not isinstance(data, str) or len(data) > 700_000:
        raise ApiError(400, "A foto precisa ter no máximo 512 KB depois da compactação.")
    match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/]+={0,2})", data)
    if not match:
        raise ApiError(400, "Envie uma foto JPG, PNG ou WebP válida.")
    try:
        contents = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ApiError(400, "O arquivo da foto está inválido.") from exc
    if not contents or len(contents) > 512 * 1024:
        raise ApiError(400, "A foto precisa ter no máximo 512 KB depois da compactação.")
    image_type = match.group(1)
    signatures = {
        "jpeg": contents.startswith(b"\xff\xd8\xff"),
        "png": contents.startswith(b"\x89PNG\r\n\x1a\n"),
        "webp": contents.startswith(b"RIFF") and contents[8:12] == b"WEBP",
    }
    if not signatures[image_type]:
        raise ApiError(400, "O conteúdo não corresponde ao formato de imagem informado.")
    return data


def normalize_phone(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    phone = re.sub(r"[\s().-]", "", raw)
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise ApiError(400, "Informe o telefone com código do país, por exemplo +55 31 99999-9999.")
    return phone


def smtp_is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM and SMTP_PORT and (bool(SMTP_USERNAME) == bool(SMTP_PASSWORD)))


def make_email_code() -> tuple[str, str, str, datetime]:
    code = f"{secrets.randbelow(100_000_000):08d}"
    salt = secrets.token_bytes(16).hex()
    digest = password_digest(code, salt)
    expires_at = datetime.utcnow() + timedelta(minutes=EMAIL_CODE_TTL_MINUTES)
    return code, salt, digest, expires_at


def send_verification_email(email: str, name: str, code: str) -> None:
    if not smtp_is_configured():
        raise ApiError(
            503,
            "A confirmação por e-mail ainda não foi configurada. Defina as variáveis SMTP no arquivo .env.",
        )
    message = EmailMessage()
    message["Subject"] = "Confirme seu e-mail no CALÊ"
    message["From"] = SMTP_FROM
    message["To"] = email
    message.set_content(
        f"Olá, {name}!\n\nSeu código para confirmar o e-mail no CALÊ é: {code}\n\n"
        f"Ele expira em {EMAIL_CODE_TTL_MINUTES} minutos. Se você não pediu esse cadastro, ignore esta mensagem."
    )
    try:
        smtp_class = smtplib.SMTP_SSL if SMTP_USE_SSL else smtplib.SMTP
        with smtp_class(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            if SMTP_USE_SSL:
                smtp.ehlo()
            elif SMTP_STARTTLS:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if SMTP_USERNAME:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        print(f"Falha ao enviar confirmação de e-mail: {type(exc).__name__}")
        raise ApiError(503, "Não foi possível enviar o código agora. Confira a configuração de e-mail e tente novamente.") from exc


def serialize_task(row: dict) -> dict:
    task_date = row["task_date"]
    task_time = row["task_time"]
    if isinstance(task_time, time):
        time_text = task_time.strftime("%H:%M")
    elif isinstance(task_time, timedelta):
        total_minutes = int(task_time.total_seconds() // 60)
        time_text = f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"
    else:
        time_text = ""
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "date": task_date.isoformat() if isinstance(task_date, date) else str(task_date),
        "time": time_text,
        "category": row["category"],
        "done": bool(row["done"]),
    }


def normalize_task(data: dict, task_id: str | None = None) -> dict:
    title = str(data.get("title", "")).strip()
    if not title:
        raise ApiError(400, "Informe o título do compromisso.")
    if len(title) > 255:
        raise ApiError(400, "O título pode ter no máximo 255 caracteres.")
    try:
        task_date = date.fromisoformat(str(data.get("date", "")))
    except ValueError as exc:
        raise ApiError(400, "Informe uma data válida.") from exc

    raw_time = str(data.get("time") or "").strip()
    try:
        task_time = datetime.strptime(raw_time, "%H:%M").time() if raw_time else None
    except ValueError as exc:
        raise ApiError(400, "Informe um horário válido.") from exc

    category = str(data.get("category") or "Pessoal").strip()[:80]
    identifier = str(task_id or data.get("id") or uuid.uuid4())
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
        raise ApiError(400, "Identificador de compromisso inválido.")
    return {
        "id": identifier,
        "title": title,
        "task_date": task_date,
        "task_time": task_time,
        "category": category or "Pessoal",
        "done": 1 if bool(data.get("done", False)) else 0,
    }


class SIAPHandler(BaseHTTPRequestHandler):
    server_version = "CALEServer/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, status: int, payload: dict, headers: tuple[tuple[str, str], ...] = ()) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def run_api(self, action, *args) -> None:
        try:
            action(*args)
        except ApiError as exc:
            self.send_json(exc.status, {"error": exc.message})
        except mysql.connector.Error as exc:
            print(f"Erro MySQL: {exc}")
            self.send_json(503, {"error": "Não foi possível acessar o banco MySQL."})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "O servidor encontrou um erro inesperado."})

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(400, "Requisição inválida.") from exc
        if length <= 0 or length > MAX_BODY_SIZE:
            raise ApiError(400, "Conteúdo da requisição inválido.")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "O conteúdo enviado não é um JSON válido.") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "O conteúdo enviado deve ser um objeto JSON.")
        return data

    def request_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def current_user(
        self, connection, required: bool = True, include_profile: bool = False
    ) -> dict | None:
        token = self.request_token()
        if not token:
            if required:
                raise ApiError(401, "Entre na sua conta para acessar os compromissos.")
            return None
        cursor = connection.cursor(dictionary=True)
        fields = "u.id, u.name, u.email"
        if include_profile:
            fields += ", u.phone, u.sms_alerts, u.avatar_data, u.password_hash"
        cursor.execute(
            f"""SELECT {fields}
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.expires_at > UTC_TIMESTAMP()""",
            (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
        )
        user = cursor.fetchone()
        cursor.close()
        if not user and required:
            raise ApiError(401, "Sua sessão expirou. Entre novamente.")
        return user

    def issue_session(self, connection, user_id: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = datetime.utcnow() + timedelta(days=SESSION_DAYS)
        cursor = connection.cursor()
        cursor.execute("DELETE FROM sessions WHERE expires_at <= UTC_TIMESTAMP()")
        cursor.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
            (token_hash, user_id, expires_at),
        )
        connection.commit()
        cookie = (
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={SESSION_DAYS * 24 * 60 * 60}"
        )
        return cookie, token_hash

    def route_get(self, path: str) -> None:
        if path in ("/api/session", "/api/account"):
            connection = db_connect()
            try:
                user = self.current_user(
                    connection, required=path == "/api/account", include_profile=True
                )
                self.send_json(200, {"user": public_user(user) if user else None})
            finally:
                connection.close()
            return
        if path == "/api/tasks":
            connection = db_connect()
            try:
                user = self.current_user(connection)
                cursor = connection.cursor(dictionary=True)
                cursor.execute(
                    """SELECT id, title, task_date, task_time, category, done
                       FROM tasks WHERE user_id = %s
                       ORDER BY task_date, task_time, created_at""",
                    (user["id"],),
                )
                self.send_json(200, {"tasks": [serialize_task(row) for row in cursor.fetchall()]})
                cursor.close()
            finally:
                connection.close()
            return
        raise ApiError(404, "Rota não encontrada.")

    def route_register(self) -> None:
        data = self.read_json()
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        if not name or len(name) > 120:
            raise ApiError(400, "Informe um nome com até 120 caracteres.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 190:
            raise ApiError(400, "Informe um e-mail válido.")
        if len(password) < 8 or len(password) > 128:
            raise ApiError(400, "A senha deve ter entre 8 e 128 caracteres.")
        if is_common_password(password):
            raise ApiError(400, "Essa senha é muito comum ou repetitiva. Escolha uma frase mais longa e exclusiva.")
        if not smtp_is_configured():
            raise ApiError(
                503,
                "A confirmação por e-mail ainda não foi configurada. Defina as variáveis SMTP no arquivo .env.",
            )
        code, code_salt, code_hash, expires_at = make_email_code()
        connection = db_connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, name, email_verified, verification_sent_at FROM users WHERE email = %s",
                (email,),
            )
            existing = cursor.fetchone()
            if existing and existing["email_verified"]:
                raise ApiError(409, "Este e-mail já possui uma conta. Entre nela.")
            if existing:
                raise ApiError(
                    409,
                    "Já existe um cadastro pendente para este e-mail. Entre com a senha escolhida para confirmar ou reenviar o código.",
                )
            else:
                now = datetime.utcnow()
                salt = secrets.token_bytes(16).hex()
                digest = password_digest(password, salt)
                try:
                    cursor.execute(
                        """INSERT INTO users
                           (name, email, password_salt, password_hash, email_verified,
                            verification_code_salt, verification_code_hash, verification_expires_at,
                            verification_sent_at, verification_attempts)
                           VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, 0)""",
                        (name, email, salt, digest, code_salt, code_hash, expires_at, now),
                    )
                except IntegrityError as exc:
                    connection.rollback()
                    raise ApiError(409, "Este e-mail já possui uma conta. Entre nela.") from exc
                send_name = name
            connection.commit()
            cursor.close()
            send_verification_email(email, send_name, code)
            self.send_json(202, {"verificationRequired": True, "email": email})
        except ApiError:
            raise
        finally:
            connection.close()

    def route_verify_email(self) -> None:
        data = self.read_json()
        email = str(data.get("email", "")).strip().lower()
        code = str(data.get("code", "")).strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not re.fullmatch(r"\d{8}", code):
            raise ApiError(400, "Informe o e-mail e o código de 8 dígitos recebido.")
        connection = db_connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT id, name, email, phone, sms_alerts, avatar_data,
                          password_hash, email_verified, verification_code_salt,
                          verification_code_hash, verification_expires_at, verification_attempts
                   FROM users WHERE email = %s FOR UPDATE""",
                (email,),
            )
            user = cursor.fetchone()
            if not user or user["email_verified"] or not user["verification_code_hash"]:
                raise ApiError(400, "E-mail ou código inválido.")
            if user["verification_attempts"] >= EMAIL_CODE_MAX_ATTEMPTS:
                raise ApiError(429, "Este código atingiu o limite de tentativas. Solicite outro.")
            if not user["verification_expires_at"] or user["verification_expires_at"] < datetime.utcnow():
                raise ApiError(410, "O código expirou. Solicite um novo código.")
            supplied_hash = password_digest(code, user["verification_code_salt"])
            if not hmac.compare_digest(supplied_hash, user["verification_code_hash"]):
                cursor.execute(
                    "UPDATE users SET verification_attempts = verification_attempts + 1 WHERE id = %s",
                    (user["id"],),
                )
                connection.commit()
                raise ApiError(400, "Código incorreto. Confira a mensagem e tente novamente.")
            cursor.execute(
                """UPDATE users SET email_verified = 1, verification_code_salt = NULL,
                   verification_code_hash = NULL, verification_expires_at = NULL,
                   verification_sent_at = NULL, verification_attempts = 0 WHERE id = %s""",
                (user["id"],),
            )
            connection.commit()
            cookie, _ = self.issue_session(connection, user["id"])
            user["email_verified"] = True
            self.send_json(200, {"user": public_user(user)}, (("Set-Cookie", cookie),))
            cursor.close()
        finally:
            connection.close()

    def route_login(self) -> None:
        data = self.read_json()
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        connection = db_connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, name, email, phone, sms_alerts, avatar_data, password_salt, password_hash, email_verified FROM users WHERE email = %s",
                (email,),
            )
            user = cursor.fetchone()
            if not user:
                raise ApiError(404, "Não encontramos uma conta com esse e-mail. Crie uma conta para continuar.")
            if not user["password_hash"] or not user["password_salt"]:
                raise ApiError(409, "Esta conta ainda não possui uma senha para acesso por e-mail.")
            if not hmac.compare_digest(
                password_digest(password, user["password_salt"]), user["password_hash"]
            ):
                raise ApiError(401, "A senha está incorreta.")
            if not user["email_verified"]:
                raise ApiError(403, "Confirme seu e-mail antes de entrar.")
            cookie, _ = self.issue_session(connection, user["id"])
            self.send_json(200, {"user": public_user(user)}, (("Set-Cookie", cookie),))
            cursor.close()
        finally:
            connection.close()

    def route_resend_verification(self) -> None:
        data = self.read_json()
        email = str(data.get("email", "")).strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 190:
            raise ApiError(400, "Informe um e-mail válido.")
        connection = db_connect()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, name, email_verified, verification_sent_at FROM users WHERE email = %s",
                (email,),
            )
            user = cursor.fetchone()
            if not user or user["email_verified"]:
                self.send_json(200, {"ok": True, "message": "Se houver um cadastro pendente, enviaremos um novo código."})
                cursor.close()
                return
            now = datetime.utcnow()
            if user["verification_sent_at"]:
                elapsed = (now - user["verification_sent_at"]).total_seconds()
                if elapsed < EMAIL_CODE_RESEND_SECONDS:
                    raise ApiError(429, "Aguarde um minuto antes de solicitar outro código.")
            code, salt, digest, expires_at = make_email_code()
            cursor.execute(
                """UPDATE users SET verification_code_salt = %s, verification_code_hash = %s,
                   verification_expires_at = %s, verification_sent_at = %s, verification_attempts = 0
                   WHERE id = %s""",
                (salt, digest, expires_at, now, user["id"]),
            )
            connection.commit()
            cursor.close()
            send_verification_email(email, user["name"], code)
            self.send_json(200, {"ok": True, "message": "Se houver um cadastro pendente, enviaremos um novo código."})
        finally:
            connection.close()

    def route_update_account(self) -> None:
        data = self.read_json()
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 120:
            raise ApiError(400, "Informe um nome com até 120 caracteres.")
        phone = normalize_phone(data.get("phone"))
        sms_alerts = data.get("smsAlerts", False)
        if not isinstance(sms_alerts, bool):
            raise ApiError(400, "A preferência de alertas está inválida.")
        if sms_alerts and not phone:
            raise ApiError(400, "Cadastre um telefone com código do país para ativar alertas por SMS.")
        has_avatar = "avatar" in data
        avatar = normalize_avatar(data.get("avatar")) if has_avatar else None

        connection = db_connect()
        try:
            user = self.current_user(connection)
            cursor = connection.cursor(dictionary=True)
            if has_avatar:
                cursor.execute(
                    """UPDATE users SET name = %s, phone = %s, sms_alerts = %s, avatar_data = %s
                       WHERE id = %s""",
                    (name, phone, sms_alerts, avatar, user["id"]),
                )
            else:
                cursor.execute(
                    "UPDATE users SET name = %s, phone = %s, sms_alerts = %s WHERE id = %s",
                    (name, phone, sms_alerts, user["id"]),
                )
            connection.commit()
            cursor.execute(
                "SELECT id, name, email, phone, sms_alerts, avatar_data, password_hash FROM users WHERE id = %s",
                (user["id"],),
            )
            updated = cursor.fetchone()
            self.send_json(200, {"user": public_user(updated)})
            cursor.close()
        finally:
            connection.close()

    def route_change_password(self) -> None:
        data = self.read_json()
        current_password = data.get("currentPassword")
        new_password = data.get("newPassword")
        if not isinstance(current_password, str) or not isinstance(new_password, str):
            raise ApiError(400, "Preencha a senha atual e a nova senha.")
        if len(new_password) < 8 or len(new_password) > 128:
            raise ApiError(400, "A nova senha deve ter entre 8 e 128 caracteres.")
        if is_common_password(new_password):
            raise ApiError(400, "Essa senha é muito comum ou repetitiva. Escolha uma frase mais longa e exclusiva.")

        connection = db_connect()
        try:
            user = self.current_user(connection)
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT password_salt, password_hash FROM users WHERE id = %s",
                (user["id"],),
            )
            credentials = cursor.fetchone()
            if not credentials or not hmac.compare_digest(
                password_digest(current_password, credentials["password_salt"]),
                credentials["password_hash"],
            ):
                raise ApiError(401, "A senha atual está incorreta.")
            salt = secrets.token_bytes(16).hex()
            digest = password_digest(new_password, salt)
            cursor.execute(
                "UPDATE users SET password_salt = %s, password_hash = %s WHERE id = %s",
                (salt, digest, user["id"]),
            )
            connection.commit()
            self.send_json(200, {"ok": True})
            cursor.close()
        finally:
            connection.close()

    def route_logout(self) -> None:
        token = self.request_token()
        if token:
            connection = db_connect()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "DELETE FROM sessions WHERE token_hash = %s",
                    (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
                )
                connection.commit()
            finally:
                connection.close()
        expired = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        self.send_json(200, {"ok": True}, (("Set-Cookie", expired),))

    def insert_task(self, cursor, user_id: int, task: dict) -> None:
        cursor.execute(
            """INSERT INTO tasks (user_id, id, title, task_date, task_time, category, done)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE title = VALUES(title), task_date = VALUES(task_date),
                 task_time = VALUES(task_time), category = VALUES(category), done = VALUES(done)""",
            (
                user_id,
                task["id"],
                task["title"],
                task["task_date"],
                task["task_time"],
                task["category"],
                task["done"],
            ),
        )

    def get_task(self, cursor, user_id: int, task_id: str) -> dict | None:
        cursor.execute(
            """SELECT id, title, task_date, task_time, category, done
               FROM tasks WHERE user_id = %s AND id = %s""",
            (user_id, task_id),
        )
        return cursor.fetchone()

    def route_create_task(self) -> None:
        data = self.read_json()
        task = normalize_task(data)
        connection = db_connect()
        try:
            user = self.current_user(connection)
            cursor = connection.cursor(dictionary=True)
            self.insert_task(cursor, user["id"], task)
            connection.commit()
            self.send_json(201, {"task": serialize_task(self.get_task(cursor, user["id"], task["id"]))})
            cursor.close()
        finally:
            connection.close()

    def route_import_tasks(self) -> None:
        data = self.read_json()
        items = data.get("tasks")
        if not isinstance(items, list) or len(items) > 1000:
            raise ApiError(400, "A lista de compromissos é inválida ou grande demais.")
        tasks = [normalize_task(item) for item in items if isinstance(item, dict)]
        if len(tasks) != len(items):
            raise ApiError(400, "A lista contém um compromisso inválido.")
        connection = db_connect()
        try:
            user = self.current_user(connection)
            cursor = connection.cursor(dictionary=True)
            for task in tasks:
                self.insert_task(cursor, user["id"], task)
            connection.commit()
            self.send_json(200, {"imported": len(tasks)})
            cursor.close()
        finally:
            connection.close()

    def route_put_task(self, task_id: str) -> None:
        task = normalize_task(self.read_json(), task_id)
        connection = db_connect()
        try:
            user = self.current_user(connection)
            cursor = connection.cursor(dictionary=True)
            self.insert_task(cursor, user["id"], task)
            connection.commit()
            self.send_json(200, {"task": serialize_task(self.get_task(cursor, user["id"], task_id))})
            cursor.close()
        finally:
            connection.close()

    def route_patch_task(self, task_id: str) -> None:
        data = self.read_json()
        if not isinstance(data.get("done"), bool):
            raise ApiError(400, "Informe o estado concluído do compromisso.")
        connection = db_connect()
        try:
            user = self.current_user(connection)
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "UPDATE tasks SET done = %s WHERE user_id = %s AND id = %s",
                (1 if data["done"] else 0, user["id"], task_id),
            )
            if cursor.rowcount == 0:
                raise ApiError(404, "Compromisso não encontrado.")
            connection.commit()
            self.send_json(200, {"task": serialize_task(self.get_task(cursor, user["id"], task_id))})
            cursor.close()
        finally:
            connection.close()

    def route_delete_task(self, task_id: str) -> None:
        connection = db_connect()
        try:
            user = self.current_user(connection)
            cursor = connection.cursor()
            cursor.execute("DELETE FROM tasks WHERE user_id = %s AND id = %s", (user["id"], task_id))
            if cursor.rowcount == 0:
                raise ApiError(404, "Compromisso não encontrado.")
            connection.commit()
            self.send_json(200, {"ok": True})
            cursor.close()
        finally:
            connection.close()

    def serve_static(self, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        allowed = {"index.html", "script.js", "style.css", "logo.svg", "logo-cale.png", "logo-cale-dark.png", "mascote-cale.png", "mascote-cale-dark.png"}
        if name not in allowed:
            self.send_json(404, {"error": "Arquivo não encontrado."})
            return
        file_path = ROOT / name
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_json(404, {"error": "Arquivo não encontrado."})
            return
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.endswith((".html", ".js", ".css", ".svg")):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer-when-downgrade")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self.run_api(self.route_get, path)
        else:
            self.serve_static(path)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        routes = {
            "/api/register": self.route_register,
            "/api/verify-email": self.route_verify_email,
            "/api/resend-verification": self.route_resend_verification,
            "/api/login": self.route_login,
            "/api/logout": self.route_logout,
            "/api/account/password": self.route_change_password,
            "/api/tasks": self.route_create_task,
            "/api/tasks/import": self.route_import_tasks,
        }
        action = routes.get(path)
        if action:
            self.run_api(action)
        else:
            self.send_json(404, {"error": "Rota não encontrada."})

    def do_PUT(self) -> None:
        if urlsplit(self.path).path == "/api/account":
            self.run_api(self.route_update_account)
            return
        match = re.fullmatch(r"/api/tasks/([^/]+)", urlsplit(self.path).path)
        if not match:
            self.send_json(404, {"error": "Rota não encontrada."})
            return
        self.run_api(self.route_put_task, unquote(match.group(1)))

    def do_PATCH(self) -> None:
        match = re.fullmatch(r"/api/tasks/([^/]+)", urlsplit(self.path).path)
        if not match:
            self.send_json(404, {"error": "Rota não encontrada."})
            return
        self.run_api(self.route_patch_task, unquote(match.group(1)))

    def do_DELETE(self) -> None:
        match = re.fullmatch(r"/api/tasks/([^/]+)", urlsplit(self.path).path)
        if not match:
            self.send_json(404, {"error": "Rota não encontrada."})
            return
        self.run_api(self.route_delete_task, unquote(match.group(1)))


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    try:
        initialize_database()
    except mysql.connector.Error as exc:
        raise SystemExit(
            "Não foi possível preparar o MySQL. Confira se o serviço está ativo e se "
            "SIAP_DB_HOST, SIAP_DB_PORT, SIAP_DB_USER e SIAP_DB_PASSWORD estão corretos.\n"
            f"Detalhe: {exc}"
        ) from exc
    server = LocalThreadingHTTPServer((APP_HOST, APP_PORT), SIAPHandler)
    print(f"CALÊ disponível em http://{APP_HOST}:{APP_PORT}")
    print(f"Banco MySQL: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    print("Pressione Ctrl+C para encerrar o servidor.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor CALÊ encerrado.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
