/* ==========================================================================
   IQRA TCS Admin Web CRM — Interactive Shell, Progressive HTMX & UI Helpers
   ========================================================================== */
(function () {
  "use strict";

  var SIDEBAR_STORAGE_KEY = "iqra_crm_sidebar_collapsed";

  /* 1. Restore desktop collapsed sidebar preference */
  try {
    if (window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1") {
      document.body.classList.add("sidebar-collapsed");
    }
  } catch (_e) {
    /* ignore storage errors */
  }

  /* 2. Live Asia/Kolkata (IST) clock in header */
  function updateIstClock() {
    var clockEl = document.getElementById("crm-ist-clock");
    if (!clockEl) return;
    try {
      var formatted = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Kolkata",
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date());
      clockEl.textContent = formatted + " IST";
    } catch (_e) {
      /* keep server-rendered fallback */
    }
  }
  updateIstClock();
  window.setInterval(updateIstClock, 30000);

  /* 3. Bulk Lead Selection Counter Helper */
  function syncLeadSelectionCount() {
    var checkboxes = document.querySelectorAll(".lead-row-checkbox:not(:disabled)");
    var checkedCount = 0;
    checkboxes.forEach(function (cb) {
      var row = cb.closest("tr");
      if (cb.checked) {
        checkedCount += 1;
        if (row) row.classList.add("is-selected");
      } else if (row) {
        row.classList.remove("is-selected");
      }
    });
    var badge = document.getElementById("bulk-selected-count");
    if (badge) {
      badge.textContent = checkedCount + " selected";
    }
    var selectAll = document.getElementById("select-all-leads");
    if (selectAll && checkboxes.length > 0) {
      selectAll.checked = checkedCount === checkboxes.length;
      selectAll.indeterminate = checkedCount > 0 && checkedCount < checkboxes.length;
    }
  }

  /* 4. Accessible Confirmation Modal & Mobile Sidebar Helpers */
  var pendingConfirmAction = null;

  function setMobileSidebarOpen(isOpen) {
    if (isOpen) {
      document.body.classList.add("sidebar-open");
    } else {
      document.body.classList.remove("sidebar-open");
    }
    var toggles = document.querySelectorAll("[data-action='toggle-mobile-sidebar']");
    toggles.forEach(function (btn) {
      btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
  }

  function openConfirmModal(message, triggerEl) {
    var modal = document.getElementById("crm-confirm-modal");
    var msgEl = document.getElementById("crm-confirm-modal-message");
    if (!modal || !msgEl) {
      return window.confirm(message);
    }
    msgEl.textContent = message;
    pendingConfirmAction = triggerEl;
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    var confirmBtn = document.getElementById("crm-confirm-modal-accept");
    if (confirmBtn) confirmBtn.focus();
    return false;
  }

  function closeConfirmModal(restoreFocus) {
    var modal = document.getElementById("crm-confirm-modal");
    var triggerToFocus = pendingConfirmAction;
    if (modal) {
      modal.classList.remove("is-open");
      modal.setAttribute("aria-hidden", "true");
    }
    pendingConfirmAction = null;
    if (restoreFocus !== false && triggerToFocus && typeof triggerToFocus.focus === "function") {
      triggerToFocus.focus();
    }
  }

  /* 5. Delegated Click & Change Handlers */
  document.addEventListener("click", function (event) {
    /* Mobile sidebar open/close */
    var mobileToggle = event.target.closest("[data-action='toggle-mobile-sidebar']");
    if (mobileToggle) {
      event.preventDefault();
      var nextOpen = !document.body.classList.contains("sidebar-open");
      setMobileSidebarOpen(nextOpen);
      return;
    }

    var backdrop = event.target.closest("#crm-sidebar-backdrop");
    if (backdrop) {
      setMobileSidebarOpen(false);
      return;
    }

    /* Desktop sidebar collapse toggle */
    var desktopToggle = event.target.closest("[data-action='toggle-desktop-sidebar']");
    if (desktopToggle) {
      event.preventDefault();
      var collapsed = document.body.classList.toggle("sidebar-collapsed");
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
      } catch (_e) {
        /* ignore */
      }
      return;
    }

    /* Password visibility toggle */
    var pwToggle = event.target.closest("[data-toggle-password]");
    if (pwToggle) {
      event.preventDefault();
      var targetId = pwToggle.getAttribute("data-toggle-password");
      var input = targetId ? document.getElementById(targetId) : null;
      if (input) {
        var nextType = input.getAttribute("type") === "password" ? "text" : "password";
        input.setAttribute("type", nextType);
        pwToggle.setAttribute(
          "aria-label",
          nextType === "password" ? "Show password" : "Hide password"
        );
        pwToggle.setAttribute(
          "title",
          nextType === "password" ? "Show password" : "Hide password"
        );
      }
      return;
    }

    /* Modal backdrop click dismissal */
    if (event.target && event.target.id === "crm-confirm-modal") {
      event.preventDefault();
      closeConfirmModal(true);
      return;
    }

    /* Modal cancel / accept buttons */
    if (event.target.closest("[data-action='cancel-confirm-modal']")) {
      event.preventDefault();
      closeConfirmModal(true);
      return;
    }

    if (event.target.closest("#crm-confirm-modal-accept")) {
      event.preventDefault();
      var targetEl = pendingConfirmAction;
      closeConfirmModal(false);
      if (targetEl) {
        targetEl.setAttribute("data-confirmed", "true");
        if (targetEl.tagName === "FORM") {
          targetEl.submit();
        } else {
          targetEl.click();
        }
      }
      return;
    }

    /* Confirmation dialog on [data-confirm] elements */
    var confirmBtn = event.target.closest("[data-confirm]");
    if (confirmBtn) {
      if (confirmBtn.getAttribute("data-confirmed") === "true") {
        confirmBtn.removeAttribute("data-confirmed");
        return;
      }
      var message = confirmBtn.getAttribute("data-confirm");
      if (message) {
        var modal = document.getElementById("crm-confirm-modal");
        if (modal) {
          event.preventDefault();
          openConfirmModal(message, confirmBtn);
        } else if (!window.confirm(message)) {
          event.preventDefault();
        }
        return;
      }
    }
  });

  document.addEventListener("change", function (event) {
    if (event.target && event.target.id === "select-all-leads") {
      var checked = event.target.checked;
      var checkboxes = document.querySelectorAll(".lead-row-checkbox");
      checkboxes.forEach(function (cb) {
        if (!cb.disabled) {
          cb.checked = checked;
        }
      });
      syncLeadSelectionCount();
      return;
    }

    if (event.target && event.target.classList.contains("lead-row-checkbox")) {
      syncLeadSelectionCount();
    }
  });

  document.addEventListener("keydown", function (event) {
    var modal = document.getElementById("crm-confirm-modal");
    var isModalOpen = modal && modal.classList.contains("is-open");

    if (event.key === "Escape") {
      if (isModalOpen) {
        event.preventDefault();
        closeConfirmModal(true);
      }
      if (document.body.classList.contains("sidebar-open")) {
        setMobileSidebarOpen(false);
      }
      return;
    }

    if (event.key === "Tab" && isModalOpen) {
      var focusables = Array.prototype.slice.call(
        modal.querySelectorAll(
          "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
        )
      );
      if (focusables.length > 0) {
        var first = focusables[0];
        var last = focusables[focusables.length - 1];
        if (event.shiftKey) {
          if (document.activeElement === first || !modal.contains(document.activeElement)) {
            event.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last || !modal.contains(document.activeElement)) {
            event.preventDefault();
            first.focus();
          }
        }
      }
    }
  });

  /* 6. Progressive HTMX-compatible [hx-get] form & link handler */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.hasAttribute("hx-get")) return;
    var hxUrl = form.getAttribute("hx-get");
    var hxTargetSelector = form.getAttribute("hx-target");
    if (!hxUrl || !hxTargetSelector) return;

    var targetEl = document.querySelector(hxTargetSelector);
    if (!targetEl || typeof window.fetch !== "function") return;

    event.preventDefault();
    var targetId = hxTargetSelector.replace(/^#/, "");
    var formData = new FormData(form);
    var params = new URLSearchParams();
    formData.forEach(function (value, key) {
      if (typeof value === "string") {
        params.append(key, value);
      }
    });
    var queryStr = params.toString();
    var requestUrl = hxUrl + (queryStr ? (hxUrl.indexOf("?") >= 0 ? "&" : "?") + queryStr : "");

    var indicator = document.getElementById("hx-loading-indicator");
    if (indicator) indicator.classList.add("is-loading");

    window
      .fetch(requestUrl, {
        method: "GET",
        headers: {
          "HX-Request": "true",
          "HX-Target": targetId,
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.text();
      })
      .then(function (html) {
        targetEl.innerHTML = html;
        if (form.getAttribute("hx-push-url") === "true" && window.history.pushState) {
          window.history.pushState({}, "", requestUrl);
        }
        syncLeadSelectionCount();
      })
      .catch(function () {
        window.location.href = requestUrl;
      })
      .finally(function () {
        if (indicator) indicator.classList.remove("is-loading");
      });
  });
})();
