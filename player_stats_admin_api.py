"""
YSL Admin Player Statistics API

Register in app.py:

    from player_stats_admin_api import player_stats_admin_api
    app.register_blueprint(player_stats_admin_api)

Set this environment variable on Render:
    ADMIN_PASSWORD=your-private-password

The existing password "adminson" is used only as a fallback so the page works
immediately. Change it in Render for better security.
"""

from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from flask import Blueprint, jsonify, request


player_stats_admin_api = Blueprint(
    "player_stats_admin_api",
    __name__,
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "adminson")

STAT_FIELDS = {
    "appearances",
    "starts",
    "goals",
    "assists",
    "clean_sheets",
    "player_of_the_match",
    "yellow_cards",
    "red_cards",
    "wins",
    "draws",
    "losses",
    "minutes_played",
}


class StatsAPIError(RuntimeError):
    pass


def sb(
    method: str,
    path: str,
    *,
    body: Any | None = None,
    prefer: str | None = None,
) -> Any:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise StatsAPIError("Supabase environment variables are missing.")

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Accept": "application/json",
    }
    payload = None

    if body is not None:
        payload = json.dumps(body).encode("utf-8")
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
        try:
            parsed = json.loads(details)
            message = (
                parsed.get("message")
                or parsed.get("details")
                or parsed.get("hint")
                or details
            )
        except ValueError:
            message = details
        raise StatsAPIError(str(message)) from exc
    except urllib.error.URLError as exc:
        raise StatsAPIError(
            f"Could not connect to Supabase: {exc.reason}"
        ) from exc


def authorised() -> bool:
    supplied = request.headers.get("X-Admin-Password", "")
    return bool(
        ADMIN_PASSWORD
        and supplied
        and hmac.compare_digest(supplied, ADMIN_PASSWORD)
    )


def require_admin():
    if not authorised():
        return jsonify({"error": "Incorrect admin password."}), 401
    return None


def as_non_negative_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a whole number.") from exc

    if number < 0:
        raise ValueError(f"{field} cannot be negative.")
    return number


def safe_text(value: Any, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


@player_stats_admin_api.post("/api/admin/stats/login")
def login():
    denied = require_admin()
    if denied:
        return denied
    return jsonify({"ok": True})


@player_stats_admin_api.get("/api/admin/stats/players")
def players():
    denied = require_admin()
    if denied:
        return denied

    try:
        player_rows = sb(
            "GET",
            "players?"
            "select=id,username,roblox_user_id,avatar_url,current_team_id,"
            "team,player_role,rating,market_value,position,availability,note,"
            "active,is_active"
            "&order=username.asc"
            "&limit=5000",
        ) or []

        team_rows = sb(
            "GET",
            "teams?select=id,name,logo_url&order=name.asc",
        ) or []
        teams = {
            str(team["id"]): team
            for team in team_rows
            if team.get("id")
        }

        stats_rows = sb(
            "GET",
            "player_stats?select=*",
        ) or []
        stats = {
            str(row["player_id"]): row
            for row in stats_rows
            if row.get("player_id")
        }

        result = []
        for player in player_rows:
            team = teams.get(str(player.get("current_team_id")))
            player_id = str(player.get("id"))
            result.append({
                "id": player.get("id"),
                "username": player.get("username") or "Unknown Player",
                "robloxId": player.get("roblox_user_id"),
                "avatarUrl": player.get("avatar_url") or "",
                "team": (
                    team.get("name")
                    if team
                    else player.get("team") or "Free Agent"
                ),
                "teamLogo": team.get("logo_url") if team else "",
                "role": player.get("player_role") or "Player",
                "rating": int(player.get("rating") or 64),
                "marketValue": int(
                    player.get("market_value") or 50000
                ),
                "position": (
                    player.get("position")
                    or "Position not assigned"
                ),
                "availability": (
                    player.get("availability")
                    or "Available"
                ),
                "note": player.get("note") or "",
                "active": bool(
                    player.get(
                        "active",
                        player.get("is_active", True),
                    )
                ),
                "stats": {
                    field: int(
                        stats.get(player_id, {}).get(field) or 0
                    )
                    for field in STAT_FIELDS
                },
            })

        return jsonify({"players": result})
    except StatsAPIError as exc:
        return jsonify({"error": str(exc)}), 503


@player_stats_admin_api.put("/api/admin/stats/players/<player_id>")
def update_player(player_id: str):
    denied = require_admin()
    if denied:
        return denied

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid request body."}), 400

    safe_id = urllib.parse.quote(str(player_id), safe="")

    try:
        player_rows = sb(
            "GET",
            f"players?id=eq.{safe_id}&select=id,username",
        ) or []
        if not player_rows:
            return jsonify({"error": "Player not found."}), 404

        stat_payload = {"player_id": player_id}
        for field in STAT_FIELDS:
            stat_payload[field] = as_non_negative_int(
                body.get(field, 0),
                field.replace("_", " ").title(),
            )
        stat_payload["updated_at"] = "now()"

        stats_rows = sb(
            "POST",
            "player_stats?on_conflict=player_id",
            body=stat_payload,
            prefer="resolution=merge-duplicates,return=representation",
        ) or []

        rating = as_non_negative_int(body.get("rating", 64), "Rating")
        if rating > 99:
            return jsonify({"error": "Rating cannot be above 99."}), 400

        market_value = as_non_negative_int(
            body.get("marketValue", 50000),
            "Market value",
        )

        player_patch = {
            "rating": rating,
            "market_value": market_value,
            "position": safe_text(body.get("position"), 50) or None,
            "availability": (
                safe_text(body.get("availability"), 50)
                or "Available"
            ),
            "note": safe_text(body.get("note"), 500) or None,
        }

        updated_players = sb(
            "PATCH",
            f"players?id=eq.{safe_id}",
            body=player_patch,
            prefer="return=representation",
        ) or []

        return jsonify({
            "ok": True,
            "player": updated_players[0] if updated_players else {},
            "stats": stats_rows[0] if stats_rows else stat_payload,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except StatsAPIError as exc:
        return jsonify({"error": str(exc)}), 503


@player_stats_admin_api.post(
    "/api/admin/stats/players/<player_id>/increment"
)
def increment_player(player_id: str):
    """
    Optional quick-add endpoint.
    Body example: {"goals": 1, "assists": 1, "appearances": 1}
    """
    denied = require_admin()
    if denied:
        return denied

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid request body."}), 400

    safe_id = urllib.parse.quote(str(player_id), safe="")

    try:
        rows = sb(
            "GET",
            f"player_stats?player_id=eq.{safe_id}&select=*",
        ) or []
        current = rows[0] if rows else {"player_id": player_id}
        updated = {"player_id": player_id}

        for field in STAT_FIELDS:
            old_value = int(current.get(field) or 0)
            addition = as_non_negative_int(body.get(field, 0), field)
            updated[field] = old_value + addition

        updated["updated_at"] = "now()"
        result = sb(
            "POST",
            "player_stats?on_conflict=player_id",
            body=updated,
            prefer="resolution=merge-duplicates,return=representation",
        ) or []

        return jsonify({
            "ok": True,
            "stats": result[0] if result else updated,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except StatsAPIError as exc:
        return jsonify({"error": str(exc)}), 503
