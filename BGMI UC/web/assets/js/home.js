/* Landing page: renders packs, custom UC calculator, FAQ and site copy. */
(function () {
  const packsGrid = UC.el("packs-grid");
  let config = null;

  function packCard(pack) {
    const featured = pack.popular ? " featured" : "";
    const tag = pack.badge
      ? '<span class="pack-tag">' + UC.escapeHtml(pack.badge) + "</span>"
      : "";
    const bonus = pack.bonus_uc
      ? '<span class="bonus-pill">+' + pack.bonus_uc + " bonus UC</span>"
      : "";
    return (
      '<article class="pack-card' + featured + '">' +
      tag +
      '<div class="uc-amount">' + pack.total_uc + " <small>UC</small></div>" +
      bonus +
      "<div>" +
      '<div class="pack-name">' + UC.escapeHtml(pack.name) + "</div>" +
      '<div class="pack-price">' + UC.formatINR(pack.price_paise) + "</div>" +
      '<div class="pack-meta">\u20b9' + pack.per_uc.toFixed(2) + " per UC</div>" +
      "</div>" +
      '<p class="pack-desc">' + UC.escapeHtml(pack.description) + "</p>" +
      '<button class="btn btn-primary btn-block" data-pack="' +
      UC.escapeHtml(pack.id) +
      '">Buy now</button>' +
      "</article>"
    );
  }

  function renderPacks(packs) {
    if (!packs.length) {
      packsGrid.innerHTML = '<p class="skeleton">No UC packs configured yet.</p>';
      return;
    }
    packsGrid.innerHTML = packs.map(packCard).join("");
    packsGrid.querySelectorAll("[data-pack]").forEach(function (button) {
      button.addEventListener("click", function () {
        window.location.href = "/checkout?pack=" + encodeURIComponent(button.dataset.pack);
      });
    });
  }

  function initCalculator(custom) {
    const box = UC.el("custom-uc");
    if (!box || !custom || custom.custom_uc_enabled === false) {
      if (box) box.classList.add("hidden");
      return;
    }
    const input = UC.el("custom-uc-input");
    const priceNode = UC.el("custom-uc-price");
    const buyButton = UC.el("custom-uc-buy");
    const rate = Number(custom.per_uc_rate || 0);
    input.min = custom.custom_uc_min;
    input.max = custom.custom_uc_max;
    input.placeholder = custom.custom_uc_min;
    UC.el("custom-uc-range").textContent =
      custom.custom_uc_min + " - " + custom.custom_uc_max.toLocaleString("en-IN") + " UC";

    function update() {
      const quantity = parseInt(input.value, 10) || 0;
      const valid = quantity >= custom.custom_uc_min && quantity <= custom.custom_uc_max;
      priceNode.textContent = valid ? UC.formatINR(Math.round(quantity * rate * 100)) : "-";
      buyButton.disabled = !valid;
    }

    input.addEventListener("input", update);
    buyButton.addEventListener("click", function () {
      const quantity = parseInt(input.value, 10) || 0;
      window.location.href = "/checkout?uc=" + quantity;
    });
    update();
  }

  function renderSite(site, payment) {
    document.querySelectorAll("[data-site-name]").forEach(function (node) {
      node.textContent = site.name;
    });
    UC.el("brand-name").textContent = site.name;
    document.title = site.name + " - BGMI UC top-up store";
    UC.el("hero-headline").innerHTML = buildHeadline(site.tagline);
    UC.el("delivery-note").textContent = site.delivery_note;
    UC.el("disclaimer").textContent = site.disclaimer;
    UC.el("highlights").innerHTML = (site.highlights || [])
      .map(function (item) {
        return "<li>" + UC.escapeHtml(item) + "</li>";
      })
      .join("");
    UC.el("steps").innerHTML = (payment.instructions || [])
      .map(function (step, index) {
        return (
          '<div class="step">' +
          '<div class="step-number">' + (index + 1) + "</div>" +
          "<div><p>" + UC.escapeHtml(step) + "</p></div>" +
          "</div>"
        );
      })
      .join("");
    UC.el("faq-list").innerHTML = (site.faq || [])
      .map(function (item, index) {
        return (
          "<details" + (index === 0 ? " open" : "") + ">" +
          "<summary>" + UC.escapeHtml(item.q) + "</summary>" +
          "<p>" + UC.escapeHtml(item.a) + "</p>" +
          "</details>"
        );
      })
      .join("");

    const supportEmail = UC.el("support-email");
    if (site.support_email) {
      supportEmail.textContent = site.support_email;
      supportEmail.href = "mailto:" + site.support_email;
    }
    const whatsapp = UC.el("support-whatsapp");
    if (site.support_whatsapp) {
      whatsapp.textContent = "WhatsApp " + site.support_whatsapp;
      whatsapp.href = "https://wa.me/" + site.support_whatsapp.replace(/\D/g, "");
    } else {
      whatsapp.classList.add("hidden");
    }
    UC.el("upi-badge").textContent = payment.upi_id;
  }

  // ---------------------------------------------------------------- reviews
  function stars(rating) {
    const full = Math.max(0, Math.min(5, parseInt(rating, 10) || 0));
    let html = '<span class="stars" aria-label="' + full + ' out of 5">';
    for (let i = 1; i <= 5; i += 1) {
      html += i <= full ? "\u2605" : '<span class="off">\u2605</span>';
    }
    return html + "</span>";
  }

  function initials(name) {
    const parts = String(name || "?")
      .replace(/[^A-Za-z0-9 _.-]/g, "")
      .split(/[ _.-]+/)
      .filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function formatReviewDate(value) {
    if (!value) return "";
    const date = new Date(value + "T00:00:00");
    if (isNaN(date.getTime())) return UC.escapeHtml(value);
    return date.toLocaleDateString("en-IN", { month: "short", year: "numeric" });
  }

  function renderReviews(reviews) {
    const grid = UC.el("reviews-grid");
    if (!reviews || !reviews.length) {
      grid.classList.add("hidden");
      const summary = UC.el("reviews-summary");
      if (summary) summary.classList.add("hidden");
      return;
    }
    grid.innerHTML = reviews
      .map(function (review) {
        return (
          '<article class="review-card">' +
          '<div class="review-head">' +
          '<div class="avatar">' + UC.escapeHtml(initials(review.name)) + "</div>" +
          "<div>" +
          '<div class="review-name">' + UC.escapeHtml(review.name) + "</div>" +
          '<div class="review-meta">' + formatReviewDate(review.date) +
          (review.pack ? " \u00b7 " + UC.escapeHtml(review.pack) : "") +
          "</div>" +
          "</div></div>" +
          stars(review.rating) +
          '<p class="review-text">' + UC.escapeHtml(review.text) + "</p>" +
          (review.pack ? '<span class="review-pack">Delivered \u00b7 ' + UC.escapeHtml(review.pack) + "</span>" : "") +
          "</article>"
        );
      })
      .join("");

    const total = reviews.length;
    const average = reviews.reduce(function (sum, r) { return sum + (parseInt(r.rating, 10) || 0); }, 0) / total;
    const summary = UC.el("reviews-summary");
    if (summary) {
      summary.innerHTML =
        '<span class="review-rating-summary">' + stars(Math.round(average)) +
        "<strong>" + average.toFixed(1) + " / 5</strong> from " + total +
        " verified orders</span>";
    }
  }

  // --------------------------------------------------------------- feedback
  function feedbackStoreKey() {
    return "uc_my_feedback_v1";
  }

  function readMyFeedback() {
    try {
      const raw = window.localStorage.getItem(feedbackStoreKey());
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      return [];
    }
  }

  function writeMyFeedback(items) {
    try {
      window.localStorage.setItem(feedbackStoreKey(), JSON.stringify(items.slice(0, 20)));
    } catch (err) {
      /* private mode / quota - the page still works, the copy is just not kept */
    }
  }

  function renderMyFeedback() {
    const items = readMyFeedback();
    const list = UC.el("my-feedback-list");
    const clearButton = UC.el("clear-my-feedback");
    clearButton.classList.toggle("hidden", !items.length);
    if (!items.length) {
      list.innerHTML = '<p class="skeleton">You have not sent any feedback from this device yet.</p>';
      return;
    }
    list.innerHTML = items
      .map(function (item, index) {
        return (
          '<div class="my-feedback-item">' +
          '<div class="review-head">' +
          '<div class="avatar">' + UC.escapeHtml(initials(item.name)) + "</div>" +
          "<div>" +
          '<div class="review-name">' + UC.escapeHtml(item.name || "Anonymous") + "</div>" +
          '<div class="review-meta">' + UC.formatDateTime(item.created_at) +
          (item.pending ? " \u00b7 not delivered to the store yet" : "") +
          "</div>" +
          "</div></div>" +
          stars(item.rating) +
          '<p class="review-text">' + UC.escapeHtml(item.message) + "</p>" +
          '<button class="btn btn-ghost btn-sm" data-remove-feedback="' + index + '" style="margin-top:10px">' +
          "Remove from this device</button>" +
          "</div>"
        );
      })
      .join("");
    list.querySelectorAll("[data-remove-feedback]").forEach(function (button) {
      button.addEventListener("click", function () {
        const index = parseInt(button.dataset.removeFeedback, 10);
        const kept = readMyFeedback().filter(function (_item, i) { return i !== index; });
        writeMyFeedback(kept);
        renderMyFeedback();
      });
    });
  }

  function initFeedback() {
    const form = UC.el("feedback-form");
    if (!form) return;
    let rating = 5;
    const picker = UC.el("rating-input");

    function paintPicker() {
      picker.innerHTML = [1, 2, 3, 4, 5]
        .map(function (value) {
          return (
            '<button type="button" data-star="' + value + '" class="' +
            (value <= rating ? "on" : "") + '" aria-label="' + value + ' star' +
            (value > 1 ? "s" : "") + '">\u2605</button>'
          );
        })
        .join("");
      picker.querySelectorAll("[data-star]").forEach(function (button) {
        button.addEventListener("click", function () {
          rating = parseInt(button.dataset.star, 10);
          paintPicker();
        });
      });
    }
    paintPicker();

    const message = UC.el("feedback-message");
    const counter = UC.el("feedback-count");
    message.addEventListener("input", function () {
      counter.textContent = message.value.length;
    });

    UC.el("clear-my-feedback").addEventListener("click", function () {
      if (!window.confirm("Remove your locally saved feedback from this device?")) return;
      writeMyFeedback([]);
      renderMyFeedback();
      UC.toast("Cleared from this device.");
    });

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const button = UC.el("feedback-submit");
      const errorNode = UC.el("feedback-error");
      const text = message.value.trim();
      if (text.length < 4) {
        UC.showError(errorNode, "Please write a little more before sending.");
        return;
      }
      UC.showError(errorNode, "");
      UC.setBusy(button, true, "Sending...");

      const entry = {
        name: UC.el("feedback-name").value.trim(),
        rating: rating,
        message: text,
        created_at: new Date().toISOString(),
        pending: false,
      };
      try {
        const result = await UC.api("/api/feedback", {
          method: "POST",
          body: { name: entry.name, rating: entry.rating, message: entry.message },
        });
        entry.created_at = result.feedback.created_at;
      } catch (err) {
        // Still keep it on this device - that is the private view the visitor expects.
        entry.pending = true;
        UC.showError(errorNode, err.message + " - it is saved on this device only.");
      }

      writeMyFeedback([entry].concat(readMyFeedback()));
      renderMyFeedback();
      form.reset();
      counter.textContent = "0";
      rating = 5;
      paintPicker();
      UC.setBusy(button, false);
      if (!entry.pending) UC.toast("Thanks! Sent privately to the store.");
    });

    renderMyFeedback();
  }

  function buildHeadline(tagline) {
    const words = String(tagline || "").split(" ");
    if (words.length < 5) return UC.escapeHtml(tagline);
    const head = words.slice(0, words.length - 3).join(" ");
    const tail = words.slice(-3).join(" ");
    return UC.escapeHtml(head) + " <span>" + UC.escapeHtml(tail) + "</span>";
  }

  function initTrackOrder() {
    const button = UC.el("track-button");
    const input = UC.el("track-input");
    if (!button || !input) return;
    function open() {
      const value = input.value.trim().toUpperCase();
      if (!value) return;
      window.location.href = "/order?id=" + encodeURIComponent(value);
    }
    button.addEventListener("click", open);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") open();
    });
  }

  async function init() {
    try {
      config = await UC.api("/api/config");
    } catch (err) {
      packsGrid.innerHTML = '<p class="skeleton">Could not load the store: ' + UC.escapeHtml(err.message) + "</p>";
      return;
    }
    renderSite(config.site, config.payment);
    renderPacks(config.packs);
    renderReviews(config.site.reviews);
    initCalculator(config.custom_uc);
    initTrackOrder();
    initFeedback();
    if (config.payment.upi_id && /yourname@upi|yourupi/i.test(config.payment.upi_id)) {
      const notice = UC.el("setup-notice");
      if (notice) notice.classList.remove("hidden");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
