(function () {
  "use strict";

  var DATA_URL = "data/schedule.json";
  var SEDE_ORDER = ["003", "001", "002"];
  // Only these two origins may ever reach an href we hand a visitor. See
  // ticketHref().
  var TICKET_URL_PREFIX =
    "https://rbvfcn.cinetecanacional.net/Ticketing/visSelectTickets.aspx?";
  var CINETECA_PREFIX = "https://www.cinetecanacional.net/";
  var STALE_HOURS = 36;
  var TZ = "America/Mexico_City";

  var DATA = null;
  var defaultDay = null;
  var unpublishedDays = [];
  var searchDebounceTimer = null;

  var state = { view: "dia", day: null, sede: "000", ciclo: "", q: "", film: "" };

  // The one visitor preference on the site, and deliberately the only piece of
  // state that does NOT round-trip through the URL. Everything in `state`
  // above describes what a shared link should reopen - a day, a sede, a film.
  // Whether the reader wants to see scores is about the reader, not the
  // screening, so sending someone a link must not turn their ratings off.
  //
  // localStorage instead, which is per-browser and never reaches us. It can
  // also throw outright rather than return null (private windows, blocked site
  // data), so both accessors are wrapped and every failure falls back to
  // showing - the preference not persisting is a smaller harm than the site
  // quietly deciding to withhold data nobody asked it to withhold.
  var RATINGS_PREF_KEY = "cartelera:ratings";
  var ratingsVisible = true;

  function loadRatingsPref() {
    try {
      return localStorage.getItem(RATINGS_PREF_KEY) !== "0";
    } catch (e) {
      return true;
    }
  }

  function saveRatingsPref(on) {
    try {
      localStorage.setItem(RATINGS_PREF_KEY, on ? "1" : "0");
    } catch (e) {
      // Nothing to do: the choice still holds for this session, it just will
      // not survive a reload.
    }
  }
  var els = {};
  var sheetPushedState = false;
  var sheetTriggerEl = null;
  var buyTriggerEl = null;

  function qs(id) {
    return document.getElementById(id);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function normalize(text) {
    return (text || "")
      .normalize("NFD")
      .replace(/\p{Diacritic}/gu, "")
      .toLowerCase();
  }

  function todayISO() {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
  }

  function nowMinutesCDMX() {
    var parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: TZ,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(new Date());
    var hh = 0,
      mm = 0;
    parts.forEach(function (p) {
      if (p.type === "hour") hh = parseInt(p.value, 10);
      if (p.type === "minute") mm = parseInt(p.value, 10);
    });
    return hh * 60 + mm;
  }

  function hoursSince(iso) {
    var then = new Date(iso).getTime();
    return Math.max(0, Math.floor((Date.now() - then) / 3600000));
  }

  // "HOY" is decided by the actual date, never by position in DATA.days. If a
  // scrape is missed, days[0] is yesterday — labelling it "HOY" would present a
  // day of already-finished screenings as today's agenda, and the stale banner
  // doesn't fire until STALE_HOURS.
  function dayLabel(iso) {
    if (iso === todayISO()) return "HOY";
    var d = new Date(iso + "T12:00:00-06:00");
    var weekday = new Intl.DateTimeFormat("es-MX", { weekday: "short", timeZone: TZ }).format(d);
    weekday = weekday.replace(".", "");
    var cap = weekday.charAt(0).toUpperCase() + weekday.slice(1);
    var dayNum = parseInt(iso.slice(8, 10), 10);
    return cap + " " + dayNum;
  }

  // ---------- boot ----------

  function init() {
    els.agenda = qs("agenda");
    els.dayStrip = qs("day-strip");
    els.sedeFilter = qs("sede-filter");
    els.searchInput = qs("search-input");
    els.searchClear = qs("search-clear");
    els.cicloSelect = qs("ciclo-select");
    els.dateKicker = qs("date-kicker");
    els.staleBanner = qs("stale-banner");
    els.errorState = qs("error-state");
    els.globalEmpty = qs("global-empty");
    els.resultsStatus = qs("results-status");
    els.clearFiltersBtn = qs("clear-filters-btn");
    els.infoBtn = qs("info-btn");
    els.agendaWrap = qs("agenda-wrap");
    els.viewToggle = qs("view-toggle");
    els.filmSheet = qs("film-sheet");
    els.sheetBody = els.filmSheet ? els.filmSheet.querySelector(".sheet-body") : null;
    els.sheetContent = els.filmSheet ? els.filmSheet.querySelector(".sheet-content") : null;
    els.sheetClose = els.filmSheet ? els.filmSheet.querySelector(".sheet-close") : null;
    els.appRoot = qs("app");
    els.buyModal = qs("buy-modal");
    els.topBar = qs("top-bar");
    els.topbarRow = els.topBar ? els.topBar.querySelector(".topbar-row") : null;

    ratingsVisible = loadRatingsPref();
    bindAboutModalTrigger();
    bindRatingsToggle();
    bindThemeRedraw();
    bindBuyModal();
    bindAutoHideTopBar();

    fetch(DATA_URL, { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      })
      .then(function (data) {
        DATA = data;
        onDataLoaded();
      })
      .catch(function (err) {
        showErrorState(err);
      });
  }

  function showErrorState(err) {
    console.error("cartelera: failed to load schedule.json", err);
    qs("top-bar").hidden = true;
    els.agendaWrap.hidden = true;
    els.errorState.hidden = false;
  }

  function isRenderableSede(code) {
    return code === "000" || (SEDE_ORDER.indexOf(code) !== -1 && Boolean(DATA.sedes[code]));
  }

  function announce(text) {
    if (els.resultsStatus) els.resultsStatus.textContent = text;
  }

  function uniqueCiclos() {
    var set = new Set();
    DATA.films.forEach(function (f) {
      if (f.ciclo) set.add(f.ciclo);
    });
    return Array.from(set).sort(function (a, b) {
      return a.localeCompare(b, "es");
    });
  }

  // Cineteca publishes its cartelera a week at a time, a few days ahead, while
  // the day picker always offers a rolling seven days. So the last days of the
  // window routinely hold nothing but a handful of advance sales at a single
  // sede — a real agenda, but a partial one. Left unmarked they render as two
  // sede columns reading "sin funciones este día", which says "the cinema is
  // dark" when the truth is "not published yet".
  //
  // A day only counts as unpublished if all three hold: it covers fewer sedes
  // than the best-covered day in the window, it carries less than half the
  // showtimes of a typical full day, and it sits in an unbroken run at the end
  // of the window. A genuinely quiet closing day keeps the normal empty copy.
  function computeUnpublishedDays() {
    var totals = {};
    var coverage = {};
    DATA.days.forEach(function (iso) {
      totals[iso] = 0;
      coverage[iso] = {};
    });
    DATA.films.forEach(function (film) {
      film.showtimes.forEach(function (st) {
        if (!(st.date in totals)) return;
        totals[st.date] += 1;
        coverage[st.date][st.sede] = true;
      });
    });

    function sedesOn(iso) {
      return Object.keys(coverage[iso]).length;
    }

    var maxSedes = 0;
    DATA.days.forEach(function (iso) {
      maxSedes = Math.max(maxSedes, sedesOn(iso));
    });

    var fullTotals = DATA.days
      .filter(function (iso) {
        return sedesOn(iso) === maxSedes;
      })
      .map(function (iso) {
        return totals[iso];
      })
      .sort(function (a, b) {
        return a - b;
      });
    if (!fullTotals.length) return [];
    var median = fullTotals[Math.floor(fullTotals.length / 2)];

    var tail = [];
    for (var i = DATA.days.length - 1; i >= 0; i--) {
      var iso = DATA.days[i];
      if (sedesOn(iso) < maxSedes && totals[iso] < median * 0.5) tail.unshift(iso);
      else break;
    }
    return tail;
  }

  function isUnpublishedDay(iso) {
    return unpublishedDays.indexOf(iso) !== -1;
  }

  function onDataLoaded() {
    var today = todayISO();
    defaultDay = DATA.days.indexOf(today) !== -1 ? today : DATA.days[0];
    unpublishedDays = computeUnpublishedDays();

    var params = new URLSearchParams(location.search);

    state.view = params.get("v") === "peli" ? "pelicula" : "dia";

    var dia = params.get("dia");
    state.day = dia && DATA.days.indexOf(dia) !== -1 ? dia : defaultDay;

    // Validated against SEDE_ORDER, not just DATA.sedes: the agenda is keyed
    // by SEDE_ORDER, so a sede present in the data but missing from that list
    // would reach renderAgenda as an undefined bucket and throw.
    var sede = params.get("sede");
    state.sede = sede && isRenderableSede(sede) ? sede : "000";

    var ciclos = uniqueCiclos();
    var ciclo = params.get("ciclo");
    state.ciclo = ciclo && ciclos.indexOf(ciclo) !== -1 ? ciclo : "";

    state.q = params.get("q") || "";
    els.searchInput.value = state.q;
    els.searchClear.hidden = !state.q;

    var filmId = params.get("film");
    var initialFilm = filmId ? findFilm(filmId) : null;
    state.film = initialFilm ? initialFilm.id : "";

    renderDayStrip();
    renderSedeFilter();
    renderCicloSelect(ciclos);
    renderDateKicker();
    renderStaleBanner();
    renderViewToggle();
    els.dayStrip.hidden = state.view === "pelicula";
    if (state.view === "pelicula") renderFilmIndex();
    else renderAgenda();
    syncUrl();
    bindFilterEvents();
    bindFilmSheetEvents();
    bindClock();

    if (initialFilm) openFilmSheet(initialFilm, false);
  }

  // ---------- keeping "now" current ----------

  // Everything time-relative - the upcoming/past split, "HOY", the date
  // kicker, the stale banner - is computed at render time, and a tab left open
  // from lunch to evening would otherwise go on listing the 14:00 screenings
  // as upcoming. So re-check once a minute and whenever the tab comes back
  // into view, and redraw only when something the page shows has actually
  // moved: the date, or the number of today's screenings that have started.
  function clockKey() {
    var today = todayISO();
    var nowMin = nowMinutesCDMX();
    var started = 0;
    DATA.films.forEach(function (film) {
      film.showtimes.forEach(function (st) {
        if (st.date === today && timeToMinutes(st.time) < nowMin) started++;
      });
    });
    return today + "|" + started;
  }

  function bindClock() {
    var lastKey = clockKey();

    function tick() {
      if (document.hidden) return;
      var key = clockKey();
      if (key === lastKey) return;

      // Never redraw out from under someone: an open sheet or warning would
      // lose its trigger to return focus to, and a focused row or chip would
      // be replaced by a fresh copy, dropping focus to <body>. Leaving
      // lastKey alone means the next tick simply tries again.
      var active = document.activeElement;
      if (!els.filmSheet.hidden || !els.buyModal.hidden) return;
      if (active && (els.agenda.contains(active) || els.dayStrip.contains(active))) return;

      var dayChanged = key.split("|")[0] !== lastKey.split("|")[0];
      lastKey = key;

      if (dayChanged) {
        // A visitor still on the old default day hadn't chosen it; they were
        // looking at "today", so they follow it. An explicit pick stays put.
        var today = todayISO();
        var wasDefault = state.day === defaultDay;
        defaultDay = DATA.days.indexOf(today) !== -1 ? today : DATA.days[0];
        if (wasDefault) state.day = defaultDay;
        renderDayStrip();
        renderDateKicker();
      }
      renderStaleBanner();

      // A redraw rebuilds every sede column, which would snap shut any "ya
      // pasaron" list the visitor had opened.
      var openPast = [];
      els.agenda.querySelectorAll(".sede-column").forEach(function (col) {
        var d = col.querySelector(".past-details");
        if (d && d.open) openPast.push(col.dataset.sede);
      });
      setState({});
      openPast.forEach(function (code) {
        var d = els.agenda.querySelector('.sede-column[data-sede="' + code + '"] .past-details');
        if (d) d.open = true;
      });
    }

    setInterval(tick, 60000);
    document.addEventListener("visibilitychange", tick);
  }

  // ---------- header controls ----------

  function renderDayStrip() {
    els.dayStrip.innerHTML = "";
    DATA.days.forEach(function (iso) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "day-chip";
      btn.dataset.day = iso;
      btn.setAttribute("aria-pressed", String(iso === state.day));
      btn.textContent = dayLabel(iso);
      btn.addEventListener("click", function () {
        setState({ day: iso });
      });
      els.dayStrip.appendChild(btn);
    });
  }

  function updateDayStripSelection() {
    var chips = els.dayStrip.querySelectorAll(".day-chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].setAttribute("aria-pressed", String(chips[i].dataset.day === state.day));
    }
  }

  function renderSedeFilter() {
    els.sedeFilter.innerHTML = "";
    var items = [{ code: "000", name: "Todas" }].concat(
      SEDE_ORDER.filter(function (c) {
        return DATA.sedes[c];
      }).map(function (c) {
        return { code: c, name: DATA.sedes[c].name };
      })
    );
    items.forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "sede-chip";
      btn.dataset.sede = item.code;
      btn.setAttribute("aria-pressed", String(item.code === state.sede));
      if (item.code !== "000") {
        var dot = document.createElement("span");
        dot.className = "sede-dot";
        dot.style.setProperty("--dot-color", "var(--sede-" + item.code + ")");
        btn.appendChild(dot);
      }
      btn.appendChild(document.createTextNode(item.name));
      btn.addEventListener("click", function () {
        setState({ sede: item.code });
      });
      els.sedeFilter.appendChild(btn);
    });
  }

  function updateSedeFilterSelection() {
    var chips = els.sedeFilter.querySelectorAll(".sede-chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].setAttribute("aria-pressed", String(chips[i].dataset.sede === state.sede));
    }
  }

  function renderViewToggle() {
    els.viewToggle.innerHTML = "";
    var items = [
      { code: "dia", label: "por día" },
      { code: "pelicula", label: "por película" },
    ];
    items.forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "view-chip";
      btn.dataset.view = item.code;
      btn.setAttribute("aria-pressed", String(item.code === state.view));
      btn.textContent = item.label;
      btn.addEventListener("click", function () {
        setState({ view: item.code });
      });
      els.viewToggle.appendChild(btn);
    });
  }

  function updateViewToggleSelection() {
    var chips = els.viewToggle.querySelectorAll(".view-chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].setAttribute("aria-pressed", String(chips[i].dataset.view === state.view));
    }
  }

  function renderCicloSelect(ciclos) {
    // Hidden when nothing carries a ciclo. Cineteca stopped publishing ciclo
    // names in September 2026 and no endpoint has exposed one since, so the
    // select would otherwise sit in the header offering a single choice that
    // filters nothing. The filter itself is left intact rather than removed:
    // if ciclos ever come back in the data, this reappears on its own.
    els.cicloSelect.hidden = ciclos.length === 0;
    els.cicloSelect.innerHTML = '<option value="">todos los ciclos</option>';
    ciclos.forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      if (c === state.ciclo) opt.selected = true;
      els.cicloSelect.appendChild(opt);
    });
  }

  // The actual calendar date, not tied to state.day: this is a header
  // orientation cue, not a filter echo, so it never changes as you browse
  // other days.
  function renderDateKicker() {
    var parts = new Intl.DateTimeFormat("es-MX", {
      weekday: "long",
      day: "numeric",
      month: "long",
      timeZone: TZ,
    }).formatToParts(new Date());
    var weekday, day, month;
    parts.forEach(function (p) {
      if (p.type === "weekday") weekday = p.value;
      if (p.type === "day") day = p.value;
      if (p.type === "month") month = p.value;
    });
    els.dateKicker.textContent = (weekday + " " + day + " de " + month).toLowerCase();
  }

  function renderStaleBanner() {
    els.staleBanner.hidden = hoursSince(DATA.generated_at) <= STALE_HOURS;
  }

  function bindFilterEvents() {
    els.searchInput.addEventListener("input", function () {
      var val = els.searchInput.value;
      els.searchClear.hidden = !val;
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(function () {
        setState({ q: val });
      }, 200);
    });

    els.searchClear.addEventListener("click", function () {
      els.searchInput.value = "";
      els.searchClear.hidden = true;
      els.searchInput.focus();
      setState({ q: "" });
    });

    els.cicloSelect.addEventListener("change", function () {
      setState({ ciclo: els.cicloSelect.value });
    });

    els.clearFiltersBtn.addEventListener("click", function () {
      els.searchInput.value = "";
      els.searchClear.hidden = true;
      state.q = "";
      state.ciclo = "";
      els.cicloSelect.value = "";
      setState({});
    });
  }

  function setState(patch) {
    Object.assign(state, patch);
    syncUrl();
    updateDayStripSelection();
    updateSedeFilterSelection();
    updateViewToggleSelection();
    els.dayStrip.hidden = state.view === "pelicula";
    if (state.view === "pelicula") renderFilmIndex();
    else renderAgenda();
  }

  function urlFor(s) {
    var params = new URLSearchParams();
    if (s.view === "pelicula") params.set("v", "peli");
    if (s.day && s.day !== defaultDay) params.set("dia", s.day);
    if (s.sede !== "000") params.set("sede", s.sede);
    if (s.ciclo) params.set("ciclo", s.ciclo);
    if (s.q.trim()) params.set("q", s.q.trim());
    if (s.film) params.set("film", s.film);
    var qsStr = params.toString();
    return location.pathname + (qsStr ? "?" + qsStr : "");
  }

  function syncUrl() {
    history.replaceState(null, "", urlFor(state));
  }

  // ---------- agenda ----------

  function matchesQuery(film, normQuery) {
    if (!normQuery) return true;
    var haystacks = [film.title, film.original_title, film.director, film.country];
    return haystacks.some(function (h) {
      return h && normalize(h).indexOf(normQuery) !== -1;
    });
  }

  function buildAgendaData() {
    var normQuery = normalize(state.q.trim());
    var bySede = {};
    SEDE_ORDER.forEach(function (c) {
      bySede[c] = [];
    });

    DATA.films.forEach(function (film) {
      if (state.ciclo && film.ciclo !== state.ciclo) return;
      if (normQuery && !matchesQuery(film, normQuery)) return;
      film.showtimes.forEach(function (st) {
        if (st.date !== state.day) return;
        if (!bySede[st.sede]) return;
        bySede[st.sede].push({ film: film, st: st });
      });
    });

    Object.keys(bySede).forEach(function (code) {
      bySede[code].sort(function (a, b) {
        var am = timeToMinutes(a.st.time);
        var bm = timeToMinutes(b.st.time);
        return am - bm;
      });
    });

    return bySede;
  }

  // Where "boletos" goes for one screening: the checkout page for that exact
  // session, read straight off the ticket anchor the scraper parsed it from.
  //
  // The prefix checks are deliberate belt-and-braces on the one link that ends
  // at a payment page. The scraper never propagates a scraped href — it rebuilds
  // every checkout URL from a hardcoded base with digit-only parameters — so a
  // hostile URL can't reach here through the normal path. This is the last point
  // before a visitor is handed to a checkout, and refusing anything that isn't
  // on a known Cineteca origin is cheaper than trusting the whole chain above
  // it. It also covers an older cached schedule.json with no buy_url in it.
  function isOn(value, prefix) {
    return typeof value === "string" && value.lastIndexOf(prefix, 0) === 0;
  }

  function ticketHref(film, st) {
    if (isOn(st.buy_url, TICKET_URL_PREFIX)) return st.buy_url;
    if (isOn(film.official_url, CINETECA_PREFIX)) return film.official_url;
    return CINETECA_PREFIX;
  }

  // Every checkout link goes through the redirect warning first. Wiring it here
  // rather than at each call site means a new link can't quietly skip it.
  function bindTicketLink(el, film, st) {
    el.href = ticketHref(film, st);
    el.target = "_blank";
    el.rel = "noopener noreferrer";
    el.addEventListener("click", function (ev) {
      // Let ctrl/cmd/middle-click through: the visitor is deliberately opening
      // it themselves, and swallowing that would be surprising.
      if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey ||
          ev.shiftKey || ev.altKey) {
        return;
      }
      ev.preventDefault();
      openBuyModal(el.href, el);
    });
  }

  function timeToMinutes(t) {
    var parts = t.split(":");
    return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
  }

  function showGlobalEmpty(filtersActive, title) {
    els.globalEmpty.hidden = false;
    els.globalEmpty.querySelector(".empty-state-title").textContent = title;
    els.clearFiltersBtn.hidden = !filtersActive;
    announce(title);
  }

  // Why a column has nothing to list matters: an unpublished day and a sede
  // that is genuinely dark look identical in the data and read very
  // differently to someone deciding whether to go. Saying so takes three
  // words — regulars already know the week goes up on Wednesday or Thursday,
  // so the copy doesn't explain the schedule, it just stops claiming there
  // are no screenings.
  function sedeEmptyText(unpublished, hadRows, isToday) {
    if (unpublished) return "sin información";
    if (hadRows && isToday) return "ya no hay funciones hoy";
    return "sin funciones este día";
  }

  function renderAgenda() {
    var bySede = buildAgendaData();
    var sedeCodes = state.sede === "000" ? SEDE_ORDER : [state.sede];
    var isToday = state.day === todayISO();
    var nowMin = isToday ? nowMinutesCDMX() : -1;
    var unpublished = isUnpublishedDay(state.day);

    // Split before anything is drawn: the sede counter and the live-region
    // summary both report what a column actually lists, not what the day held
    // before its first screening started.
    var split = {};
    var total = 0;
    var listedTotal = 0;
    sedeCodes.forEach(function (code) {
      var upcoming = [];
      var past = [];
      bySede[code].forEach(function (entry) {
        if (isToday && timeToMinutes(entry.st.time) < nowMin) past.push(entry);
        else upcoming.push(entry);
      });
      split[code] = { upcoming: upcoming, past: past };
      total += bySede[code].length;
      listedTotal += upcoming.length;
    });

    els.agenda.className = state.sede === "000" ? "mode-all" : "mode-single";

    var filtersActive = Boolean(state.q.trim() || state.ciclo);

    if (total === 0) {
      els.agenda.innerHTML = "";
      els.agenda.hidden = true;
      showGlobalEmpty(
        filtersActive,
        filtersActive
          ? "ningún resultado para tu búsqueda"
          : unpublished
          ? "sin información"
          : "sin funciones este día"
      );
      return;
    }

    els.agenda.hidden = false;
    els.globalEmpty.hidden = true;
    els.agenda.innerHTML = "";

    var dayText = dayLabel(state.day);
    var whenText = dayText === "HOY" ? "hoy" : "el " + dayText.toLowerCase();
    announce(
      listedTotal === 0
        ? "ya no hay funciones " + whenText
        : listedTotal + (listedTotal === 1 ? " función " : " funciones ") + whenText
    );

    sedeCodes.forEach(function (code) {
      var sedeInfo = DATA.sedes[code];
      if (!sedeInfo) return;
      var rows = bySede[code];
      var upcoming = split[code].upcoming;
      var past = split[code].past;

      var column = document.createElement("section");
      column.className = "sede-column";
      column.dataset.sede = code;
      column.setAttribute("aria-label", sedeInfo.name);

      var header = document.createElement("div");
      header.className = "sede-header";
      header.innerHTML =
        '<div class="sede-header-left">' +
        '<span class="medallion sm" aria-hidden="true" style="--line-color: var(--sede-' +
        code +
        ')">' +
        escapeHtml(sedeInfo.name.charAt(0)) +
        "</span>" +
        '<span class="kicker" style="--ink-4: var(--sede-' +
        code +
        ')">' +
        escapeHtml(sedeInfo.name.toUpperCase()) +
        "</span>" +
        "</div>" +
        // No "0 funciones": the line underneath already says what is missing,
        // and a zero next to "sin información" claims a fact we don't have.
        (upcoming.length
          ? '<span class="sede-count">' +
            upcoming.length +
            " " +
            (upcoming.length === 1 ? "función" : "funciones") +
            "</span>"
          : "");
      column.appendChild(header);

      var rule = document.createElement("div");
      rule.className = "sede-rule";
      column.appendChild(rule);

      if (upcoming.length === 0) {
        var empty = document.createElement("p");
        empty.className = "sede-empty";
        empty.textContent = sedeEmptyText(unpublished, rows.length > 0, isToday);
        column.appendChild(empty);
      } else {
        var list = document.createElement("div");
        list.className = "show-list";
        upcoming.forEach(function (entry) {
          list.appendChild(buildShowRow(entry.film, entry.st, false));
        });
        column.appendChild(list);
      }

      if (past.length > 0) {
        var details = document.createElement("details");
        details.className = "past-details";
        var summaryEl = document.createElement("summary");
        summaryEl.textContent = "ya pasaron (" + past.length + ")";
        details.appendChild(summaryEl);
        var pastList = document.createElement("div");
        pastList.className = "show-list";
        past.forEach(function (entry) {
          pastList.appendChild(buildShowRow(entry.film, entry.st, true));
        });
        details.appendChild(pastList);
        column.appendChild(details);
      }

      els.agenda.appendChild(column);
    });
  }

  function buildShowRow(film, st, isPast) {
    var row = document.createElement("article");
    row.className = "show-row";
    row.dataset.past = String(isPast);

    var link = document.createElement("button");
    link.type = "button";
    link.className = "show-row-link";
    link.setAttribute("aria-haspopup", "dialog");
    if (isPast) link.disabled = true;
    link.addEventListener("click", function () {
      openFilmSheet(film, true);
    });

    var posterWrap = document.createElement("div");
    posterWrap.className = "poster-wrap";
    var img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.src = film.poster;
    img.addEventListener(
      "error",
      function () {
        posterWrap.innerHTML = '<div class="poster-placeholder">' + escapeHtml(film.title) + "</div>";
      },
      { once: true }
    );
    posterWrap.appendChild(img);
    link.appendChild(posterWrap);

    var info = document.createElement("div");
    info.className = "show-row-info";

    var time = document.createElement("time");
    time.className = "show-time";
    time.dateTime = st.datetime;
    time.textContent = st.time;
    info.appendChild(time);

    var title = document.createElement("div");
    title.className = "show-title";
    title.textContent = film.title;
    info.appendChild(title);

    var metaParts = [film.director, film.country].filter(Boolean);
    if (metaParts.length) {
      var meta = document.createElement("div");
      meta.className = "show-meta";
      meta.textContent = metaParts.join(" · ");
      info.appendChild(meta);
    }

    var subParts = [];
    if (film.duration_min) subParts.push(film.duration_min + "'");
    if (film.classification) subParts.push(film.classification);
    if (subParts.length) {
      var sub = document.createElement("div");
      sub.className = "show-sub";
      sub.textContent = subParts.join(" · ");
      info.appendChild(sub);
    }

    var badge = buildLetterboxdBadge(film, false);
    if (badge) info.appendChild(badge);

    link.appendChild(info);
    row.appendChild(link);

    var actions = document.createElement("div");
    actions.className = "show-row-actions";

    var buyBtn = document.createElement("a");
    buyBtn.className = "pill-btn buy-btn";
    bindTicketLink(buyBtn, film, st);
    buyBtn.textContent = "boletos";
    if (isPast) buyBtn.tabIndex = -1;
    actions.appendChild(buyBtn);
    row.appendChild(actions);

    return row;
  }

  // ---------- film sheet + film index helpers ----------

  function findFilm(id) {
    for (var i = 0; i < DATA.films.length; i++) {
      if (DATA.films[i].id === id) return DATA.films[i];
    }
    return null;
  }

  function futureShowtimes(film) {
    var today = todayISO();
    var nowMin = nowMinutesCDMX();
    return film.showtimes
      .filter(function (st) {
        if (st.date > today) return true;
        if (st.date === today) return timeToMinutes(st.time) >= nowMin;
        return false;
      })
      .sort(function (a, b) {
        if (a.date !== b.date) return a.date < b.date ? -1 : 1;
        return timeToMinutes(a.time) - timeToMinutes(b.time);
      });
  }

  function groupByDate(sts) {
    var byDate = {};
    sts.forEach(function (st) {
      if (!byDate[st.date]) byDate[st.date] = [];
      byDate[st.date].push(st);
    });
    return DATA.days
      .filter(function (d) {
        return byDate[d];
      })
      .map(function (d) {
        return { date: d, times: byDate[d] };
      });
  }

  // Stars are drawn, not typed. "★" resolves to whatever symbol font the OS
  // falls back to: its ink sits about a pixel above the baseline while digits
  // sit on it, so the score always looked like it was sagging next to them and
  // no amount of flex alignment could fix it — the offset is inside the glyph.
  // Some platforms also render it as a color emoji. A path has none of that:
  // the box is the ink, so centering it is exact everywhere.
  var STAR_R = 10;
  var STAR_STEP = 23; // 2R + gap
  var SVG_NS = "http://www.w3.org/2000/svg";

  function starPoints(cx, cy, outer) {
    var inner = outer * 0.382; // golden ratio — the classic five-point star
    var pts = [];
    for (var i = 0; i < 10; i++) {
      var rad = i % 2 ? inner : outer;
      var a = -Math.PI / 2 + (i * Math.PI) / 5;
      pts.push([cx + rad * Math.cos(a), cy + rad * Math.sin(a)]);
    }
    return pts;
  }

  // The viewBox is the path's own bounding box, not the circle it was drawn
  // from. A five-point star only reaches 0.809R below centre and 0.951R to the
  // side, so a square box leaves dead space along the bottom — and dead space
  // in the box is exactly what made the text stars impossible to align. Hug
  // the ink and `align-self: center` becomes exact.
  var STAR_ROW = (function () {
    var d = "";
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (var i = 0; i < 5; i++) {
      var pts = starPoints(STAR_R + i * STAR_STEP, STAR_R, STAR_R);
      d += "M" + pts.map(function (p) {
        minX = Math.min(minX, p[0]); maxX = Math.max(maxX, p[0]);
        minY = Math.min(minY, p[1]); maxY = Math.max(maxY, p[1]);
        return p[0].toFixed(2) + "," + p[1].toFixed(2);
      }).join("L") + "Z";
    }
    return { d: d, x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  })();

  function buildStars(rating) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "lb-stars");
    svg.setAttribute(
      "viewBox",
      STAR_ROW.x.toFixed(2) + " " + STAR_ROW.y.toFixed(2) + " " +
        STAR_ROW.w.toFixed(2) + " " + STAR_ROW.h.toFixed(2)
    );
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");

    var empty = document.createElementNS(SVG_NS, "path");
    empty.setAttribute("d", STAR_ROW.d);
    empty.setAttribute("class", "lb-star-empty");
    svg.appendChild(empty);

    // One clipped copy over the top: exact fractional fill, no half-star glyph.
    var clipId = "lbclip" + (buildStars.n = (buildStars.n || 0) + 1);
    var clip = document.createElementNS(SVG_NS, "clipPath");
    clip.setAttribute("id", clipId);
    var rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", STAR_ROW.x.toFixed(2));
    rect.setAttribute("y", STAR_ROW.y.toFixed(2));
    rect.setAttribute("height", STAR_ROW.h.toFixed(2));
    rect.setAttribute(
      "width",
      (Math.max(0, Math.min(1, rating / 5)) * STAR_ROW.w).toFixed(2)
    );
    clip.appendChild(rect);
    svg.appendChild(clip);

    var full = document.createElementNS(SVG_NS, "path");
    full.setAttribute("d", STAR_ROW.d);
    full.setAttribute("class", "lb-star-full");
    full.setAttribute("clip-path", "url(#" + clipId + ")");
    svg.appendChild(full);

    return svg;
  }

  // The single gate for the whole feature: no match, or a match Letterboxd
  // hasn't scored yet, means no badge anywhere. The scraper only ever writes
  // `letterboxd` when it is confident, so there is nothing to second-guess here.
  //
  // `asLink` must stay false in the day rows and the film cards: the row wraps
  // its info in an <a> and the card root is a <button>, and an anchor nested in
  // either is invalid and swallows the parent's click target. Only the sheet,
  // where the badge stands free, links out.
  function buildLetterboxdBadge(film, asLink) {
    var lb = film.letterboxd;
    if (!lb || typeof lb.rating !== "number") return null;

    var linked = !!(asLink && lb.url);

    // Ratings hidden. The gate above still stands, which makes the toggle a
    // pure subtraction: exactly the same films carry a Letterboxd presence,
    // they just stop carrying the number. Where there is somewhere to send
    // the reader - the sheet, the only surface that can hold an anchor - the
    // lockup becomes an invitation to go and read the score at the source,
    // which is the point of turning it off in the first place. In the rows
    // and the cards there is no link to keep and a bare lockup would be a
    // logo saying nothing, so nothing is drawn at all.
    if (!ratingsVisible) return linked ? buildLetterboxdLink(lb.url) : null;

    var badge = document.createElement(linked ? "a" : "span");
    badge.className = "lb-badge";
    if (linked) {
      badge.href = lb.url;
      badge.target = "_blank";
      badge.rel = "noopener noreferrer";
    }

    var score = lb.rating.toLocaleString("es-MX", {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
    badge.setAttribute("aria-label", "calificación en letterboxd: " + score + " de 5");

    badge.appendChild(buildLetterboxdMark());

    badge.appendChild(buildStars(lb.rating));

    var value = document.createElement("span");
    value.className = "lb-score";
    value.textContent = score;
    badge.appendChild(value);

    // The sheet's badge is the only one that navigates, so it borrows the
    // outbound arrow "ver tráiler ↗" already uses — without it the link looks
    // exactly like the inert badge in the rows and nobody would try it.
    if (linked) {
      var arrow = document.createElement("span");
      arrow.className = "lb-out";
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = "↗";
      badge.appendChild(arrow);
    }

    return badge;
  }

  // The lockup is a raster asset with its wordmark baked in one ink, so unlike
  // every other mark on the page it cannot follow --ink-* into the dark
  // palette: the wordmark is black, and on #111315 it would simply be gone.
  // letterboxd-dark.png is the same file with that wordmark recoloured white
  // at the dark ramp's matching alpha and the three brand dots left exactly
  // as they are — inverting the whole image would have flipped the dots too,
  // and their color is the entire reason this is an image rather than text.
  //
  // Read at build time rather than switched in CSS because `content: url()`
  // on a real <img> is not supported in Firefox. theme.js fires
  // cartelera:themechange and bindThemeRedraw() below redraws, which is the
  // same redraw the ratings toggle already does.
  function buildLetterboxdMark() {
    var mark = document.createElement("img");
    mark.className = "lb-mark";
    mark.src = document.documentElement.getAttribute("data-theme") === "dark"
      ? "letterboxd-dark.png"
      : "letterboxd.png";
    mark.alt = "";
    mark.loading = "lazy";
    mark.decoding = "async";
    return mark;
  }

  // The ratings-off form of the badge: the lockup and the outbound arrow, and
  // nothing else. It says a rating exists and that reading it means leaving
  // this site, which is exactly what the toggle is for. There is no visible
  // label - "ver calificación" beside a logo that already says letterboxd was
  // the same sentence twice, and it made the quiet form of the badge wider
  // than the loud one. The aria-label carries the words for screen readers,
  // which is the one place the lockup genuinely says nothing.
  function buildLetterboxdLink(url) {
    var badge = document.createElement("a");
    badge.className = "lb-badge";
    badge.href = url;
    badge.target = "_blank";
    badge.rel = "noopener noreferrer";
    badge.setAttribute("aria-label", "ver la calificación en letterboxd");

    badge.appendChild(buildLetterboxdMark());

    var arrow = document.createElement("span");
    arrow.className = "lb-out";
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "↗";
    badge.appendChild(arrow);

    return badge;
  }

  function buildFilmSheetBody(film) {
    var frag = document.createDocumentFragment();
    var futures = futureShowtimes(film);

    var head = document.createElement("div");
    head.className = "sheet-head";

    var posterWrap = document.createElement("div");
    posterWrap.className = "poster-wrap";
    var img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.src = film.poster;
    img.addEventListener(
      "error",
      function () {
        posterWrap.innerHTML = '<div class="poster-placeholder">' + escapeHtml(film.title) + "</div>";
      },
      { once: true }
    );
    posterWrap.appendChild(img);
    head.appendChild(posterWrap);

    var info = document.createElement("div");
    info.className = "sheet-head-info";

    var h2 = document.createElement("h2");
    h2.className = "sheet-title";
    h2.id = "film-sheet-title";
    h2.textContent = film.title;
    info.appendChild(h2);

    if (film.original_title && film.original_title !== film.title) {
      var orig = document.createElement("div");
      orig.className = "sheet-original";
      orig.textContent = film.original_title;
      info.appendChild(orig);
    }

    var metaParts = [film.director, film.country, film.year].filter(Boolean);
    if (metaParts.length) {
      var meta = document.createElement("div");
      meta.className = "show-meta";
      meta.textContent = metaParts.join(" · ");
      info.appendChild(meta);
    }

    var subParts = [];
    if (film.duration_min) subParts.push(film.duration_min + "'");
    if (film.classification) subParts.push(film.classification);
    if (subParts.length) {
      var sub = document.createElement("div");
      sub.className = "show-sub";
      sub.textContent = subParts.join(" · ");
      info.appendChild(sub);
    }

    var sheetBadge = buildLetterboxdBadge(film, true);
    if (sheetBadge) info.appendChild(sheetBadge);

    if (film.ciclo) {
      var ciclo = document.createElement("span");
      ciclo.className = "kicker";
      ciclo.textContent = film.ciclo;
      info.appendChild(ciclo);
    }

    head.appendChild(info);
    frag.appendChild(head);

    if (film.synopsis) {
      var syn = document.createElement("p");
      syn.className = "sheet-synopsis";
      syn.textContent = film.synopsis;
      frag.appendChild(syn);
    }

    if (film.cast) {
      var cast = document.createElement("p");
      cast.className = "sheet-cast";
      cast.textContent = "con " + film.cast;
      frag.appendChild(cast);
    }

    var links = document.createElement("div");
    links.className = "sheet-links";
    if (film.trailer_youtube_id) {
      var trailer = document.createElement("a");
      trailer.className = "sheet-link";
      trailer.href = "https://www.youtube.com/watch?v=" + film.trailer_youtube_id;
      trailer.target = "_blank";
      trailer.rel = "noopener noreferrer";
      trailer.textContent = "ver tráiler ↗";
      links.appendChild(trailer);
    }
    if (links.children.length) frag.appendChild(links);

    var hr = document.createElement("div");
    hr.className = "hairline";
    hr.setAttribute("aria-hidden", "true");
    frag.appendChild(hr);

    if (futures.length === 0) {
      var empty = document.createElement("p");
      empty.className = "sheet-empty";
      empty.textContent = "sin funciones próximas esta semana";
      frag.appendChild(empty);
      return frag;
    }

    var distinctSedes = [];
    futures.forEach(function (st) {
      if (distinctSedes.indexOf(st.sede) === -1) distinctSedes.push(st.sede);
    });
    var multiSede = distinctSedes.length > 1;

    var timesHead = document.createElement("div");
    timesHead.className = "sheet-times-head";

    var timesKicker = document.createElement("span");
    timesKicker.className = "kicker";
    timesKicker.textContent = "PRÓXIMAS FUNCIONES";
    timesHead.appendChild(timesKicker);

    var count = document.createElement("span");
    count.className = "sheet-count";
    count.textContent = futures.length + (futures.length === 1 ? " función" : " funciones");
    timesHead.appendChild(count);

    if (!multiSede && DATA.sedes[distinctSedes[0]]) {
      var sedeLabel = document.createElement("span");
      sedeLabel.className = "kicker";
      sedeLabel.style.setProperty("--ink-4", "var(--sede-" + distinctSedes[0] + ")");
      sedeLabel.textContent = DATA.sedes[distinctSedes[0]].name;
      timesHead.appendChild(sedeLabel);
    }

    frag.appendChild(timesHead);

    var daysWrap = document.createElement("div");
    daysWrap.className = "sheet-days";
    groupByDate(futures).forEach(function (group) {
      var row = document.createElement("div");
      row.className = "sheet-day-row";

      var label = document.createElement("div");
      label.className = "sheet-day-label";
      label.textContent = dayLabel(group.date);
      row.appendChild(label);

      var chips = document.createElement("div");
      chips.className = "sheet-day-chips";
      group.times.forEach(function (st) {
        var chip = document.createElement("a");
        chip.className = "time-chip";
        bindTicketLink(chip, film, st);

        var dot = document.createElement("span");
        dot.className = "sede-dot";
        dot.style.setProperty("--dot-color", "var(--sede-" + st.sede + ")");
        chip.appendChild(dot);

        var time = document.createElement("time");
        time.className = "time-chip-time";
        time.dateTime = st.datetime;
        time.textContent = st.time;
        chip.appendChild(time);

        if (multiSede && DATA.sedes[st.sede]) {
          var sedeSpan = document.createElement("span");
          sedeSpan.className = "time-chip-sede";
          sedeSpan.textContent = DATA.sedes[st.sede].name;
          chip.appendChild(sedeSpan);
        }

        chips.appendChild(chip);
      });
      row.appendChild(chips);
      daysWrap.appendChild(row);
    });
    frag.appendChild(daysWrap);

    return frag;
  }

  function openFilmSheet(film, push) {
    var sheet = els.filmSheet;
    if (!sheet) return;
    sheetTriggerEl = document.activeElement;
    state.film = film.id;
    els.sheetBody.innerHTML = "";
    els.sheetBody.appendChild(buildFilmSheetBody(film));
    sheet.hidden = false;
    document.documentElement.classList.add("sheet-open");
    if (els.appRoot) els.appRoot.inert = true;
    requestAnimationFrame(function () {
      sheet.classList.add("visible");
    });
    if (push) {
      history.pushState({ sheet: film.id }, "", urlFor(state));
      sheetPushedState = true;
    } else {
      syncUrl();
    }
    if (els.sheetClose) els.sheetClose.focus();
  }

  function closeFilmSheet(fromPop) {
    var sheet = els.filmSheet;
    if (!sheet || sheet.hidden) return;
    if (sheetPushedState && !fromPop) {
      sheetPushedState = false;
      history.back();
      return;
    }
    sheetPushedState = false;
    sheet.classList.remove("visible");
    document.documentElement.classList.remove("sheet-open");
    if (els.appRoot) els.appRoot.inert = false;
    state.film = "";
    setTimeout(function () {
      sheet.hidden = true;
    }, 240);
    syncUrl();
    if (sheetTriggerEl && typeof sheetTriggerEl.focus === "function") {
      sheetTriggerEl.focus();
    }
    sheetTriggerEl = null;
  }

  function bindFilmSheetEvents() {
    var sheet = els.filmSheet;
    if (!sheet) return;

    if (els.sheetClose) {
      els.sheetClose.addEventListener("click", function () {
        closeFilmSheet(false);
      });
    }
    sheet.addEventListener("click", function (ev) {
      if (sheet.hidden) return;
      if (els.sheetContent && els.sheetContent.contains(ev.target)) return;
      closeFilmSheet(false);
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape" || sheet.hidden) return;
      // The buy warning can sit on top of the sheet. Its Escape handler is
      // bound in init(), before this one, and marks the event handled when it
      // closes - so one Escape peels off one layer instead of both.
      if (ev.defaultPrevented) return;
      closeFilmSheet(false);
    });
    window.addEventListener("popstate", function () {
      if (!sheet.hidden) closeFilmSheet(true);
    });
  }

  function buildFilmCard(film) {
    var card = document.createElement("button");
    card.type = "button";
    card.className = "film-card";
    card.setAttribute("aria-haspopup", "dialog");

    var posterWrap = document.createElement("div");
    posterWrap.className = "poster-wrap";
    var img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.src = film.poster;
    img.addEventListener(
      "error",
      function () {
        posterWrap.innerHTML = '<div class="poster-placeholder">' + escapeHtml(film.title) + "</div>";
      },
      { once: true }
    );
    posterWrap.appendChild(img);
    card.appendChild(posterWrap);

    var info = document.createElement("div");
    info.className = "film-card-info";

    var title = document.createElement("div");
    title.className = "show-title";
    title.textContent = film.title;
    info.appendChild(title);

    var metaParts = [film.director, film.country].filter(Boolean);
    if (metaParts.length) {
      var meta = document.createElement("div");
      meta.className = "show-meta";
      meta.textContent = metaParts.join(" · ");
      info.appendChild(meta);
    }

    // Duration and clasificación, the same line the day rows and the sheet
    // carry, so the badge below it reads the same way everywhere. This card
    // used to list the run count, date range and sede dots instead; all of
    // that is a tap away in the sheet, and the sede chips above already filter
    // the view, so it was costing four lines to say what the sheet says better.
    var subParts = [];
    if (film.duration_min) subParts.push(film.duration_min + "'");
    if (film.classification) subParts.push(film.classification);
    if (subParts.length) {
      var sub = document.createElement("div");
      sub.className = "show-sub";
      sub.textContent = subParts.join(" · ");
      info.appendChild(sub);
    }

    var badge = buildLetterboxdBadge(film, false);
    if (badge) info.appendChild(badge);

    card.appendChild(info);
    card.addEventListener("click", function () {
      openFilmSheet(film, true);
    });

    return card;
  }

  function renderFilmIndex() {
    els.agenda.className = "mode-films";

    var normQuery = normalize(state.q.trim());
    var filtered = [];
    DATA.films.forEach(function (film) {
      if (state.ciclo && film.ciclo !== state.ciclo) return;
      if (normQuery && !matchesQuery(film, normQuery)) return;
      var futures = futureShowtimes(film);
      if (state.sede !== "000") {
        futures = futures.filter(function (st) {
          return st.sede === state.sede;
        });
      }
      if (!futures.length) return;
      filtered.push(film);
    });

    filtered.sort(function (a, b) {
      return a.title.localeCompare(b.title, "es");
    });

    var filtersActive = Boolean(state.q.trim() || state.ciclo || state.sede !== "000");

    if (filtered.length === 0) {
      els.agenda.innerHTML = "";
      els.agenda.hidden = true;
      showGlobalEmpty(filtersActive, filtersActive ? "ningún resultado para tu búsqueda" : "sin funciones esta semana");
      return;
    }

    els.agenda.hidden = false;
    els.globalEmpty.hidden = true;
    els.agenda.innerHTML = "";

    var summary =
      filtered.length + (filtered.length === 1 ? " película" : " películas") + " esta semana";
    announce(summary);

    var head = document.createElement("div");
    head.className = "film-index-head";
    var kicker = document.createElement("span");
    kicker.className = "kicker";
    kicker.textContent = summary;
    head.appendChild(kicker);
    els.agenda.appendChild(head);

    filtered.forEach(function (film) {
      els.agenda.appendChild(buildFilmCard(film));
    });
  }

  // ---------- about modal (opens only from the header info button) ----------
  // croquis's about-modal.js gated a first-visit auto-show on localStorage.
  // That was deliberately not ported: visitors here want the agenda straight
  // away, not an intro screen. Don't reintroduce an auto-show on load.

  function bindAboutModalTrigger() {
    var modal = qs("about-modal");
    if (!modal || !els.infoBtn) return;
    var content = modal.querySelector(".about-content");
    var startBtn = modal.querySelector(".about-start-btn");
    var appRoot = qs("app");

    // Focus has to be moved in by hand: the info button that opened this sits
    // inside #app, which goes inert, so without it focus drops to <body> and a
    // keyboard or screen-reader visitor is left outside the dialog. The panel
    // itself takes it (tabindex=-1 in index.html) rather than the first
    // control, so a screen reader starts from the top and nothing gets a ring.
    function openModal() {
      modal.hidden = false;
      if (appRoot) appRoot.inert = true;
      if (content) content.focus();
      requestAnimationFrame(function () {
        modal.classList.add("visible");
      });
    }
    function closeModal() {
      if (modal.hidden) return;
      modal.classList.remove("visible");
      if (appRoot) appRoot.inert = false;
      setTimeout(function () {
        modal.hidden = true;
      }, 240);
      els.infoBtn.focus();
    }

    els.infoBtn.addEventListener("click", function () {
      if (modal.hidden) openModal();
    });
    if (startBtn) startBtn.addEventListener("click", closeModal);
    modal.addEventListener("click", function (ev) {
      if (modal.hidden) return;
      if (content && content.contains(ev.target)) return;
      closeModal();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !modal.hidden) closeModal();
    });
  }

  // role=switch + aria-checked, and not the aria-pressed the day/sede/view
  // chips use. That note stands for those: they are toggle buttons filtering
  // one list in place. This is a genuine on/off setting drawn as a switch, so
  // the role has to match the thing on screen.
  function bindRatingsToggle() {
    var btn = qs("ratings-toggle");
    if (!btn) return;
    btn.setAttribute("aria-checked", ratingsVisible ? "true" : "false");
    btn.addEventListener("click", function () {
      ratingsVisible = !ratingsVisible;
      btn.setAttribute("aria-checked", ratingsVisible ? "true" : "false");
      saveRatingsPref(ratingsVisible);
      // Redraws whichever view is up. An open film sheet needs no handling:
      // the info button that opens this modal lives inside #app, which an
      // open sheet marks inert, so the two can never be on screen together.
      if (DATA) setState({});
    });
  }

  // theme.js owns the palette: it sets data-theme, persists the choice and
  // writes the button's label, all of which CSS picks up on its own. The one
  // thing it cannot repaint is the Letterboxd lockup — see
  // buildLetterboxdMark(). So the same redraw the ratings toggle uses hangs
  // off its event. Redrawing the whole view for one <img> is cheap: it is
  // what every keystroke in the search field already does.
  //
  // An open film sheet needs no handling here for the same reason the ratings
  // toggle needs none — #theme-toggle lives in #app, which an open sheet marks
  // inert, so the two can never be reachable at once.
  function bindThemeRedraw() {
    document.addEventListener("cartelera:themechange", function () {
      if (DATA) setState({});
    });
  }

  // ---------- redirect warning ----------

  // Shown before every hand-off to Cineteca's checkout, every time — no
  // "don't show again". It names no film, sede or hour on purpose: repeating
  // our own reading of the screening would invite the visitor to check it
  // against itself, when the point is to make them read Cineteca's page.
  function openBuyModal(href, trigger) {
    var modal = els.buyModal;
    if (!modal) {
      window.open(href, "_blank", "noopener");
      return;
    }
    var proceed = modal.querySelector(".buy-continue");
    if (proceed) proceed.href = href;
    buyTriggerEl = trigger || null;

    modal.hidden = false;
    if (els.appRoot) els.appRoot.inert = true;
    // The sheet is a sibling of #app, so it needs muting separately or a time
    // chip behind this modal stays clickable.
    if (els.filmSheet && !els.filmSheet.hidden) els.filmSheet.inert = true;
    requestAnimationFrame(function () {
      modal.classList.add("visible");
      if (proceed) proceed.focus();
    });
  }

  function closeBuyModal() {
    var modal = els.buyModal;
    if (!modal || modal.hidden) return;
    modal.classList.remove("visible");
    // Raised from a time chip, the warning closes back onto the sheet, which
    // still owns the page: #app has to stay muted underneath it.
    var sheetOpen = els.filmSheet && !els.filmSheet.hidden;
    if (els.appRoot && !sheetOpen) els.appRoot.inert = false;
    if (els.filmSheet) els.filmSheet.inert = false;
    var trigger = buyTriggerEl;
    buyTriggerEl = null;
    setTimeout(function () {
      modal.hidden = true;
    }, 240);
    if (trigger && document.contains(trigger)) trigger.focus();
  }

  function bindBuyModal() {
    var modal = els.buyModal;
    if (!modal) return;
    var content = modal.querySelector(".buy-content");
    var proceed = modal.querySelector(".buy-continue");
    var cancel = modal.querySelector(".buy-cancel");

    // `proceed` stays a real anchor with a real href, so the new tab opens as a
    // plain user-activated navigation and no popup blocker gets involved.
    if (proceed) proceed.addEventListener("click", function () { closeBuyModal(); });
    if (cancel) cancel.addEventListener("click", closeBuyModal);
    modal.addEventListener("click", function (ev) {
      if (modal.hidden) return;
      if (content && content.contains(ev.target)) return;
      closeBuyModal();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape" || modal.hidden) return;
      ev.preventDefault(); // the film sheet's handler checks this
      closeBuyModal();
    });
  }

  // ---------- auto-hiding filter rows ----------

  // Scrolling down parks the filter rows behind the masthead (see
  // `#top-bar.is-collapsed` in style.css); scrolling up brings them back.
  // Nothing here touches `state` or the URL — this is view chrome, not a filter.
  function bindAutoHideTopBar() {
    var bar = els.topBar;
    var row = els.topbarRow;
    if (!bar || !row) return;

    var hideDistance = 0;
    var lastY = Math.max(0, window.scrollY);
    var ticking = false;

    // offsetHeight is transform-independent, so this reads the same whether the
    // bar is collapsed or not. The zero guard is showErrorState(), which hides
    // the bar outright ([hidden] is display:none).
    function measure() {
      if (!bar.offsetHeight) return;
      var cs = getComputedStyle(bar);
      var chrome =
        (parseFloat(cs.paddingTop) || 0) +
        (parseFloat(cs.paddingBottom) || 0) +
        (parseFloat(cs.borderBottomWidth) || 0);
      hideDistance = Math.max(0, bar.offsetHeight - chrome - row.offsetHeight);
      bar.style.setProperty("--topbar-hide", hideDistance + "px");
    }

    function update() {
      ticking = false;
      var y = Math.max(0, window.scrollY);
      var delta = y - lastY;
      lastY = y;

      // Near the top, or while a chip or the search field holds focus, the bar
      // stays whole — never pull a control out from under a keyboard user.
      if (y <= hideDistance || bar.contains(document.activeElement)) {
        bar.classList.remove("is-collapsed");
        return;
      }
      // The 4px dead zone rides out trackpad and rubber-band jitter; the 80px
      // floor keeps a short flick near the top from collapsing anything.
      if (delta > 4 && y > hideDistance + 80) bar.classList.add("is-collapsed");
      else if (delta < -4) bar.classList.remove("is-collapsed");
    }

    // Three things change the hide distance after load, and all three resize the
    // bar: the chips don't exist until onDataLoaded() fills them, setState()
    // hides #day-strip outright in "por película", and #filters-row wraps to a
    // second line on narrow phones.
    if (window.ResizeObserver) new ResizeObserver(measure).observe(bar);
    else window.addEventListener("resize", measure);
    measure();

    window.addEventListener(
      "scroll",
      function () {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(update);
      },
      { passive: true }
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
