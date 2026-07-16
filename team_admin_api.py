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


@team_admin_api.get("/api/playersheet")
def public_playersheet():
    """
    Return Playersheet data with manager roles derived from team_managers
    and club logos derived from teams.
    """
    try:
        teams = sb(
            "GET",
            "teams?select=id,name,logo_url,active"
            "&active=eq.true&order=name.asc",
        ) or []

        managers = sb(
            "GET",
            "team_managers?select=team_id,discord_id,staff_role,active"
            "&active=eq.true",
        ) or []

        players = sb(
            "GET",
            "players?select=id,username,roblox_user_id,discord_id,"
            "current_team_id,team,player_role,rating,market_value,releases,"
            "avatar_url,active,is_active&order=rating.desc,username.asc",
        ) or []

        teams_by_id = {
            str(team["id"]): team
            for team in teams
        }

        staff_by_discord = {
            str(manager["discord_id"]): manager
            for manager in managers
            if manager.get("discord_id")
        }

        output = []

        for player in players:
            staff = staff_by_discord.get(
                str(player.get("discord_id") or "")
            )

            team_id = (
                str(player.get("current_team_id"))
                if player.get("current_team_id")
                else ""
            )

            if staff:
                team_id = str(staff.get("team_id") or team_id)
                displayed_role = staff.get("staff_role") or "Player"
            else:
                displayed_role = player.get("player_role") or "Player"

            team_record = teams_by_id.get(team_id)

            team_name = (
                team_record.get("name")
                if team_record
                else player.get("team") or "Free Agent"
            )

            is_free_agent = (
                str(team_name).strip().lower() == "free agent"
            )

            team_logo = (
                "https://www.fifacm.com/content/media/imgs/fifa21/teams/256/l111592.png"
                if is_free_agent
                else (
                    team_record.get("logo_url")
                    if team_record
                    else ""
                )
            )

            output.append({
                "id": player.get("id"),
                "username": player.get("username"),
                "roblox_user_id": player.get("roblox_user_id"),
                "team": team_name,
                "teamLogo": team_logo or "",
                "role": displayed_role,
                "rating": player.get("rating") or 64,
                "value": player.get("market_value") or 50000,
                "releases": (
                    1
                    if player.get("releases") is None
                    else max(0, int(player.get("releases")))
                ),
                "avatarUrl": player.get("avatar_url") or "",
                "active": bool(
                    player.get(
                        "active",
                        player.get("is_active", True),
                    )
                ),
            })

        return jsonify({
            "teams": [
                {
                    "id": team.get("id"),
                    "name": team.get("name"),
                    "logo_url": team.get("logo_url") or "",
                }
                for team in teams
            ],
            "players": output,
        })
    except (APIError, ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 503
