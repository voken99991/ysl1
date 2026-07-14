from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, session

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("YSL_SECRET_KEY", "replace-this-secret")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "adminson")
BOT_API_KEY = os.getenv("BOT_API_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)

PASSWORD_ITERATIONS = 310_000
TEMP_PASSWORD_LENGTH = 8


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def supabase_request(
    method: str,
    path: str,
    *,
    body: Any | None = None,
    prefer: str | None = None,
) -> Any:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError(
            "SUPABASE_URL or SUPABASE_SECRET_KEY is missing in Render."
        )

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Accept": "application/json",
    }

    payload = None

    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    if prefer:
        headers["Prefer"] = prefer

    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        data=payload,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None

    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Supabase returned HTTP {exc.code}: {details}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not connect to Supabase: {exc.reason}"
        ) from exc


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def normalise_username(username: str) -> str:
    return username.strip().lower()


def generate_temporary_password(length: int = TEMP_PASSWORD_LENGTH) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )

    return "pbkdf2_sha256${iterations}${salt}${hash}".format(
        iterations=PASSWORD_ITERATIONS,
        salt=base64.urlsafe_b64encode(salt).decode("ascii"),
        hash=base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored.split("$", 3)

        if algorithm != "pbkdf2_sha256":
            return False

        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_b64.encode("ascii"))

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )

        return hmac.compare_digest(actual, expected)

    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Website content
# ---------------------------------------------------------------------------

def read_site_content() -> dict[str, Any]:
    rows = supabase_request(
        "GET",
        "site_content?id=eq.1&select=content",
    )

    if not rows:
        return {}

    content = rows[0].get("content")
    return content if isinstance(content, dict) else {}


def save_site_content(content: dict[str, Any]) -> None:
    updated = supabase_request(
        "PATCH",
        "site_content?id=eq.1",
        body={"content": content},
        prefer="return=representation",
    )

    if isinstance(updated, list) and updated:
        return

    inserted = supabase_request(
        "POST",
        "site_content",
        body={"id": 1, "content": content},
        prefer="return=representation",
    )

    if not isinstance(inserted, list) or not inserted:
        raise RuntimeError("Supabase did not confirm the save.")


# ---------------------------------------------------------------------------
# Player accounts
# ---------------------------------------------------------------------------

def get_player_by_username(username: str) -> dict[str, Any] | None:
    key = urllib.parse.quote(normalise_username(username), safe="")

    rows = supabase_request(
        "GET",
        f"players?username_key=eq.{key}&select=*",
    )

    if not rows:
        return None

    return rows[0]


def get_player_by_discord_id(discord_id: str) -> dict[str, Any] | None:
    value = urllib.parse.quote(str(discord_id), safe="")

    rows = supabase_request(
        "GET",
        f"players?discord_id=eq.{value}&select=*",
    )

    if not rows:
        return None

    return rows[0]


def create_or_reset_player(
    *,
    username: str,
    discord_id: str,
    roblox_user_id: str | None = None,
) -> str:
    clean_username = username.strip()

    if not clean_username:
        raise ValueError("Roblox username is required.")

    temporary_password = generate_temporary_password()
    password_hash = hash_password(temporary_password)

    payload = {
        "username": clean_username,
        "username_key": normalise_username(clean_username),
        "discord_id": str(discord_id),
        "roblox_user_id": str(roblox_user_id or ""),
        "password_hash": password_hash,
        "must_change_password": True,
        "is_active": True,
    }

    existing = get_player_by_discord_id(str(discord_id))

    if existing:
        player_id = urllib.parse.quote(str(existing["id"]), safe="")

        supabase_request(
            "PATCH",
            f"players?id=eq.{player_id}",
            body=payload,
            prefer="return=representation",
        )
    else:
        supabase_request(
            "POST",
            "players",
            body=payload,
            prefer="return=representation",
        )

    return temporary_password


def queue_player_notification(
    *,
    discord_id: str,
    notification_type: str,
    message: str,
) -> None:
    supabase_request(
        "POST",
        "player_notifications",
        body={
            "discord_id": str(discord_id),
            "notification_type": notification_type,
            "message": message,
            "delivered": False,
        },
        prefer="return=minimal",
    )


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def admin_logged_in() -> bool:
    return bool(session.get("admin"))


def player_logged_in() -> bool:
    return bool(session.get("player_id"))


def csrf_valid() -> bool:
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")

    return bool(
        supplied
        and expected
        and hmac.compare_digest(supplied, expected)
    )


def bot_authorised() -> bool:
    supplied = request.headers.get("X-Bot-Key", "")

    return bool(
        BOT_API_KEY
        and supplied
        and hmac.compare_digest(supplied, BOT_API_KEY)
    )


# ---------------------------------------------------------------------------
# Static website
# ---------------------------------------------------------------------------

@app.get("/")
def homepage():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/<path:filename>")
def serve_file(filename: str):
    return send_from_directory(BASE_DIR, filename)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "supabaseConfigured": bool(
            SUPABASE_URL and SUPABASE_SECRET_KEY
        ),
        "botApiConfigured": bool(BOT_API_KEY),
    })


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

@app.get("/api/session")
def admin_session():
    if not admin_logged_in():
        return jsonify({"authenticated": False})

    session.setdefault("csrf", secrets.token_urlsafe(32))

    return jsonify({
        "authenticated": True,
        "csrf": session["csrf"],
    })


