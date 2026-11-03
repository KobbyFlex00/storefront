(function () {
  "use strict";
  // Confirm destructive actions.
  document.querySelectorAll("form[data-confirm]").forEach((f) => {
    f.addEventListener("submit", (e) => { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
  });
  document.querySelectorAll("[data-confirm-btn]").forEach((b) => {
    b.addEventListener("click", (e) => { if (!window.confirm(b.dataset.confirmBtn)) e.preventDefault(); });
  });
  // Keep colour pickers and hex inputs in sync.
  document.querySelectorAll("input[type=color][data-sync]").forEach((picker) => {
    const text = document.getElementById(picker.dataset.sync);
    picker.addEventListener("input", () => { text.value = picker.value; });
    text.addEventListener("input", () => { if (/^#[0-9a-fA-F]{6}$/.test(text.value)) picker.value = text.value; });
  });
  // Mobile menu.
  const burger = document.getElementById("burger");
  const side = document.getElementById("side");
  if (burger && side) {
    burger.addEventListener("click", () => {
      const open = side.classList.toggle("open");
      burger.setAttribute("aria-expanded", String(open));
    });
  }
})();
