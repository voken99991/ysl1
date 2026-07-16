from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, session
from transfer_api import transfer_api

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("YSL_SECRET_KEY", "replace-this-secret")
app.register_blueprint(transfer_api)

app.config.update(
    SESSION_COOKIE_NAME="ysl_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_REFRESH_EACH_REQUEST=True,
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


def supabase_request(
    method: str,
    path: str,
    *,
    body: Any | None = None,
    prefer: str | None = None,
) -> Any:
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is missing.")

    if not SUPABASE_SECRET_KEY:
        raise RuntimeError(
            "SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY is missing."
        )

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Accept": "application/json",
    }

    payload = None

    if body is not None:
        payload = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
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
        with urllib.request.urlopen(req, timeout=25) as response:
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
        raise RuntimeError("Supabase did not confirm the site-content save.")


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

    return "pbkdf2_sha256${iterations}${salt}${digest}".format(
        iterations=PASSWORD_ITERATIONS,
        salt=base64.urlsafe_b64encode(salt).decode("ascii"),
        digest=base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)

        if algorithm != "pbkdf2_sha256":
            return False

        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )

        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def get_player_by_username(username: str) -> dict[str, Any] | None:
    key = urllib.parse.quote(normalise_username(username), safe="")

    rows = supabase_request(
        "GET",
        f"players?username_key=eq.{key}&select=*",
    )

    return rows[0] if rows else None


def get_player_by_discord_id(discord_id: str) -> dict[str, Any] | None:
    value = urllib.parse.quote(str(discord_id), safe="")

    rows = supabase_request(
        "GET",
        f"players?discord_id=eq.{value}&select=*",
    )

    return rows[0] if rows else None


def get_player_by_roblox_id(roblox_user_id: str) -> dict[str, Any] | None:
    value = urllib.parse.quote(str(roblox_user_id), safe="")

    rows = supabase_request(
        "GET",
        f"players?roblox_user_id=eq.{value}&select=*",
    )

    return rows[0] if rows else None


