"""End-to-end tests for the UC store API.

Runs the real HTTP server on an ephemeral port against a throwaway SQLite file.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import iso, utcnow  # noqa: E402
from app.http_utils import RateLimiter  # noqa: E402

ADMIN_PASSWORD = "secret1234"
ADMIN_USER = "tester"


class StoreApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="uc-store-test-")
        cls.config = load_config()
        cls.config.set("server.db_path", str(Path(cls.tmp.name) / "test.db"))
        cls.config.set("payment.upi_id", "teststore@okaxis")
        cls.config.set("payment.payee_name", "Test Store")
        cls.config.set("payment.upi_qr_mode", "dynamic")
        cls.config.set("admin.username", ADMIN_USER)
        cls.config.set("admin.password", ADMIN_PASSWORD)
        # Keep the per-IP budgets high so the flow tests never trip them.
        cls.config.set("security.orders_per_10_minutes", 5000)
        cls.config.set("security.payment_submissions_per_10_minutes", 5000)
        cls.config.set("security.order_reads_per_minute", 5000)
        cls.config.set("security.qr_requests_per_minute", 5000)
        cls.config.set("security.admin_logins_per_10_minutes", 5000)
        cls.db = server.prepare(cls.config)
        cls.httpd = server.build_server(cls.config, cls.db, "127.0.0.1", 0, quiet=True)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.session_cookie = ""

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.db.close_all()
        try:
            cls.tmp.cleanup()
        except PermissionError:  # pragma: no cover - Windows can keep the file locked
            pass

    def setUp(self) -> None:
        # The rate-limit test deliberately lowers the budgets to prove the limiter;
        # put the generous test budgets back for every other test.
        self.config.set("security.orders_per_10_minutes", 5000)
        self.config.set("security.feedback_per_hour", 5000)

    # -- helpers ---------------------------------------------------------------
    def request(self, method: str, path: str, body=None, headers=None, cookie: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        request_headers = dict(headers or {})
        payload = None
        if body is not None:
            payload = json.dumps(body)
            request_headers["Content-Type"] = "application/json"
        if cookie:
            request_headers["Cookie"] = cookie
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        result = {
            "status": response.status,
            "headers": {k.lower(): v for k, v in response.getheaders()},
            "body": raw,
        }
        connection.close()
        return result

    def json_request(self, method: str, path: str, body=None, cookie: str | None = None):
        result = self.request(method, path, body=body, cookie=cookie)
        parsed = json.loads(result["body"]) if result["body"] else None
        return result["status"], parsed, result["headers"]

    def login(self) -> str:
        status, data, headers = self.json_request(
            "POST", "/api/admin/login", {"username": ADMIN_USER, "password": ADMIN_PASSWORD}
        )
        self.assertEqual(status, 200, data)
        cookie = headers.get("set-cookie", "").split(";")[0]
        self.assertTrue(cookie.startswith("admin_session="))
        return cookie

    def create_order(self, **overrides):
        payload = {
            "pack_id": "uc-660",
            "player_id": "5123456789",
            "player_name": "ShadowOP",
            "contact": "buyer@example.com",
            "payment_method": "upi",
        }
        payload.update(overrides)
        status, data, _headers = self.json_request("POST", "/api/orders", payload)
        return status, data

    # -- tests -----------------------------------------------------------------
    def test_01_health(self):
        status, data, _ = self.json_request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

    def test_02_public_config_hides_secrets(self):
        status, data, _ = self.json_request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertTrue(any(pack["id"] == "uc-660" for pack in data["packs"]))
        self.assertEqual(data["payment"]["upi_id"], "teststore@okaxis")
        self.assertEqual(
            set(data), {"site", "packs", "payment", "custom_uc", "status_labels"}
        )
        serialized = json.dumps(data)
        self.assertNotIn(ADMIN_PASSWORD, serialized)
        self.assertNotIn("password_hash", serialized)
        self.assertNotIn("admin_password", serialized)

    def test_03_create_order_happy_path(self):
        status, data = self.create_order()
        self.assertEqual(status, 201, data)
        order = data["order"]
        self.assertRegex(order["id"], r"^UC-[0-9A-F]{8}$")
        self.assertEqual(order["status"], "pending_payment")
        self.assertEqual(order["amount_paise"], 72500)
        self.assertEqual(order["total_uc"], 660)
        link = unquote(order["payment"]["upi_link"])
        self.assertTrue(link.startswith("upi://pay?"))
        self.assertIn("pa=teststore@okaxis", link)
        self.assertIn("am=725.00", link)
        self.assertIn("cu=INR", link)
        self.assertIn(order["id"], link)
        self.assertIn("5123456789", link)  # player id lands in the payment note
        self.assertTrue(order["payment"]["qr_url"].startswith("/api/orders/"))
        self.assertEqual(order["contact_masked"], "bu***@example.com")
        self.assertTrue(order["expires_in_seconds"] > 0)

    def test_04_custom_uc_order_is_priced_server_side(self):
        status, data, _headers = self.json_request(
            "POST",
            "/api/orders",
            {
                "custom_uc": 1000,
                "player_id": "5999888777",
                "player_name": "CustomBuyer",
                "price": 1,  # ignored: pricing is always server-side
                "amount_paise": 1,
            },
        )
        self.assertEqual(status, 201, data)
        order = data["order"]
        self.assertEqual(order["uc_amount"], 1000)
        self.assertEqual(order["amount_paise"], 1000 * 120)  # 1.2 rupees per UC

    def test_05_order_validation(self):
        status, data = self.create_order(player_id="123")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "invalid_player_id")

        status, data = self.create_order(pack_id="does-not-exist")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "invalid_pack")

        status, data = self.create_order(pack_id=None, custom_uc=5)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "invalid_uc_quantity")

        # Both selectors at once is ambiguous - reject it instead of guessing.
        status, data = self.create_order(custom_uc=500)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "conflicting_selection")

        status, data = self.create_order(player_name="x")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "invalid_player_name")

        status, data = self.create_order(contact="not-an-email")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "invalid_contact")

    def test_06_order_lookup_and_unknown_order(self):
        _status, data = self.create_order()
        order_id = data["order"]["id"]
        status, fetched, _ = self.json_request("GET", f"/api/orders/{order_id}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["order"]["id"], order_id)

        status, missing, _ = self.json_request("GET", "/api/orders/UC-DEADBEEF")
        self.assertEqual(status, 404)

    def test_07_qr_png_endpoint(self):
        _status, data = self.create_order()
        order_id = data["order"]["id"]
        result = self.request("GET", f"/api/orders/{order_id}/qr.png")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["headers"]["content-type"], "image/png")
        self.assertTrue(result["body"].startswith(b"\x89PNG\r\n\x1a\n"))

    def test_08_payment_submission_flow(self):
        _status, data = self.create_order()
        order_id = data["order"]["id"]

        status, submitted, _ = self.json_request(
            "POST", f"/api/orders/{order_id}/payment", {"utr": "4312 9876 5432"}
        )
        self.assertEqual(status, 200, submitted)
        self.assertEqual(submitted["order"]["status"], "awaiting_review")
        self.assertEqual(submitted["order"]["utr"], "431298765432")

        status, short_utr, _ = self.json_request(
            "POST", f"/api/orders/{order_id}/payment", {"utr": "12"}
        )
        self.assertEqual(status, 400)

    def test_09_duplicate_utr_is_rejected(self):
        _status, first = self.create_order()
        _status, second = self.create_order()
        utr = "990011223344"
        status, _body, _headers = self.json_request(
            "POST", f"/api/orders/{first['order']['id']}/payment", {"utr": utr}
        )
        self.assertEqual(status, 200)
        status, data, _ = self.json_request(
            "POST", f"/api/orders/{second['order']['id']}/payment", {"utr": utr}
        )
        self.assertEqual(status, 409)
        self.assertEqual(data["error"]["code"], "duplicate_utr")

    def test_10_cancel_unpaid_order(self):
        _status, data = self.create_order()
        order_id = data["order"]["id"]
        status, cancelled, _ = self.json_request("POST", f"/api/orders/{order_id}/cancel", {})
        self.assertEqual(status, 200)
        self.assertEqual(cancelled["order"]["status"], "cancelled")

    def test_11_admin_auth_required(self):
        status, data, _ = self.json_request("GET", "/api/admin/orders")
        self.assertEqual(status, 401)
        self.assertEqual(data["error"]["code"], "unauthorized")

        status, data, _ = self.json_request(
            "POST", "/api/admin/login", {"username": ADMIN_USER, "password": "wrong-password"}
        )
        self.assertEqual(status, 401)

        status, session, _ = self.json_request("GET", "/api/admin/session")
        self.assertEqual(status, 200)
        self.assertFalse(session["authenticated"])

    def test_12_admin_verify_and_deliver(self):
        cookie = self.login()
        self.session_cookie = cookie

        _status, data = self.create_order()
        order_id = data["order"]["id"]
        self.json_request("POST", f"/api/orders/{order_id}/payment", {"utr": "556677889900"})

        status, listed, _ = self.json_request("GET", "/api/admin/orders?status=awaiting_review", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(any(order["id"] == order_id for order in listed["orders"]))
        target = next(order for order in listed["orders"] if order["id"] == order_id)
        self.assertEqual(target["contact"], "buyer@example.com")  # admins see the full contact

        status, updated, _ = self.json_request(
            "POST",
            f"/api/admin/orders/{order_id}/status",
            {"status": "processing", "admin_note": "matched in bank statement"},
            cookie=cookie,
        )
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["order"]["status"], "processing")

        status, delivered, _ = self.json_request(
            "POST", f"/api/admin/orders/{order_id}/status", {"status": "completed"}, cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(delivered["order"]["status"], "completed")
        self.assertTrue(delivered["order"]["delivered_at"])

        # Delivered orders can no longer receive payment proofs.
        status, data, _ = self.json_request(
            "POST", f"/api/orders/{order_id}/payment", {"utr": "111122223333"}
        )
        self.assertEqual(status, 409)

        status, stats, _ = self.json_request("GET", "/api/admin/stats", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(stats["revenue_paise"]["completed"], 72500)

    def test_13_admin_reject_and_bad_status(self):
        cookie = self.session_cookie or self.login()
        _status, data = self.create_order()
        order_id = data["order"]["id"]

        status, data, _ = self.json_request(
            "POST", f"/api/admin/orders/{order_id}/status", {"status": "hacked"}, cookie=cookie
        )
        self.assertEqual(status, 400)

        status, rejected, _ = self.json_request(
            "POST",
            f"/api/admin/orders/{order_id}/status",
            {"status": "rejected", "admin_note": "no matching credit"},
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(rejected["order"]["status"], "rejected")
        self.assertEqual(rejected["order"]["admin_note"], "no matching credit")
        # Admin-only notes never leak to the buyer view.
        _status, public, _ = self.json_request("GET", f"/api/orders/{order_id}")
        detail = json.dumps(public["order"])
        self.assertNotIn("no matching credit", detail)

    def test_14_csv_export(self):
        cookie = self.session_cookie or self.login()
        result = self.request("GET", "/api/admin/orders.csv", cookie=cookie)
        self.assertEqual(result["status"], 200)
        self.assertIn("text/csv", result["headers"]["content-type"])
        self.assertIn(b"order_id", result["body"])

    def test_15_expired_order_blocks_and_flags(self):
        _status, data = self.create_order()
        order_id = data["order"]["id"]
        self.db.execute(
            "UPDATE orders SET expires_at = ? WHERE id = ?", [iso(utcnow()), order_id]
        )
        status, fetched, _ = self.json_request("GET", f"/api/orders/{order_id}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["order"]["status"], "expired")
        # A late payment is still accepted so support can review it.
        status, late, _ = self.json_request(
            "POST", f"/api/orders/{order_id}/payment", {"utr": "778899001122"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(late["order"]["status"], "awaiting_review")

    def test_16_static_files_and_traversal(self):
        index = self.request("GET", "/")
        self.assertEqual(index["status"], 200)
        self.assertIn(b"<title>", index["body"])
        self.assertIn("content-security-policy", index["headers"])

        admin = self.request("GET", "/admin")
        self.assertEqual(admin["status"], 200)
        self.assertIn(b"Admin sign in", admin["body"])

        css = self.request("GET", "/assets/css/styles.css")
        self.assertEqual(css["status"], 200)

        for path in ("/assets/../config.json", "/../config.json", "/config.json", "/data/store.db"):
            blocked = self.request("GET", path)
            self.assertEqual(blocked["status"], 404, path)

    # -- pricing (admin price editing) -----------------------------------------
    def test_18_pricing_endpoints_require_admin(self):
        status, _data, _headers = self.json_request("GET", "/api/admin/pricing")
        self.assertEqual(status, 401)
        status, _data, _headers = self.json_request(
            "PUT", "/api/admin/pricing", {"packs": [{"name": "x", "uc": 10, "price": 10}]}
        )
        self.assertEqual(status, 401)

        cookie = self.login()
        status, data, _headers = self.json_request("GET", "/api/admin/pricing", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(data["meta"]["source"], "config.json")
        self.assertEqual(len(data["packs"]), len(self.config.packs()))
        self.assertIn("limits", data)

    def test_19_admin_price_change_applies_to_new_orders(self):
        cookie = self.login()
        packs = [
            {"id": "uc-660", "name": "Best value", "uc": 600, "bonus_uc": 60, "price": 799, "badge": "", "popular": True},
            {"name": "Cheap pack", "uc": 60, "bonus_uc": 0, "price": 99},
        ]
        status, data, _headers = self.json_request(
            "PUT",
            "/api/admin/pricing",
            {"packs": packs, "custom_uc_rate": 1.5},
            cookie=cookie,
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["meta"]["source"], "dashboard")
        self.assertEqual(data["meta"]["updated_by"], ADMIN_USER)
        # A pack without an id gets a slug generated from its name.
        self.assertEqual([pack["id"] for pack in data["packs"]], ["uc-660", "cheap-pack"])
        self.assertEqual(data["packs"][0]["price_paise"], 79900)
        self.assertEqual(data["custom_uc_rate"], 1.5)

        # The storefront reflects the new prices straight away - no restart.
        _status, public, _headers = self.json_request("GET", "/api/config")
        self.assertEqual(public["payment"]["upi_id"], "teststore@okaxis")
        self.assertEqual(len(public["packs"]), 2)
        self.assertEqual(public["custom_uc"]["per_uc_rate"], 1.5)
        cheap = next(pack for pack in public["packs"] if pack["id"] == "cheap-pack")
        self.assertEqual(cheap["price_paise"], 9900)

        # New orders are priced from the edited table, not from config.json.
        status, order, _headers = self.json_request(
            "POST",
            "/api/orders",
            {"pack_id": "uc-660", "player_id": "5123456789", "player_name": "PricedBuyer"},
        )
        self.assertEqual(status, 201, order)
        self.assertEqual(order["order"]["amount_paise"], 79900)
        self.assertEqual(order["order"]["total_uc"], 660)

        status, custom, _headers = self.json_request(
            "POST",
            "/api/orders",
            {"custom_uc": 100, "player_id": "5123456789", "player_name": "CustomRate"},
        )
        self.assertEqual(status, 201, custom)
        self.assertEqual(custom["order"]["amount_paise"], 15000)  # 100 UC x 1.50

        # Removed packs disappear from the storefront too.
        status, gone, _headers = self.json_request(
            "POST",
            "/api/orders",
            {"pack_id": "uc-8100", "player_id": "5123456789", "player_name": "OldPack"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(gone["error"]["code"], "invalid_pack")

    def test_20_pricing_validation_rejects_bad_input(self):
        cookie = self.login()
        good = {"id": "uc-60", "name": "Starter", "uc": 60, "bonus_uc": 0, "price": 85}
        # Start from a known-good table so this test does not depend on ordering.
        status, _data, _headers = self.json_request(
            "PUT", "/api/admin/pricing", {"packs": [good], "custom_uc_rate": 1.2}, cookie=cookie
        )
        self.assertEqual(status, 200)

        for label, payload in [
            ("empty list", {"packs": []}),
            ("missing packs", {}),
            ("zero price", {"packs": [dict(good, price=0)]}),
            ("negative price", {"packs": [dict(good, price=-5)]}),
            ("zero uc", {"packs": [dict(good, uc=0)]}),
            ("junk uc", {"packs": [dict(good, uc="lots")]}),
            ("blank name", {"packs": [dict(good, name="  ")]}),
            ("too many packs", {"packs": [dict(good, name=f"Pack {i}") for i in range(31)]}),
            ("bad rate", {"packs": [good], "custom_uc_rate": "free"}),
        ]:
            status, data, _headers = self.json_request(
                "PUT", "/api/admin/pricing", payload, cookie=cookie
            )
            self.assertEqual(status, 400, label)
            self.assertIn(data["error"]["code"], {"invalid_packs", "invalid_rate"})

        # A rejected save must not have changed anything.
        _status, data, _headers = self.json_request("GET", "/api/admin/pricing", cookie=cookie)
        self.assertEqual(data["meta"]["source"], "dashboard")
        self.assertEqual(len(data["packs"]), 1)
        self.assertEqual(data["packs"][0]["price_paise"], 8500)
        self.assertEqual(data["custom_uc_rate"], 1.2)

    def test_21_reset_pricing_returns_to_config(self):
        cookie = self.login()
        status, data, _headers = self.json_request(
            "POST", "/api/admin/pricing/reset", {}, cookie=cookie
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["meta"]["source"], "config.json")
        self.assertEqual(len(data["packs"]), len(self.config.packs()))
        self.assertEqual(data["custom_uc_rate"], float(self.config.get("payment.custom_uc_rate")))

        _status, public, _headers = self.json_request("GET", "/api/config")
        best = next(pack for pack in public["packs"] if pack["id"] == "uc-660")
        self.assertEqual(best["price_paise"], 72500)

        status, order, _headers = self.json_request(
            "POST",
            "/api/orders",
            {"pack_id": "uc-660", "player_id": "5123456789", "player_name": "BackToConfig"},
        )
        self.assertEqual(status, 201, order)
        self.assertEqual(order["order"]["amount_paise"], 72500)

    # -- reviews & feedback ----------------------------------------------------
    def test_23_seeded_reviews_are_public(self):
        status, config, _headers = self.json_request("GET", "/api/config")
        self.assertEqual(status, 200)
        reviews = config["site"]["reviews"]
        self.assertGreaterEqual(len(reviews), 3)
        for review in reviews:
            self.assertTrue(1 <= review["rating"] <= 5)
            self.assertTrue(review["text"])
            self.assertTrue(review["name"])
            self.assertTrue(review["date"].startswith("2026"), review["date"])

    def test_24_visitor_feedback_stays_private(self):
        secret = "Automated privacy check for order delivery speed"
        status, posted, _headers = self.json_request(
            "POST",
            "/api/feedback",
            {"name": "Test Visitor", "rating": 4, "message": secret, "contact": "visitor@example.com"},
        )
        self.assertEqual(status, 201, posted)
        self.assertTrue(posted["ok"])
        feedback_id = posted["feedback"]["id"]
        self.assertIn("not posted publicly", posted["note"])

        # The public storefront payload must not carry visitor feedback anywhere.
        _status, public, _headers = self.json_request("GET", "/api/config")
        self.assertNotIn(secret, json.dumps(public))
        self.assertNotIn("feedback", public["site"])
        # ...and the buyer-facing order endpoints do not expose it either.
        _status, order, _headers = self.json_request("GET", "/api/orders/UC-DEADBEEF")
        self.assertNotIn(secret, json.dumps(order))

        # The shop owner sees it in the private inbox.
        status, _data, _headers = self.json_request("GET", "/api/admin/feedback")
        self.assertEqual(status, 401)
        cookie = self.login()
        status, inbox, _headers = self.json_request("GET", "/api/admin/feedback", cookie=cookie)
        self.assertEqual(status, 200)
        entry = next(item for item in inbox["feedback"] if item["id"] == feedback_id)
        self.assertEqual(entry["message"], secret)
        self.assertEqual(entry["contact"], "visitor@example.com")

        status, _data, _headers = self.json_request(
            "DELETE", f"/api/admin/feedback/{feedback_id}", cookie=cookie
        )
        self.assertEqual(status, 200)
        status, _data, _headers = self.json_request(
            "DELETE", f"/api/admin/feedback/{feedback_id}", cookie=cookie
        )
        self.assertEqual(status, 404)

    def test_25_feedback_validation(self):
        for label, payload in [
            ("short message", {"rating": 5, "message": "hi"}),
            ("missing message", {"rating": 5}),
            ("long message", {"rating": 5, "message": "x" * 501}),
            ("rating too high", {"rating": 6, "message": "nice store"}),
            ("rating missing", {"message": "nice store"}),
            ("rating junk", {"rating": "five", "message": "nice store"}),
        ]:
            status, data, _headers = self.json_request("POST", "/api/feedback", payload)
            self.assertEqual(status, 400, label)
            self.assertIn(data["error"]["code"], {"invalid_message", "invalid_rating"})

        # A blank name is fine and becomes "Anonymous".
        status, data, _headers = self.json_request(
            "POST", "/api/feedback", {"rating": 5, "message": "Great and quick service"}
        )
        self.assertEqual(status, 201, data)
        self.assertEqual(data["feedback"]["name"], "Anonymous")

    def test_99_zz_rate_limits(self):
        server.StoreHandler.limiter = RateLimiter()
        self.config.set("security.orders_per_10_minutes", 2)
        statuses = []
        for _ in range(4):
            status, data = self.create_order()
            statuses.append(status)
            if status == 429:
                self.assertEqual(data["error"]["code"], "rate_limited")
                break
        self.assertEqual(statuses[:3], [201, 201, 429], statuses)

        server.StoreHandler.limiter = RateLimiter()
        self.config.set("security.feedback_per_hour", 1)
        statuses = []
        for _ in range(3):
            status, data, _headers = self.json_request(
                "POST", "/api/feedback", {"rating": 5, "message": "Spam check message"}
            )
            statuses.append(status)
            if status == 429:
                self.assertEqual(data["error"]["code"], "rate_limited")
                break
        self.assertEqual(statuses[:2], [201, 429], statuses)


if __name__ == "__main__":
    unittest.main(verbosity=2)
