"""UPI payment helpers.

Two things happen here:

* ``build_upi_link`` produces a standard ``upi://pay`` deep link that any Indian
  UPI app understands (GPay, PhonePe, Paytm, BHIM, ...).
* ``qr_png_bytes`` renders that link as a QR code image.  ``qrcode`` +
  ``Pillow`` are used when importable; if they are missing the caller falls back
  to the store's own static QR image.
"""

from __future__ import annotations

import io
from urllib.parse import urlencode

QR_AVAILABLE_HINT = (
    "Qrcode generation needs the 'qrcode' package with Pillow: "
    "python -m pip install \"qrcode[pil]\""
)


class QRUnavailable(RuntimeError):
    pass


def build_upi_link(
    *,
    upi_id: str,
    payee_name: str,
    amount_paise: int | None = None,
    note: str = "",
    reference: str = "",
    currency: str = "INR",
) -> str:
    """Build a UPI intent link.

    ``am`` is the amount in rupees with two decimals, ``tn`` is the note shown in
    the payer's UPI app and ``tr`` is our own transaction reference.
    """
    if not upi_id:
        raise ValueError("upi_id is required")
    params: list[tuple[str, str]] = [("pa", upi_id.strip())]
    if payee_name:
        params.append(("pn", payee_name.strip()[:60]))
    if amount_paise:
        params.append(("am", f"{amount_paise / 100:.2f}"))
    params.append(("cu", currency or "INR"))
    if note:
        params.append(("tn", " ".join(note.split())[:50]))
    if reference:
        params.append(("tr", reference.strip()[:35]))
    return "upi://pay?" + urlencode(params)


def qr_png_bytes(data: str, box_size: int = 9, border: int = 2) -> bytes:
    """Render ``data`` as a PNG QR code."""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise QRUnavailable(QR_AVAILABLE_HINT) from exc

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0b0b14", back_color="#ffffff")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def qr_available() -> bool:
    try:
        import qrcode  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True
