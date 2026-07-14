from __future__ import annotations

import hmac
import json
import os
import secrets
import urllib.error
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
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)


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
    # First try to update the existing row.
    updated_rows = supabase_request(
        "PATCH",
        "site_content?id=eq.1",
        body={"content": content},
        prefer="return=representation",
    )

    if isinstance(updated_rows, list) and updated_rows:
        return

    # If row 1 does not exist, create it.
    inserted_rows = supabase_request(
        "POST",
        "site_content",
        body={"id": 1, "content": content},
        prefer="return=representation",
    )

    if not isinstance(inserted_rows, list) or not inserted_rows:
        raise RuntimeError("Supabase did not confirm the save.")


def is_admin() -> bool:
    return bool(session.get("admin"))


def csrf_valid() -> bool:
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")

    return bool(
        supplied
        and expected
        and hmac.compare_digest(supplied, expected)
    )


@app.get("/")
def homepage():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "supabaseConfigured": bool(
                SUPABASE_URL and SUPABASE_SECRET_KEY
            ),
        }
    )


@app.get("/api/session")
def get_session():
    if not is_admin():
        return jsonify({"authenticated": False})

    session.setdefault("csrf", secrets.token_urlsafe(32))

    return jsonify(
        {
            "authenticated": True,
            "csrf": session["csrf"],
        }
    )


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password", ""))

    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return jsonify({"error": "Incorrect password."}), 401

    session.clear()
    session["admin"] = True
    session["csrf"] = secrets.token_urlsafe(32)

    return jsonify(
        {
            "ok": True,
            "csrf": session["csrf"],
        }
    )


@app.post("/api/logout")
def logout():
    if not is_admin():
        return jsonify({"error": "Not logged in."}), 401

    if not csrf_valid():
        return jsonify({"error": "Invalid security token."}), 403

    session.clear()
    return jsonify({"ok": True})


@app.get("/api/site")
def get_site():
    try:
        content = read_site_content()
        return jsonify(content)

    except RuntimeError as exc:
        app.logger.exception("Failed to read site content.")
        return jsonify({"error": str(exc)}), 503


@app.post("/api/site")
def save_site():
    if not is_admin():
        return jsonify({"error": "Authentication required."}), 401

    if not csrf_valid():
        return jsonify({"error": "Invalid security token."}), 403

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid website data."}), 400

    try:
        # Save the complete object unchanged:
        # hero, teams, fixtures, standings, theme, settings, and future fields.
        save_site_content(payload)

        # Read it back to verify that Supabase actually stored it.
        saved = read_site_content()

        if saved != payload:
            return jsonify({
                "error": "Supabase responded, but the saved data did not match."
            }), 500

        return jsonify({
            "ok": True,
            "savedKeys": sorted(payload.keys()),
        })

    except RuntimeError as exc:
        app.logger.exception("Failed to save site content.")
        return jsonify({"error": str(exc)}), 503


@app.get("/<path:filename>")
def serve_file(filename: str):
    return send_from_directory(BASE_DIR, filename)


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
