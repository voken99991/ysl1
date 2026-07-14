"""
YSL website backend
Script 1: app.py

This replaces browser-only localStorage with shared Supabase storage.

Required Render environment variables:
- ADMIN_PASSWORD
- YSL_SECRET_KEY
- SUPABASE_URL
- SUPABASE_SECRET_KEY

Required Supabase table:

create table if not exists public.site_content (
  id integer primary key,
  content jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

insert into public.site_content (id, content)
values (1, '{}'::jsonb)
on conflict (id) do nothing;
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory, session
from dotenv import load_dotenv


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    static_folder=None,
)

app.secret_key = os.getenv(
    "YSL_SECRET_KEY",
    "replace-this-with-a-long-random-secret",
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.getenv("COOKIE_SECURE", "true").strip().lower() == "true"
    ),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "adminson")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)

DEFAULT_CONTENT: dict[str, Any] = {
    "hero": {
        "top": "YSL",
        "bottom": "REIMAGINED",
        "description": "The start of your MPS journey.",
        "primaryText": "View Fixtures",
        "primaryLink": "fixtures.html",
        "secondaryText": "League Standings",
        "secondaryLink": "standings.html",
    },
    "fixtures": [
        {
            "date": "DATE",
            "time": "TIME",
            "home": "TEAM NAME",
            "away": "TEAM NAME",
            "venue": "VENUE NAME",
        },
        {
            "date": "DATE",
            "time": "TIME",
            "home": "TEAM NAME",
            "away": "TEAM NAME",
            "venue": "VENUE NAME",
        },
        {
            "date": "DATE",
            "time": "TIME",
            "home": "TEAM NAME",
            "away": "TEAM NAME",
            "venue": "VENUE NAME",
        },
    ],
    "theme": {
        "lightBg": "#ffffff",
        "darkBg": "#14091f",
        "mainPurple": "#43118d",
        "accentPurple": "#8c45e9",
    },
    "settings": {
        "websiteName": "YSL",
        "leagueName": "Youth Soccer League",
        "discordInvite": "",
    },
}


def supabase_ready() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def supabase_request(
    method: str,
    path: str,
    *,
    body: Any | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    if not supabase_ready():
        raise RuntimeError(
            "SUPABASE_URL or SUPABASE_SECRET_KEY is missing."
        )

    url = f"{SUPABASE_URL}/rest/v1/{path}"
    payload = None

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Accept": "application/json",
    }

    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
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


def read_shared_content() -> dict[str, Any]:
    rows = supabase_request(
        "GET",
        "site_content?id=eq.1&select=content",
    )

    if not rows:
        write_shared_content(DEFAULT_CONTENT)
        return DEFAULT_CONTENT

    content = rows[0].get("content")

    if not isinstance(content, dict):
        return DEFAULT_CONTENT

    return merge_content(DEFAULT_CONTENT, content)


def write_shared_content(content: dict[str, Any]) -> None:
    supabase_request(
        "POST",
        "site_content?on_conflict=id",
        body={
            "id": 1,
            "content": content,
        },
        extra_headers={
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )


def merge_content(
    base: dict[str, Any],
    saved: dict[str, Any],
) -> dict[str, Any]:
    fixtures = saved.get("fixtures")

    if not isinstance(fixtures, list) or len(fixtures) != 3:
        fixtures = base["fixtures"]

    return {
        "hero": {
            **base["hero"],
            **(
                saved.get("hero")
                if isinstance(saved.get("hero"), dict)
                else {}
            ),
        },
        "fixtures": fixtures,
        "theme": {
            **base["theme"],
            **(
                saved.get("theme")
                if isinstance(saved.get("theme"), dict)
                else {}
            ),
        },
        "settings": {
            **base["settings"],
            **(
                saved.get("settings")
                if isinstance(saved.get("settings"), dict)
                else {}
            ),
        },
    }


def validate_content(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Content must be a JSON object.")

    encoded = json.dumps(value).encode("utf-8")

    if len(encoded) > 500_000:
        raise ValueError("Website content is too large.")

    return merge_content(DEFAULT_CONTENT, value)


def authenticated() -> bool:
    return bool(session.get("ysl_admin"))


def valid_csrf() -> bool:
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")

    return bool(
        supplied
        and expected
        and hmac.compare_digest(supplied, expected)
    )


@app.get("/")
def homepage():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/<path:filename>")
def website_file(filename: str):
    return send_from_directory(BASE_DIR, filename)


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "supabase_configured": supabase_ready(),
        }
    )


@app.get("/api/session")
def get_session():
    if not authenticated():
        return jsonify({"authenticated": False})

    session.setdefault("csrf_token", secrets.token_urlsafe(32))

    return jsonify(
        {
            "authenticated": True,
            "csrf": session["csrf_token"],
        }
    )


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", ""))

    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return jsonify({"error": "Incorrect password."}), 401

    session.clear()
    session["ysl_admin"] = True
    session["csrf_token"] = secrets.token_urlsafe(32)

    return jsonify(
        {
            "ok": True,
            "csrf": session["csrf_token"],
        }
    )


@app.post("/api/logout")
def logout():
    if not authenticated():
        return jsonify({"error": "Not logged in."}), 401

    if not valid_csrf():
        return jsonify({"error": "Invalid security token."}), 403

    session.clear()
    return jsonify({"ok": True})


@app.get("/api/site")
def get_site():
    try:
        content = read_shared_content()
    except RuntimeError as exc:
        app.logger.exception("Could not read shared website content.")
        return jsonify({"error": str(exc)}), 503

    return jsonify(content)


@app.post("/api/site")
def save_site():
    if not authenticated():
        return jsonify({"error": "Authentication required."}), 401

    if not valid_csrf():
        return jsonify({"error": "Invalid security token."}), 403

    try:
        content = validate_content(request.get_json(silent=True))
        write_shared_content(content)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        app.logger.exception("Could not save shared website content.")
        return jsonify({"error": str(exc)}), 503

    return jsonify({"ok": True})


@app.after_request
def add_headers(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"

    return response


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
