// Sends a person from a film's link-preview page (docs/p/<id>/index.html,
// written by scraper/previews.py) on to the film in the app. Loaded
// synchronously from each page's <head>, so it runs before anything paints.
//
// One shared file rather than a script inlined into every page: pages are
// never pruned, so a fix made here reaches every link ever shared, while a
// fix to the page template only reaches films still in the cartelera.
(function () {
  "use strict";

  var script = document.currentScript;
  var film = script && script.getAttribute("data-film");
  if (!film) return;

  // Link previewers that run JavaScript - iMessage builds its preview in a
  // web view - would otherwise follow the redirect and read the app's generic
  // tags instead of this page's. They announce themselves (iMessage's user
  // agent carries facebookexternalhit and Twitterbot), so they stay.
  //
  // Crawler names only, one by one. Anything broader catches people: a bare
  // "bot" matches Cubot's phones ("CUBOT X30"), and "pinterest" matches the
  // Pinterest app's own in-app browser. scraper/test_previews.py runs this
  // exact pattern against real user agents on both sides of the line.
  var CRAWLER_UA_RE = /facebookexternalhit|facebot|twitterbot|whatsapp|telegrambot|slackbot|discordbot|linkedinbot|skypeuripreview|redditbot|applebot|googlebot|bingbot|embedly|iframely|pinterestbot|vkshare/i;
  if (CRAWLER_UA_RE.test(navigator.userAgent)) return;

  var root = document.documentElement;
  // Paint nothing but the browser's canvas for the frame before the app
  // arrives, in the theme the visitor chose on the site if they chose one
  // (theme.js stores it). The page's own link stays visible for anyone this
  // script doesn't reach - no JavaScript, or a user agent wrongly matched
  // above - so nobody is ever left on a blank page.
  try {
    var theme = localStorage.getItem("cartelera:theme");
    if (theme === "light" || theme === "dark") root.style.colorScheme = theme;
  } catch (e) {
    // Private windows and blocked site data throw; the OS scheme is fine.
  }
  root.style.visibility = "hidden";
  location.replace("../../?film=" + encodeURIComponent(film));
  // If the navigation stalls - a slow connection, an in-app browser that
  // holds script-initiated navigation - show the page again, link and all.
  // A forward that lands takes this page, and the timer, with it.
  setTimeout(function () {
    root.style.visibility = "";
  }, 2000);
})();
