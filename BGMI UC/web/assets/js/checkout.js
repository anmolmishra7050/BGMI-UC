/* Checkout: choose pack or custom UC, validate buyer details, create the order. */
(function () {
  let config = null;
  let packs = [];
  let selectedPackId = "";
  let customUc = 0;

  function packLabel(pack) {
    const bonus = pack.bonus_uc ? " (+" + pack.bonus_uc + " bonus)" : "";
    return (
      pack.name + " - " + pack.total_uc + " UC" + bonus + " - " + UC.formatINR(pack.price_paise)
    );
  }

  function populatePacks() {
    const select = UC.el("pack-select");
    select.innerHTML = packs
      .map(function (pack) {
        return (
          '<option value="' + UC.escapeHtml(pack.id) + '">' + UC.escapeHtml(packLabel(pack)) + "</option>"
        );
      })
      .join("");
    if (selectedPackId && packs.some(function (p) { return p.id === selectedPackId; })) {
      select.value = selectedPackId;
    } else {
      selectedPackId = select.value;
    }
  }

  function currentSelection() {
    const customRaw = parseInt(UC.el("custom-uc-input").value, 10) || 0;
    if (customRaw > 0) {
      const rate = Number((config.custom_uc && config.custom_uc.per_uc_rate) || 0);
      return {
        customUc: customRaw,
        packId: "",
        uc: 0,
        totalUc: customRaw,
        amountPaise: Math.round(customRaw * rate * 100),
        title: customRaw + " UC - custom order",
      };
    }
    const pack = packs.find(function (item) {
      return item.id === UC.el("pack-select").value;
    });
    if (!pack) return null;
    return {
      customUc: 0,
      packId: pack.id,
      uc: pack.uc,
      totalUc: pack.total_uc,
      amountPaise: pack.price_paise,
      title: pack.total_uc + " UC - " + pack.name,
    };
  }

  function renderSummary() {
    const selection = currentSelection();
    const node = UC.el("summary");
    if (!selection) {
      node.innerHTML = '<p class="hint">Select a pack to see the total.</p>';
      return;
    }
    node.innerHTML =
      '<div class="kv-row"><dt>Pack</dt><dd>' + UC.escapeHtml(selection.title) + "</dd></div>" +
      '<div class="kv-row"><dt>UC delivered</dt><dd>' + selection.totalUc + " UC</dd></div>" +
      '<div class="kv-row"><dt>Player ID</dt><dd class="mono">' +
      UC.escapeHtml(UC.el("player-id").value.trim() || "not set") + "</dd></div>" +
      '<div class="kv-row"><dt>Payment</dt><dd>UPI (QR / UPI ID)</dd></div>' +
      '<div class="kv-row" style="border-top:1px solid var(--border);padding-top:12px">' +
      "<dt>Total payable</dt><dd style=\"color:var(--gold);font-size:1.15rem\">" +
      UC.formatINR(selection.amountPaise) + "</dd></div>";
  }

  function validate(selection) {
    const playerId = UC.el("player-id").value.replace(/\D/g, "");
    if (!selection) return "Choose a UC pack or enter a custom amount.";
    if (playerId.length < 8 || playerId.length > 12) {
      return "Enter a valid BGMI player ID (8-12 digits).";
    }
    const name = UC.el("player-name").value.trim();
    if (name.length < 2) return "Enter your in-game character name.";
    const min = config.payment.min_order_paise || 0;
    const max = config.payment.max_order_paise || Number.MAX_SAFE_INTEGER;
    if (selection.amountPaise < min) {
      return "Minimum order value is " + UC.formatINR(min) + ".";
    }
    if (selection.amountPaise > max) {
      return "Maximum order value is " + UC.formatINR(max) + ".";
    }
    const custom = config.custom_uc || {};
    if (selection.customUc) {
      if (custom.custom_uc_enabled === false) return "Custom UC amounts are disabled.";
      if (selection.customUc < custom.custom_uc_min || selection.customUc > custom.custom_uc_max) {
        return (
          "Custom UC must be between " + custom.custom_uc_min + " and " + custom.custom_uc_max + "."
        );
      }
    }
    return "";
  }

  async function submit(event) {
    event.preventDefault();
    const button = UC.el("submit-order");
    const errorNode = UC.el("form-error");
    const selection = currentSelection();
    const problem = validate(selection);
    if (problem) {
      UC.showError(errorNode, problem);
      return;
    }
    UC.showError(errorNode, "");
    UC.setBusy(button, true, "Creating order...");
    try {
      const payload = {
        player_id: UC.el("player-id").value.replace(/\D/g, ""),
        player_name: UC.el("player-name").value.trim(),
        contact: UC.el("contact").value.trim(),
        note: UC.el("note").value.trim(),
        payment_method: "upi",
      };
      if (selection.customUc) {
        payload.custom_uc = selection.customUc;
      } else {
        payload.pack_id = selection.packId;
      }
      const result = await UC.api("/api/orders", { method: "POST", body: payload });
      window.location.href = "/order?id=" + encodeURIComponent(result.order.id);
    } catch (err) {
      UC.showError(errorNode, err.message);
      UC.setBusy(button, false);
    }
  }

  function bindMediaQuery() {
    document.querySelectorAll("[data-site-name]").forEach(function (node) {
      node.textContent = config.site.name;
    });
    document.title = "Checkout - " + config.site.name;
    UC.el("disclaimer").textContent = config.site.disclaimer;
    UC.el("custom-uc-hint").textContent =
      "Custom UC: " +
      config.custom_uc.custom_uc_min +
      " - " +
      config.custom_uc.custom_uc_max +
      " UC at \u20b9" +
      Number(config.custom_uc.per_uc_rate).toFixed(2) +
      " per UC. Leave blank to use a pack.";
    if (config.custom_uc.custom_uc_enabled === false) {
      UC.el("custom-uc-input").disabled = true;
    }
  }

  async function init() {
    try {
      config = await UC.api("/api/config");
    } catch (err) {
      UC.showError(UC.el("form-error"), "Could not load the store: " + err.message);
      return;
    }
    packs = config.packs;
    selectedPackId = UC.qs("pack");
    customUc = parseInt(UC.qs("uc"), 10) || 0;

    bindMediaQuery();
    populatePacks();
    if (customUc) {
      UC.el("custom-uc-input").value = customUc;
    }
    renderSummary();

    UC.el("pack-select").addEventListener("change", function () {
      UC.el("custom-uc-input").value = "";
      selectedPackId = UC.el("pack-select").value;
      renderSummary();
    });
    ["custom-uc-input", "player-id", "player-name"].forEach(function (id) {
      UC.el(id).addEventListener("input", renderSummary);
    });
    UC.el("checkout-form").addEventListener("submit", submit);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
