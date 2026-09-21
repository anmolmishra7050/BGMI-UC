# BGMI UC Store

A complete, self-hosted storefront for selling BGMI UC with **UPI payments**:
buyer-facing store + checkout + a QR payment page with UTR verification, plus an
admin panel to verify payments and mark orders delivered.

No build step, no npm, no pip install - **Python 3.10+ standard library only**.

```
python server.py
```

Then open <http://127.0.0.1:8080> — admin panel at <http://127.0.0.1:8080/admin>.

---

## 1. Set up your store (5 minutes)

Open `config.json` and change these first:

| Key | What it does |
| --- | --- |
| `payment.upi_id` | **Your UPI ID** (e.g. `yourname@okhdfcbank`). This is where buyers pay. |
| `payment.payee_name` | The name shown inside the buyer's UPI app. |
| `payment.static_qr_image` | Path to your own QR image, if you want to use it (see below). |
| `packs` | Your UC packs, prices and bonus UC. Delete/add freely. |
| `admin.username` / `admin.password` | Admin login. Change the password properly with `python server.py --set-admin-password`. |
| `site.*` | Store name, tagline, FAQ, support email, WhatsApp number, disclaimer. |
| `security.*` | Per-IP request budgets (order spam, login brute force, QR scraping). |

Restart the server after editing `config.json`.

## 2. Your UPI QR code

Two modes, controlled by `payment.upi_qr_mode`:

**`"dynamic"` (default)** — the server generates a fresh QR per order. It encodes a
standard `upi://pay` link containing your UPI ID, the exact amount, the order ID
and a note with the player ID, so payments are trivially matched against the bank
statement. Nothing to upload. Requires the optional `qrcode` library:

```bash
python -m pip install "qrcode[pil]"
```

**`"static"` — use your own QR image** (a QR from your bank app, Paytm/PhonePe
merchant QR, etc.):

1. Save your QR image as `web/assets/my-qr.png` (PNG or JPG).
2. In `config.json` set:
   ```json
   "payment": { "upi_qr_mode": "static", "static_qr_image": "assets/my-qr.png" }
   ```
3. Restart. Every order page now shows your image instead of a generated one.

> With a static QR the amount is **not** baked into the QR, so the page keeps
> showing the exact amount, your UPI ID and the order reference in large text and
> asks the buyer to pay that exact amount. Keep `payment.upi_id` correct either
> way — the "Open in a UPI app" button and the copy button are built from it.

## 3. How an order flows

1. Buyer picks a pack (or a custom UC amount) and enters their **player ID** +
   character name on `/checkout`.
2. The server creates the order (`UC-XXXXXXXX`), prices it server-side and shows
   the QR + amount on `/order?id=...`. The order expires after
   `payment.order_expiry_minutes` (default 30).
3. Buyer pays, then submits the **UTR / UPI transaction ID** on the same page.
   Status: `awaiting_review`.
4. You open `/admin`, check the credit in your bank/UPI app, and press
   **Payment verified**, then **Mark delivered** once the UC is topped up.
   Status: `processing` → `completed`.
5. The buyer's order page updates automatically (it polls every few seconds) and
   shows the full activity timeline. Rejecting a payment sets `rejected` with your
   note kept private to the admin panel.

Statuses: `pending_payment`, `awaiting_review`, `processing`, `completed`,
`rejected`, `expired`, `cancelled`.

## 4. Reviews & private feedback

The bottom of the storefront has two separate things:

**Published reviews (social proof)** come from `site.reviews` in `config.json` and are
visible to everyone. Edit, add or delete entries freely - they are plain text you own:

```json
"reviews": [
  { "name": "Rohit S.", "rating": 5, "date": "2026-08-24", "pack": "660 UC", "text": "Paid with GPay late at night..." }
]
```

The storefront shows each as a card with initials, a star rating, the month/year, the
pack bought and the quote, plus an average rating line. Empty the array to hide the
section entirely.

**Visitor feedback is private.** The "Send your feedback" form does *not* publish
anything:

* the entry is saved in **that visitor's browser** (`localStorage`, key
  `uc_my_feedback_v1`), so they can see what they sent and remove it themselves - a
  "Clear my feedback from this device" button wipes it;
* a copy is sent to your **admin inbox** (`/admin` → *Feedback*), where you can read it
  and delete it;
* no public endpoint ever returns it - `POST /api/feedback` accepts, `GET
  /api/admin/feedback` (admin only) reads. There is no public list endpoint at all, so
  another visitor cannot see someone else's feedback, and the tests assert it.

