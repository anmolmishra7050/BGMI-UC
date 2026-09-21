"""API routes for the UC store.

Public surface
--------------
GET  /api/health
GET  /api/config                      site + packs + payment settings
POST /api/feedback                    private feedback for the shop owner only
GET  /api/orders/{id}                 order status for the buyer
POST /api/orders/{id}/payment         submit UPI reference (UTR)
POST /api/orders/{id}/cancel          buyer cancels before paying
GET  /api/orders/{id}/qr.png          UPI QR image for that order

Admin surface (session cookie or X-Admin-Token header)
------------------------------------------------------
POST /api/admin/login
POST /api/admin/logout
GET  /api/admin/session
GET  /api/admin/feedback              feedback inbox (never shown publicly)
DELETE /api/admin/feedback/{id}       remove a feedback entry
GET  /api/admin/pricing               current packs + custom-UC rate
POST /api/admin/pricing               save pack prices (PUT works too)
POST /api/admin/pricing/reset         revert prices to config.json
GET  /api/admin/stats
GET  /api/admin/orders
GET  /api/admin/orders.csv
GET  /api/admin/orders/{id}
POST /api/admin/orders/{id}/status
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Pattern
from urllib.parse import urlparse

from . import auth, pricing
from .config import WEB_DIR, Config
from .db import Database, expires_at, iso, new_order_id, parse_iso, utcnow
from .http_utils import (
    ApiError,
    Response,
    RateLimiter,
    error_response,
    json_response,
    static_response,
)
from .payments import QRUnavailable, build_upi_link, qr_available, qr_png_bytes

ORDER_ID_PATTERN = r"(?P<order_id>UC-[A-Za-z0-9]{4,12})"

STATUS_LABELS = {
    "pending_payment": "Awaiting payment",
    "awaiting_review": "Payment submitted - under review",
    "processing": "Payment verified - delivering UC",
    "completed": "Delivered",
    "rejected": "Payment rejected",
    "expired": "Order expired",
    "cancelled": "Cancelled",
}

STATUS_MESSAGES = {
    "pending_payment": "Scan the QR with any UPI app and pay the exact amount shown.",
    "awaiting_review": "We received your payment reference. Verification usually takes a few minutes.",
    "processing": "Payment verified. Your UC is being delivered to your player ID.",
    "completed": "Delivered. Happy fragging! Keep this page as your receipt.",
    "rejected": "We could not match this payment. Contact support with your UTR.",
    "expired": "This order expired before payment. Create a new one to continue.",
    "cancelled": "This order was cancelled.",
}

EVENT_LABELS = {
    "order_created": "Order created",
    "payment_submitted": "Payment proof submitted",
    "payment_updated": "Payment proof updated",
    "payment_verified": "Payment verified",
    "delivery_started": "Delivery started",
    "delivered": "UC delivered",
    "rejected": "Payment rejected",
    "cancelled": "Order cancelled",
    "expired": "Order expired",
    "note": "Note from support",
}

ADMIN_SETTABLE_STATUSES = {
    "pending_payment",
    "awaiting_review",
    "processing",
    "completed",
    "rejected",
    "cancelled",
}

STATUS_TO_EVENT = {
    "processing": "delivery_started",
    "completed": "delivered",
    "rejected": "rejected",
    "cancelled": "cancelled",
    "awaiting_review": "payment_verified",
    "pending_payment": "note",
}


@dataclass
class Ctx:
    config: Config
    db: Database
    limiter: RateLimiter
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    client_ip: str = "0.0.0.0"

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


Handler = Callable[[Ctx, re.Match[str]], Response]
ROUTES: list[tuple[str, Pattern[str], Handler]] = []


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    compiled = re.compile("^" + pattern + "$")

    def decorator(func: Handler) -> Handler:
        ROUTES.append((method, compiled, func))
        return func

    return decorator


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def format_inr(paise: int) -> str:
    """Format paise as Indian rupees, e.g. 749900 -> '\u20b9 7,499.00'."""
    rupees, paise_part = divmod(int(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"\u20b9 {digits}.{paise_part:02d}"


MAX_REVIEWS = 24


def normalise_reviews(site: dict[str, Any]) -> list[dict[str, Any]]:
    """The storefront's published reviews (from config.json `site.reviews`).

    These are the shop's own social proof. Feedback submitted by visitors never
    enters this list - it goes to the admin inbox instead.
    """
    reviews: list[dict[str, Any]] = []
    for raw in list(site.get("reviews") or [])[:MAX_REVIEWS]:
        if not isinstance(raw, dict):
            continue
        text = " ".join(str(raw.get("text") or "").split())[:400]
        if not text:
            continue
        try:
            rating = int(raw.get("rating", 5))
        except (TypeError, ValueError):
            rating = 5
        reviews.append(
            {
                "name": " ".join(str(raw.get("name") or "Player").split())[:40] or "Player",
                "rating": min(5, max(1, rating)),
                "date": sget(raw.get("date"), 32),
                "pack": " ".join(str(raw.get("pack") or "").split())[:20],
                "text": text,
            }
        )
    return reviews


def mask_contact(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    if "@" in value:
        name, _, domain = value.partition("@")
        visible = name[:2]
        return f"{visible}{'*' * max(1, len(name) - 2)}@{domain}"
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 4:
        return "*" * (len(digits) - 4) + digits[-4:]
    return "*" * len(value)


def require_rate_limit(ctx: Ctx, key: str, limit: int, window: float, message: str) -> None:
    if not ctx.limiter.allow(key, limit, window):
        raise ApiError(429, message, "rate_limited")


def security_limit(ctx: Ctx, name: str, default: int) -> int:
    """Per-IP request budgets are tunable from config.json under "security"."""
    try:
        return int(ctx.config.get(f"security.{name}", default))
    except (TypeError, ValueError):
        return default


def sget(value: Any, max_len: int = 200) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def validate_player_id(raw: Any) -> str:
    value = sget(raw, 24)
    digits = re.sub(r"\D", "", value)
    if not 8 <= len(digits) <= 12:
        raise ApiError(400, "BGMI player ID must be 8-12 digits", "invalid_player_id")
    return digits


def validate_player_name(raw: Any) -> str:
    value = " ".join(sget(raw, 40).split())
    if not 2 <= len(value) <= 32:
        raise ApiError(400, "Character name must be 2-32 characters", "invalid_player_name")
    if any(ord(ch) < 32 for ch in value):
        raise ApiError(400, "Character name contains invalid characters", "invalid_player_name")
    return value


def validate_contact(raw: Any) -> str:
    value = sget(raw, 80)
    if not value:
        return ""
    if "@" in value:
        if not re.match(r"^[^@\s]{1,64}@[^@\s.]+\.[A-Za-z]{2,}$", value):
            raise ApiError(400, "Enter a valid email address", "invalid_contact")
        return value
    digits = re.sub(r"\D", "", value)
    if not 10 <= len(digits) <= 15:
        raise ApiError(400, "Enter a valid email or phone number", "invalid_contact")
    return digits


def validate_utr(raw: Any) -> str:
    value = re.sub(r"\s+", "", sget(raw, 40)).upper()
    if not 6 <= len(value) <= 40:
        raise ApiError(
            400,
            "UTR / UPI transaction ID must be 6-40 characters",
            "invalid_utr",
        )
    if not re.match(r"^[A-Za-z0-9\-/]+$", value):
        raise ApiError(
            400,
            "UTR can only contain letters, digits, '-' and '/'",
            "invalid_utr",
        )
    return value


def get_order_or_404(ctx: Ctx, order_id: str):
    row = ctx.db.query_one("SELECT * FROM orders WHERE id = ?", [order_id.upper()])
    if row is None:
        raise ApiError(404, "Order not found", "order_not_found")
    return row


def maybe_expire(ctx: Ctx, row) -> Any:
    """Flip a stale pending order to 'expired' on read."""
    if row["status"] != "pending_payment":
        return row
    deadline = parse_iso(row["expires_at"])
    if deadline is None or deadline > utcnow():
        return row
    ctx.db.execute(
        "UPDATE orders SET status = 'expired', updated_at = ? WHERE id = ? AND status = 'pending_payment'",
        [iso(), row["id"]],
    )
    ctx.db.add_event(row["id"], "expired", "Payment window closed")
    return get_order_or_404(ctx, row["id"])


def payment_payload(ctx: Ctx, row) -> dict[str, Any]:
    payment = ctx.config.payment
    upi_id = sget(payment.get("upi_id"), 80)
    amount = int(row["amount_paise"])
    return {
        "method": "upi",
        "upi_id": upi_id,
        "payee_name": sget(payment.get("payee_name"), 60),
        "upi_link": row["upi_link"] or "",
        "qr_url": f"/api/orders/{row['id']}/qr.png?v={row['updated_at']}",
        "qr_mode": payment.get("upi_qr_mode", "dynamic"),
        "amount_paise": amount,
        "amount_display": format_inr(amount),
        "reference": row["id"],
        "instructions": list(payment.get("instructions") or []),
        "expires_at": row["expires_at"],
    }


def timeline_payload(ctx: Ctx, row, include_admin_detail: bool) -> list[dict[str, Any]]:
    events = []
    for event in ctx.db.order_events(row["id"]):
        detail = event["detail"] or ""
        if event["actor"] == "admin" and not include_admin_detail:
            detail = ""
        events.append(
            {
                "event": event["event"],
                "label": EVENT_LABELS.get(event["event"], event["event"]),
                "detail": detail,
                "actor": event["actor"],
                "created_at": event["created_at"],
            }
        )
    return events


def public_order(ctx: Ctx, row) -> dict[str, Any]:
    status = row["status"]
    deadline = parse_iso(row["expires_at"])
    remaining = 0
    if status == "pending_payment" and deadline:
        remaining = max(0, int((deadline - utcnow()).total_seconds()))
    return {
        "id": row["id"],
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "status_message": STATUS_MESSAGES.get(status, ""),
        "pack_id": row["pack_id"],
        "pack_name": row["pack_name"],
        "uc_amount": int(row["uc_amount"]),
        "bonus_uc": int(row["bonus_uc"]),
        "total_uc": int(row["uc_amount"]) + int(row["bonus_uc"]),
        "amount_paise": int(row["amount_paise"]),
        "amount_display": format_inr(int(row["amount_paise"])),
        "player_id": row["player_id"],
        "player_name": row["player_name"],
        "contact_masked": mask_contact(row["contact"]),
        "buyer_note": row["buyer_note"] or "",
        "utr": row["utr"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
        "expires_in_seconds": remaining,
        "paid_at": row["paid_at"],
        "delivered_at": row["delivered_at"],
        "payment": payment_payload(ctx, row),
        "timeline": timeline_payload(ctx, row, include_admin_detail=False),
    }


def admin_order(ctx: Ctx, row) -> dict[str, Any]:
    payload = public_order(ctx, row)
    payload["contact"] = row["contact"] or ""
    payload["admin_note"] = row["admin_note"] or ""
    payload["timeline"] = timeline_payload(ctx, row, include_admin_detail=True)
    return payload


def require_admin(ctx: Ctx):
    token = ctx.cookies.get("admin_session") or ctx.header("x-admin-token") or ""
    session = auth.get_session(ctx.db, token)
    if session is None:
        raise ApiError(401, "Admin login required", "unauthorized")
    if ctx.method in {"POST", "PUT", "PATCH", "DELETE"}:
        assert_same_origin(ctx)
    return session


def assert_same_origin(ctx: Ctx) -> None:
    """Reject cross-site state changes when cookie auth is in play."""
    origin = ctx.header("origin")
    if not origin:
        return
    host = urlparse(origin).netloc
    if host and host != ctx.header("host"):
        raise ApiError(403, "Cross-origin request blocked", "bad_origin")


# --------------------------------------------------------------------------- #
# public routes
# --------------------------------------------------------------------------- #
@route("GET", r"/api/health")
def api_health(ctx: Ctx, _match) -> Response:
    return json_response(
        {
            "status": "ok",
            "time": iso(),
            "orders": ctx.db.query_one("SELECT COUNT(*) AS c FROM orders")["c"],
            "qr_generation": qr_available(),
        }
    )


@route("GET", r"/api/config")
def api_config(ctx: Ctx, _match) -> Response:
    site = ctx.config.site
    payment = ctx.config.payment
    packs = pricing.effective_packs(ctx.db, ctx.config)
    settings = {
        "per_uc_rate": pricing.effective_custom_rate(ctx.db, ctx.config),
        "custom_uc_min": int(payment.get("custom_uc_min", 60) or 60),
        "custom_uc_max": int(payment.get("custom_uc_max", 20000) or 20000),
    }
    return json_response(
        {
            "site": {
                "name": sget(site.get("name"), 60),
                "tagline": sget(site.get("tagline"), 200),
                "currency": sget(site.get("currency"), 8) or "INR",
                "support_email": sget(site.get("support_email"), 80),
                "support_whatsapp": sget(site.get("support_whatsapp"), 40),
                "delivery_note": sget(site.get("delivery_note"), 400),
                "disclaimer": sget(site.get("disclaimer"), 600),
                "highlights": list(site.get("highlights") or []),
                "faq": list(site.get("faq") or []),
                "reviews": normalise_reviews(site),
            },
            "packs": packs,
            "payment": {
                "enabled_methods": ["upi"],
                "upi_id": sget(payment.get("upi_id"), 80),
                "payee_name": sget(payment.get("payee_name"), 60),
                "qr_mode": payment.get("upi_qr_mode", "dynamic"),
                "expiry_minutes": int(payment.get("order_expiry_minutes", 30) or 30),
                "min_order_paise": int(round(float(payment.get("min_order_amount", 20) or 20) * 100)),
                "max_order_paise": int(round(float(payment.get("max_order_amount", 100000) or 100000) * 100)),
                "custom_uc_enabled": bool(payment.get("custom_uc_enabled", True)),
                "instructions": list(payment.get("instructions") or []),
            },
            "custom_uc": settings,
            "status_labels": STATUS_LABELS,
        }
    )


@route("POST", r"/api/feedback")
def api_submit_feedback(ctx: Ctx, _match) -> Response:
    """Take a visitor's feedback.

    Deliberately private: the entry is stored for the shop owner and is never
    returned by any public endpoint, so a visitor's words are not published on
    the storefront. The browser also keeps its own copy locally.
    """
    require_rate_limit(
        ctx,
        f"feedback:{ctx.client_ip}",
        security_limit(ctx, "feedback_per_hour", 6),
        3600,
        "You have already sent feedback recently. Try again later.",
    )
    message = " ".join(str(ctx.body.get("message") or "").split())
    if len(message) < 4:
        raise ApiError(400, "Please write a little more before sending", "invalid_message")
    if len(message) > 500:
        raise ApiError(400, "Feedback must be 500 characters or fewer", "invalid_message")

    try:
        rating = int(ctx.body.get("rating") or 0)
    except (TypeError, ValueError):
        raise ApiError(400, "Rating must be a number from 1 to 5", "invalid_rating") from None
    if not 1 <= rating <= 5:
        raise ApiError(400, "Rating must be from 1 to 5 stars", "invalid_rating")

    name = " ".join(str(ctx.body.get("name") or "").split())[:40] or "Anonymous"
    contact = validate_contact(ctx.body.get("contact")) if ctx.body.get("contact") else ""
    created = iso()
    cursor = ctx.db.execute(
        "INSERT INTO feedback (name, rating, message, contact, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [name, rating, message, contact, created],
    )
    return json_response(
        {
            "ok": True,
            "feedback": {
                "id": cursor.lastrowid,
                "name": name,
                "rating": rating,
                "message": message,
                "created_at": created,
            },
            "note": "Thanks! Your feedback goes privately to the store - it is not posted publicly.",
        },
        201,
    )


@route("POST", r"/api/orders")
def api_create_order(ctx: Ctx, _match) -> Response:
    require_rate_limit(
        ctx,
        f"create:{ctx.client_ip}",
        security_limit(ctx, "orders_per_10_minutes", 12),
        600,
        "Too many orders from this IP. Try again later.",
    )
    data = ctx.body
    payment = ctx.config.payment

    player_id = validate_player_id(data.get("player_id"))
    player_name = validate_player_name(data.get("player_name"))
    contact = validate_contact(data.get("contact"))
    buyer_note = sget(data.get("note"), 200)
    method = sget(data.get("payment_method"), 20).lower() or "upi"
    if method != "upi":
        raise ApiError(400, "Only UPI payments are supported right now", "unsupported_method")

    pack_id = sget(data.get("pack_id"), 60)
    custom_uc_raw = data.get("custom_uc")
    has_custom = custom_uc_raw not in (None, "", 0, "0")
    if pack_id and has_custom:
        raise ApiError(
            400, "Send either pack_id or custom_uc, not both", "conflicting_selection"
        )

    if pack_id:
        pack = pricing.find_pack(ctx.db, ctx.config, pack_id)
        if pack is None:
            raise ApiError(400, "Unknown UC pack", "invalid_pack")
        uc_amount = pack["uc"]
        bonus_uc = pack["bonus_uc"]
        amount_paise = pack["price_paise"]
        pack_name = f"{pack['total_uc']} UC - {pack['name']}"
    elif has_custom:
        if not payment.get("custom_uc_enabled", True):
            raise ApiError(400, "Custom UC amounts are disabled", "custom_uc_disabled")
        try:
            quantity = int(custom_uc_raw)
        except (TypeError, ValueError):
            raise ApiError(400, "Enter a valid UC quantity", "invalid_uc_quantity") from None
        minimum = int(payment.get("custom_uc_min", 60) or 60)
        maximum = int(payment.get("custom_uc_max", 20000) or 20000)
        if not minimum <= quantity <= maximum:
            raise ApiError(
                400, f"Custom UC must be between {minimum} and {maximum}", "invalid_uc_quantity"
            )
        rate = pricing.effective_custom_rate(ctx.db, ctx.config)
        amount_paise = int(round(quantity * rate * 100))
        uc_amount = quantity
        bonus_uc = 0
        pack_name = f"{quantity} UC - Custom"
        pack_id = ""
    else:
        raise ApiError(400, "Choose a UC pack or enter a custom amount", "missing_pack")

    minimum_order = int(round(float(payment.get("min_order_amount", 20) or 20) * 100))
    maximum_order = int(round(float(payment.get("max_order_amount", 100000) or 100000) * 100))
    if amount_paise < minimum_order:
        raise ApiError(400, f"Minimum order value is {format_inr(minimum_order)}", "amount_too_low")
    if amount_paise > maximum_order:
        raise ApiError(400, f"Maximum order value is {format_inr(maximum_order)}", "amount_too_high")

    order_id = new_order_id()
    while ctx.db.query_one("SELECT 1 FROM orders WHERE id = ?", [order_id]):
        order_id = new_order_id()

    expiry_minutes = int(payment.get("order_expiry_minutes", 30) or 30)
    now = iso()
    upi_link = build_upi_link(
        upi_id=sget(payment.get("upi_id"), 80),
        payee_name=sget(payment.get("payee_name"), 60),
        amount_paise=amount_paise,
        note=f"{pack_name} {player_id}",
        reference=order_id,
    )

    ctx.db.execute(
        """
        INSERT INTO orders (
            id, pack_id, pack_name, uc_amount, bonus_uc, amount_paise, player_id,
            player_name, contact, buyer_note, status, payment_method, upi_link,
            created_at, updated_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment', ?, ?, ?, ?, ?)
        """,
        [
            order_id,
            pack_id,
            pack_name,
            uc_amount,
            bonus_uc,
            amount_paise,
            player_id,
            player_name,
            contact,
            buyer_note,
            method,
            upi_link,
            now,
            now,
            expires_at(expiry_minutes),
        ],
    )
    ctx.db.add_event(order_id, "order_created", f"{pack_name} for player {player_id}", "buyer")

    row = get_order_or_404(ctx, order_id)
    return json_response({"order": public_order(ctx, row)}, 201)


@route("GET", r"/api/orders/" + ORDER_ID_PATTERN)
def api_get_order(ctx: Ctx, match) -> Response:
    require_rate_limit(
        ctx,
        f"read:{ctx.client_ip}",
        security_limit(ctx, "order_reads_per_minute", 240),
        60,
        "Slow down a little.",
    )
    row = maybe_expire(ctx, get_order_or_404(ctx, match.group("order_id")))
    return json_response({"order": public_order(ctx, row)})


@route("POST", r"/api/orders/" + ORDER_ID_PATTERN + r"/payment")
def api_submit_payment(ctx: Ctx, match) -> Response:
    require_rate_limit(
        ctx,
        f"pay:{ctx.client_ip}",
        security_limit(ctx, "payment_submissions_per_10_minutes", 20),
        600,
        "Too many payment submissions. Try again later.",
    )
    row = maybe_expire(ctx, get_order_or_404(ctx, match.group("order_id")))
    status = row["status"]
    if status in {"completed", "cancelled", "rejected"}:
        raise ApiError(409, f"This order is {STATUS_LABELS.get(status, status)}", "order_closed")

    utr = validate_utr(ctx.body.get("utr"))
    duplicate = ctx.db.query_one(
        "SELECT id FROM orders WHERE utr = ? AND id <> ? AND status IN ('awaiting_review','processing','completed')",
        [utr, row["id"]],
    )
    if duplicate is not None:
        raise ApiError(
            409,
            "This UTR is already attached to another order. Contact support if that was a mistake.",
            "duplicate_utr",
        )

    first_submission = status in {"pending_payment", "expired"}
    if first_submission:
        ctx.db.execute(
            "UPDATE orders SET status = 'awaiting_review', utr = ?, "
            "paid_at = COALESCE(paid_at, ?), updated_at = ? WHERE id = ?",
            [utr, iso(), iso(), row["id"]],
        )
    else:
        ctx.db.execute(
            "UPDATE orders SET status = 'awaiting_review', utr = ?, updated_at = ? WHERE id = ?",
            [utr, iso(), row["id"]],
        )
    ctx.db.add_event(
        row["id"],
        "payment_submitted" if first_submission else "payment_updated",
        f"UTR {utr}",
        "buyer",
    )
    fresh = get_order_or_404(ctx, row["id"])
    return json_response({"order": public_order(ctx, fresh)})


@route("POST", r"/api/orders/" + ORDER_ID_PATTERN + r"/cancel")
def api_cancel_order(ctx: Ctx, match) -> Response:
    row = maybe_expire(ctx, get_order_or_404(ctx, match.group("order_id")))
    if row["status"] not in {"pending_payment", "expired"}:
        raise ApiError(409, "Only unpaid orders can be cancelled", "cannot_cancel")
    ctx.db.execute(
        "UPDATE orders SET status = 'cancelled', updated_at = ? WHERE id = ?",
        [iso(), row["id"]],
    )
    ctx.db.add_event(row["id"], "cancelled", "Cancelled by buyer", "buyer")
    return json_response({"order": public_order(ctx, get_order_or_404(ctx, row["id"]))})


@route("GET", r"/api/orders/" + ORDER_ID_PATTERN + r"/qr\.png")
def api_order_qr(ctx: Ctx, match) -> Response:
    require_rate_limit(
        ctx,
        f"qr:{ctx.client_ip}",
        security_limit(ctx, "qr_requests_per_minute", 120),
        60,
        "Slow down a little.",
    )
    row = get_order_or_404(ctx, match.group("order_id"))
    payment = ctx.config.payment

    if payment.get("upi_qr_mode") == "static":
        static_rel = sget(payment.get("static_qr_image"), 200)
        candidate = (WEB_DIR / static_rel).resolve()
        try:
            candidate.relative_to(WEB_DIR.resolve())
        except ValueError:
            candidate = None
        if candidate and candidate.is_file():
            return Response(
                200,
                candidate.read_bytes(),
                "image/png" if candidate.suffix.lower() == ".png" else "image/jpeg",
                {"Cache-Control": "no-store"},
            )

    link = row["upi_link"] or build_upi_link(
        upi_id=sget(payment.get("upi_id"), 80),
        payee_name=sget(payment.get("payee_name"), 60),
        amount_paise=int(row["amount_paise"]),
        note=row["pack_name"],
        reference=row["id"],
    )
    try:
        png = qr_png_bytes(link)
    except QRUnavailable as exc:
        return error_response(503, str(exc), "qr_unavailable")
    return Response(200, png, "image/png", {"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- #
# admin routes
# --------------------------------------------------------------------------- #
@route("POST", r"/api/admin/login")
def api_admin_login(ctx: Ctx, _match) -> Response:
    require_rate_limit(
        ctx,
        f"login:{ctx.client_ip}",
        security_limit(ctx, "admin_logins_per_10_minutes", 8),
        600,
        "Too many login attempts. Wait a few minutes.",
    )
    username = sget(ctx.body.get("username"), 60)
    password = str(ctx.body.get("password") or "")
    if not auth.check_credentials(ctx.db, username, password):
        raise ApiError(401, "Invalid username or password", "invalid_credentials")

    hours = int(ctx.config.admin.get("session_hours", 12) or 12)
    token, expires = auth.create_session(ctx.db, auth.admin_username(ctx.db), hours)
    cookie = (
        f"admin_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={hours * 3600}"
    )
    if ctx.config.admin.get("cookie_secure"):
        cookie += "; Secure"
    return json_response(
        {"ok": True, "username": auth.admin_username(ctx.db), "expires_at": expires, "token": token},
        headers={"Set-Cookie": cookie},
    )


@route("POST", r"/api/admin/logout")
def api_admin_logout(ctx: Ctx, _match) -> Response:
    token = ctx.cookies.get("admin_session") or ctx.header("x-admin-token")
    auth.delete_session(ctx.db, token)
    return json_response(
        {"ok": True},
        headers={"Set-Cookie": "admin_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"},
    )


@route("GET", r"/api/admin/session")
def api_admin_session(ctx: Ctx, _match) -> Response:
    token = ctx.cookies.get("admin_session") or ctx.header("x-admin-token")
    session = auth.get_session(ctx.db, token)
    if session is None:
        return json_response({"authenticated": False})
    return json_response(
        {"authenticated": True, "username": session["username"], "expires_at": session["expires_at"]}
    )


@route("GET", r"/api/admin/feedback")
def api_admin_feedback(ctx: Ctx, _match) -> Response:
    require_admin(ctx)
    rows = ctx.db.query(
        "SELECT id, name, rating, message, contact, created_at FROM feedback "
        "ORDER BY id DESC LIMIT 200"
    )
    total = ctx.db.query_one("SELECT COUNT(*) AS c FROM feedback")
    return json_response(
        {
            "feedback": [dict(row) for row in rows],
            "total": total["c"] if total else 0,
        }
    )


@route("DELETE", r"/api/admin/feedback/(?P<feedback_id>\d+)")
@route("POST", r"/api/admin/feedback/(?P<feedback_id>\d+)/delete")
def api_admin_feedback_delete(ctx: Ctx, match) -> Response:
    require_admin(ctx)
    cursor = ctx.db.execute(
        "DELETE FROM feedback WHERE id = ?", [int(match.group("feedback_id"))]
    )
    if not cursor.rowcount:
        raise ApiError(404, "Feedback entry not found", "feedback_not_found")
    return json_response({"ok": True})


def pricing_payload(ctx: Ctx) -> dict[str, Any]:
    payment = ctx.config.payment
    return {
        "packs": pricing.effective_packs(ctx.db, ctx.config),
        "custom_uc_rate": pricing.effective_custom_rate(ctx.db, ctx.config),
        "meta": pricing.meta(ctx.db),
        "config_packs": ctx.config.packs(),
        "limits": {
            "max_packs": pricing.MAX_PACKS,
            "max_uc": pricing.MAX_UC,
            "min_price": pricing.MIN_PRICE,
            "max_price": pricing.MAX_PRICE,
            "custom_uc_min": int(payment.get("custom_uc_min", 60) or 60),
            "custom_uc_max": int(payment.get("custom_uc_max", 20000) or 20000),
            "min_order_paise": int(
                round(float(payment.get("min_order_amount", 20) or 20) * 100)
            ),
        },
    }


@route("GET", r"/api/admin/pricing")
def api_admin_get_pricing(ctx: Ctx, _match) -> Response:
    require_admin(ctx)
    return json_response(pricing_payload(ctx))


@route("PUT", r"/api/admin/pricing")
@route("POST", r"/api/admin/pricing")
def api_admin_update_pricing(ctx: Ctx, _match) -> Response:
    session = require_admin(ctx)
    pricing.save_pricing(
        ctx.db,
        packs=ctx.body.get("packs"),
        custom_uc_rate=ctx.body.get("custom_uc_rate"),
        actor=session["username"],
    )
    payload = pricing_payload(ctx)
    payload["ok"] = True
    return json_response(payload)


@route("POST", r"/api/admin/pricing/reset")
def api_admin_reset_pricing(ctx: Ctx, _match) -> Response:
    require_admin(ctx)
    pricing.reset_pricing(ctx.db)
    payload = pricing_payload(ctx)
    payload["ok"] = True
    return json_response(payload)


@route("GET", r"/api/admin/stats")
def api_admin_stats(ctx: Ctx, _match) -> Response:
    require_admin(ctx)
    rows = ctx.db.query(
        "SELECT status, COUNT(*) AS count, COALESCE(SUM(amount_paise), 0) AS total "
        "FROM orders GROUP BY status"
    )
    counts = {row["status"]: row["count"] for row in rows}
    totals = {row["status"]: int(row["total"]) for row in rows}
    today = iso(utcnow().replace(hour=0, minute=0, second=0, microsecond=0))
    today_row = ctx.db.query_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(amount_paise), 0) AS total FROM orders "
        "WHERE created_at >= ?",
        [today],
    )
    delivered_today = ctx.db.query_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(amount_paise), 0) AS total FROM orders "
        "WHERE status = 'completed' AND delivered_at >= ?",
        [today],
    )
    return json_response(
        {
            "counts": counts,
            "revenue_paise": {
                "completed": totals.get("completed", 0),
                "in_review": totals.get("awaiting_review", 0) + totals.get("processing", 0),
            },
            "orders_today": today_row["count"] if today_row else 0,
            "value_today_paise": int(today_row["total"]) if today_row else 0,
            "delivered_today": delivered_today["count"] if delivered_today else 0,
            "delivered_today_paise": int(delivered_today["total"]) if delivered_today else 0,
            "needs_action": counts.get("awaiting_review", 0),
        }
    )


def _order_filters(ctx: Ctx) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    status = sget(ctx.query.get("status"), 30)
    if status and status != "all":
        if status not in STATUS_LABELS:
            raise ApiError(400, "Unknown status filter", "invalid_status")
        clauses.append("status = ?")
        params.append(status)
    search = sget(ctx.query.get("q"), 60)
    if search:
        like = f"%{search}%"
        clauses.append(
            "(id LIKE ? OR player_id LIKE ? OR player_name LIKE ? OR utr LIKE ? OR contact LIKE ?)"
        )
        params.extend([like] * 5)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


@route("GET", r"/api/admin/orders")
def api_admin_orders(ctx: Ctx, _match) -> Response:
    require_admin(ctx)
    where, params = _order_filters(ctx)
    try:
        limit = min(200, max(1, int(ctx.query.get("limit", 50))))
        offset = max(0, int(ctx.query.get("offset", 0)))
    except ValueError:
        raise ApiError(400, "limit/offset must be numbers", "invalid_pagination") from None

    total_row = ctx.db.query_one(f"SELECT COUNT(*) AS c FROM orders {where}", params)
    rows = ctx.db.query(
        f"SELECT * FROM orders {where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    counts = {
        row["status"]: row["c"]
        for row in ctx.db.query("SELECT status, COUNT(*) AS c FROM orders GROUP BY status")
    }
    return json_response(
        {
            "orders": [admin_order(ctx, row) for row in rows],
            "total": total_row["c"] if total_row else 0,
            "limit": limit,
            "offset": offset,
            "counts": counts,
        }
    )


@route("GET", r"/api/admin/orders\.csv")
def api_admin_orders_csv(ctx: Ctx, _match) -> Response:
    require_admin(ctx)
    where, params = _order_filters(ctx)
    rows = ctx.db.query(f"SELECT * FROM orders {where} ORDER BY created_at DESC", params)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "order_id", "created_at", "status", "pack", "uc", "amount_inr", "player_id",
            "player_name", "contact", "utr", "paid_at", "delivered_at", "admin_note",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["id"], row["created_at"], row["status"], row["pack_name"],
                int(row["uc_amount"]) + int(row["bonus_uc"]),
                f"{int(row['amount_paise']) / 100:.2f}", row["player_id"], row["player_name"],
                row["contact"] or "", row["utr"] or "", row["paid_at"] or "",
                row["delivered_at"] or "", row["admin_note"] or "",
            ]
        )
    return Response(
        200,
        buffer.getvalue().encode("utf-8"),
        "text/csv; charset=utf-8",
        {
            "Content-Disposition": 'attachment; filename="orders.csv"',
            "Cache-Control": "no-store",
        },
    )


@route("GET", r"/api/admin/orders/" + ORDER_ID_PATTERN)
def api_admin_order_detail(ctx: Ctx, match) -> Response:
    require_admin(ctx)
    row = get_order_or_404(ctx, match.group("order_id"))
    return json_response({"order": admin_order(ctx, row)})


@route("POST", r"/api/admin/orders/" + ORDER_ID_PATTERN + r"/status")
def api_admin_set_status(ctx: Ctx, match) -> Response:
    session = require_admin(ctx)
    row = get_order_or_404(ctx, match.group("order_id"))

    status = sget(ctx.body.get("status"), 30)
    if status not in ADMIN_SETTABLE_STATUSES:
        raise ApiError(400, "Unknown order status", "invalid_status")

    admin_note = sget(ctx.body.get("admin_note"), 300)
    now = iso()
    delivered_at = now if status == "completed" else row["delivered_at"]

    ctx.db.execute(
        """
        UPDATE orders
           SET status = ?, admin_note = ?, updated_at = ?, delivered_at = ?
         WHERE id = ?
        """,
        [status, admin_note or (row["admin_note"] or ""), now, delivered_at, row["id"]],
    )
    event = STATUS_TO_EVENT.get(status, "note")
    ctx.db.add_event(row["id"], event, admin_note or None, "admin")
    if status == "completed" and not row["delivered_at"]:
        ctx.db.add_event(row["id"], "delivered", f"Delivered by {session['username']}", "admin")

    fresh = get_order_or_404(ctx, row["id"])
    return json_response({"order": admin_order(ctx, fresh)})


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #
def handle(ctx: Ctx) -> Response:
    for method, pattern, handler in ROUTES:
        if method != ctx.method:
            continue
        match = pattern.match(ctx.path)
        if match:
            return handler(ctx, match)
    if ctx.path.startswith("/api/"):
        return error_response(404, "No such API endpoint", "not_found")
    if ctx.method not in {"GET", "HEAD"}:
        return error_response(405, "Method not allowed", "method_not_allowed")
    return static_response(WEB_DIR, ctx.path)
