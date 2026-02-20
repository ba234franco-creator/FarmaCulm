from __future__ import annotations
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import sqlite3
import smtplib
import ssl
import unicodedata
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from xml.sax import handler


BASE_DIR = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE_DIR / "data" / "productos.json"
DB_FILE = BASE_DIR / "data" / "farmaculm.db"
USERS_EXPORT_FILE = BASE_DIR / "data" / "usuarios_export.json"
ORDERS_DIR = BASE_DIR / "pedidos"
ORDERS_FILE = ORDERS_DIR / "pedidos.log"

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN_LENGTH = 4
HASH_ROUNDS = 120_000
RESET_CODE_LENGTH = 6
RESET_CODE_TTL_MINUTES = 15


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def normalize_email(value: Any) -> str:
    return sanitize_text(value).lower()


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt_bytes = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, HASH_ROUNDS
    )
    return digest.hex(), salt_bytes.hex()


def verify_password(password: str, expected_hash: str, salt_hex: str) -> bool:
    computed_hash, _ = hash_password(password, salt_hex)
    return hmac.compare_digest(expected_hash, computed_hash)


def init_database() -> None:
    DB_FILE.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_password_resets_email
            ON password_resets(email)
            """
        )
        conn.commit()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def build_users_export_payload() -> dict[str, Any]:
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, nombre, email, password_hash, created_at
            FROM usuarios
            ORDER BY id DESC
            """
        ).fetchall()

    users = [
        {
            "id": int(row["id"]),
            "nombre": str(row["nombre"]),
            "email": str(row["email"]),
            "password_hash": str(row["password_hash"]),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]

    return {
        "ok": True,
        "total_usuarios": len(users),
        "usuarios": users,
    }


def save_users_export_json() -> dict[str, Any]:
    payload = build_users_export_payload()
    USERS_EXPORT_FILE.parent.mkdir(exist_ok=True)
    USERS_EXPORT_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def load_products() -> list[dict[str, Any]]:
    if not PRODUCTS_FILE.exists():
        return []
    with PRODUCTS_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, list):
        return data
    return []


def normalize_search_text(value: Any) -> str:
    text = sanitize_text(value).lower()
    normalized = unicodedata.normalize("NFD", text)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def parse_limit(raw_limit: str, default: int = 12, max_limit: int = 40) -> int:
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, max_limit))


def parse_bool(raw_value: Any, default: bool = False) -> bool:
    if raw_value is None:
        return default
    value = sanitize_text(raw_value).lower()
    if not value:
        return default
    return value in {"1", "true", "t", "yes", "y", "si", "sí", "on"}


def parse_int(raw_value: Any, default: int) -> int:
    try:
        return int(sanitize_text(raw_value))
    except (TypeError, ValueError):
        return default


def build_registration_email_settings() -> dict[str, Any]:
    smtp_ssl = parse_bool(os.environ.get("SMTP_SSL"), default=False)
    smtp_tls = parse_bool(os.environ.get("SMTP_TLS"), default=not smtp_ssl)
    default_port = 465 if smtp_ssl else 587

    host = sanitize_text(os.environ.get("SMTP_HOST"))
    port = parse_int(os.environ.get("SMTP_PORT"), default=default_port)
    username = sanitize_text(os.environ.get("SMTP_USER"))
    password = sanitize_text(os.environ.get("SMTP_PASSWORD"))
    from_email = sanitize_text(os.environ.get("SMTP_FROM"), username)
    from_name = sanitize_text(os.environ.get("SMTP_FROM_NAME"), "FARMACULM")
    enabled = parse_bool(os.environ.get("ENABLE_REGISTER_EMAIL"), default=True)

    return {
        "enabled": enabled,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "from_email": from_email,
        "from_name": from_name,
        "smtp_ssl": smtp_ssl,
        "smtp_tls": smtp_tls,
    }


