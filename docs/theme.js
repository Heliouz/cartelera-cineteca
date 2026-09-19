/* theme.js — light / dark controller, ported from the origin project.

   Loaded from <head> as a plain synchronous <script>, before style.css
   finishes parsing, so data-theme is on <html> for the first paint. app.js
   cannot do this job: it is the last thing in <body>, which is long after
   the page has painted, and the visitor would see the light palette flash
   before it flipped.

   Resolution order on first load:
     1. localStorage["cartelera:theme"]  -> "light" | "dark"
     2. matchMedia("(prefers-color-scheme: dark)")
     3. "light"

   Once the visitor clicks the button their choice is persisted and the OS
   preference stops being followed — toggling once and then having the OS
   flip the page back reads as a bug. Until then the page does track the OS,
   so someone who never touches the button still gets their system palette.

   The preference does NOT round-trip through the URL, for the same reason
   `cartelera:ratings` doesn't (see app.js): everything in app.js's `state`
   describes what a shared link should reopen — a day, a sede, a film —
   and whether the reader wants a dark page is about the reader. Sending
   someone a link must not repaint their site. */
(function () {
  "use strict";

  var STORAGE_KEY = "cartelera:theme";
  // Kept in sync with --bg in both palettes so iOS Safari and Android Chrome
  // paint their chrome in the same tone as the page behind it.
  var META_THEME_COLOR = { light: "#f8f8f8", dark: "#111315" };

  function getStored() {
    try {
      var v = localStorage.getItem(STORAGE_KEY);
      return (v === "dark" || v === "light") ? v : null;
    } catch (e) {
      // Private windows and blocked site data throw outright rather than
      // returning null. Falling through to the OS preference is the right
      // answer anyway; the choice just will not survive a reload.
      return null;
    }
  }

  function getSystemPref() {
    return (window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches)
      ? "dark" : "light";
  }

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark"
      ? "dark" : "light";
  }

  function setMetaThemeColor(theme) {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", META_THEME_COLOR[theme] || META_THEME_COLOR.light);
  }

  // Applied at parse time, inside <head>, so the custom properties resolve to
  // the chosen palette before anything is painted.
  document.documentElement.setAttribute("data-theme", getStored() || getSystemPref());
  setMetaThemeColor(currentTheme());

  function applyTheme(theme, persist) {
    if (theme !== "dark" && theme !== "light") return;
    document.documentElement.setAttribute("data-theme", theme);
    setMetaThemeColor(theme);
    if (persist) {
      try { localStorage.setItem(STORAGE_KEY, theme); } catch (e) {}
    }
    var btn = document.getElementById("theme-toggle");
    if (btn) updateButtonState(btn, theme);
    // The one thing CSS cannot repaint on its own is the Letterboxd lockup,
    // which is a raster asset with its wordmark baked in one ink. app.js
    // listens for this and redraws — see onThemeChange() there.
    document.dispatchEvent(new CustomEvent("cartelera:themechange", { detail: { theme: theme } }));
  }

  function updateButtonState(btn, theme) {
    var dark = (theme === "dark");
    btn.setAttribute("aria-pressed", String(dark));
    // Label and title name the ACTION the click performs, not the state that
    // is active — so a screen reader hears what tapping will do.
    var action = dark ? "modo claro" : "modo oscuro";
    btn.setAttribute("aria-label", "activar " + action);
    btn.setAttribute("title", action);
  }

  function init() {
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      updateButtonState(btn, currentTheme());
      btn.addEventListener("click", function () {
        applyTheme(currentTheme() === "dark" ? "light" : "dark", true);
      });
    }

    // Follow OS changes only while the visitor has made no explicit choice.
    // The moment they tap (persist = true) getStored() stops returning null
    // and this becomes a no-op.
    var mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
    if (mq && typeof mq.addEventListener === "function") {
      mq.addEventListener("change", function (ev) {
        if (getStored() !== null) return;
        applyTheme(ev.matches ? "dark" : "light", false);
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
