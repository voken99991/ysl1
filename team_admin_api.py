"""
YSL team administration API.

Place beside app.py and register:

    from team_admin_api import team_admin_api
    app.register_blueprint(team_admin_api)
"""

from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request


team_admin_api = Blueprint("team_admin_api", __name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)
BOT_API_KEY = os.getenv("BOT_API_KEY", "")


class APIError(RuntimeError):
    pass


def sb(
    method: str,
    path: str,
    *,
    body: Any | None = None,
    prefer: str | None = None,
) -> Any:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise APIError("Supabase environment variables are missing.")

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
        raise APIError(details) from exc
    except urllib.error.URLError as exc:
        raise APIError(f"Could not connect to Supabase: {exc.reason}") from exc


def authorised() -> bool:
    supplied = request.headers.get("X-Bot-Key", "")
    return bool(
        BOT_API_KEY
        and supplied
        and hmac.compare_digest(supplied, BOT_API_KEY)
    )


def require_bot():
    if not authorised():
        return jsonify({"error": "Invalid bot API key."}), 401
    return None


def payload() -> dict[str, Any]:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@team_admin_api.post("/api/bot/teams/create")
def create_team():
    denied = require_bot()
    if denied:
        return denied

    body = payload()
    name = str(body.get("name") or "").strip()
    role_id = str(body.get("discordRoleId") or "").strip()
    logo_url = str(body.get("logoUrl") or "").strip()

    if not name:
        return jsonify({"error": "Team name is required."}), 400

    try:
        rows = sb(
            "POST",
            "teams",
            body={
                "name": name,
                "logo_url": logo_url or None,
                "discord_role_id": role_id or None,
                "budget": 300000,
                "reserved_budget": 0,
                "active": True,
            },
            prefer="return=representation",
        )
        return jsonify({"ok": True, "team": rows[0]})
    except APIError as exc:
        return jsonify({"error": str(exc)}), 400


@team_admin_api.post("/api/bot/teams/assign-manager")
def assign_manager():
    denied = require_bot()
    if denied:
        return denied

    body = payload()
    team_id = str(body.get("teamId") or "")
    discord_id = str(body.get("discordId") or "")
    staff_role = str(body.get("staffRole") or "Manager")

    if staff_role not in {"Manager", "Assistant Manager"}:
        return jsonify({"error": "Invalid staff role."}), 400

    try:
        # Disable previous active assignment for this member.
        safe_discord = urllib.parse.quote(discord_id, safe="")
        sb(
            "PATCH",
            f"team_managers?discord_id=eq.{safe_discord}&active=eq.true",
            body={"active": False},
            prefer="return=minimal",
        )

        rows = sb(
            "POST",
            "team_managers",
            body={
                "team_id": team_id,
                "discord_id": discord_id,
                "staff_role": staff_role,
                "active": True,
            },
            prefer="return=representation",
        )
        return jsonify({"ok": True, "assignment": rows[0]})
    except APIError as exc:
        return jsonify({"error": str(exc)}), 400


