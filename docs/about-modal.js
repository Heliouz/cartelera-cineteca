/* about-modal.js: first-visit welcome modal (title + how-to-play).

   No trigger button, no ×. It opens once automatically, on the first
   page load ever (gated by localStorage), and never again after it's
   been dismissed via "empezar" (backdrop/Escape work too, as a safety
   net). Purely presentational otherwise: two-step show/hide ([hidden]
   drives display, .visible class drives the opacity/transform
   transition), focus trap via `inert` on sibling elements. */

(function () {
  const LS_SEEN_INTRO = "croquis_seen_intro";

  function init() {
    const modal = document.getElementById("about-modal");
    if (!modal) return;

    let seen = true;
    try { seen = localStorage.getItem(LS_SEEN_INTRO) === "1"; } catch (_) { /* noop */ }
    if (seen) return;

    const content = modal.querySelector(".about-content");
    const startBtn = modal.querySelector(".about-start-btn");

    const inertTargets = () => [
      document.getElementById("top-strip"),
      document.getElementById("map-wrap"),
      document.getElementById("bottom-cta"),
      document.getElementById("final-sheet"),
    ].filter((el) => el && !el.hidden);

    function closeModal() {
      if (modal.hidden) return;
      try { localStorage.setItem(LS_SEEN_INTRO, "1"); } catch (_) { /* noop */ }
      modal.classList.remove("visible");
      for (const el of inertTargets()) el.inert = false;
      setTimeout(() => { modal.hidden = true; }, 240);
    }

    modal.hidden = false;
    for (const el of inertTargets()) el.inert = true;
    requestAnimationFrame(() => {
      modal.classList.add("visible");
      startBtn?.focus({ preventScroll: true });
    });

    if (startBtn) startBtn.addEventListener("click", closeModal);

    modal.addEventListener("click", (ev) => {
      if (modal.hidden) return;
      if (content && content.contains(ev.target)) return;
      closeModal();
    });

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !modal.hidden) closeModal();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
