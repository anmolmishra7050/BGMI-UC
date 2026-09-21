/* Order status page: UPI QR payment, UTR submission, live status polling. */
(function () {
  let orderId = UC.qs("id").trim().toUpperCase();
  let order = null;
  let config = null;
  let pollTimer = null;
  let tickTimer = null;
  let secondsLeft = 0;

  const OPEN_STATUSES = ["pending_payment", "awaiting_review", "processing"];

  function render() {
    UC.el("loading").classList.add("hidden");
    UC.el("not-found").classList.add("hidden");
    UC.el("order-view").classList.remove("hidden");

    UC.el("order-id").textContent = order.id;
    UC.el("status-pill").innerHTML = UC.statusPill(order.status, order.status_label);
    UC.el("status-message").textContent = order.status_message;
    UC.el("order-amount").textContent = order.amount_display;
    UC.el("pay-amount").textContent = order.amount_display;
    UC.el("upi-id").textContent = order.payment.upi_id || "-";
    UC.el("payment-reference").textContent = order.payment.reference || order.id;

    const qr = UC.el("qr-image");
    if (qr.dataset.src !== order.payment.qr_url) {
      qr.dataset.src = order.payment.qr_url;
      qr.src = order.payment.qr_url;
    }
    UC.el("qr-mode-hint").textContent =
      order.payment.qr_mode === "static"
        ? "Scanning sends the payment to the UPI ID above - enter this exact amount."
        : "This QR already contains the exact amount for this order.";
    UC.el("open-upi-app").href = order.payment.upi_link || "#";
    UC.el("payment-instructions").innerHTML = (order.payment.instructions || [])
      .map(function (step) {
        return "<li>" + UC.escapeHtml(step) + "</li>";
      })
      .join("");

    UC.el("order-details").innerHTML = [
      ["Pack", UC.escapeHtml(order.pack_name)],
      ["UC delivered", order.total_uc + " UC" + (order.bonus_uc ? " (incl. " + order.bonus_uc + " bonus)" : "")],
      ["Player ID", '<span class="mono">' + UC.escapeHtml(order.player_id) + "</span>"],
      ["Character", UC.escapeHtml(order.player_name)],
      ["Contact", UC.escapeHtml(order.contact_masked || "not provided")],
      ["UPI reference we received", '<span class="mono">' + UC.escapeHtml(order.utr || "-") + "</span>"],
      ["Created", UC.formatDateTime(order.created_at)],
      ["Payment submitted", UC.formatDateTime(order.paid_at)],
      ["Delivered", UC.formatDateTime(order.delivered_at)],
    ]
      .map(function (row) {
        return '<div class="kv-row"><dt>' + row[0] + "</dt><dd>" + row[1] + "</dd></div>";
      })
      .join("");

    UC.el("timeline").innerHTML = order.timeline
      .slice()
      .reverse()
      .map(function (event) {
        return (
          "<li><div class=\"dot\"></div><div>" +
          '<div class="event-label">' + UC.escapeHtml(event.label) + "</div>" +
          '<div class="event-time">' + UC.formatDateTime(event.created_at) + "</div>" +
          (event.detail ? '<div class="event-detail">' + UC.escapeHtml(event.detail) + "</div>" : "") +
          "</div></li>"
        );
      })
      .join("");

    const showPayment = order.status === "pending_payment" || order.status === "expired";
    UC.el("payment-panel").classList.toggle("hidden", !showPayment);
    UC.el("countdown-row").classList.toggle("hidden", order.status !== "pending_payment");

    const utrPanel = UC.el("utr-panel");
    const canSubmit = ["pending_payment", "expired", "awaiting_review"].indexOf(order.status) >= 0;
    utrPanel.classList.toggle("hidden", !canSubmit);
    if (order.status === "awaiting_review") {
      UC.el("utr-title").textContent = "Verification in progress";
      UC.el("submit-utr").textContent = "Update payment proof";
      UC.el("utr-input").value = order.utr || "";
    }
    UC.el("cancel-order").classList.toggle(
      "hidden",
      ["pending_payment", "expired"].indexOf(order.status) < 0
    );

    if (order.status === "pending_payment") {
      secondsLeft = order.expires_in_seconds || 0;
      updateCountdown();
    }
    document.title = order.id + " - " + order.status_label;
  }

  function updateCountdown() {
    const node = UC.el("countdown");
    if (!node) return;
    node.textContent = UC.formatClock(secondsLeft);
    node.classList.toggle("expired", secondsLeft <= 0);
    if (secondsLeft <= 0 && order && order.status === "pending_payment") {
      refresh();
    }
  }

  function startTimers() {
    clearInterval(tickTimer);
    tickTimer = setInterval(function () {
      if (secondsLeft > 0) {
        secondsLeft -= 1;
        updateCountdown();
      }
    }, 1000);
    clearInterval(pollTimer);
    pollTimer = setInterval(function () {
      if (document.hidden) return;
      if (order && OPEN_STATUSES.indexOf(order.status) >= 0) refresh(true);
    }, 7000);
  }

  async function refresh(silent) {
    try {
      const data = await UC.api("/api/orders/" + encodeURIComponent(orderId));
      const previousStatus = order ? order.status : null;
      order = data.order;
      secondsLeft = order.expires_in_seconds || secondsLeft;
      if (previousStatus && previousStatus !== order.status) {
        UC.toast("Order status: " + order.status_label);
      }
      render();
    } catch (err) {
      if (!silent) showNotFound(err.status === 404 ? "We could not find that order ID." : err.message);
    }
  }

  function showNotFound(message) {
    UC.el("loading").classList.add("hidden");
    UC.el("order-view").classList.add("hidden");
    UC.el("not-found").classList.remove("hidden");
    UC.el("lookup-input").value = orderId || "";
    UC.el("lookup-error").textContent = message || "";
  }

  async function submitUtr() {
    const value = UC.el("utr-input").value.trim();
    const errorNode = UC.el("utr-error");
    if (value.replace(/\s/g, "").length < 6) {
      UC.showError(errorNode, "Enter the UTR from your UPI receipt (at least 6 characters).");
      return;
    }
    const button = UC.el("submit-utr");
    UC.showError(errorNode, "");
    UC.setBusy(button, true, "Submitting...");
    try {
      const data = await UC.api("/api/orders/" + encodeURIComponent(orderId) + "/payment", {
        method: "POST",
        body: { utr: value },
      });
      order = data.order;
      secondsLeft = order.expires_in_seconds || secondsLeft;
      render();
      UC.toast("Payment proof submitted. We are verifying it now.");
    } catch (err) {
      UC.showError(errorNode, err.message);
      UC.setBusy(button, false);
    }
  }

  async function cancelOrder() {
    if (!window.confirm("Cancel this order? You can create a new one any time.")) return;
    try {
      const data = await UC.api("/api/orders/" + encodeURIComponent(orderId) + "/cancel", {
        method: "POST",
        body: {},
      });
      order = data.order;
      render();
      UC.toast("Order cancelled.");
    } catch (err) {
      UC.toast(err.message);
    }
  }

  function bind() {
    UC.el("copy-order-id").addEventListener("click", function () {
      UC.copy(orderId, "Order ID");
    });
    UC.el("copy-upi").addEventListener("click", function () {
      UC.copy(order.payment.upi_id, "UPI ID");
    });
    UC.el("copy-reference").addEventListener("click", function () {
      UC.copy(order.payment.reference || orderId, "Reference");
    });
    UC.el("submit-utr").addEventListener("click", submitUtr);
    UC.el("utr-input").addEventListener("keydown", function (event) {
      if (event.key === "Enter") submitUtr();
    });
    UC.el("cancel-order").addEventListener("click", cancelOrder);
    UC.el("lookup-button").addEventListener("click", function () {
      const value = UC.el("lookup-input").value.trim().toUpperCase();
      if (!value) return;
      window.location.href = "/order?id=" + encodeURIComponent(value);
    });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && order && OPEN_STATUSES.indexOf(order.status) >= 0) refresh(true);
    });
  }

  async function init() {
    bind();
    try {
      config = await UC.api("/api/config");
      document.querySelectorAll("[data-site-name]").forEach(function (node) {
        node.textContent = config.site.name;
      });
      UC.el("disclaimer").textContent = config.site.disclaimer;
      const email = UC.el("support-email");
      email.textContent = config.site.support_email || "-";
      email.href = "mailto:" + (config.site.support_email || "");
      const whatsapp = UC.el("support-whatsapp");
      if (config.site.support_whatsapp) {
        whatsapp.textContent = "WhatsApp " + config.site.support_whatsapp;
        whatsapp.href = "https://wa.me/" + config.site.support_whatsapp.replace(/\D/g, "");
      } else {
        whatsapp.classList.add("hidden");
      }
    } catch (err) {
      /* the order view still works without the marketing config */
    }

    if (!orderId) {
      showNotFound("");
      return;
    }
    await refresh();
    startTimers();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