def create_or_reset_player(
    *,
    username: str,
    discord_id: str,
    roblox_user_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    clean_username = username.strip()

    if not clean_username:
        raise ValueError("Roblox username is required.")

    temporary_password = generate_temporary_password()

    payload = {
        "username": clean_username,
        "username_key": normalise_username(clean_username),
        "discord_id": str(discord_id),
        "roblox_user_id": str(roblox_user_id or ""),
        "password_hash": hash_password(temporary_password),
        "must_change_password": True,
        "is_active": True,
    }

    existing = get_player_by_discord_id(str(discord_id))

    if existing:
        player_id = urllib.parse.quote(str(existing["id"]), safe="")

        rows = supabase_request(
            "PATCH",
            f"players?id=eq.{player_id}",
            body=payload,
            prefer="return=representation",
        )
    else:
        rows = supabase_request(
            "POST",
            "players",
            body=payload,
            prefer="return=representation",
        )

    saved_player = rows[0] if isinstance(rows, list) and rows else payload
    return temporary_password, saved_player


def activate_playersheet_player(
    *,
    discord_id: str,
    roblox_username: str,
    roblox_user_id: str,
) -> dict[str, Any]:
    existing = get_player_by_roblox_id(roblox_user_id)

    payload = {
        "username": roblox_username,
        "username_key": normalise_username(roblox_username),
        "discord_id": str(discord_id),
        "roblox_user_id": str(roblox_user_id),
        "active": True,
        "is_active": True,
        "left_at": None,
        "avatar_url": get_roblox_avatar_headshot(roblox_user_id),
        "updated_at": utc_now_iso(),
    }

    if existing:
        player_id = urllib.parse.quote(str(existing["id"]), safe="")

        rows = supabase_request(
            "PATCH",
            f"players?id=eq.{player_id}",
            body=payload,
            prefer="return=representation",
        )
    else:
        payload.update({
            "rating": 64,
            "market_value": 50000,
            "team": "Free Agent",
            "player_role": "Player",
            "releases": 1,
            "avatar_url": get_roblox_avatar_headshot(roblox_user_id),
            "joined_at": utc_now_iso(),
            "must_change_password": True,
            "password_hash": hash_password(generate_temporary_password()),
        })

        rows = supabase_request(
            "POST",
            "players",
            body=payload,
            prefer="return=representation",
        )

    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Supabase did not confirm player activation.")

    return rows[0]


def deactivate_playersheet_player(discord_id: str) -> dict[str, Any] | None:
    existing = get_player_by_discord_id(discord_id)

    if not existing:
        return None

    player_id = urllib.parse.quote(str(existing["id"]), safe="")

    rows = supabase_request(
        "PATCH",
        f"players?id=eq.{player_id}",
        body={
            "active": False,
            "is_active": False,
            "left_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
        prefer="return=representation",
    )

    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Supabase did not confirm player deactivation.")

    return rows[0]



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_roblox_avatar_headshot(roblox_user_id: str) -> str:
    """
    Resolve the current Roblox avatar headshot for a user ID.

    Returns an empty string if Roblox has not generated the thumbnail yet
    or if the request fails.
    """
    user_id = str(roblox_user_id).strip()

    if not user_id.isdigit():
        return ""

    query = urllib.parse.urlencode({
        "userIds": user_id,
        "size": "150x150",
        "format": "Png",
        "isCircular": "false",
    })

    req = urllib.request.Request(
        f"https://thumbnails.roblox.com/v1/users/avatar-headshot?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "YSL-Website/1.0",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            body = json.loads(response.read().decode("utf-8"))

        items = body.get("data") if isinstance(body, dict) else None

        if not isinstance(items, list) or not items:
            return ""

        image_url = items[0].get("imageUrl")
        return str(image_url or "")

    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        TypeError,
    ):
        return ""

def admin_logged_in() -> bool:
    return bool(session.get("admin"))


def player_logged_in() -> bool:
    return bool(session.get("player_id"))


def admin_csrf_valid() -> bool:
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = session.get("admin_csrf", "")

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


def clear_admin_session() -> None:
    session.pop("admin", None)
    session.pop("admin_csrf", None)


def clear_player_session() -> None:
    for key in (
        "player_id",
        "player_username",
        "player_discord_id",
        "must_change_password",
    ):
        session.pop(key, None)


@app.get("/")
def homepage():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/<path:filename>")
def serve_file(filename: str):
    return send_from_directory(BASE_DIR, filename)


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "supabaseConfigured": bool(
            SUPABASE_URL and SUPABASE_SECRET_KEY
        ),
        "botApiConfigured": bool(BOT_API_KEY),
    })


@app.get("/api/session")
def get_admin_session():
    if not admin_logged_in():
        return jsonify({"authenticated": False})

    session.setdefault("admin_csrf", secrets.token_urlsafe(32))
    session.permanent = True

    return jsonify({
        "authenticated": True,
        "csrf": session["admin_csrf"],
    })


@app.post("/api/login")
def admin_login():
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password", ""))

    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return jsonify({"error": "Incorrect password."}), 401

    session.permanent = True
    session["admin"] = True
    session["admin_csrf"] = secrets.token_urlsafe(32)

    return jsonify({
        "ok": True,
        "csrf": session["admin_csrf"],
    })


@app.post("/api/logout")
def admin_logout():
    if not admin_logged_in():
        return jsonify({"error": "Not logged in."}), 401

    if not admin_csrf_valid():
        return jsonify({"error": "Invalid security token."}), 403

    clear_admin_session()
    return jsonify({"ok": True})


@app.get("/api/site")
def get_site():
    try:
        return jsonify(read_site_content())
    except RuntimeError as exc:
        app.logger.exception("Failed to read site content.")
        return jsonify({"error": str(exc)}), 503


@app.post("/api/site")
def save_site():
    if not admin_logged_in():
        return jsonify({"error": "Authentication required."}), 401

    if not admin_csrf_valid():
        return jsonify({"error": "Invalid security token."}), 403

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({
            "error": "Website data must be a JSON object."
        }), 400

    try:
        save_site_content(payload)

        return jsonify({
            "ok": True,
            "savedKeys": sorted(payload.keys()),
        })
    except RuntimeError as exc:
        app.logger.exception("Failed to save site content.")
        return jsonify({"error": str(exc)}), 503


