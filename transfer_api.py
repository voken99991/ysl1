"""
YSL Transfer API — Phase 2

Place this file beside the website's app.py, then register the blueprint:

    from transfer_api import transfer_api
    app.register_blueprint(transfer_api)

This module is the single source of truth for:
- manager/team lookup
- budgets
- offers
- negotiations
- player decisions
- staff budget corrections
- completed transfer details
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

        raise TransferAPIError(str(message)) from exc
    except urllib.error.URLError as exc:
        raise TransferAPIError(
            f"Could not connect to Supabase: {exc.reason}"
        ) from exc


def rpc(name: str, payload: dict[str, Any]) -> Any:
    return supabase_request("POST", f"rpc/{name}", body=payload)


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


def one(rows: Any) -> dict[str, Any] | None:
    return rows[0] if isinstance(rows, list) and rows else None


def api_error(exc: Exception, status: int = 400) -> tuple[Any, int]:
    return jsonify({"error": str(exc)}), status


def get_team(team_id: str) -> dict[str, Any] | None:
    safe_id = urllib.parse.quote(str(team_id), safe="")
    return one(supabase_request(
        "GET",
        "teams?"
        f"id=eq.{safe_id}"
        "&select=id,name,logo_url,discord_role_id,budget,reserved_budget,active",
    ))


def get_manager_team(discord_id: str) -> dict[str, Any] | None:
    safe_id = urllib.parse.quote(str(discord_id), safe="")
    manager = one(supabase_request(
        "GET",
        "team_managers?"
        f"discord_id=eq.{safe_id}&active=eq.true"
        "&select=team_id,staff_role",
    ))

    if not manager:
        return None

    team = get_team(str(manager["team_id"]))
    if team:
        team["manager_role"] = manager.get("staff_role")
    return team


def get_active_manager(team_id: str) -> dict[str, Any] | None:
    safe_id = urllib.parse.quote(str(team_id), safe="")
    return one(supabase_request(
        "GET",
        "team_managers?"
        f"team_id=eq.{safe_id}&staff_role=eq.Manager&active=eq.true"
        "&select=discord_id,staff_role",
    ))


def get_player_by_discord(discord_id: str) -> dict[str, Any] | None:
    safe_id = urllib.parse.quote(str(discord_id), safe="")
    return one(supabase_request(
        "GET",
        "players?"
        f"discord_id=eq.{safe_id}"
        "&select=id,username,roblox_user_id,discord_id,current_team_id,"
        "team,player_role,rating,market_value,active,is_active",
    ))


def get_offer_details(offer_id: str) -> dict[str, Any] | None:
    safe_id = urllib.parse.quote(str(offer_id), safe="")
    offer = one(supabase_request(
        "GET",
        "transfer_offers?"
        f"id=eq.{safe_id}"
        "&select=*",
    ))

    if not offer:
        return None

    player_id = urllib.parse.quote(str(offer["player_id"]), safe="")
    player = one(supabase_request(
        "GET",
        "players?"
        f"id=eq.{player_id}"
        "&select=id,username,roblox_user_id,discord_id,current_team_id,"
        "team,player_role,rating,market_value",
    ))

    buying_team = get_team(str(offer["buying_team_id"]))
    selling_team = (
        get_team(str(offer["selling_team_id"]))
        if offer.get("selling_team_id")
        else None
    )
    buying_manager = get_active_manager(str(offer["buying_team_id"]))
    selling_manager = (
        get_active_manager(str(offer["selling_team_id"]))
        if offer.get("selling_team_id")
        else None
    )

    return {
        "offer": offer,
        "player": player,
        "buyingTeam": buying_team,
        "sellingTeam": selling_team,
        "buyingManager": buying_manager,
        "sellingManager": selling_manager,
    }


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
        return api_error(exc, 503)


@transfer_api.post("/api/bot/transfers/context")
def transfer_context():
    denied = require_bot()
    if denied:
        return denied

    payload = json_body()
    manager_id = str(payload.get("managerDiscordId") or "")
    player_id = str(payload.get("playerDiscordId") or "")

    try:
        manager_team = get_manager_team(manager_id)
        player = get_player_by_discord(player_id)

        if not manager_team:
            return jsonify({
                "error": "You are not assigned as an active team manager."
            }), 403

        if not player:
            return jsonify({
                "error": "That Discord member has no linked YSL player record."
            }), 404

        selling_team = (
            get_team(str(player["current_team_id"]))
            if player.get("current_team_id")
            else None
        )
        selling_manager = (
            get_active_manager(str(player["current_team_id"]))
            if player.get("current_team_id")
            else None
        )

        budget = int(manager_team.get("budget") or 0)
        reserved = int(manager_team.get("reserved_budget") or 0)

        return jsonify({
            "managerTeam": {
                **manager_team,
                "available_budget": max(budget - reserved, 0),
            },
            "player": player,
            "sellingTeam": selling_team,
            "sellingManager": selling_manager,
        })
    except TransferAPIError as exc:
        return api_error(exc, 503)


@transfer_api.post("/api/bot/transfers/manager-budget")
def manager_budget():
    denied = require_bot()
    if denied:
        return denied

    payload = json_body()
    discord_id = str(payload.get("managerDiscordId") or "")

    try:
        team = get_manager_team(discord_id)
        if not team:
            return jsonify({
                "error": "You are not assigned to an active team."
            }), 404

        budget = int(team.get("budget") or 0)
        reserved = int(team.get("reserved_budget") or 0)

        return jsonify({
            "team": {
                **team,
                "available_budget": max(budget - reserved, 0),
            }
        })
    except TransferAPIError as exc:
        return api_error(exc, 503)


@transfer_api.get("/api/bot/transfers/offer/<offer_id>")
def offer_details(offer_id: str):
    denied = require_bot()
    if denied:
        return denied

    try:
        details = get_offer_details(offer_id)
        if not details:
            return jsonify({"error": "Offer not found."}), 404
        return jsonify(details)
    except TransferAPIError as exc:
        return api_error(exc, 503)


@transfer_api.get("/api/bot/transfers/pending/<discord_id>")
def pending_for_user(discord_id: str):
    denied = require_bot()
    if denied:
        return denied

    try:
        safe_id = urllib.parse.quote(discord_id, safe="")
        rows = supabase_request(
            "GET",
            "transfer_offers?"
            f"awaiting_discord_id=eq.{safe_id}"
            "&status=in.(pending_seller,pending_buyer,pending_player)"
            "&select=id&order=created_at.desc",
        )

        details = []
        for row in rows or []:
            item = get_offer_details(str(row["id"]))
            if item:
                details.append(item)

        return jsonify({"offers": details})
    except TransferAPIError as exc:
        return api_error(exc, 503)


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

        offer_id = offer.get("id") if isinstance(offer, dict) else None
        return jsonify({
            "ok": True,
            "details": get_offer_details(str(offer_id)),
        })
    except (ValueError, TypeError):
        return jsonify({"error": "Amount must be a whole number."}), 400
    except TransferAPIError as exc:
        return api_error(exc)


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

        offer_id = offer.get("id") if isinstance(offer, dict) else None
        return jsonify({
            "ok": True,
            "details": get_offer_details(str(offer_id)),
        })
    except TransferAPIError as exc:
        return api_error(exc)


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

        return jsonify({
            "ok": True,
            "details": get_offer_details(str(offer.get("id"))),
        })
    except (ValueError, TypeError):
        return jsonify({
            "error": "Counter amount must be a whole number."
        }), 400
    except TransferAPIError as exc:
        return api_error(exc)


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

        return jsonify({
            "ok": True,
            "details": get_offer_details(str(offer.get("id"))),
        })
    except TransferAPIError as exc:
        return api_error(exc)


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

        return jsonify({
            "ok": True,
            "details": get_offer_details(str(offer.get("id"))),
        })
    except TransferAPIError as exc:
        return api_error(exc)


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
        return api_error(exc)


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
        return api_error(exc, 503)