@team_admin_api.post("/api/bot/guild-settings")
def set_guild_settings():
    denied = require_bot()
    if denied:
        return denied

    body = payload()
    guild_id = str(body.get("guildId") or "")
    if not guild_id:
        return jsonify({"error": "guildId is required."}), 400

    record = {
        "guild_id": guild_id,
        "signing_channel_id": (
            str(body["signingChannelId"])
            if body.get("signingChannelId")
            else None
        ),
        "budget_channel_id": (
            str(body["budgetChannelId"])
            if body.get("budgetChannelId")
            else None
        ),
        "budget_message_id": (
            str(body["budgetMessageId"])
            if body.get("budgetMessageId")
            else None
        ),
        "updated_at": "now()",
    }

    try:
        rows = sb(
            "POST",
            "guild_settings",
            body=record,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return jsonify({"ok": True, "settings": rows[0]})
    except APIError as exc:
        return jsonify({"error": str(exc)}), 400


@team_admin_api.get("/api/bot/guild-settings/<guild_id>")
def get_guild_settings(guild_id: str):
    denied = require_bot()
    if denied:
        return denied

    try:
        safe_id = urllib.parse.quote(guild_id, safe="")
        rows = sb(
            "GET",
            f"guild_settings?guild_id=eq.{safe_id}&select=*",
        )
        return jsonify({"settings": rows[0] if rows else None})
    except APIError as exc:
        return jsonify({"error": str(exc)}), 503


@team_admin_api.get("/api/bot/teams")
def list_teams():
    denied = require_bot()
    if denied:
        return denied

    try:
        rows = sb(
            "GET",
            "teams?select=id,name,logo_url,discord_role_id,budget,"
            "reserved_budget,active&active=eq.true&order=name.asc",
        )
        return jsonify({"teams": rows or []})
    except APIError as exc:
        return jsonify({"error": str(exc)}), 503


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def first_or_none(rows: Any) -> dict[str, Any] | None:
    return rows[0] if isinstance(rows, list) and rows else None


def find_active_staff(discord_id: str) -> dict[str, Any] | None:
    safe_id = urllib.parse.quote(discord_id, safe="")
    rows = sb(
        "GET",
        "team_managers?discord_id=eq."
        f"{safe_id}&active=eq.true&select=id,team_id,discord_id,"
        "staff_role,active,teams(id,name,logo_url,discord_role_id)",
    )
    return first_or_none(rows)


def find_player(discord_id: str) -> dict[str, Any] | None:
    safe_id = urllib.parse.quote(discord_id, safe="")
    rows = sb(
        "GET",
        "players?discord_id=eq."
        f"{safe_id}&select=id,username,discord_id,current_team_id,"
        "team,player_role,active,is_active",
    )
    return first_or_none(rows)


@team_admin_api.post("/api/bot/teams/delete")
def delete_team():
    denied = require_bot()
    if denied:
        return denied

    body = payload()
    team_id = str(body.get("teamId") or "").strip()
    if not team_id:
        return jsonify({"error": "teamId is required."}), 400

    safe_team = urllib.parse.quote(team_id, safe="")
    removed_at = utc_now()

    try:
        team_rows = sb(
            "GET",
            f"teams?id=eq.{safe_team}&select=id,name,discord_role_id,active",
        )
        team = first_or_none(team_rows)
        if not team:
            return jsonify({"error": "Team not found."}), 404

        sb(
            "PATCH",
            f"team_managers?team_id=eq.{safe_team}&active=eq.true",
            body={"active": False, "removed_at": removed_at},
            prefer="return=minimal",
        )

        released_players = sb(
            "PATCH",
            f"players?current_team_id=eq.{safe_team}",
            body={
                "current_team_id": None,
                "team": "Free Agent",
                "player_role": "Player",
            },
            prefer="return=representation",
        ) or []

        rows = sb(
            "PATCH",
            f"teams?id=eq.{safe_team}",
            body={"active": False, "updated_at": removed_at},
            prefer="return=representation",
        )

        return jsonify({
            "ok": True,
            "team": rows[0],
            "releasedPlayers": released_players,
        })
    except APIError as exc:
        return jsonify({"error": str(exc)}), 400


@team_admin_api.post("/api/bot/managers/sack")
def sack_manager():
    denied = require_bot()
    if denied:
        return denied

    body = payload()
    discord_id = str(body.get("discordId") or "").strip()
    expected_role = str(body.get("staffRole") or "").strip()

    if not discord_id:
        return jsonify({"error": "discordId is required."}), 400

    try:
        assignment = find_active_staff(discord_id)
        if not assignment:
            return jsonify({"error": "This member has no active staff role."}), 404

        if expected_role and assignment.get("staff_role") != expected_role:
            return jsonify({
                "error": (
                    f"This member is registered as {assignment.get('staff_role')}, "
                    f"not {expected_role}."
                )
            }), 400

        safe_assignment = urllib.parse.quote(str(assignment["id"]), safe="")
        rows = sb(
            "PATCH",
            f"team_managers?id=eq.{safe_assignment}",
            body={"active": False, "removed_at": utc_now()},
            prefer="return=representation",
        )

        player = find_player(discord_id)
        if player:
            safe_player = urllib.parse.quote(str(player["id"]), safe="")
            sb(
                "PATCH",
                f"players?id=eq.{safe_player}",
                body={"player_role": "Player"},
                prefer="return=minimal",
            )

        return jsonify({
            "ok": True,
            "assignment": rows[0],
            "team": assignment.get("teams"),
            "member": player,
        })
    except APIError as exc:
        return jsonify({"error": str(exc)}), 400


@team_admin_api.post("/api/bot/players/release")
def release_player():
    denied = require_bot()
    if denied:
        return denied

    body = payload()
    discord_id = str(body.get("discordId") or "").strip()
    if not discord_id:
        return jsonify({"error": "discordId is required."}), 400

    try:
        player = find_player(discord_id)
        if not player:
            return jsonify({"error": "Player not found."}), 404

        old_team = player.get("team") or "Free Agent"
        safe_player = urllib.parse.quote(str(player["id"]), safe="")
        rows = sb(
            "PATCH",
            f"players?id=eq.{safe_player}",
            body={
                "current_team_id": None,
                "team": "Free Agent",
                "player_role": "Player",
            },
            prefer="return=representation",
        )

        return jsonify({
            "ok": True,
            "player": rows[0],
            "oldTeam": old_team,
        })
    except APIError as exc:
        return jsonify({"error": str(exc)}), 400


@team_admin_api.post("/api/bot/members/left")
def member_left():
    denied = require_bot()
    if denied:
        return denied

    body = payload()
    discord_id = str(body.get("discordId") or "").strip()
    if not discord_id:
        return jsonify({"error": "discordId is required."}), 400

    try:
        staff = find_active_staff(discord_id)
        player = find_player(discord_id)
        timestamp = utc_now()

        event_type = None
        role_name = None
        team_name = None
        username = None

        if staff:
            role_name = staff.get("staff_role") or "Manager"
            team = staff.get("teams") or {}
            team_name = team.get("name") or "Unknown Club"
            username = (
                (player or {}).get("username")
                or str(body.get("displayName") or "Unknown member")
            )
            event_type = "manager_left"

            safe_assignment = urllib.parse.quote(str(staff["id"]), safe="")
            sb(
                "PATCH",
                f"team_managers?id=eq.{safe_assignment}",
                body={"active": False, "removed_at": timestamp},
                prefer="return=minimal",
            )

        if player:
            username = player.get("username") or username
            if not event_type:
                event_type = "player_left"
                role_name = "Player"
                team_name = player.get("team") or "Free Agent"

            safe_player = urllib.parse.quote(str(player["id"]), safe="")
            sb(
                "PATCH",
                f"players?id=eq.{safe_player}",
                body={
                    "active": False,
                    "is_active": False,
                    "left_at": timestamp,
                    "current_team_id": None,
                    "team": "Free Agent",
                    "player_role": "Player",
                },
                prefer="return=minimal",
            )

        return jsonify({
            "ok": True,
            "matched": bool(staff or player),
            "eventType": event_type,
            "username": username,
            "role": role_name,
            "team": team_name,
            "discordId": discord_id,
        })
    except APIError as exc:
        return jsonify({"error": str(exc)}), 400