def send_email_message(
    to_email: str, subject: str, text_body: str, html_body: str
) -> tuple[bool, str]:
    settings = build_registration_email_settings()
    if not settings["enabled"]:
        return False, "disabled"

    if not settings["host"] or not settings["from_email"]:
        return False, "smtp_not_configured"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f'{settings["from_name"]} <{settings["from_email"]}>'
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        if settings["smtp_ssl"]:
            with smtplib.SMTP_SSL(
                settings["host"],
                settings["port"],
                context=ssl.create_default_context(),
                timeout=20,
            ) as server:
                if settings["username"] and settings["password"]:
                    server.login(settings["username"], settings["password"])
                server.send_message(msg)
            return True, ""

        with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as server:
            server.ehlo()
            if settings["smtp_tls"]:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if settings["username"] and settings["password"]:
                server.login(settings["username"], settings["password"])
            server.send_message(msg)
        return True, ""
    except Exception as error:  # noqa: BLE001
        return False, str(error)


def send_registration_email(nombre: str, email: str) -> tuple[bool, str]:
    text_body = (
        f"Hola {nombre},\n\n"
        "Tu registro en FARMACULM se realizo correctamente.\n"
        "Ya puedes iniciar sesion y usar la plataforma.\n\n"
        "Equipo FARMACULM"
    )
    html_body = (
        "<html><body>"
        f"<p>Hola <strong>{nombre}</strong>,</p>"
        "<p>Tu registro en <strong>FARMACULM</strong> se realizo correctamente.</p>"
        "<p>Ya puedes iniciar sesion y usar la plataforma.</p>"
        "<p>Equipo FARMACULM</p>"
        "</body></html>"
    )
    return send_email_message(
        to_email=email,
        subject="Registro exitoso en FARMACULM",
        text_body=text_body,
        html_body=html_body,
    )


def hash_reset_code(email: str, code: str) -> str:
    secret = sanitize_text(
        os.environ.get("RESET_CODE_SECRET"),
        "farmaculm-reset-secret",
    )
    payload = f"{normalize_email(email)}:{sanitize_text(code)}:{secret}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_reset_code() -> str:
    max_value = 10**RESET_CODE_LENGTH
    return f"{secrets.randbelow(max_value):0{RESET_CODE_LENGTH}d}"


def send_password_reset_code_email(nombre: str, email: str, code: str) -> tuple[bool, str]:
    text_body = (
        f"Hola {nombre},\n\n"
        f"Tu codigo para restablecer la contrasena es: {code}\n"
        f"Este codigo vence en {RESET_CODE_TTL_MINUTES} minutos.\n\n"
        "Si no solicitaste este cambio, ignora este mensaje.\n\n"
        "Equipo FARMACULM"
    )
    html_body = (
        "<html><body>"
        f"<p>Hola <strong>{nombre}</strong>,</p>"
        f"<p>Tu codigo para restablecer la contrasena es: "
        f"<strong style='font-size:20px;letter-spacing:2px'>{code}</strong></p>"
        f"<p>Este codigo vence en {RESET_CODE_TTL_MINUTES} minutos.</p>"
        "<p>Si no solicitaste este cambio, ignora este mensaje.</p>"
        "<p>Equipo FARMACULM</p>"
        "</body></html>"
    )
    return send_email_message(
        to_email=email,
        subject="Codigo de recuperacion - FARMACULM",
        text_body=text_body,
        html_body=html_body,
    )


def search_products(
    products: list[dict[str, Any]], query: str, limit: int = 12
) -> list[dict[str, Any]]:
    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return products[:limit]

    query_terms = [term for term in normalized_query.split() if term]
    scored_products: list[tuple[int, int, dict[str, Any]]] = []

    for index, product in enumerate(products):
        name = normalize_search_text(product.get("nombre", ""))
        category = normalize_search_text(product.get("categoria", ""))
        haystack = f"{name} {category}".strip()
        if not haystack:
            continue

        score = 0
        if normalized_query == name:
            score += 140
        if name.startswith(normalized_query):
            score += 100
        if normalized_query in name:
            score += 80
        if normalized_query in category:
            score += 35

        for term in query_terms:
            if term in name:
                score += 16
            elif term in category:
                score += 10
            elif term in haystack:
                score += 5

        if score > 0:
            scored_products.append((score, index, product))

    scored_products.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored_products[:limit]]


