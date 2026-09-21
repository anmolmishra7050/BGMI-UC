"""Configuration for the UC store.

Everything tunable lives in ``config.json`` at the project root.  Missing keys
fall back to the defaults below so the store still boots with no config file at
all.  A few secrets can also be supplied through environment variables, which is
handy when deploying.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "site": {
        "name": "UC Vault",
        "tagline": "Instant BGMI UC top-ups with UPI. Verified by hand, delivered fast.",
        "currency": "INR",
        "support_email": "support@example.com",
        "support_whatsapp": "",
        "delivery_note": (
            "UC is delivered to your BGMI player ID within 5-15 minutes "
            "after your payment is verified."
        ),
        "disclaimer": (
            "This store is an independent reseller and is not affiliated with, "
            "endorsed by, or sponsored by KRAFTON, Inc. or Level Infinite. "
            "BGMI and UC are trademarks of their respective owners."
        ),
        "highlights": [
            "100% manual verification - no chargebacks, no fraud",
            "UC delivered to your own player ID, never a middleman account",
            "Live order tracking with UPI reference (UTR) proof",
        ],
        "faq": [
            {
                "q": "How fast is the delivery?",
                "a": (
                    "Orders are usually fulfilled within 5-15 minutes of payment "
                    "verification. During peak hours it can take up to an hour."
                ),
            },
            {
                "q": "What is a UTR and where do I find it?",
                "a": (
                    "UTR (Unique Transaction Reference) is the 12-digit number your "
                    "UPI app shows after a successful payment. Open the payment in "
                    "GPay / PhonePe / Paytm and copy the UPI transaction ID."
                ),
            },
            {
                "q": "Do you need my account password?",
                "a": (
                    "Never. We only need your numeric BGMI player ID and character "
                    "name. Anyone asking for your password or OTP is a scammer."
                ),
            },
            {
                "q": "What if I entered the wrong player ID?",
                "a": (
                    "Message support immediately with your order ID. If the order has "
                    "not been delivered yet we will update the player ID for you."
                ),
            },
        ],
        # Displayed at the bottom of the storefront as social proof. Edit or
        # delete these freely - they are plain text, nothing is generated.
        "reviews": [
            {
                "name": "Rohit S.",
                "rating": 5,
                "date": "2026-08-24",
                "pack": "660 UC",
                "text": (
                    "Paid with GPay late at night and the UC was in my account before "
                    "the next match ended. Legit seller."
                ),
            },
            {
                "name": "Aman Verma",
                "rating": 5,
                "date": "2026-07-11",
                "pack": "1800 UC",
                "text": (
                    "Was sceptical about paying first, but the order page kept updating "
                    "so I could see it was being handled. Second order done today."
                ),
            },
            {
                "name": "ShadowKing_YT",
                "rating": 4,
                "date": "2026-06-29",
                "pack": "325 UC",
                "text": (
                    "Took around 20 minutes because it was peak hour, but the price is "
                    "much better than buying in game."
                ),
            },
            {
                "name": "Priya N.",
                "rating": 5,
                "date": "2026-05-18",
                "pack": "60 UC",
                "text": "Small top-up for the Royale Pass, delivered in about 6 minutes.",
            },
            {
                "name": "Faizan K.",
                "rating": 5,
                "date": "2026-03-30",
                "pack": "3850 UC",
                "text": (
                    "Ordered the big pack for a mythic outfit. Support answered on "
                    "WhatsApp within a minute."
                ),
            },
            {
                "name": "Nikhil Raut",
                "rating": 4,
                "date": "2026-02-14",
                "pack": "660 UC",
                "text": (
                    "Good service overall. Only suggestion is adding more payment "
                    "apps to the QR page."
                ),
            },
            {
                "name": "ARJUN_OP",
                "rating": 5,
                "date": "2026-01-22",
                "pack": "8100 UC",
                "text": (
                    "Third order from this store. Prices stay the same and they never "
                    "ask for your password."
                ),
            },
        ],
    },
    "payment": {
        "upi_id": "yourname@upi",
        "payee_name": "UC Vault",
        # "dynamic" builds a fresh QR per order (amount + order note baked in).
        # "static" serves your own QR image from web/ instead.
        "upi_qr_mode": "dynamic",
        "static_qr_image": "assets/my-qr.png",
        "order_expiry_minutes": 30,
        "min_order_amount": 20,
        "max_order_amount": 100000,
        "custom_uc_enabled": True,
        # Rupees per UC for the "custom amount" calculator.
        "custom_uc_rate": 1.2,
        "custom_uc_min": 60,
        "custom_uc_max": 20000,
        "instructions": [
            "Scan the QR with any UPI app (GPay, PhonePe, Paytm, BHIM).",
            "Pay the exact amount shown - partial payments cannot be traced.",
            "Copy the UTR / UPI transaction ID from your payment receipt.",
            "Paste it below and submit. We verify and deliver your UC.",
        ],
    },
    "packs": [
        {
            "id": "uc-60",
            "name": "Starter",
            "uc": 60,
            "bonus_uc": 0,
            "price": 85,
            "description": "Perfect for a quick Royale Pass mission top-up.",
        },
        {
            "id": "uc-325",
            "name": "Popular",
            "uc": 300,
            "bonus_uc": 25,
            "price": 375,
            "badge": "Most bought",
            "description": "The sweet spot for a new outfit or gun skin.",
        },
        {
            "id": "uc-660",
            "name": "Best value",
            "uc": 600,
            "bonus_uc": 60,
            "price": 725,
            "popular": True,
            "description": "Enough UC for a crate opening plus change.",
        },
        {
            "id": "uc-1800",
            "name": "Elite",
            "uc": 1800,
            "bonus_uc": 0,
            "price": 1899,
            "description": "For the mythic outfit grinders.",
        },
        {
            "id": "uc-3850",
            "name": "Pro",
            "uc": 3850,
            "bonus_uc": 0,
            "price": 3799,
            "description": "Big spender territory. Best per-UC rate for most players.",
        },
        {
            "id": "uc-8100",
            "name": "Whale",
            "uc": 8100,
            "bonus_uc": 0,
            "price": 7499,
            "description": "Maximum UC, lowest per-UC cost.",
        },
    ],
    "security": {
        "orders_per_10_minutes": 12,
        "payment_submissions_per_10_minutes": 20,
        "order_reads_per_minute": 240,
        "qr_requests_per_minute": 120,
        "admin_logins_per_10_minutes": 8,
        "feedback_per_hour": 6
    },
    "admin": {
        "username": "admin",
        # Used only to seed / reset the admin password. The real hash lives in the DB.
        "password": "admin123",
        "session_hours": 12,
        "cookie_secure": False,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8080,
        "db_path": "data/store.db",
    },
}

ENV_OVERRIDES = {
    "UC_UPI_ID": "payment.upi_id",
    "UC_PAYEE_NAME": "payment.payee_name",
    "UC_UPI_QR_MODE": "payment.upi_qr_mode",
    "UC_STATIC_QR_IMAGE": "payment.static_qr_image",
    "UC_ADMIN_USERNAME": "admin.username",
    "UC_ADMIN_PASSWORD": "admin.password",
    "UC_HOST": "server.host",
    "UC_PORT": "server.port",
    "UC_DB_PATH": "server.db_path",
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{0,39}$")


def slugify_pack_id(name: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    slug = slug[:32]
    return slug if slug and ID_PATTERN.match(slug) else fallback


def normalize_packs(raw_packs: Any) -> list[dict[str, Any]]:
    """Turn raw pack dictionaries into the shape the API and UI expect.

    Prices are converted to paise so every calculation stays in integers.  Rows
    that cannot be priced at all are dropped rather than breaking the store.
    """
    packs: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(raw_packs if isinstance(raw_packs, list) else []):
        if not isinstance(raw, dict):
            continue
        try:
            uc = int(raw.get("uc", 0) or 0)
            bonus = int(raw.get("bonus_uc", 0) or 0)
            price = float(raw.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if uc <= 0 or price <= 0 or bonus < 0:
            continue
        pack_id = str(raw.get("id") or "").strip().lower()
        if not ID_PATTERN.match(pack_id) or pack_id in used_ids:
            pack_id = slugify_pack_id(raw.get("name", ""), f"pack-{index + 1}")
        if pack_id in used_ids:
            pack_id = f"{pack_id}-{index + 1}"
        used_ids.add(pack_id)
        total = uc + bonus
        packs.append(
            {
                "id": pack_id,
                "name": str(raw.get("name") or f"{total} UC").strip()[:40],
                "uc": uc,
                "bonus_uc": bonus,
                "total_uc": total,
                "price_paise": int(round(price * 100)),
                "badge": str(raw.get("badge") or "").strip()[:20],
                "popular": bool(raw.get("popular")),
                "description": str(raw.get("description") or "").strip()[:200],
                "per_uc": round(price / total, 4) if total else 0.0,
            }
        )
    return packs


def _coerce_like(default: Any, raw: str) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            return default
    return raw


class Config:
    """Dotted-path access to the merged configuration tree."""

    def __init__(self, data: dict[str, Any], warnings: list[str] | None = None):
        self._data = data
        self.warnings = list(warnings or [])

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if isinstance(node, dict):
                if part not in node:
                    return default
                node = node[part]
            elif isinstance(node, list):
                try:
                    node = node[int(part)]
                except (ValueError, IndexError):
                    return default
            else:
                return default
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node: Any = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    # -- convenience accessors -------------------------------------------------
    @property
    def site(self) -> dict[str, Any]:
        return self.get("site", {})

    @property
    def payment(self) -> dict[str, Any]:
        return self.get("payment", {})

    @property
    def admin(self) -> dict[str, Any]:
        return self.get("admin", {})

    @property
    def server(self) -> dict[str, Any]:
        return self.get("server", {})

    @property
    def db_path(self) -> Path:
        raw = str(self.get("server.db_path", "data/store.db"))
        path = Path(raw)
        return path if path.is_absolute() else BASE_DIR / path

    def packs(self) -> list[dict[str, Any]]:
        """Pack list from config.json, normalised (prices converted to paise)."""
        return normalize_packs(self.get("packs", []))

    def find_pack(self, pack_id: str) -> dict[str, Any] | None:
        for pack in self.packs():
            if pack["id"] == pack_id:
                return pack
        return None


def _apply_env_overrides(data: dict[str, Any], defaults: dict[str, Any]) -> None:
    config = Config(data)
    for env_key, path in ENV_OVERRIDES.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        config.set(path, _coerce_like(Config(defaults).get(path, ""), raw))


def _load_raw_config() -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if not CONFIG_PATH.exists():
        warnings.append(
            f"No config.json found at {CONFIG_PATH}. Running on built-in defaults."
        )
        return {}, warnings
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not read config.json ({exc}). Using defaults.")
        return {}, warnings
    if not isinstance(raw, dict):
        warnings.append("config.json must contain a JSON object. Using defaults.")
        return {}, warnings
    return raw, warnings


def load_config() -> Config:
    raw, warnings = _load_raw_config()
    merged = _deep_merge(DEFAULTS, raw)
    _apply_env_overrides(merged, DEFAULTS)
    config = Config(merged, warnings)

    upi_id = str(config.get("payment.upi_id", "")).strip()
    if not upi_id or upi_id == DEFAULTS["payment"]["upi_id"]:
        config.warnings.append(
            "payment.upi_id is still the placeholder - set your real UPI ID "
            "in config.json so buyers pay the right account."
        )
    static_qr = WEB_DIR / str(config.get("payment.static_qr_image", ""))
    if config.get("payment.upi_qr_mode") == "static" and not static_qr.is_file():
        config.warnings.append(
            f"payment.upi_qr_mode is 'static' but {static_qr} does not exist. "
            "Falling back to dynamically generated QR codes."
        )
    if str(config.get("admin.password", "")) == DEFAULTS["admin"]["password"]:
        config.warnings.append(
            "Admin password is still the default 'admin123'. Change it with: "
            "python server.py --set-admin-password"
        )
    return config
