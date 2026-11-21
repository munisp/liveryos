/* LiveryOS embeddable booking widget (GA booking-widget parity).
 *
 * Usage on a client site:
 *   <div data-livery-widget data-key="lvk_..." data-tenant="hotel-abc"></div>
 *   <script src="https://<bench>/assets/livery_core/widget/widget.js" defer></script>
 *
 * The widget key is origin-bound and rate-limited server-side
 * (livery_core.widget.widget_keys). This script never stores the key beyond
 * the page, never collects card data (payment happens in the bench-hosted
 * confirmation step), and shows the quote provenance (engine version) the
 * rate engine returns — quotes are never "smoothed" client-side.
 */
"use strict";

(function () {
  const API_BASE = "/api/method/livery_core.api.widget";

  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "style") el.style.cssText = v; else el.setAttribute(k, v);
    });
    (children || []).forEach((c) => el.appendChild(
      typeof c === "string" ? document.createTextNode(c) : c));
    return el;
  }

  function fmt(minor, ccy) {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: ccy })
      .format(minor / 100);
  }

  async function post(path, body) {
    const res = await fetch(`${API_BASE}.${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 429) throw new Error("busy — please retry in a moment");
    if (res.status === 403) throw new Error("booking unavailable on this site");
    if (!res.ok) throw new Error(`request failed (${res.status})`);
    return (await res.json()).message;
  }

  function mount(root) {
    const key = root.dataset.key;
    const tenant = root.dataset.tenant;
    root.style.cssText = "font-family:system-ui;max-width:420px;border:1px solid #d8d2c4;" +
      "border-radius:12px;padding:16px;background:#fdfcf9;color:#2b2b28";

    const form = h("form", { style: "display:grid;gap:8px" });
    const fields = {};
    [["pickup", "Pickup address"], ["dropoff", "Dropoff address"],
     ["pickup_at", "Pickup time", "datetime-local"]].forEach(([name, ph, type]) => {
      fields[name] = h("input", {
        name, placeholder: ph, type: type || "text", required: "required",
        style: "padding:10px;border:1px solid #d8d2c4;border-radius:8px",
      });
      form.appendChild(fields[name]);
    });
    const cls = h("select", { name: "service_class",
      style: "padding:10px;border:1px solid #d8d2c4;border-radius:8px" });
    ["economy", "business", "executive", "sprinter"].forEach((c) =>
      cls.appendChild(h("option", { value: c }, [c])));
    form.appendChild(cls);
    const btn = h("button", { type: "submit",
      style: "padding:12px;border:0;border-radius:8px;background:#2f5d3a;color:#fff;font-weight:600" },
      ["Get quote"]);
    form.appendChild(btn);
    const out = h("div", { style: "margin-top:10px;font-size:14px" }, []);
    root.appendChild(form);
    root.appendChild(out);

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      out.textContent = "Quoting…";
      btn.disabled = true;
      try {
        const q = await post("quote", {
          key, tenant,
          origin: location.origin,
          pickup: fields.pickup.value,
          dropoff: fields.dropoff.value,
          pickup_at: fields.pickup_at.value,
          service_class: cls.value,
        });
        out.textContent = "";
        out.appendChild(h("div", {}, [
          `${fmt(q.quoted_minor, q.currency)} — ${q.service_class} `,
        ]));
        out.appendChild(h("div", { style: "color:#6b675f;font-size:12px" }, [
          `quote engine ${q.engine_version} · valid ${q.valid_minutes} min`,
        ]));
        const book = h("button", {
          style: "margin-top:8px;padding:12px;border:0;border-radius:8px;" +
                 "background:#1d3a5f;color:#fff;font-weight:600;width:100%",
        }, ["Confirm booking"]);
        book.addEventListener("click", async () => {
          book.disabled = true;
          book.textContent = "Booking…";
          try {
            const b = await post("book", { key, tenant, quote_id: q.quote_id,
                                           origin: location.origin });
            out.textContent = `Booked — reference ${b.sales_order}. ` +
              "Confirmation follows by your chosen channel.";
          } catch (e2) { out.textContent = e2.message; book.disabled = false; }
        });
        out.appendChild(book);
      } catch (e) {
        out.textContent = e.message;
      } finally {
        btn.disabled = false;
      }
    });
  }

  document.querySelectorAll("[data-livery-widget]").forEach(mount);
})();
