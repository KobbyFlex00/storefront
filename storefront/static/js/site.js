// Storefront behaviour: filters, quick view, bag and WhatsApp checkout.
// All customer/admin text is inserted with textContent, never innerHTML (XSS-safe).
(function () {
  "use strict";
  const data = JSON.parse(document.getElementById("store-data").textContent);
  const byId = new Map(data.products.map((p) => [p.id, p]));
  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  function money(minor) {
    const whole = Math.floor(minor / 100);
    const cents = minor % 100;
    return data.currency + whole.toLocaleString("en-US") + (cents ? "." + String(cents).padStart(2, "0") : "");
  }

  // ---------------------------------------------------------------- nav
  const toggle = $("#menu-toggle");
  const links = $("#nav-links");
  if (toggle && links) {
    toggle.addEventListener("click", () => {
      const open = links.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
    links.addEventListener("click", (e) => {
      if (e.target.closest("a")) {
        links.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  // ---------------------------------------------------------------- filters
  document.querySelectorAll(".chip[data-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === chip));
      const f = chip.dataset.filter;
      document.querySelectorAll("#grid .card").forEach((card) => {
        card.hidden = Boolean(f) && card.dataset.category !== f;
      });
    });
  });

  // ---------------------------------------------------------------- dialogs
  function openDialog(d) {
    if (typeof d.showModal === "function") d.showModal();
    else d.setAttribute("open", "");
  }
  function closeDialog(d) {
    if (typeof d.close === "function") d.close();
    else d.removeAttribute("open");
  }
  document.querySelectorAll("dialog").forEach((d) => {
    d.addEventListener("click", (e) => {
      if (e.target === d || e.target.closest("[data-close]")) closeDialog(d);
    });
  });

  // ---------------------------------------------------------------- quick view
  const quick = $("#quick");
  let current = null;
  let size = "";

  function showQuick(p) {
    current = p;
    size = p.sizes.length === 1 ? p.sizes[0] : "";
    $("#quick-name").textContent = p.name;
    $("#quick-price").textContent = p.priceText;
    $("#quick-was").textContent = p.wasText;
    $("#quick-desc").textContent = p.description;
    $("#quick-hint").textContent = p.soldOut ? "This piece is sold out. Ask us about a restock or made-to-order." : "";
    $("#quick-qty").value = 1;
    const img = $("#quick-img");
    img.src = p.images[0] || "";
    img.alt = p.name;
    img.hidden = !p.images.length;
    const thumbs = $("#quick-thumbs");
    thumbs.replaceChildren();
    if (p.images.length > 1) {
      p.images.forEach((src, i) => {
        const b = el("button", "thumb" + (i === 0 ? " active" : ""));
        b.type = "button";
        b.setAttribute("aria-label", "Photo " + (i + 1));
        const t = el("img");
        t.src = src;
        t.alt = "";
        b.appendChild(t);
        b.addEventListener("click", () => {
          img.src = src;
          thumbs.querySelectorAll(".thumb").forEach((x) => x.classList.toggle("active", x === b));
        });
        thumbs.appendChild(b);
      });
    }
    const sizes = $("#quick-sizes");
    sizes.replaceChildren();
    p.sizes.forEach((s) => {
      const b = el("button", "size" + (s === size ? " active" : ""), s);
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(s === size));
      b.addEventListener("click", () => {
        size = s;
        sizes.querySelectorAll(".size").forEach((x) => {
          x.classList.toggle("active", x === b);
          x.setAttribute("aria-checked", String(x === b));
        });
        $("#quick-hint").textContent = "";
      });
      sizes.appendChild(b);
    });
    const add = $("#quick-add");
    if (add) {
      add.disabled = p.soldOut;
      add.textContent = p.soldOut ? "Sold out" : "Add to bag";
    }
    openDialog(quick);
  }

  document.querySelectorAll("[data-product]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = byId.get(Number(btn.dataset.product));
      if (p) showQuick(p);
    });
  });

  $("#quick-ask").addEventListener("click", () => {
    if (current) {
      const items = $("#enquiry-items");
      const line = current.name + (size ? " (size " + size + ")" : "");
      items.value = items.value ? items.value + "\n" + line : line;
    }
    closeDialog(quick);
  });

  // ---------------------------------------------------------------- bag
  if (!data.cart) return;
  const KEY = "storefront-bag-v1";
  const cartDialog = $("#cart");

  function load() {
    try {
      const raw = JSON.parse(localStorage.getItem(KEY) || "[]");
      // Only keep lines for products that still exist and can be bought; prices always come from the page.
      return Array.isArray(raw)
        ? raw.filter((l) => byId.has(l.id) && !byId.get(l.id).soldOut)
            .map((l) => ({ id: l.id, size: String(l.size || "").slice(0, 20), qty: Math.min(20, Math.max(1, l.qty | 0)) }))
        : [];
    } catch (e) {
      return [];
    }
  }
  let bag = load();
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(bag)); } catch (e) { /* private mode: bag lasts this visit */ }
    render();
  }

  function total() {
    return bag.reduce((sum, l) => sum + byId.get(l.id).price * l.qty, 0);
  }

  function render() {
    const count = bag.reduce((n, l) => n + l.qty, 0);
    const badge = $("#cart-count");
    badge.textContent = count;
    badge.hidden = count === 0;
    const list = $("#cart-lines");
    list.replaceChildren();
    bag.forEach((l, i) => {
      const p = byId.get(l.id);
      const li = el("li", "line");
      if (p.images[0]) {
        const im = el("img");
        im.src = p.images[0];
        im.alt = "";
        li.appendChild(im);
      }
      const info = el("div", "line-info");
      info.appendChild(el("p", "line-name", p.name));
      info.appendChild(el("p", "muted small", (l.size ? "Size " + l.size + " · " : "") + money(p.price)));
      const qty = el("div", "line-qty");
      const minus = el("button", "qty-btn", "−");
      const plus = el("button", "qty-btn", "+");
      minus.type = plus.type = "button";
      minus.setAttribute("aria-label", "Fewer");
      plus.setAttribute("aria-label", "More");
      minus.addEventListener("click", () => { l.qty -= 1; if (l.qty < 1) bag.splice(i, 1); save(); });
      plus.addEventListener("click", () => { l.qty = Math.min(20, l.qty + 1); save(); });
      qty.append(minus, el("span", null, String(l.qty)), plus);
      info.appendChild(qty);
      li.appendChild(info);
      li.appendChild(el("p", "line-total", money(p.price * l.qty)));
      list.appendChild(li);
    });
    $("#cart-empty").hidden = bag.length > 0;
    $("#cart-foot").hidden = bag.length === 0;
    $("#cart-total").textContent = money(total());
  }

  $("#quick-add").addEventListener("click", () => {
    if (!current || current.soldOut) return;
    if (current.sizes.length > 1 && !size) {
      $("#quick-hint").textContent = "Please choose a size.";
      return;
    }
    const qty = Math.min(20, Math.max(1, parseInt($("#quick-qty").value, 10) || 1));
    const existing = bag.find((l) => l.id === current.id && l.size === size);
    if (existing) existing.qty = Math.min(20, existing.qty + qty);
    else bag.push({ id: current.id, size: size, qty: qty });
    save();
    closeDialog(quick);
    openDialog(cartDialog);
  });

  $("#cart-open").addEventListener("click", () => { render(); openDialog(cartDialog); });

  function whatsappMessage() {
    const lines = ["Hi " + data.store + "! I'd like to order:", ""];
    bag.forEach((l, i) => {
      const p = byId.get(l.id);
      lines.push((i + 1) + ". " + p.name + (l.size ? " — Size " + l.size : "") + " × " + l.qty + " — " + money(p.price * l.qty));
    });
    lines.push("", "Total: " + money(total()));
    const loc = $("#cart-location").value.trim();
    if (loc) lines.push("Delivery to: " + loc);
    return lines.join("\n");
  }
  window.__storefrontMessage = whatsappMessage; // used by the end-to-end test

  $("#cart-send").addEventListener("click", () => {
    if (!bag.length || !data.whatsapp) return;
    const url = "https://wa.me/" + encodeURIComponent(data.whatsapp) + "?text=" + encodeURIComponent(whatsappMessage());
    window.open(url, "_blank", "noopener");
  });

  render();
})();