def search_products_exact(
    products: list[dict[str, Any]], query: str, limit: int = 1
) -> list[dict[str, Any]]:
    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return products[:limit]

    exact_matches = [
        product
        for product in products
        if normalize_search_text(product.get("nombre", "")) == normalized_query
    ]
    if exact_matches:
        return exact_matches[:limit]

    exact_id_matches = [
        product
        for product in products
        if normalize_search_text(product.get("id", "")) == normalized_query
    ]
    return exact_id_matches[:limit]


class FarmaculmHandler(SimpleHTTPRequestHandler):
    server_version = "FarmaculmPythonServer/1.0"

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any] | None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "JSON invalido"})
            return None

        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "El cuerpo debe ser un objeto JSON"})
            return None
        return payload

    def _handle_register(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        nombre = sanitize_text(payload.get("nombre"))
        email = normalize_email(payload.get("email"))
        password = sanitize_text(payload.get("password"))

        if len(nombre) < 2:
            self._send_json(
                400, {"ok": False, "error": "El nombre debe tener al menos 2 caracteres"}
            )
            return

        if not EMAIL_PATTERN.match(email):
            self._send_json(400, {"ok": False, "error": "Correo electronico invalido"})
            return

        if len(password) < PASSWORD_MIN_LENGTH:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": f"La contrasena debe tener al menos {PASSWORD_MIN_LENGTH} caracteres",
                },
            )
            return

        password_hash, salt = hash_password(password)
        created_at = utc_now_iso()

        try:
            with get_db_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO usuarios (nombre, email, password_hash, salt, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (nombre, email, password_hash, salt, created_at),
                )
                user_id = int(cursor.lastrowid)
                conn.commit()
        except sqlite3.IntegrityError:
            self._send_json(
                409, {"ok": False, "error": "Este correo ya esta registrado"}
            )
            return

        save_users_export_json()
        email_sent, email_error = send_registration_email(nombre=nombre, email=email)
        if not email_sent and email_error not in {"disabled", "smtp_not_configured"}:
            print(
                "[WARN] Registro creado, pero no se pudo enviar correo "
                f"de bienvenida a {email}: {email_error}"
            )

        self._send_json(
            201,
            {
                "ok": True,
                "message": "Usuario registrado correctamente",
                "email_notificacion": email_sent,
                "usuario": {
                    "id": user_id,
                    "nombre": nombre,
                    "email": email,
                    "created_at": created_at,
                },
            },
        )

    def _handle_login(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        email = normalize_email(payload.get("email"))
        password = sanitize_text(payload.get("password"))

        if not EMAIL_PATTERN.match(email):
            self._send_json(400, {"ok": False, "error": "Correo electronico invalido"})
            return

        if len(password) < PASSWORD_MIN_LENGTH:
            self._send_json(400, {"ok": False, "error": "Contrasena invalida"})
            return

        with get_db_connection() as conn:
            user = conn.execute(
                """
                SELECT id, nombre, email, password_hash, salt, created_at
                FROM usuarios
                WHERE email = ?
                """,
                (email,),
            ).fetchone()

        if not user or not verify_password(password, user["password_hash"], user["salt"]):
            self._send_json(401, {"ok": False, "error": "Credenciales incorrectas"})
            return

        self._send_json(
            200,
            {
                "ok": True,
                "message": "Inicio de sesion correcto",
                "usuario": {
                    "id": int(user["id"]),
                    "nombre": str(user["nombre"]),
                    "email": str(user["email"]),
                    "created_at": str(user["created_at"]),
                },
            },
        )

    def _handle_password_reset_request(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        email = normalize_email(payload.get("email"))
        if not EMAIL_PATTERN.match(email):
            self._send_json(400, {"ok": False, "error": "Correo electronico invalido"})
            return

        with get_db_connection() as conn:
            user = conn.execute(
                """
                SELECT nombre, email
                FROM usuarios
                WHERE email = ?
                """,
                (email,),
            ).fetchone()

        if not user:
            self._send_json(
                200,
                {
                    "ok": True,
                    "message": "Si el correo esta registrado, te enviamos un codigo.",
                },
            )
            return

        code = generate_reset_code()
        code_hash = hash_reset_code(email=email, code=code)
        created_at = utc_now_iso()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=RESET_CODE_TTL_MINUTES)).isoformat()

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE password_resets
                SET used = 1
                WHERE email = ? AND used = 0
                """,
                (email,),
            )
            conn.execute(
                """
                INSERT INTO password_resets (email, code_hash, expires_at, used, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (email, code_hash, expires_at, created_at),
            )
            conn.commit()

        email_sent, email_error = send_password_reset_code_email(
            nombre=str(user["nombre"]),
            email=str(user["email"]),
            code=code,
        )

        if not email_sent:
            if email_error == "disabled":
                self._send_json(
                    503,
                    {"ok": False, "error": "La recuperacion por correo esta deshabilitada."},
                )
                return
            if email_error == "smtp_not_configured":
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "error": "No hay correo configurado para enviar el codigo de recuperacion.",
                    },
                )
                return

            self._send_json(
                500,
                {"ok": False, "error": "No fue posible enviar el codigo. Intenta de nuevo."},
            )
            print(
                "[WARN] No se pudo enviar codigo de recuperacion "
                f"a {email}: {email_error}"
            )
            return

        self._send_json(
            200,
            {
                "ok": True,
                "message": "Te enviamos un codigo a tu correo.",
                "expires_minutes": RESET_CODE_TTL_MINUTES,
            },
        )

    def _handle_password_reset_confirm(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        email = normalize_email(payload.get("email"))
        code = sanitize_text(payload.get("code"))
        new_password = sanitize_text(payload.get("new_password"))

        if not EMAIL_PATTERN.match(email):
            self._send_json(400, {"ok": False, "error": "Correo electronico invalido"})
            return

        if len(code) != RESET_CODE_LENGTH or not code.isdigit():
            self._send_json(
                400,
                {"ok": False, "error": f"El codigo debe tener {RESET_CODE_LENGTH} digitos"},
            )
            return

        if len(new_password) < PASSWORD_MIN_LENGTH:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": f"La contrasena debe tener al menos {PASSWORD_MIN_LENGTH} caracteres",
                },
            )
            return

        with get_db_connection() as conn:
            reset_row = conn.execute(
                """
                SELECT id, code_hash, expires_at, used
                FROM password_resets
                WHERE email = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (email,),
            ).fetchone()

        if not reset_row or int(reset_row["used"]) == 1:
            self._send_json(
                400,
                {"ok": False, "error": "No hay un codigo activo para este correo"},
            )
            return

        try:
            expires_at = datetime.fromisoformat(str(reset_row["expires_at"]))
        except ValueError:
            expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        if expires_at <= datetime.now(timezone.utc):
            with get_db_connection() as conn:
                conn.execute(
                    """
                    UPDATE password_resets
                    SET used = 1
                    WHERE id = ?
                    """,
                    (int(reset_row["id"]),),
                )
                conn.commit()
            self._send_json(400, {"ok": False, "error": "El codigo ya vencio. Solicita uno nuevo."})
            return

        expected_hash = str(reset_row["code_hash"])
        current_hash = hash_reset_code(email=email, code=code)
        if not hmac.compare_digest(expected_hash, current_hash):
            self._send_json(401, {"ok": False, "error": "Codigo de recuperacion incorrecto"})
            return

        password_hash, salt = hash_password(new_password)

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE usuarios
                SET password_hash = ?, salt = ?
                WHERE email = ?
                """,
                (password_hash, salt, email),
            )
            conn.execute(
                """
                UPDATE password_resets
                SET used = 1
                WHERE id = ?
                """,
                (int(reset_row["id"]),),
            )
            conn.commit()

        self._send_json(
            200,
            {"ok": True, "message": "Contrasena actualizada correctamente"},
        )

    def _handle_order(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        items = payload.get("items", [])
        if not isinstance(items, list) or len(items) == 0:
            self._send_json(400, {"ok": False, "error": "El pedido debe incluir items"})
            return

        order_id = f"PED-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        order_record = {
            "id": order_id,
            "timestamp": utc_now_iso(),
            "cliente": sanitize_text(payload.get("cliente"), "Sin nombre"),
            "items": items,
            "total": payload.get("total", 0),
            "origen": "web",
        }

        ORDERS_DIR.mkdir(exist_ok=True)
        with ORDERS_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps(order_record, ensure_ascii=False) + "\n")

        self._send_json(
            201,
            {
                "ok": True,
                "message": "Pedido guardado correctamente",
                "order_id": order_id,
            },
        )

    def _handle_users(self) -> None:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, nombre, email, created_at
                FROM usuarios
                ORDER BY id DESC
                """
            ).fetchall()

        users = [
            {
                "id": int(row["id"]),
                "nombre": str(row["nombre"]),
                "email": str(row["email"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

        self._send_json(200, {"ok": True, "count": len(users), "usuarios": users})

    def _handle_users_export(self) -> None:
        payload = save_users_export_json()
        payload["archivo"] = str(USERS_EXPORT_FILE.relative_to(BASE_DIR))
        payload["seguridad"] = "Las contrasenas no se guardan en texto plano; se exporta password_hash."
        self._send_json(200, payload)

    def do_GET(self) -> None:  # noqa: N802 - standard library method name
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        if path == "/api/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "FARMACULM API",
                    "python": platform.python_version(),
                    "timestamp": utc_now_iso(),
                },
            )
            return

        if path == "/api/productos":
            all_products = load_products()
            query = sanitize_text(query_params.get("q", [""])[0])
            limit = parse_limit(query_params.get("limit", ["12"])[0], default=12, max_limit=80)
            exact_only = parse_bool(query_params.get("exact", ["0"])[0], default=False)

            if query:
                if exact_only:
                    products = search_products_exact(all_products, query, limit=1)
                else:
                    products = search_products(all_products, query, limit)
            elif "limit" in query_params:
                products = all_products[:limit]
            else:
                products = all_products

            self._send_json(
                200,
                {
                    "ok": True,
                    "query": query,
                    "exact": exact_only,
                    "total": len(all_products),
                    "count": len(products),
                    "productos": products,
                },
            )
            return

        if path == "/api/usuarios":
            self._handle_users()
            return

        if path == "/api/usuarios-export":
            self._handle_users_export()
            return

        super().do_GET()

    def do_OPTIONS(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/api/register":
            self._handle_register()
            return

        if path == "/api/login":
            self._handle_login()
            return

        if path == "/api/password/request-reset":
            self._handle_password_reset_request()
            return

        if path == "/api/password/confirm-reset":
            self._handle_password_reset_confirm()
            return

        if path == "/api/pedidos":
            self._handle_order()
            return

        self._send_json(404, {"ok": False, "error": "Ruta no encontrada"})


def run_server() -> None:
    init_database()
    save_users_export_json()
    port = int(os.environ.get("PORT", 10000))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.serve_forever()

    print(f"Servidor activo en https://farmaculm.onrender.com")
    print(f"API health:    https://farmaculm.onrender.com/api/health")
    print(f"API productos: https://farmaculm.onrender.com/api/productos")
    print(f"API usuarios:  https://farmaculm.onrender.com/api/usuarios")
    print(f"API export:    https://farmaculm.onrender.com/api/usuarios-export")
    print(f"API register:  https://farmaculm.onrender.com/api/register")
    print(f"API login:     https://farmaculm.onrender.com/api/login")
    print(f"API reset req: https://farmaculm.onrender.com/api/password/request-reset")
    print(f"API reset ok:  https://farmaculm.onrender.com/api/password/confirm-reset")
    email_settings = build_registration_email_settings()
    if (
        email_settings["enabled"]
        and email_settings["host"]
        and email_settings["from_email"]
    ):
        print(
            "Email registro: activo "
            f"({email_settings['from_email']} via {email_settings['host']}:{email_settings['port']})"
        )
    elif not email_settings["enabled"]:
        print("Email registro: deshabilitado (ENABLE_REGISTER_EMAIL=0)")
    else:
        print(
            "Email registro: pendiente de configurar "
            "(SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)"
        )
    print("Presiona Ctrl + C para detener.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\nServidor detenido.")


if __name__ == "__main__":
    run_server()