What other visitors see is therefore only ever your `site.reviews` list. Feedback is
rate limited (`security.feedback_per_hour`, default 6 per IP per hour), validated
(rating 1-5, message 4-500 characters) and stored in the `feedback` table.

## 5. Admin panel

* Login at `/admin` with `admin.username` / `admin.password` from `config.json`.
  The password is stored as a PBKDF2-SHA256 hash in the database, never in plain
  text, and sessions are cookie-based (`HttpOnly`, `SameSite=Strict`).
* Change it any time:
  ```bash
  python server.py --set-admin-password                # interactive
  python server.py --set-admin-password 'YourPass123'  # non-interactive
  ```
* Dashboard shows orders needing verification, orders/revenue today, delivered
  revenue, a searchable + filterable order table, a detail drawer with the full
  timeline and buyer details, and a **CSV export** for accounting.

### Editing prices from the dashboard

The **Packs & prices** panel lets you change every UC pack and the custom-UC rate
without touching `config.json` or restarting the server:

* Edit a pack's name, UC, bonus UC, price, badge text and "featured" flag inline.
* **+ Add package** appends a new pack; **Remove** deletes one.
* **Save prices** applies everything at once and the storefront plus checkout pick
  it up for the next page load - new orders are always priced from the saved
  table, never from anything the browser sends.
* **Reset to config.json** throws the overrides away and goes back to the prices in
  the config file.

The panel always tells you where the live prices come from ("from config.json" or
"edited in dashboard by <user> on <date>").

How it is stored: overrides live in the `settings` table of `data/store.db`, so
`config.json` stays your baseline and stays valid. Resolution order per request is
**database override → config.json → built-in defaults**. Pack ids are kept stable
when prices change, so existing `/checkout?pack=<id>` links keep working. Delete
the rows (or press reset) to fall back. API: `GET/PUT /api/admin/pricing` and
`POST /api/admin/pricing/reset`, all admin-only.

Validation: name 1-40 chars, UC 1-1,000,000, bonus UC 0-1,000,000, price ₹1-₹1,000,000,
max 30 packs, custom rate 0-₹1,000 per UC. A rejected save changes nothing.

## 6. Running it for real

```bash
python server.py --host 0.0.0.0 --port 8080      # reachable from your network
python server.py --open                          # open a browser on start
python server.py --quiet                         # less request logging
```

Secrets can also come from the environment, which is handy for a host or a
process manager: `UC_UPI_ID`, `UC_PAYEE_NAME`, `UC_UPI_QR_MODE`,
`UC_STATIC_QR_IMAGE`, `UC_ADMIN_USERNAME`, `UC_ADMIN_PASSWORD`, `UC_HOST`,
`UC_PORT`, `UC_DB_PATH`.

Deployment checklist:

* Put it behind HTTPS (Caddy/nginx/Cloudflare tunnel). UPI QR pages should never
  be served over plain HTTP on the public internet.
* Set `"admin": { "cookie_secure": true }` once HTTPS is in place.
* Bind to `127.0.0.1` and reverse-proxy to it if you are not using a container.
* Back up `data/store.db` (SQLite, single file) — that is all your order history.

## 7. Project layout

```
config.json          everything you edit
server.py            HTTP server, routing glue, expiry worker, CLI
app/config.py        config loading + defaults + env overrides
app/db.py            SQLite schema and helpers (orders, events, sessions)
app/auth.py          PBKDF2 admin passwords + sessions
app/payments.py      UPI deep links + dynamic QR PNG generation
app/pricing.py       admin price overrides (packs + custom-UC rate) over config.json
app/routes.py        the whole JSON API
app/http_utils.py    responses, static files, rate limiter
web/                 frontend: store, checkout, order/QR page, admin panel (vanilla JS)
tests/test_api.py    end-to-end API tests
data/store.db        created on first run
```

## 8. Tests

```bash
python -m unittest discover -s tests -v
```

24 tests covering order creation and server-side pricing, validation, QR generation,
UTR submission, duplicate-UTR protection, admin auth, verify/deliver/reject, expiry,
CSV export, admin price editing (including that new orders use the edited prices and
that config.json can be restored), price validation, published reviews, feedback
privacy (submitted feedback must never appear in any public payload), the admin
feedback inbox and its delete path, rate limiting and static-file traversal
protection.

## 9. Legal / practical note

Selling UC to other players is against KRAFTON's terms of service for most
regions, and in-game top-up resale can get accounts or your payment account
restricted. This project is a working storefront; how you use it, the prices you
set, and the permissions you have to resell UC are on you. The footer disclaimer
in `config.json` states clearly that the store is an independent reseller — keep
something like it so buyers are not misled.
