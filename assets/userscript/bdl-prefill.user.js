// ==UserScript==
// @name         BDL form prefill
// @namespace    bdl.sextant
// @version      1.0.0
// @description  Fills a reviewed submission form from a BDL pack. Never submits: you review, solve any CAPTCHA, and click.
// @match        *://*/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/* ---------------------------------------------------------------------------
 * What this does, and what it refuses to do.
 *
 * It fills. It does not submit, does not solve a CAPTCHA, does not click a
 * checkbox that says "I agree", and does not hide what it touched: every field
 * it writes gets a dashed outline and a tooltip naming the slot.
 *
 * Flow
 *   1. paste a pack (bdl forms prefill --manifest ... --out ...) once
 *   2. on a matching submission page, press Fill
 *   3. read what was filled, fill what was not, solve the CAPTCHA, click submit
 *   4. the acknowledgement page is captured as a receipt; save it
 *   5. bdl forms adopt --file <receipt> stores the map for next time
 *
 * The receipt is what makes the second encounter cheap: it carries the field
 * names, the slot each one carried, and the page the site answered with.
 * ------------------------------------------------------------------------- */

(function () {
  "use strict";

  var PACK_KEY = "bdl.pack";
  var PENDING_KEY = "bdl.pending";
  var RECEIPTS_KEY = "bdl.receipts";
  var MAX_RECEIPTS = 20;

  // ---------------------------------------------------------------- storage
  var store = {
    get: function (key, fallback) {
      try {
        var raw = window.localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
      } catch (error) {
        return fallback;
      }
    },
    set: function (key, value) {
      try {
        window.localStorage.setItem(key, JSON.stringify(value));
      } catch (error) {
        /* private mode: the panel still works for this page */
      }
    },
    drop: function (key) {
      try {
        window.localStorage.removeItem(key);
      } catch (error) {
        /* ignore */
      }
    },
  };

  // ------------------------------------------------------------- slot rules
  // A field's meaning is in its label, not its name: WPForms writes
  // "wpforms[fields][3]" and the label beside it says "Tell us about your site".
  var SLOT_RULES = [
    ["email", /e-?mail|e-?post/i],
    ["url", /web\s?-?site|\burl\b|homepage|home page|\blink\b|your site/i],
    ["message", /message|comment|description|details?|notes?|\bbody\b|tell us|question|enquir|inquir|feedback/i],
    ["subject", /subject|topic|title|headline/i],
    ["company", /company|organi[sz]ation|business|brand/i],
    ["phone", /phone|\btel\b|telephone|mobile/i],
    ["name", /\bname\b|full name|contact/i],
  ];

  var NEVER_AUTOFILL = ["phone", "captcha"];
  var TECHNICAL = /csrf|token|nonce|wpnonce|redirect|referer|utm_|loadtime|honeypot|gotcha|botcheck|antispam/i;
  var HONEYPOT = /(^|[\[\-_])(hp|honeypot|gotcha|botcheck|antispam|spam)($|[\]\-_\d])/i;

  function textOf(el) {
    var parts = [el.name || "", el.id || "", el.getAttribute("placeholder") || "",
                 el.getAttribute("aria-label") || "", el.getAttribute("title") || ""];
    var label = null;
    if (el.id) {
      try {
        label = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      } catch (error) {
        label = null;
      }
    }
    if (!label) {
      label = el.closest("label");
    }
    if (label) {
      parts.push(label.textContent || "");
    }
    return parts.join(" ");
  }

  function guessSlot(el, slotMap) {
    var name = el.name || el.id || "";
    if (slotMap && name && slotMap[name]) {
      return slotMap[name];
    }
    var type = (el.getAttribute("type") || "").toLowerCase();
    if (type === "email") return "email";
    if (type === "url") return "url";
    if (type === "tel") return "phone";
    if (el.tagName === "TEXTAREA") {
      // A textarea whose label is prose is still the message.
      var named = matchRule(textOf(el), ["email", "url", "subject", "message"]);
      return named || "message";
    }
    return matchRule(textOf(el), null);
  }

  function matchRule(haystack, only) {
    for (var index = 0; index < SLOT_RULES.length; index += 1) {
      var slot = SLOT_RULES[index][0];
      if (only && only.indexOf(slot) === -1) continue;
      if (SLOT_RULES[index][1].test(haystack)) return slot;
    }
    return "";
  }

  function isFillable(el) {
    if (!el || !el.tagName) return false;
    if (["INPUT", "TEXTAREA", "SELECT"].indexOf(el.tagName) === -1) return false;
    if (el.disabled || el.readOnly) return false;
    var type = (el.getAttribute("type") || "").toLowerCase();
    if (["hidden", "submit", "button", "reset", "file", "password", "checkbox", "radio", "search"].indexOf(type) !== -1) {
      return false;
    }
    if (el.name && (TECHNICAL.test(el.name) || HONEYPOT.test(el.name))) return false;
    var box = el.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) return false;
    return true;
  }

  // ----------------------------------------------------------------- filling
  function setValue(el, value) {
    var proto = el.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype
              : el.tagName === "SELECT" ? window.HTMLSelectElement.prototype
              : window.HTMLInputElement.prototype;
    var descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (descriptor && descriptor.set) {
      descriptor.set.call(el, value);
    } else {
      el.value = value;
    }
    // React and Vue listen for these, and ignore a bare assignment.
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  function fillForm(target) {
    var slotMap = target.slot_map && Object.keys(target.slot_map).length ? target.slot_map : null;
    var values = target.values || {};
    var filled = [];
    var unmapped = [];
    var seen = {};

    var elements = document.querySelectorAll("form input, form textarea, form select");
    if (!elements.length) {
      elements = document.querySelectorAll("input, textarea, select");
    }
    Array.prototype.forEach.call(elements, function (el) {
      if (!isFillable(el)) return;
      var slot = guessSlot(el, slotMap);
      if (!slot) {
        unmapped.push({ name: el.name || el.id || el.tagName.toLowerCase(), label: textOf(el).trim().slice(0, 80) });
        return;
      }
      if (NEVER_AUTOFILL.indexOf(slot) !== -1) {
        unmapped.push({ name: el.name || el.id, slot: slot, reason: "never autofilled" });
        return;
      }
      if (!values[slot]) {
        unmapped.push({ name: el.name || el.id, slot: slot, reason: "no value for this slot" });
        return;
      }
      setValue(el, values[slot]);
      el.style.outline = "2px dashed #2f9e44";
      el.style.outlineOffset = "1px";
      el.title = "BDL: " + slot;
      filled.push({ name: el.name || el.id, slot: slot });
      seen[el.name || el.id] = slot;
    });

    target.slot_map = seen;
    return { filled: filled, unmapped: unmapped };
  }

  // --------------------------------------------------------------- receipts
  function fingerprintOf(text) {
    // FNV-1a: no crypto.subtle on an insecure origin, and a stable short hash is
    // all a "did the page change" check needs.
    var hash = 0x811c9dc5;
    for (var index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = (hash + ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>> 0;
    }
    return ("00000000" + hash.toString(16)).slice(-8);
  }

  function stamp() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  function makeReceipt(pending, responseUrl) {
    var body = document.body ? document.body.innerText || "" : "";
    return {
      kind: "bdl.submit_receipt",
      schema_version: "1.0.0",
      receipt_id: fingerprintOf(pending.target_domain + "|" + pending.posted_at + "|" + responseUrl),
      center_domain: pending.center_domain,
      target_domain: pending.target_domain,
      method: pending.method || "POST",
      submission_url: pending.submission_url,
      response_url: responseUrl,
      response_title: document.title || "",
      response_fingerprint: { algorithm: "fnv1a-32", value: fingerprintOf(body.slice(0, 20000)) },
      posted_at: pending.posted_at,
      captured_at: stamp(),
      field_names: pending.field_names || [],
      slot_map: pending.slot_map || {},
      values: pending.values || {},
      shape: pending.shape || "",
      captcha_present: !!pending.captcha_present,
      success_selector: pending.success_selector || "",
      submitted_by_human: true,
      agent: "bdl-prefill-userscript/1.0.0",
    };
  }

  function saveReceipt(receipt) {
    var list = store.get(RECEIPTS_KEY, []);
    list = list.filter(function (item) { return item.receipt_id !== receipt.receipt_id; });
    list.unshift(receipt);
    store.set(RECEIPTS_KEY, list.slice(0, MAX_RECEIPTS));
  }

  function download(receipt) {
    var name = "bdl-receipt-" + (receipt.target_domain || "target").replace(/[^a-z0-9.-]/gi, "-") +
               "-" + receipt.receipt_id + ".json";
    var blob = new Blob([JSON.stringify(receipt, null, 2) + "\n"], { type: "application/json" });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    setTimeout(function () {
      URL.revokeObjectURL(link.href);
      link.remove();
    }, 1000);
    return name;
  }

  // ------------------------------------------------------------------- match
  function currentTarget(pack) {
    if (!pack || !pack.targets) return null;
    var here = location.origin + location.pathname.replace(/\/$/, "");
    var best = null;
    pack.targets.forEach(function (target) {
      var url;
      try {
        url = new URL(target.submission_url);
      } catch (error) {
        return;
      }
      var there = url.origin + url.pathname.replace(/\/$/, "");
      if (here === there) {
        best = target;
      } else if (!best && here.indexOf(there) === 0) {
        best = target; // a ?page=submit form still matches its page
      }
    });
    return best;
  }

  // -------------------------------------------------------------- submit tap
  function watchSubmits() {
    document.addEventListener("submit", function (event) {
      var target = window.__bdlTarget;
      if (!target) return;
      var form = event.target;
      var here = location.href;
      var fields = form ? form.querySelectorAll("input, textarea, select") : [];
      var names = [];
      Array.prototype.forEach.call(fields, function (el) {
        if (el.name) names.push(el.name);
      });
      var pending = {
        center_domain: target.center_domain,
        target_domain: target.target_domain,
        method: ((form && form.getAttribute("method")) || target.method || "POST").toUpperCase(),
        submission_url: target.submission_url,
        posted_at: stamp(),
        field_names: names.length ? names : Object.keys(target.slot_map || {}),
        slot_map: target.slot_map || {},
        values: target.values || {},
        shape: target.shape || "",
        captcha_present: !!target.captcha ||
          !!document.querySelector("[class*=captcha], [id*=captcha], iframe[src*=recaptcha]"),
        success_selector: target.success_selector || "",
      };
      store.set(PENDING_KEY, pending);
      // An AJAX form does not navigate, so the acknowledgement has to be taken
      // from this page once it settles. A navigating form replaces the document
      // before this fires, and boot() captures the new URL instead.
      setTimeout(function () {
        var still = store.get(PENDING_KEY, null);
        if (still && still.posted_at === pending.posted_at && location.href === here) {
          finish(still, here, true);
        }
      }, 5000);
    }, true);
  }

  function finish(pending, responseUrl, samePage) {
    var receipt = makeReceipt(pending, responseUrl);
    receipt.response_is_form_page = !!samePage;
    saveReceipt(receipt);
    store.drop(PENDING_KEY);
    return receipt;
  }

  // ------------------------------------------------------------------- panel
  function panel() {
    var root = document.createElement("div");
    root.id = "bdl-panel";
    root.style.cssText = [
      "position:fixed", "right:16px", "bottom:16px", "width:330px", "z-index:2147483647",
      "font:12px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
      "color:#1f2328", "background:#ffffff", "border:1px solid #d0d7de", "border-radius:10px",
      "box-shadow:0 8px 24px rgba(0,0,0,.18)", "padding:0", "display:none",
    ].join(";");
    var header = document.createElement("div");
    header.style.cssText = "display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border-bottom:1px solid #eaeef2;font-weight:600";
    header.innerHTML = '<span>BDL prefill</span>';
    var close = document.createElement("button");
    close.textContent = "×";
    close.style.cssText = "border:0;background:none;font-size:16px;cursor:pointer;line-height:1";
    close.onclick = function () { root.style.display = "none"; };
    header.appendChild(close);
    var body = document.createElement("div");
    body.style.cssText = "padding:10px;max-height:60vh;overflow:auto";

    var status = document.createElement("div");
    status.style.cssText = "margin-bottom:8px;color:#57606a";

    var area = document.createElement("textarea");
    area.placeholder = "paste prefill-<center>-<batch>.json here";
    area.style.cssText = "width:100%;height:70px;box-sizing:border-box;font:11px/1.4 ui-monospace,monospace;border:1px solid #d0d7de;border-radius:6px;padding:6px";

    var actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:6px;margin-top:8px;flex-wrap:wrap";

    function button(label, onclick, primary) {
      var element = document.createElement("button");
      element.textContent = label;
      element.style.cssText = [
        "cursor:pointer", "border-radius:6px", "padding:4px 9px", "font-size:12px",
        primary ? "background:#1f6feb;color:#fff;border:1px solid #1f6feb"
                : "background:#f6f8fa;color:#1f2328;border:1px solid #d0d7de",
      ].join(";");
      element.onclick = onclick;
      return element;
    }

    var report = document.createElement("div");
    report.style.cssText = "margin-top:8px;white-space:pre-wrap;font-size:11px;color:#1f2328";

    function render() {
      var pack = store.get(PACK_KEY, null);
      var target = currentTarget(pack);
      window.__bdlTarget = null;
      area.style.display = pack ? "none" : "block";
      actions.innerHTML = "";
      report.textContent = "";

      // A receipt is worth saving whether or not this page is in the pack: the
      // acknowledgement page almost never is.
      var receipts = store.get(RECEIPTS_KEY, []);
      if (receipts.length) {
        actions.appendChild(button("Save receipt (" + receipts.length + ")", function () {
          var name = download(receipts[0]);
          report.textContent = "Saved " + name +
            "\nThen: bdl forms adopt --file <path> --reviewed-by <you>";
        }, !pack));
      }

      if (!pack) {
        status.textContent = "No pack loaded.";
        actions.appendChild(button("Load pack", function () {
          try {
            var parsed = JSON.parse(area.value);
            if (parsed.kind !== "bdl.prefill_pack") throw new Error("not a prefill pack");
            store.set(PACK_KEY, parsed);
            render();
          } catch (error) {
            status.textContent = "That is not a pack: " + error.message;
          }
        }, true));
        return;
      }

      status.textContent = (pack.targets || []).length + " target(s), built " + (pack.generated_at || "?");
      if (!target) {
        report.textContent = "This page is not in the pack.\n" + location.href;
        actions.appendChild(button("Clear pack", function () {
          store.drop(PACK_KEY);
          render();
        }));
        return;
      }

      target.center_domain = pack.center_domain;
      window.__bdlTarget = target;
      report.textContent = [
        target.target_domain,
        "mapping: " + (target.mapping || "none") + (target.reused_from ? " (from " + target.reused_from + ")" : ""),
        target.captcha ? "CAPTCHA present: solve it yourself" : "",
        (target.gaps || []).length ? "gaps: " + target.gaps.join(", ") : "",
      ].filter(Boolean).join("\n");

      actions.appendChild(button("Fill form", function () {
        var result = fillForm(target);
        var lines = ["filled " + result.filled.length + " field(s)"];
        result.filled.forEach(function (item) { lines.push("  " + item.slot + " → " + item.name); });
        if (result.unmapped.length) {
          lines.push("left for you:");
          result.unmapped.forEach(function (item) {
            lines.push("  " + (item.slot || "?") + " " + item.name + (item.reason ? " (" + item.reason + ")" : ""));
          });
        }
        lines.push("", "Review, solve any CAPTCHA, then click submit yourself.");
        report.textContent = lines.join("\n");
      }, true));

      actions.appendChild(button("Clear pack", function () {
        store.drop(PACK_KEY);
        render();
      }));
    }

    body.appendChild(status);
    body.appendChild(area);
    body.appendChild(actions);
    body.appendChild(report);
    root.appendChild(header);
    root.appendChild(body);

    var launcher = document.createElement("button");
    launcher.textContent = "BDL";
    launcher.style.cssText = [
      "position:fixed", "right:16px", "bottom:16px", "z-index:2147483647",
      "background:#1f6feb", "color:#fff", "border:0", "border-radius:999px",
      "padding:8px 14px", "font:600 12px/1 -apple-system,sans-serif", "cursor:pointer",
      "box-shadow:0 4px 12px rgba(0,0,0,.25)",
    ].join(";");

    function show() {
      root.style.display = "block";
      launcher.style.display = "none";
      render();
    }

    function hide() {
      root.style.display = "none";
      launcher.style.display = "block";
    }

    close.onclick = hide;
    launcher.onclick = show;
    hide();

    document.body.appendChild(root);
    document.body.appendChild(launcher);

    return { render: render, show: show, hide: hide };
  }

  // -------------------------------------------------------------------- boot
  function boot() {
    var ui = panel();

    // A receipt is only meaningful if a submit was seen on some page before this
    // one: the acknowledgement is a different URL from the form.
    var pending = store.get(PENDING_KEY, null);
    if (pending && location.href.split("?")[0] !== String(pending.submission_url).split("?")[0]) {
      var receipt = finish(pending, location.href, false);
      ui.render();
      var note = document.createElement("div");
      note.textContent = "Receipt captured for " + receipt.target_domain + ". Open the BDL panel to save it.";
      note.style.cssText = [
        "position:fixed", "right:16px", "bottom:64px", "z-index:2147483647",
        "background:#1a7f37", "color:#fff", "padding:8px 12px", "border-radius:8px",
        "font:12px/1.4 -apple-system,sans-serif", "max-width:320px",
      ].join(";");
      document.body.appendChild(note);
      setTimeout(function () { note.remove(); }, 12000);
    }

    watchSubmits();
    ui.render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
