/* Admin dashboard: session, stats, order list, payment verification actions. */
(function () {
  let currentOrderId = "";
  let currentStatus = "all";
  let searchTerm = "";
  let refreshTimer = null;
  let config = null;

  function showLogin() {
    UC.el("login-view").classList.remove("hidden");
    UC.el("dashboard-view").classList.add("hidden");
    UC.el("logout-button").classList.add("hidden");
    UC.el("session-user").textContent = "";
    clearInterval(refreshTimer);
  }

  function showDashboard(username) {
    UC.el("login-view").classList.add("hidden");
    UC.el("dashboard-view").classList.remove("hidden");
    UC.el("logout-button").classList.remove("hidden");
    UC.el("session-user").textContent = username ? "signed in as " + username : "";
    loadSettings();
    loadStats();
    loadOrders();
    loadPricing();
    loadFeedback();
    clearInterval(refreshTimer);
    refreshTimer = setInterval(function () {
      if (!document.hidden) {
        loadStats();
        loadOrders(true);
      }
    }, 20000);
  }

  async function loadSettings() {
    if (!config) {
      try {
        config = await UC.api("/api/config");
      } catch (err) {
        return;
      }
      document.querySelectorAll("[data-site-name]").forEach(function (node) {
        node.textContent = config.site.name;
      });
    }
    const payment = config.payment;
    const rows = [
      ["UPI ID", '<span class="mono">' + UC.escapeHtml(payment.upi_id) + "</span>"],
      ["Payee name", UC.escapeHtml(payment.payee_name)],
      ["QR mode", payment.qr_mode === "static" ? "your own image (static)" : "generated per order (dynamic)"],
      ["Payment window", payment.expiry_minutes + " minutes"],
      ["Order limits", UC.formatINR(payment.min_order_paise) + " - " + UC.formatINR(payment.max_order_paise)],
      ["Support email", UC.escapeHtml(config.site.support_email || "-")],
    ];
    const packs = config.packs
      .map(function (pack) {
        return pack.total_uc + " UC \u2192 " + UC.formatINR(pack.price_paise);
      })
      .join(" \u00b7 ");
    rows.push(["Packs", packs || "none"]);
    UC.el("settings-list").innerHTML = rows
      .map(function (row) {
        return '<div class="kv-row"><dt>' + row[0] + "</dt><dd>" + row[1] + "</dd></div>";
      })
      .join("");
  }

  // --------------------------------------------------------------- feedback
  async function loadFeedback() {
    const list = UC.el("feedback-list");
    try {
      const data = await UC.api("/api/admin/feedback");
      UC.el("feedback-count-label").textContent = data.total ? "(" + data.total + ")" : "";
      if (!data.feedback.length) {
        list.innerHTML = '<p class="skeleton">No feedback yet.</p>';
        return;
      }
      list.innerHTML = data.feedback
        .map(function (item) {
          return (
            '<div class="my-feedback-item">' +
            '<div class="stack-row">' +
            "<div>" +
            '<div class="review-name">' + UC.escapeHtml(item.name || "Anonymous") + "</div>" +
            '<div class="review-meta">' + UC.formatDateTime(item.created_at) +
            (item.contact ? " \u00b7 " + UC.escapeHtml(item.contact) : "") +
            "</div></div>" +
            stars(item.rating) +
            "</div>" +
            '<p class="review-text">' + UC.escapeHtml(item.message) + "</p>" +
            '<button class="btn btn-danger btn-sm" data-delete-feedback="' + item.id +
            '" style="margin-top:10px">Delete</button>' +
            "</div>"
          );
        })
        .join("");
      list.querySelectorAll("[data-delete-feedback]").forEach(function (button) {
        button.addEventListener("click", async function () {
          if (!window.confirm("Delete this feedback entry?")) return;
          try {
            await UC.api("/api/admin/feedback/" + button.dataset.deleteFeedback, {
              method: "DELETE",
            });
            UC.toast("Feedback deleted.");
            loadFeedback();
          } catch (err) {
            UC.toast(err.message);
          }
        });
      });
    } catch (err) {
      if (err.status === 401) {
        showLogin();
        return;
      }
      list.innerHTML = '<p class="skeleton">' + UC.escapeHtml(err.message) + "</p>";
    }
  }

  function stars(rating) {
    const full = Math.max(0, Math.min(5, parseInt(rating, 10) || 0));
    let html = '<span class="stars" aria-label="' + full + ' out of 5">';
    for (let i = 1; i <= 5; i += 1) {
      html += i <= full ? "\u2605" : '<span class="off">\u2605</span>';
    }
    return html + "</span>";
  }

  // ---------------------------------------------------------------- pricing
  function pricingRow(pack, id) {
    const value = function (key) {
      return pack && pack[key] !== undefined && pack[key] !== null ? pack[key] : "";
    };
    // The id is carried on the row and sent back on save, so editing a price
    // never renames the pack (which would break existing checkout links).
    const cell = function (label, inner) {
      return '<td data-label="' + label + '">' + inner + "</td>";
    };
    return (
      '<tr class="pricing-row" data-id="' + UC.escapeHtml(id || "") + '">' +
      cell("Pack name", '<input class="input" data-field="name" value="' + UC.escapeHtml(value("name")) + '" maxlength="40" />') +
      cell("UC", '<input class="input" data-field="uc" type="number" min="1" step="1" value="' + UC.escapeHtml(value("uc")) + '" style="max-width:110px" />') +
      cell("Bonus UC", '<input class="input" data-field="bonus_uc" type="number" min="0" step="1" value="' + UC.escapeHtml(value("bonus_uc") || 0) + '" style="max-width:110px" />') +
      cell("Price (₹)", '<input class="input" data-field="price" type="number" min="1" step="0.01" value="' + priceValue(pack) + '" style="max-width:130px" />') +
      cell("Badge", '<input class="input" data-field="badge" value="' + UC.escapeHtml(value("badge")) + '" maxlength="20" style="max-width:150px" />') +
      cell("Featured", '<input type="checkbox" data-field="popular"' + (pack && pack.popular ? " checked" : "") + " />") +
      cell("Per UC", '<span class="hint" data-per-uc>-</span>') +
      cell("", '<button class="btn btn-danger btn-sm" data-remove>Remove</button>') +
      "</tr>"
    );
  }

  function priceValue(pack) {
    if (!pack || pack.price_paise === undefined) return "";
    return (pack.price_paise / 100).toFixed(2);
  }

  function renderPricing(data) {
    const body = UC.el("pricing-body");
    body.innerHTML = data.packs
      .map(function (pack) {
        return pricingRow(pack, pack.id);
      })
      .join("");
    UC.el("pricing-rate").value = data.custom_uc_rate;
    body.querySelectorAll("[data-remove]").forEach(function (button) {
      button.addEventListener("click", function () {
        const row = button.closest("tr");
        row.remove();
        recalcPricing();
      });
    });
    body.querySelectorAll("input").forEach(function (input) {
      input.addEventListener("input", recalcPricing);
    });
    recalcPricing();
    UC.el("pricing-status").textContent =
      data.meta.source === "dashboard"
        ? "current prices: edited in dashboard" +
          (data.meta.updated_by ? " by " + data.meta.updated_by : "") +
          (data.meta.updated_at ? " on " + UC.formatDateTime(data.meta.updated_at) : "")
        : "current prices: from config.json";
  }

  function recalcPricing() {
    document.querySelectorAll("#pricing-body .pricing-row").forEach(function (row) {
      const uc = parseInt(row.querySelector('[data-field="uc"]').value, 10) || 0;
      const bonus = parseInt(row.querySelector('[data-field="bonus_uc"]').value, 10) || 0;
      const price = parseFloat(row.querySelector('[data-field="price"]').value) || 0;
      const total = uc + bonus;
      row.querySelector("[data-per-uc]").textContent =
        total > 0 && price > 0 ? "₹" + (price / total).toFixed(2) : "-";
    });
  }

  function collectPricing() {
    const packs = [];
    document.querySelectorAll("#pricing-body .pricing-row").forEach(function (row) {
      const read = function (field) {
        return row.querySelector('[data-field="' + field + '"]').value;
      };
      packs.push({
        id: row.dataset.id || "",
        name: read("name").trim(),
        uc: read("uc"),
        bonus_uc: read("bonus_uc") || 0,
        price: read("price"),
        badge: read("badge").trim(),
        popular: row.querySelector('[data-field="popular"]').checked,
      });
    });
    return packs;
  }

  async function loadPricing() {
    try {
      renderPricing(await UC.api("/api/admin/pricing"));
    } catch (err) {
      if (err.status === 401) {
        showLogin();
        return;
      }
      UC.el("pricing-body").innerHTML =
        '<tr><td colspan="8" class="skeleton">' + UC.escapeHtml(err.message) + "</td></tr>";
    }
  }

  async function savePricing() {
    const button = UC.el("pricing-save");
    const packs = collectPricing();
    if (!packs.length) {
      UC.showError(UC.el("pricing-error"), "Keep at least one UC pack in the store.");
      return;
    }
    UC.showError(UC.el("pricing-error"), "");
    UC.setBusy(button, true, "Saving...");
    try {
      const data = await UC.api("/api/admin/pricing", {
        method: "PUT",
        body: { packs: packs, custom_uc_rate: UC.el("pricing-rate").value },
      });
      renderPricing(data);
      config = null; // the storefront config changed - refetch next time
      UC.toast("Prices saved. New orders use them right away.");
      loadSettings();
    } catch (err) {
      UC.showError(UC.el("pricing-error"), err.message);
    } finally {
      UC.setBusy(button, false);
    }
  }

  async function resetPricing() {
    if (!window.confirm("Reset all packs and the custom UC rate back to config.json?")) return;
    try {
      const data = await UC.api("/api/admin/pricing/reset", { method: "POST", body: {} });
      renderPricing(data);
      config = null;
      UC.toast("Prices reset to config.json.");
      loadSettings();
    } catch (err) {
      UC.showError(UC.el("pricing-error"), err.message);
    }
  }

  function addPricingRow() {
    const body = UC.el("pricing-body");
    body.insertAdjacentHTML(
      "beforeend",
      pricingRow(
        { name: "New pack", uc: 100, bonus_uc: 0, price_paise: 12000, badge: "", popular: false },
        ""
      )
    );
    const row = body.lastElementChild;
    row.querySelector("[data-remove]").addEventListener("click", function () {
      row.remove();
      recalcPricing();
    });
    row.querySelectorAll("input").forEach(function (input) {
      input.addEventListener("input", recalcPricing);
    });
    recalcPricing();
  }

  async function loadStats() {
    try {
      const stats = await UC.api("/api/admin/stats");
      UC.el("stat-needs-action").textContent = stats.needs_action;
      UC.el("stat-orders-today").textContent = stats.orders_today;
      UC.el("stat-delivered-today").textContent =
        stats.delivered_today + " (+" + UC.formatINR(stats.delivered_today_paise) + ")";
      UC.el("stat-revenue-today").textContent = UC.formatINR(stats.value_today_paise);
      UC.el("stat-revenue-total").textContent = UC.formatINR(stats.revenue_paise.completed);
    } catch (err) {
      if (err.status === 401) showLogin();
    }
  }

  function renderOrder(order) {
    return (
      "<tr data-order=\"" + UC.escapeHtml(order.id) + '">' +
      "<td>" + UC.formatDateTime(order.created_at) + "</td>" +
      '<td class="mono">' + UC.escapeHtml(order.id) + "</td>" +
      "<td>" + UC.escapeHtml(order.pack_name) + "</td>" +
      "<td>" + UC.formatINR(order.amount_paise) + "</td>" +
      "<td>" + UC.escapeHtml(order.player_name) + " <span class=\"mono hint\">" +
      UC.escapeHtml(order.player_id) + "</span></td>" +
      '<td class="mono">' + (order.utr ? UC.escapeHtml(order.utr) : "-") + "</td>" +
      "<td>" + UC.statusPill(order.status, order.status_label) + "</td>" +
      "</tr>"
    );
  }

  async function loadOrders(silent) {
    const params = new URLSearchParams();
    params.set("status", currentStatus);
    if (searchTerm) params.set("q", searchTerm);
    params.set("limit", "100");
    try {
      const data = await UC.api("/api/admin/orders?" + params.toString());
      const body = UC.el("orders-body");
      if (!data.orders.length) {
        body.innerHTML = '<tr><td colspan="7" class="skeleton">No orders match this filter yet.</td></tr>';
      } else {
        body.innerHTML = data.orders.map(renderOrder).join("");
        body.querySelectorAll("[data-order]").forEach(function (row) {
          row.addEventListener("click", function () {
            openDrawer(row.dataset.order);
          });
        });
      }
      UC.el("table-summary").textContent =
        data.total + " order(s) \u00b7 awaiting review: " + (data.counts.awaiting_review || 0);
    } catch (err) {
      if (err.status === 401) {
        showLogin();
        return;
      }
      if (!silent) UC.toast(err.message);
    }
  }

  function detailRows(order) {
    return [
      ["Status", UC.escapeHtml(order.status_label)],
      ["Pack", UC.escapeHtml(order.pack_name)],
      ["Amount", UC.formatINR(order.amount_paise)],
      ["Player ID", '<span class="mono">' + UC.escapeHtml(order.player_id) + "</span>"],
      ["Character", UC.escapeHtml(order.player_name)],
      ["Contact", UC.escapeHtml(order.contact || "not provided")],
      ["UTR", '<span class="mono">' + UC.escapeHtml(order.utr || "-") + "</span>"],
      ["Buyer note", UC.escapeHtml(order.buyer_note || "-")],
      ["Created", UC.formatDateTime(order.created_at)],
      ["Payment submitted", UC.formatDateTime(order.paid_at)],
      ["Delivered", UC.formatDateTime(order.delivered_at)],
      ["Expires", UC.formatDateTime(order.expires_at)],
    ];
  }

  async function openDrawer(orderId) {
    currentOrderId = orderId;
    UC.el("drawer").classList.add("open");
    UC.el("backdrop").classList.add("open");
    UC.el("drawer").setAttribute("aria-hidden", "false");
    UC.el("drawer-order-id").textContent = orderId;
    UC.el("drawer-body").innerHTML = '<p class="skeleton">Loading...</p>';
    UC.showError(UC.el("drawer-error"), "");
    try {
      const data = await UC.api("/api/admin/orders/" + encodeURIComponent(orderId));
      const order = data.order;
      UC.el("drawer-status").innerHTML = UC.statusPill(order.status, order.status_label);
      UC.el("drawer-note").value = order.admin_note || "";
      UC.el("drawer-body").innerHTML =
        '<dl class="kv">' +
        detailRows(order)
          .map(function (row) {
            return '<div class="kv-row"><dt>' + row[0] + "</dt><dd>" + row[1] + "</dd></div>";
          })
          .join("") +
        "</dl>" +
        '<h4 style="margin-top:20px">Activity</h4>' +
        '<ul class="timeline">' +
        order.timeline
          .slice()
          .reverse()
          .map(function (event) {
            return (
              "<li><div class=\"dot\"></div><div>" +
              '<div class="event-label">' + UC.escapeHtml(event.label) + "</div>" +
              '<div class="event-time">' + UC.formatDateTime(event.created_at) + " \u00b7 " +
              UC.escapeHtml(event.actor) + "</div>" +
              (event.detail ? '<div class="event-detail">' + UC.escapeHtml(event.detail) + "</div>" : "") +
              "</div></li>"
            );
          })
          .join("") +
        "</ul>";
    } catch (err) {
      UC.el("drawer-body").innerHTML = '<p class="skeleton">' + UC.escapeHtml(err.message) + "</p>";
    }
  }

  function closeDrawer() {
    UC.el("drawer").classList.remove("open");
    UC.el("backdrop").classList.remove("open");
    UC.el("drawer").setAttribute("aria-hidden", "true");
  }

  async function setStatus(status) {
    if (!currentOrderId) return;
    if (status === "completed" && !window.confirm("Mark this order as delivered?")) return;
    if (status === "rejected" && !window.confirm("Reject this payment proof?")) return;
    try {
      await UC.api("/api/admin/orders/" + encodeURIComponent(currentOrderId) + "/status", {
        method: "POST",
        body: { status: status, admin_note: UC.el("drawer-note").value.trim() },
      });
      UC.toast("Order updated to " + status.replace("_", " ") + ".");
      await loadOrders(true);
      await loadStats();
      await openDrawer(currentOrderId);
    } catch (err) {
      UC.showError(UC.el("drawer-error"), err.message);
    }
  }

  async function login(event) {
    event.preventDefault();
    const button = UC.el("login-button");
    UC.setBusy(button, true, "Signing in...");
    try {
      const data = await UC.api("/api/admin/login", {
        method: "POST",
        body: {
          username: UC.el("username").value.trim(),
          password: UC.el("password").value,
        },
      });
      UC.showError(UC.el("login-error"), "");
      UC.el("password").value = "";
      showDashboard(data.username);
    } catch (err) {
      UC.showError(UC.el("login-error"), err.message);
    } finally {
      UC.setBusy(button, false);
    }
  }

  async function logout() {
    try {
      await UC.api("/api/admin/logout", { method: "POST", body: {} });
    } catch (err) {
      /* ignore */
    }
    showLogin();
  }

  async function init() {
    UC.el("login-form").addEventListener("submit", login);
    UC.el("logout-button").addEventListener("click", logout);
    UC.el("refresh-button").addEventListener("click", function () {
      loadStats();
      loadOrders();
    });
    UC.el("export-button").addEventListener("click", function () {
      const params = new URLSearchParams();
      params.set("status", currentStatus);
      if (searchTerm) params.set("q", searchTerm);
      window.location.href = "/api/admin/orders.csv?" + params.toString();
    });
    UC.el("filter-status").addEventListener("change", function (event) {
      currentStatus = event.target.value;
      loadOrders();
    });
    let debounce = null;
    UC.el("filter-search").addEventListener("input", function (event) {
      searchTerm = event.target.value.trim();
      clearTimeout(debounce);
      debounce = setTimeout(function () {
        loadOrders();
      }, 320);
    });
    UC.el("drawer-close").addEventListener("click", closeDrawer);
    UC.el("backdrop").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeDrawer();
    });
    UC.el("action-processing").addEventListener("click", function () {
      setStatus("processing");
    });
    UC.el("action-completed").addEventListener("click", function () {
      setStatus("completed");
    });
    UC.el("action-rejected").addEventListener("click", function () {
      setStatus("rejected");
    });
    UC.el("feedback-refresh").addEventListener("click", loadFeedback);
    UC.el("pricing-save").addEventListener("click", savePricing);
    UC.el("pricing-reset").addEventListener("click", resetPricing);
    UC.el("pricing-add").addEventListener("click", addPricingRow);

    loadSettings();
    try {
      const session = await UC.api("/api/admin/session");
      if (session.authenticated) {
        showDashboard(session.username);
      } else {
        showLogin();
      }
    } catch (err) {
      showLogin();
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
