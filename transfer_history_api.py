"""
YSL public transfer history API.

Register in app.py:

    from transfer_history_api import transfer_history_api
    app.register_blueprint(transfer_history_api)

Routes:
    GET /api/transfers/history
    GET /api/transfers/history/summary

Supported history query parameters:
    page=1
    perPage=24
    search=player or club
    type=all|transfer|signing|release
    team=Arsenal
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

from flask import Blueprint, jsonify, request


transfer_history_api = Blueprint("transfer_history_api", __name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)

TEAM_ICON_URLS = {
    "arsenal": "https://cdn.discordapp.com/emojis/1313190736019062896.png?size=128&quality=lossless",
    "chelsea": "https://cdn.discordapp.com/emojis/1313186798733885520.png?size=128&quality=lossless",
    "chelsea fc": "https://cdn.discordapp.com/emojis/1313186798733885520.png?size=128&quality=lossless",
    "liverpool": "https://cdn.discordapp.com/emojis/1313188977297330296.png?size=128&quality=lossless",
    "liverpool fc": "https://cdn.discordapp.com/emojis/1313188977297330296.png?size=128&quality=lossless",
    "manchester city": "https://cdn.discordapp.com/emojis/1313186951842496582.png?size=128&quality=lossless",
    "man city": "https://cdn.discordapp.com/emojis/1313186951842496582.png?size=128&quality=lossless",
    "manchester united": "https://cdn.discordapp.com/emojis/1313187459219198024.png?size=128&quality=lossless",
    "man united": "https://cdn.discordapp.com/emojis/1313187459219198024.png?size=128&quality=lossless",
    "newcastle united": "https://cdn.discordapp.com/emojis/1313189182008721419.png?size=128&quality=lossless",
    "newcastle": "https://cdn.discordapp.com/emojis/1313189182008721419.png?size=128&quality=lossless",
    "free agent": "https://cdn.discordapp.com/emojis/1527676672881459381.png?size=128&quality=lossless",
    "free agents": "https://cdn.discordapp.com/emojis/1527676672881459381.png?size=128&quality=lossless",
}


class HistoryAPIError(RuntimeError):
    pass


def sb(
    method: str,
    path: str,
    *,
    body: Any | None = None,
    prefer: str | None = None,
) -> Any:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise HistoryAPIError("Supabase environment variables are missing.")

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
        raise HistoryAPIError(str(message)) from exc
    except urllib.error.URLError as exc:
        raise HistoryAPIError(
            f"Could not connect to Supabase: {exc.reason}"
        ) from exc


def norm(value: Any) -> str:
    return str(value or "").strip().lower()


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def transfer_type(row: dict[str, Any]) -> str:
    from_team = norm(row.get("from_team"))
    to_team = norm(row.get("to_team"))
    note = norm(row.get("note"))

    if to_team in {"", "free agent", "free agents"}:
        return "release"
    if from_team in {"", "free agent", "free agents"}:
        return "signing"
    if "release" in note:
        return "release"
    return "transfer"


def icon_for(team_name: Any) -> str:
    return TEAM_ICON_URLS.get(norm(team_name), "")


def get_players(player_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not player_ids:
        return {}

    escaped = ",".join(
        urllib.parse.quote(str(player_id), safe="")
        for player_id in player_ids
    )
    rows = sb(
        "GET",
        "players?"
        f"id=in.({escaped})"
        "&select=id,username,roblox_user_id,avatar_url,rating,player_role",
    ) or []
    return {str(row["id"]): row for row in rows if row.get("id")}


def get_all_history() -> list[dict[str, Any]]:
    # YSL is small enough to perform filtering in Python. This also avoids
    # PostgREST OR-filter escaping problems with user-entered search text.
    return sb(
        "GET",
        "player_transfers?"
        "select=id,player_id,from_team,to_team,transfer_value,"
        "transferred_at,note"
        "&order=transferred_at.desc"
        "&limit=5000",
    ) or []


def serialise(
    row: dict[str, Any],
    players: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    player = players.get(str(row.get("player_id")), {})
    from_team = row.get("from_team") or "Free Agent"
    to_team = row.get("to_team") or "Free Agent"

    return {
        "id": row.get("id"),
        "playerId": row.get("player_id"),
        "playerName": player.get("username") or "Unknown Player",
        "robloxId": player.get("roblox_user_id"),
        "avatarUrl": player.get("avatar_url") or "",
        "rating": integer(player.get("rating"), 64),
        "role": player.get("player_role") or "Player",
        "fromTeam": from_team,
        "fromTeamIcon": icon_for(from_team),
        "toTeam": to_team,
        "toTeamIcon": icon_for(to_team),
        "value": integer(row.get("transfer_value")),
        "type": transfer_type(row),
        "completedAt": row.get("transferred_at"),
        "note": row.get("note") or "",
        "profileUrl": (
            f"player.html?id={urllib.parse.quote(str(row.get('player_id')), safe='')}"
            if row.get("player_id")
            else ""
        ),
    }


@transfer_history_api.get("/api/transfers/history")
def history():
    try:
        page = max(integer(request.args.get("page"), 1), 1)
        per_page = min(max(integer(request.args.get("perPage"), 24), 6), 100)
        search = norm(request.args.get("search"))
        selected_type = norm(request.args.get("type")) or "all"
        selected_team = norm(request.args.get("team"))

        rows = get_all_history()
        player_ids = list({
            str(row["player_id"])
            for row in rows
            if row.get("player_id")
        })
        players = get_players(player_ids)
        records = [serialise(row, players) for row in rows]

        if selected_type != "all":
            records = [
                record for record in records
                if record["type"] == selected_type
            ]

        if selected_team:
            records = [
                record for record in records
                if selected_team in {
                    norm(record["fromTeam"]),
                    norm(record["toTeam"]),
                }
            ]

        if search:
            records = [
                record for record in records
                if search in norm(record["playerName"])
                or search in norm(record["fromTeam"])
                or search in norm(record["toTeam"])
                or search in norm(record["note"])
            ]

        total = len(records)
        start = (page - 1) * per_page
        end = start + per_page

        teams = sorted({
            record["fromTeam"] for record in records
            if norm(record["fromTeam"]) not in {"", "free agent", "free agents"}
        } | {
            record["toTeam"] for record in records
            if norm(record["toTeam"]) not in {"", "free agent", "free agents"}
        })

        return jsonify({
            "transfers": records[start:end],
            "pagination": {
                "page": page,
                "perPage": per_page,
                "total": total,
                "pages": max((total + per_page - 1) // per_page, 1),
            },
            "teams": teams,
        })
    except HistoryAPIError as exc:
        return jsonify({"error": str(exc)}), 503


@transfer_history_api.get("/api/transfers/history/summary")
def summary():
    try:
        rows = get_all_history()
        player_ids = list({
            str(row["player_id"])
            for row in rows
            if row.get("player_id")
        })
        players = get_players(player_ids)
        records = [serialise(row, players) for row in rows]
        counts = Counter(record["type"] for record in records)

        paid = [
            record for record in records
            if record["type"] == "transfer" and record["value"] > 0
        ]
        highest = max(paid, key=lambda record: record["value"], default=None)

        return jsonify({
            "total": len(records),
            "transfers": counts["transfer"],
            "signings": counts["signing"],
            "releases": counts["release"],
            "totalSpent": sum(record["value"] for record in paid),
            "recordTransfer": highest,
        })
    except HistoryAPIError as exc:
        return jsonify({"error": str(exc)}), 503