@app.get("/api/players")
def public_playersheet():
    try:
        rows = supabase_request(
            "GET",
            "players?select=id,username,roblox_user_id,team,player_role,"
            "rating,market_value,releases,avatar_url,active,is_active"
            "&order=rating.desc,username.asc",
        )

        players = []

        for row in rows or []:
            players.append({
                "id": row.get("id"),
                "username": row.get("username"),
                "roblox_user_id": row.get("roblox_user_id"),
                "team": row.get("team") or "Free Agent",
                "role": row.get("player_role") or "Player",
                "rating": row.get("rating") or 64,
                "value": row.get("market_value") or 50000,
                "releases": (
                    1
                    if row.get("releases") is None
                    else max(0, int(row.get("releases")))
                ),
                "avatarUrl": (
                    row.get("avatar_url")
                    or get_roblox_avatar_headshot(
                        str(row.get("roblox_user_id") or "")
                    )
                ),
                "active": bool(
                    row.get("active", row.get("is_active", True))
                ),
            })

        return jsonify({"players": players})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.get("/api/player/session")
def get_player_session():
    if not player_logged_in():
        return jsonify({"authenticated": False})

    session.permanent = True

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
        return jsonify({
            "error": "Incorrect username or password."
        }), 401

    session.permanent = True
    clear_player_session()

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
    clear_player_session()
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

    try:
        player = get_player_by_username(
            str(session["player_username"])
        )

        if not player or not verify_password(
            current_password,
            str(player.get("password_hash", "")),
        ):
            return jsonify({
                "error": "Current password is incorrect."
            }), 401

        player_id = urllib.parse.quote(str(player["id"]), safe="")

        rows = supabase_request(
            "PATCH",
            f"players?id=eq.{player_id}",
            body={
                "password_hash": hash_password(new_password),
                "must_change_password": False,
            },
            prefer="return=representation",
        )

        if not isinstance(rows, list) or not rows:
            raise RuntimeError(
                "Supabase did not confirm the password update."
            )

        session["must_change_password"] = False
        session.modified = True

        return jsonify({"ok": True})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


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
        temporary_password, saved_player = create_or_reset_player(
            username=username,
            discord_id=discord_id,
            roblox_user_id=roblox_user_id,
        )

        return jsonify({
            "ok": True,
            "username": saved_player.get("username", username),
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
            return jsonify({
                "error": "Player account not found."
            }), 404

        temporary_password, saved_player = create_or_reset_player(
            username=str(player["username"]),
            discord_id=discord_id,
            roblox_user_id=str(player.get("roblox_user_id", "")),
        )

        return jsonify({
            "ok": True,
            "username": saved_player.get(
                "username",
                player["username"],
            ),
            "temporaryPassword": temporary_password,
            "mustChangePassword": True,
        })
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.post("/api/bot/playersheet/activate")
def bot_playersheet_activate():
    if not bot_authorised():
        return jsonify({"error": "Invalid bot API key."}), 401

    payload = request.get_json(silent=True) or {}

    discord_id = str(payload.get("discordId", "")).strip()
    roblox_username = str(payload.get("robloxUsername", "")).strip()
    roblox_user_id = str(payload.get("robloxId", "")).strip()

    if not discord_id or not roblox_username or not roblox_user_id:
        return jsonify({
            "error": "discordId, robloxUsername and robloxId are required."
        }), 400

    try:
        player = activate_playersheet_player(
            discord_id=discord_id,
            roblox_username=roblox_username,
            roblox_user_id=roblox_user_id,
        )

        return jsonify({
            "ok": True,
            "player": {
                "id": player.get("id"),
                "username": player.get("username"),
                "robloxId": player.get("roblox_user_id"),
                "rating": player.get("rating", 64),
                "value": player.get("market_value", 50000),
                "team": player.get("team", "Free Agent"),
                "role": player.get("player_role", "Player"),
                "releases": player.get("releases", 1),
                "avatarUrl": player.get("avatar_url", ""),
                "active": True,
            },
        })
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.post("/api/bot/playersheet/deactivate")
def bot_playersheet_deactivate():
    if not bot_authorised():
        return jsonify({"error": "Invalid bot API key."}), 401

    payload = request.get_json(silent=True) or {}
    discord_id = str(payload.get("discordId", "")).strip()

    if not discord_id:
        return jsonify({"error": "discordId is required."}), 400

    try:
        player = deactivate_playersheet_player(discord_id)

        return jsonify({
            "ok": True,
            "found": player is not None,
        })
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.after_request
def no_api_cache(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        debug=False,
    )