@app.post("/api/login")
def admin_login():
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password", ""))

    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return jsonify({"error": "Incorrect password."}), 401

    session.clear()
    session["admin"] = True
    session["csrf"] = secrets.token_urlsafe(32)

    return jsonify({
        "ok": True,
        "csrf": session["csrf"],
    })


@app.post("/api/logout")
def admin_logout():
    if not admin_logged_in():
        return jsonify({"error": "Not logged in."}), 401

    if not csrf_valid():
        return jsonify({"error": "Invalid security token."}), 403

    session.clear()
    return jsonify({"ok": True})


@app.get("/api/site")
def get_site():
    try:
        return jsonify(read_site_content())
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.post("/api/site")
def save_site():
    if not admin_logged_in():
        return jsonify({"error": "Authentication required."}), 401

    if not csrf_valid():
        return jsonify({"error": "Invalid security token."}), 403

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid website data."}), 400

    try:
        save_site_content(payload)
        return jsonify({"ok": True})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


# ---------------------------------------------------------------------------
# Player website API
# ---------------------------------------------------------------------------

@app.get("/api/player/session")
def player_session():
    if not player_logged_in():
        return jsonify({"authenticated": False})

    return jsonify({
        "authenticated": True,
        "player": {
            "id": session["player_id"],
            "username": session["player_username"],
            "mustChangePassword": bool(
                session.get("must_change_password")
            ),
        },
    })


@app.post("/api/player/login")
def player_login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))

    if not username or not password:
        return jsonify({
            "error": "Username and password are required."
        }), 400

    try:
        player = get_player_by_username(username)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503

    if (
        not player
        or not player.get("is_active", True)
        or not verify_password(
            password,
            str(player.get("password_hash", "")),
        )
    ):
        return jsonify({"error": "Incorrect username or password."}), 401

    session.clear()
    session["player_id"] = player["id"]
    session["player_username"] = player["username"]
    session["player_discord_id"] = player["discord_id"]
    session["must_change_password"] = bool(
        player.get("must_change_password")
    )

    return jsonify({
        "ok": True,
        "player": {
            "username": player["username"],
            "mustChangePassword": bool(
                player.get("must_change_password")
            ),
        },
    })


@app.post("/api/player/logout")
def player_logout():
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/player/change-password")
def player_change_password():
    if not player_logged_in():
        return jsonify({"error": "Login required."}), 401

    payload = request.get_json(silent=True) or {}
    current_password = str(payload.get("currentPassword", ""))
    new_password = str(payload.get("newPassword", ""))

    if len(new_password) < 8:
        return jsonify({
            "error": "New password must be at least 8 characters."
        }), 400

    username = str(session["player_username"])

    try:
        player = get_player_by_username(username)

        if not player or not verify_password(
            current_password,
            str(player.get("password_hash", "")),
        ):
            return jsonify({
                "error": "Current password is incorrect."
            }), 401

        player_id = urllib.parse.quote(str(player["id"]), safe="")

        supabase_request(
            "PATCH",
            f"players?id=eq.{player_id}",
            body={
                "password_hash": hash_password(new_password),
                "must_change_password": False,
            },
            prefer="return=representation",
        )

        session["must_change_password"] = False

        # The bot should DM a confirmation, not the actual password.
        # If the player forgets it, the bot can generate a new temporary one.
        queue_player_notification(
            discord_id=str(player["discord_id"]),
            notification_type="password_changed",
            message=(
                f"Your YSL website password was changed successfully "
                f"for Roblox account {player['username']}."
            ),
        )

        return jsonify({"ok": True})

    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


# ---------------------------------------------------------------------------
# Discord bot API
# ---------------------------------------------------------------------------

@app.post("/api/bot/create-player")
def bot_create_player():
    if not bot_authorised():
        return jsonify({"error": "Invalid bot API key."}), 401

    payload = request.get_json(silent=True) or {}

    username = str(payload.get("username", "")).strip()
    discord_id = str(payload.get("discordId", "")).strip()
    roblox_user_id = str(payload.get("robloxUserId", "")).strip()

    if not username or not discord_id:
        return jsonify({
            "error": "username and discordId are required."
        }), 400

    try:
        temporary_password = create_or_reset_player(
            username=username,
            discord_id=discord_id,
            roblox_user_id=roblox_user_id,
        )

        return jsonify({
            "ok": True,
            "username": username,
            "temporaryPassword": temporary_password,
            "mustChangePassword": True,
        })

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.post("/api/bot/reset-player-password")
def bot_reset_player_password():
    if not bot_authorised():
        return jsonify({"error": "Invalid bot API key."}), 401

    payload = request.get_json(silent=True) or {}
    discord_id = str(payload.get("discordId", "")).strip()

    if not discord_id:
        return jsonify({"error": "discordId is required."}), 400

    try:
        player = get_player_by_discord_id(discord_id)

        if not player:
            return jsonify({"error": "Player account not found."}), 404

        temporary_password = create_or_reset_player(
            username=str(player["username"]),
            discord_id=discord_id,
            roblox_user_id=str(player.get("roblox_user_id", "")),
        )

        return jsonify({
            "ok": True,
            "username": player["username"],
            "temporaryPassword": temporary_password,
            "mustChangePassword": True,
        })

    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.after_request
def no_api_cache(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"

    return response


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        debug=False,
    )
