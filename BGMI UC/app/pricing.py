"""Effective pricing for the store.

`config.json` holds the shop's starting prices.  Admins can edit packs and the
custom-UC rate from the dashboard; those edits are stored in the database and
override the config from then on, so price changes take effect immediately
without touching files or restarting the server.

Resolution order for every read: database override -> config.json -> built-ins.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Config, normalize_packs
from .db import Database, iso
from .http_utils import ApiError

PACKS_KEY = "packs_json"
CUSTOM_RATE_KEY = "custom_uc_rate"
UPDATED_AT_KEY = "pricing_updated_at"
UPDATED_BY_KEY = "pricing_updated_by"

MAX_PACKS = 30
MAX_UC = 1_000_000
MAX_BONUS_UC = 1_000_000
MIN_PRICE = 1.0
MAX_PRICE = 1_000_000.0
MAX_CUSTOM_RATE = 1_000.0


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
def stored_packs(db: Database) -> list[Any] | None:
    """The raw admin-saved pack list, or None when prices come from config."""
    raw = db.get_setting(PACKS_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, list) else None


def effective_packs(db: Database, config: Config) -> list[dict[str, Any]]:
    override = stored_packs(db)
    if override is not None:
        packs = normalize_packs(override)
        if packs:
            return packs
    return config.packs()


def effective_custom_rate(db: Database, config: Config) -> float:
    raw = db.get_setting(CUSTOM_RATE_KEY)
    if raw:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
    return float(config.get("payment.custom_uc_rate", 1.2) or 1.2)


def find_pack(db: Database, config: Config, pack_id: str) -> dict[str, Any] | None:
    for pack in effective_packs(db, config):
        if pack["id"] == pack_id:
            return pack
    return None


def is_customised(db: Database) -> bool:
    return bool(db.get_setting(PACKS_KEY) or db.get_setting(CUSTOM_RATE_KEY))


def meta(db: Database) -> dict[str, Any]:
    return {
        "source": "dashboard" if is_customised(db) else "config.json",
        "updated_at": db.get_setting(UPDATED_AT_KEY),
        "updated_by": db.get_setting(UPDATED_BY_KEY),
    }


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #
def _as_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"{field} must be a whole number", "invalid_packs") from None
    if not minimum <= number <= maximum:
        raise ApiError(
            400, f"{field} must be between {minimum} and {maximum}", "invalid_packs"
        )
    return number


def _as_price(value: Any) -> float:
    try:
        price = round(float(value), 2)
    except (TypeError, ValueError):
        raise ApiError(400, "Price must be a number", "invalid_packs") from None
    if not MIN_PRICE <= price <= MAX_PRICE:
        raise ApiError(
            400,
            f"Price must be between \u20b9{MIN_PRICE:.0f} and \u20b9{MAX_PRICE:,.0f}",
            "invalid_packs",
        )
    return price


def validate_packs(raw_packs: Any) -> list[dict[str, Any]]:
    """Validate and normalise an admin-submitted pack list."""
    if not isinstance(raw_packs, list) or not raw_packs:
        raise ApiError(400, "Send at least one UC pack", "invalid_packs")
    if len(raw_packs) > MAX_PACKS:
        raise ApiError(400, f"At most {MAX_PACKS} packs are supported", "invalid_packs")

    cleaned: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_packs):
        if not isinstance(raw, dict):
            raise ApiError(400, "Each pack must be an object", "invalid_packs")
        name = " ".join(str(raw.get("name") or "").split())
        if not 1 <= len(name) <= 40:
            raise ApiError(
                400, f"Pack {index + 1}: name must be 1-40 characters", "invalid_packs"
            )
        pack_id = str(raw.get("id") or "").strip().lower()
        entry: dict[str, Any] = {
            "id": pack_id,
            "name": name,
            "uc": _as_int(raw.get("uc"), f"Pack {index + 1}: UC", 1, MAX_UC),
            "bonus_uc": _as_int(raw.get("bonus_uc") or 0, f"Pack {index + 1}: bonus UC", 0, MAX_BONUS_UC),
            "price": _as_price(raw.get("price")),
            "badge": " ".join(str(raw.get("badge") or "").split())[:20],
            "popular": bool(raw.get("popular")),
            "description": " ".join(str(raw.get("description") or "").split())[:200],
        }
        cleaned.append(entry)

    # Assign the final ids in one pass: a supplied id is kept when it is valid and
    # unique, otherwise one is slugified from the pack name. This keeps existing
    # pack ids stable across price edits while never colliding.
    normalised = normalize_packs(cleaned)
    if len(normalised) != len(cleaned):
        raise ApiError(400, "One of the packs could not be read", "invalid_packs")
    for clean, pack in zip(cleaned, normalised):
        clean["id"] = pack["id"]
    return cleaned


def save_pricing(
    db: Database,
    *,
    packs: Any,
    custom_uc_rate: Any = None,
    actor: str = "admin",
) -> None:
    cleaned = validate_packs(packs)
    payload = [dict(pack) for pack in cleaned]

    if custom_uc_rate is not None and custom_uc_rate != "":
        try:
            rate = round(float(custom_uc_rate), 2)
        except (TypeError, ValueError):
            raise ApiError(400, "Custom UC rate must be a number", "invalid_rate") from None
        if not 0 <= rate <= MAX_CUSTOM_RATE:
            raise ApiError(
                400,
                f"Custom UC rate must be between 0 and \u20b9{MAX_CUSTOM_RATE:,.0f}",
                "invalid_rate",
            )
        db.set_setting(CUSTOM_RATE_KEY, f"{rate:.2f}")

    db.set_setting(PACKS_KEY, json.dumps(payload, separators=(",", ":")))
    db.set_setting(UPDATED_AT_KEY, iso())
    db.set_setting(UPDATED_BY_KEY, actor)


def reset_pricing(db: Database) -> None:
    """Drop the overrides and go back to config.json."""
    for key in (PACKS_KEY, CUSTOM_RATE_KEY, UPDATED_AT_KEY, UPDATED_BY_KEY):
        db.execute("DELETE FROM settings WHERE key = ?", [key])
