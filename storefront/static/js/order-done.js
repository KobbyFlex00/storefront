(function () {
  "use strict";
  var page = document.querySelector("[data-clear-bag]");
  if (page && page.dataset.clearBag === "1") {
    try { localStorage.removeItem("storefront-bag-v1"); } catch (e) { /* storage blocked */ }
  }
})();
