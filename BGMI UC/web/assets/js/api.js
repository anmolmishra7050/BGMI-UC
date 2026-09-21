/* Shared helpers for every page. Exposes a single global `UC`. */
window.UC = (function () {
  async function api(path, options) {
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {});
    let body;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
    const response = await fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: body,
      credentials: "same-origin",
      cache: "no-store",
    });
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        data = null;
      }
    }
    if (!response.ok) {
      const error = new Error(
        (data && data.error && data.error.message) || "Request failed (" + response.status + ")"
      );
      error.status = response.status;
      error.code = data && data.error ? data.error.code : undefined;
      error.data = data;
      throw error;
    }
    return data;
  }

  function formatINR(paise) {
    const value = Number(paise || 0) / 100;
    return "\u20b9" + value.toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function formatDateTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatClock(totalSeconds) {
    const seconds = Math.max(0, Math.floor(totalSeconds));
    const minutes = Math.floor(seconds / 60);
    return String(minutes).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0");
  }

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value).replace(
      /[&<>"']/g,
      function (ch) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
      }
    );
  }

  function el(id) {
    return document.getElementById(id);
  }

  function qs(name) {
    return new URLSearchParams(window.location.search).get(name) || "";
  }

  function toast(message) {
    let node = document.querySelector(".toast");
    if (!node) {
      node = document.createElement("div");
      node.className = "toast";
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(node._timer);
    node._timer = setTimeout(function () {
      node.classList.remove("show");
    }, 2200);
  }

  async function copy(text, label) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (err) {
      const helper = document.createElement("textarea");
      helper.value = text;
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      document.execCommand("copy");
      helper.remove();
    }
    toast((label || "Copied") + ": " + text);
  }

  function statusPill(status, label) {
    return (
      '<span class="status-pill status-' +
      escapeHtml(status) +
      '">' +
      escapeHtml(label || status) +
      "</span>"
    );
  }

  function showError(node, message) {
    if (!node) return;
    if (!message) {
      node.textContent = "";
      node.classList.remove("show");
      return;
    }
    node.textContent = message;
    node.classList.add("show");
  }

  function setBusy(button, busy, busyLabel) {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.textContent;
      button.textContent = busyLabel || "Please wait...";
      button.disabled = true;
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
    }
  }

  return {
    api: api,
    formatINR: formatINR,
    formatDateTime: formatDateTime,
    formatClock: formatClock,
    escapeHtml: escapeHtml,
    el: el,
    qs: qs,
    toast: toast,
    copy: copy,
    statusPill: statusPill,
    showError: showError,
    setBusy: setBusy,
  };
})();
