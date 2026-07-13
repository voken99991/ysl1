from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from werkzeug.utils import secure_filename

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_FILE = DATA_DIR / "site.json"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("YSL_SECRET_KEY", "change-this-secret-before-production")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("COOKIE_SECURE", "false").lower() == "true"

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "adminson")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "svg"}
ALLOWED_THEME_KEYS = {
    "--bg", "--panel", "--line", "--text", "--muted",
    "--purple", "--violet", "--pink", "--green",
    "--radius", "--font-body", "--font-display",
}

login_attempts: dict[str, deque[float]] = defaultdict(deque)


def asset_version() -> str:
    digest = hashlib.sha256()
    for path in (
        BASE_DIR / "templates" / "index.html",
        BASE_DIR / "static" / "css" / "site.css",
        BASE_DIR / "static" / "js" / "site.js",
        BASE_DIR / "static" / "js" / "editor.js",
    ):
        try:
            digest.update(path.read_bytes())
        except FileNotFoundError:
            continue
    return digest.hexdigest()[:16]


@app.context_processor
def inject_asset_version() -> dict[str, str]:
    return {"asset_version": asset_version()}


def read_site() -> dict[str, Any]:
    if not DATA_FILE.exists():
        return {"html": "", "theme": {}, "updated_at": None}

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"html": "", "theme": {}, "updated_at": None}

    return {
        "html": data.get("html", ""),
        "theme": data.get("theme", {}),
        "updated_at": data.get("updated_at"),
    }


def write_site(data: dict[str, Any]) -> None:
    temp = DATA_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(DATA_FILE)


def is_admin() -> bool:
    return bool(session.get("admin"))


def csrf_valid() -> bool:
    token = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    return bool(token and expected and hmac.compare_digest(token, expected))


def require_admin() -> tuple[dict[str, Any], int] | None:
    if not is_admin():
        return {"error": "Authentication required."}, 401
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not csrf_valid():
        return {"error": "Invalid CSRF token."}, 403
    return None


def extension_allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = login_attempts[ip]

    while attempts and now - attempts[0] > 600:
        attempts.popleft()

    return len(attempts) >= 8


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/session")
def api_session():
    if not is_admin():
        return jsonify({"authenticated": False})

    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)

    return jsonify({
        "authenticated": True,
        "csrf": session["csrf"],
    })


@app.post("/api/login")
def api_login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()

    if rate_limited(ip):
        return jsonify({"error": "Too many login attempts. Try again later."}), 429

    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password", ""))

    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        login_attempts[ip].append(time.time())
        return jsonify({"error": "Incorrect password."}), 401

    login_attempts[ip].clear()
    session.clear()
    session["admin"] = True
    session["csrf"] = secrets.token_urlsafe(32)

    return jsonify({
        "ok": True,
        "csrf": session["csrf"],
    })


@app.post("/api/logout")
def api_logout():
    denied = require_admin()
    if denied:
        return jsonify(denied[0]), denied[1]

    session.clear()
    return jsonify({"ok": True})


@app.get("/api/site")
def api_site_get():
    return jsonify(read_site())


@app.post("/api/site")
def api_site_save():
    denied = require_admin()
    if denied:
        return jsonify(denied[0]), denied[1]

    payload = request.get_json(silent=True) or {}
    html = payload.get("html", "")
    theme = payload.get("theme", {})

    if not isinstance(html, str):
        return jsonify({"error": "HTML must be a string."}), 400
    if len(html.encode("utf-8")) > 2_500_000:
        return jsonify({"error": "The page is too large to save."}), 413
    if not isinstance(theme, dict):
        return jsonify({"error": "Theme must be an object."}), 400

    safe_theme = {
        key: str(value)[:250]
        for key, value in theme.items()
        if key in ALLOWED_THEME_KEYS
    }

    saved = {
        "html": html,
        "theme": safe_theme,
        "updated_at": int(time.time()),
    }
    write_site(saved)

    return jsonify({
        "ok": True,
        "updated_at": saved["updated_at"],
    })


@app.post("/api/reset")
def api_reset():
    denied = require_admin()
    if denied:
        return jsonify(denied[0]), denied[1]

    if DATA_FILE.exists():
        DATA_FILE.unlink()

    return jsonify({"ok": True})


@app.post("/api/upload")
def api_upload():
    denied = require_admin()
    if denied:
        return jsonify(denied[0]), denied[1]

    uploaded = request.files.get("file")

    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "No file was uploaded."}), 400

    if not extension_allowed(uploaded.filename):
        return jsonify({"error": "Unsupported file type."}), 400

    original = secure_filename(uploaded.filename)
    extension = original.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{extension}"
    target = UPLOAD_DIR / filename
    uploaded.save(target)

    return jsonify({
        "ok": True,
        "url": f"/static/uploads/{filename}",
        "name": original,
    })


@app.after_request
def disable_api_cache(response):
    if request.path == "/" or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
