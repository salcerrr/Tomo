"""
Cuentas de usuario: registro, inicio de sesión y protección de rutas.

- Contraseñas con hash (scrypt, el predeterminado de Werkzeug); nunca se
  guardan ni se registran en claro.
- La sesión es una cookie firmada (HttpOnly, SameSite=Lax) que guarda
  solo el id del usuario.
- Contra la fuerza bruta: se limitan los intentos fallidos por email y
  por dirección IP.
- Contra CSRF: además de SameSite, los pedidos que modifican datos se
  rechazan si traen un Origin de otro sitio.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from functools import wraps
from typing import Optional
from urllib.parse import urlparse

from flask import Blueprint, Flask, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from tomo import store

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MIN_PASSWORD = 8
MAX_PASSWORD = 128
MAX_NAME = 40

SESSION_DAYS = 30

# Intentos fallidos permitidos antes de bloquear un rato. El límite por IP
# es más alto para que un error de tipeo no bloquee a toda una red (una
# casa, una universidad).
MAX_FAILURES = 5
MAX_FAILURES_IP = 20
FAILURE_WINDOW = 15 * 60

# Cuentas nuevas que se pueden crear desde una misma IP por hora.
MAX_SIGNUPS_PER_IP = 10
SIGNUP_WINDOW = 60 * 60

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# Un hash de mentira para gastar el mismo tiempo cuando el email no
# existe (así no se puede adivinar qué emails tienen cuenta).
_DUMMY_HASH = generate_password_hash("tomo-dummy-password")


# --------------------------------
# Límite de intentos (en memoria)
# --------------------------------

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _recent(key: str, now: float) -> list[float]:

    attempts = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW]

    if attempts:
        _failures[key] = attempts
    else:
        _failures.pop(key, None)

    return attempts


def _blocked(keys: list[str]) -> int:
    """Segundos que faltan para poder reintentar (0 si no está bloqueado)."""

    now = time.time()

    with _failures_lock:

        wait = 0

        for key in keys:

            attempts = _recent(key, now)

            limit = MAX_FAILURES_IP if key.startswith("ip:") else MAX_FAILURES

            if len(attempts) >= limit:
                wait = max(wait, int(FAILURE_WINDOW - (now - attempts[0])) + 1)

        return wait


def _register_failure(keys: list[str]) -> None:

    now = time.time()

    with _failures_lock:

        for key in keys:
            _recent(key, now)
            _failures.setdefault(key, []).append(now)


def _clear_failures(keys: list[str]) -> None:

    with _failures_lock:

        for key in keys:
            _failures.pop(key, None)


_signups: dict[str, list[float]] = {}


def _signup_wait(ip: str) -> int:
    """Segundos que faltan para poder crear otra cuenta desde esta IP."""

    now = time.time()

    with _failures_lock:

        recent = [t for t in _signups.get(ip, []) if now - t < SIGNUP_WINDOW]

        if recent:
            _signups[ip] = recent
        else:
            _signups.pop(ip, None)

        if len(recent) >= MAX_SIGNUPS_PER_IP:
            return int(SIGNUP_WINDOW - (now - recent[0])) + 1

        return 0


def _record_signup(ip: str) -> None:

    with _failures_lock:
        _signups.setdefault(ip, []).append(time.time())


def reset_rate_limits() -> None:
    """Solo para las pruebas."""

    with _failures_lock:
        _failures.clear()
        _signups.clear()


# --------------------------------
# Validación
# --------------------------------

def clean_email(value) -> str:

    email = str(value or "").strip()

    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ValueError("Ingresá un email válido.")

    return email


def check_password(value) -> str:

    password = str(value or "")

    if len(password) < MIN_PASSWORD:
        raise ValueError(
            f"La contraseña tiene que tener al menos {MIN_PASSWORD} caracteres."
        )

    if len(password) > MAX_PASSWORD:
        raise ValueError(
            f"La contraseña no puede pasar de {MAX_PASSWORD} caracteres."
        )

    return password


def is_admin(user: Optional[dict]) -> bool:
    """
    El administrador puede forzar actualizaciones pesadas del catálogo y
    bajar los diagnósticos. Se define con TOMO_ADMIN_EMAILS (emails
    separados por coma). Si no está definida, es la primera cuenta
    (la dueña de la instalación).
    """

    if user is None:
        return False

    configured = [
        email.strip().lower()
        for email in os.environ.get("TOMO_ADMIN_EMAILS", "").split(",")
        if email.strip()
    ]

    if configured:
        return user["email"].lower() in configured

    return user["id"] == store.LEGACY_USER_ID


def public_user(user: dict) -> dict:

    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"] or user["email"].split("@")[0],
        "is_admin": is_admin(user),
    }


def _client_ip() -> str:

    return request.remote_addr or "?"


# --------------------------------
# Sesión
# --------------------------------

def login_user(user: dict) -> None:

    # Se descarta la sesión anterior para no heredar nada de ella.
    session.clear()

    session["uid"] = user["id"]
    session.permanent = True


def current_user() -> Optional[dict]:

    if "user" in g:
        return g.user

    user = None

    uid = session.get("uid")

    if isinstance(uid, int):
        user = store.get_user(uid)

        if user is None:
            session.clear()

    g.user = user

    return user


def login_required(view):
    """Las rutas con este decorador solo responden a usuarios con sesión."""

    @wraps(view)
    def wrapped(*args, **kwargs):

        user = current_user()

        if user is None:

            return jsonify({
                "ok": False,
                "auth": True,
                "error": "Tenés que iniciar sesión."
            }), 401

        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """Solo para el administrador (ver is_admin). Usar debajo de login_required."""

    @wraps(view)
    def wrapped(*args, **kwargs):

        if not is_admin(current_user()):

            return jsonify({
                "ok": False,
                "error": "Esto es solo para el administrador."
            }), 403

        return view(*args, **kwargs)

    return wrapped


def user_id() -> int:
    """Id del usuario de este pedido (solo dentro de login_required)."""

    user = current_user()

    assert user is not None

    return user["id"]


# --------------------------------
# Rutas
# --------------------------------

@bp.route("/me")
def me():

    user = current_user()

    return jsonify({
        "ok": True,
        "user": public_user(user) if user else None,
        # Para avisar en el formulario que hay datos viejos que conservar.
        "legacy_unclaimed": store.legacy_unclaimed() if user is None else False,
    })


@bp.route("/register", methods=["POST"])
def register():

    body = request.get_json(silent=True) or {}

    try:

        email = clean_email(body.get("email"))
        password = check_password(body.get("password"))

    except ValueError as error:

        return jsonify({"ok": False, "error": str(error)}), 400

    name = str(body.get("name") or "").strip()[:MAX_NAME]

    keys = [f"ip:{_client_ip()}"]

    # Evita que alguien cree cuentas en masa desde una misma IP, y que
    # pruebe emails ajenos para ver cuáles ya existen.
    wait = max(_blocked(keys), _signup_wait(_client_ip()))

    if wait:
        return _too_many(wait)

    try:

        user = store.create_user(
            email, name, generate_password_hash(password)
        )

    except store.EmailTaken:

        _register_failure(keys)

        return jsonify({
            "ok": False,
            "error": "Ya existe una cuenta con ese email."
        }), 409

    _record_signup(_client_ip())

    login_user(user)

    return jsonify({"ok": True, "user": public_user(user)}), 201


@bp.route("/login", methods=["POST"])
def login():

    body = request.get_json(silent=True) or {}

    email = str(body.get("email") or "").strip()[:254]
    password = str(body.get("password") or "")[:MAX_PASSWORD]

    keys = [f"ip:{_client_ip()}", f"email:{email.lower()}"]

    wait = _blocked(keys)

    if wait:
        return _too_many(wait)

    user = store.get_user_by_email(email) if email else None

    valid = check_password_hash(
        user["password_hash"] if user else _DUMMY_HASH,
        password
    )

    if not user or not valid:

        _register_failure(keys)

        return jsonify({
            "ok": False,
            "error": "Email o contraseña incorrectos."
        }), 401

    _clear_failures(keys)

    store.touch_login(user["id"])

    login_user(user)

    return jsonify({"ok": True, "user": public_user(user)})


@bp.route("/logout", methods=["POST"])
def logout():

    session.clear()

    return jsonify({"ok": True})


@bp.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    """Borra la cuenta y todos sus datos. Pide la contraseña."""

    body = request.get_json(silent=True) or {}

    user = current_user()

    assert user is not None

    if not check_password_hash(
        user["password_hash"], str(body.get("password") or "")
    ):

        return jsonify({
            "ok": False,
            "error": "La contraseña no es correcta."
        }), 403

    store.delete_user(user["id"])

    session.clear()

    return jsonify({"ok": True})


def _too_many(wait: int):

    minutes = max(1, round(wait / 60))

    return jsonify({
        "ok": False,
        "error": f"Demasiados intentos. Probá de nuevo en {minutes} min."
    }), 429


# --------------------------------
# Configuración de la app
# --------------------------------

def init_app(app: Flask, secret_key: str) -> None:

    app.secret_key = secret_key

    app.config.update(
        SESSION_COOKIE_NAME="tomo_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Detrás de HTTPS (en un servidor) la cookie viaja solo cifrada.
        SESSION_COOKIE_SECURE=os.environ.get("TOMO_HTTPS") == "1",
        PERMANENT_SESSION_LIFETIME=SESSION_DAYS * 86400,
    )

    app.register_blueprint(bp)

    @app.before_request
    def reject_cross_site_writes():

        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None

        origin = request.headers.get("Origin")

        if origin and urlparse(origin).netloc != request.host:

            return jsonify({
                "ok": False,
                "error": "Pedido rechazado."
            }), 403

        return None

    @app.after_request
    def security_headers(response):

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")

        # Con HTTPS, que el navegador no vuelva a intentar por HTTP.
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000"
            )

        # Las respuestas de la API son personales: que no se guarden.
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"

        return response


def load_secret_key() -> str:
    """
    La clave que firma las sesiones. Sale de SECRET_KEY si está definida;
    si no, se genera una vez y se guarda en la base, así las sesiones
    sobreviven a los reinicios y las comparten varios procesos.
    """

    from_env = os.environ.get("SECRET_KEY")

    if from_env:
        return from_env

    stored = store.get_meta("secret_key")

    if isinstance(stored, str) and len(stored) >= 32:
        return stored

    candidate = secrets.token_hex(32)

    store.set_meta_if_absent("secret_key", candidate)

    final = store.get_meta("secret_key")

    return final if isinstance(final, str) else candidate