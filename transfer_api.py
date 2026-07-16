"""
YSL Transfer API blueprint — Phase 1

Add this file beside app.py and register it:

    from transfer_api import transfer_api
    app.register_blueprint(transfer_api)

Required environment variables:
    SUPABASE_URL
    SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY
    BOT_API_KEY
    TRANSFER_STAFF_ROLE_IDS=123,456

The Discord bot and the future website Transfer Centre both call this API.
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


transfer_api = Blueprint("transfer_api", __name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)
BOT_API_KEY = os.getenv("BOT_API_KEY", "")

TRANSFER_STAFF_ROLE_IDS = {
    value.strip()
    for value in os.getenv("TRANSFER_STAFF_ROLE_IDS", "").split(",")
    if value.strip()
}


class TransferAPIError(RuntimeError):
    pass


def supabase_request(
    method: str,
    path: str,
    *,
    body: Any | None = None,
    prefer: str | None = None,
) -> Any:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise TransferAPIError(
            "Supabase transfer API environment variables are missing."
        )

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
            text = response.read().decode("utf-8")
            return json.loads(text) if text else None
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise TransferAPIError(
            f"Supabase returned HTTP {exc.code}: {details}"
        ) from exc
    except urllib.error.URLError as exc:
        raise TransferAPIError(
            f"Could not connect to Supabase: {exc.reason}"
        ) from exc


def rpc(name: str, payload: dict[str, Any]) -> Any:
    return supabase_request(
        "POST",
        f"rpc/{name}",
        body=payload,
    )


def bot_authorised() -> bool:
    supplied = request.headers.get("X-Bot-Key", "")

    return bool(
        BOT_API_KEY
        and supplied
        and hmac.compare_digest(supplied, BOT_API_KEY)
    )


def require_bot() -> tuple[Any, int] | None:
    if not bot_authorised():
        return jsonify({"error": "Invalid bot API key."}), 401
    return None


def json_body() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def rpc_error(exc: TransferAPIError) -> tuple[Any, int]:
    message = str(exc)

    # PostgREST returns PostgreSQL exception text inside its JSON response.
    # Keep it visible so Discord can show the manager the real reason.
    return jsonify({"error": message}), 400


@transfer_api.get("/api/transfers/teams")
def list_teams():
    try:
        rows = supabase_request(
            "GET",
            "teams?select=id,name,logo_url,discord_role_id,budget,"
            "reserved_budget,active&active=eq.true&order=name.asc",
        )

        teams = []

        for row in rows or []:
            budget = int(row.get("budget") or 0)
            reserved = int(row.get("reserved_budget") or 0)

            teams.append({
                **row,
                "available_budget": max(budget - reserved, 0),
            })

        return jsonify({"teams": teams})
    except TransferAPIError as exc:
        return jsonify({"error": str(exc)}), 503


@transfer_api.get("/api/transfers/offers/<discord_id>")
def offers_for_discord(discord_id: str):
    try:
        safe_id = urllib.parse.quote(discord_id, safe="")
        rows = supabase_request(
            "GET",
            "transfer_offers?"
            f"awaiting_discord_id=eq.{safe_id}"
            "&select=*,player:players(id,username,roblox_user_id,discord_id,"
            "team,player_role,rating,market_value),"
            "buying_team:teams!transfer_offers_buying_team_id_fkey(id,name,logo_url),"
            "selling_team:teams!transfer_offers_selling_team_id_fkey(id,name,logo_url)"
            "&order=created_at.desc",
        )

        return jsonify({"offers": rows or []})
    except TransferAPIError as exc:
        return jsonify({"error": str(exc)}), 503


@transfer_api.post("/api/bot/transfers/offer")
def create_offer():
    denied = require_bot()
    if denied:
        return denied

    payload = json_body()

    try:
        offer = rpc("ysl_create_transfer_offer", {
            "p_player_id": payload.get("playerId"),
            "p_buying_team_id": payload.get("buyingTeamId"),
            "p_amount": int(payload.get("amount") or 0),
            "p_manager_discord_id": str(
                payload.get("managerDiscordId") or ""
            ),
        })

        return jsonify({"ok": True, "offer": offer})
    except (ValueError, TypeError):
        return jsonify({"error": "Amount must be a whole number."}), 400
    except TransferAPIError as exc:
        return rpc_error(exc)


@transfer_api.post("/api/bot/transfers/sign")
def create_free_agent_signing():
    denied = require_bot()
    if denied:
        return denied

    payload = json_body()

    try:
        offer = rpc("ysl_create_free_agent_signing", {
            "p_player_id": payload.get("playerId"),
            "p_buying_team_id": payload.get("buyingTeamId"),
            "p_manager_discord_id": str(
                payload.get("managerDiscordId") or ""
            ),
        })

        return jsonify({"ok": True, "offer": offer})
    except TransferAPIError as exc:
        return rpc_error(exc)


@transfer_api.post("/api/bot/transfers/seller-response")
def seller_response():
    denied = require_bot()
    if denied:
        return denied

    payload = json_body()

    try:
        counter_amount = payload.get("counterAmount")

        offer = rpc("ysl_seller_respond_offer", {
            "p_offer_id": payload.get("offerId"),
            "p_manager_discord_id": str(
                payload.get("managerDiscordId") or ""
            ),
            "p_action": str(payload.get("action") or ""),
            "p_counter_amount": (
                int(counter_amount)
                if counter_amount not in (None, "")
                else None
            ),
            "p_message": str(payload.get("message") or ""),
        })

        return jsonify({"ok": True, "offer": offer})
    except (ValueError, TypeError):
        return jsonify({
            "error": "Counter amount must be a whole number."
        }), 400
    except TransferAPIError as exc:
        return rpc_error(exc)


@transfer_api.post("/api/bot/transfers/buyer-response")
def buyer_response():
    denied = require_bot()
    if denied:
        return denied

    payload = json_body()

    try:
        offer = rpc("ysl_buyer_respond_counter", {
            "p_offer_id": payload.get("offerId"),
            "p_manager_discord_id": str(
                payload.get("managerDiscordId") or ""
            ),
            "p_action": str(payload.get("action") or ""),
        })

        return jsonify({"ok": True, "offer": offer})
    except TransferAPIError as exc:
        return rpc_error(exc)


@transfer_api.post("/api/bot/transfers/player-response")
def player_response():
    denied = require_bot()
    if denied:
        return denied

    payload = json_body()

    try:
        offer = rpc("ysl_player_respond_offer", {
            "p_offer_id": payload.get("offerId"),
            "p_player_discord_id": str(
                payload.get("playerDiscordId") or ""
            ),
            "p_action": str(payload.get("action") or ""),
        })

        return jsonify({"ok": True, "offer": offer})
    except TransferAPIError as exc:
        return rpc_error(exc)


@transfer_api.post("/api/bot/budget/adjust")
def staff_adjust_budget():
    denied = require_bot()
    if denied:
        return denied

    payload = json_body()
    caller_role_ids = {
        str(role_id)
        for role_id in payload.get("staffRoleIds", [])
    }

    if (
        TRANSFER_STAFF_ROLE_IDS
        and not caller_role_ids.intersection(TRANSFER_STAFF_ROLE_IDS)
    ):
        return jsonify({
            "error": "You do not have a configured transfer staff role."
        }), 403

    try:
        team = rpc("ysl_staff_adjust_budget", {
            "p_team_id": payload.get("teamId"),
            "p_amount": int(payload.get("amount") or 0),
            "p_reason": str(payload.get("reason") or ""),
            "p_staff_discord_id": str(
                payload.get("staffDiscordId") or ""
            ),
        })

        return jsonify({"ok": True, "team": team})
    except (ValueError, TypeError):
        return jsonify({
            "error": "Budget adjustment must be a whole number."
        }), 400
    except TransferAPIError as exc:
        return rpc_error(exc)


@transfer_api.get("/api/transfers/team/<team_id>/ledger")
def team_budget_ledger(team_id: str):
    try:
        safe_id = urllib.parse.quote(team_id, safe="")
        rows = supabase_request(
            "GET",
            "team_budget_ledger?"
            f"team_id=eq.{safe_id}"
            "&select=*&order=created_at.desc&limit=100",
        )

        return jsonify({"entries": rows or []})
    except TransferAPIError as exc:
        return jsonify({"error": str(exc)}), 503
