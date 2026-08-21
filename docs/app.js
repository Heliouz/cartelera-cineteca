(function () {
  "use strict";

  var DATA_URL = "data/schedule.json";
  var SEDE_ORDER = ["003", "001", "002"];
  var STALE_HOURS = 36;
  var TZ = "America/Mexico_City";

  var DATA = null;
  var defaultDay = null;
  var hasAutoScrolled = false;
  var searchDebounceTimer = null;

  var state = { day: null, sede: "000", ciclo: "", q: "" };
  var els = {};

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

  function dayLabel(iso, index) {
    if (index === 0) return "HOY";
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
    els.updatedKicker = qs("updated-kicker");
    els.staleBanner = qs("stale-banner");
    els.errorState = qs("error-state");
    els.globalEmpty = qs("global-empty");
    els.clearFiltersBtn = qs("clear-filters-btn");
    els.infoBtn = qs("info-btn");
    els.agendaWrap = qs("agenda-wrap");

    bindAboutModalTrigger();

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

  function uniqueCiclos() {
    var set = new Set();
    DATA.films.forEach(function (f) {
      if (f.ciclo) set.add(f.ciclo);
    });
    return Array.from(set).sort(function (a, b) {
      return a.localeCompare(b, "es");
    });
  }

  function onDataLoaded() {
    var today = todayISO();
    defaultDay = DATA.days.indexOf(today) !== -1 ? today : DATA.days[0];

    var params = new URLSearchParams(location.search);
    var dia = params.get("dia");
    state.day = dia && DATA.days.indexOf(dia) !== -1 ? dia : defaultDay;

    var sede = params.get("sede");
    state.sede = sede && (sede === "000" || DATA.sedes[sede]) ? sede : "000";

    var ciclos = uniqueCiclos();
    var ciclo = params.get("ciclo");
    state.ciclo = ciclo && ciclos.indexOf(ciclo) !== -1 ? ciclo : "";

    state.q = params.get("q") || "";
    els.searchInput.value = state.q;
    els.searchClear.hidden = !state.q;

    renderDayStrip();
    renderSedeFilter();
    renderCicloSelect(ciclos);
    renderUpdatedKicker();
    renderStaleBanner();
    renderAgenda();
    syncUrl();
    bindFilterEvents();
  }

  // ---------- header controls ----------

  function renderDayStrip() {
    els.dayStrip.innerHTML = "";
    DATA.days.forEach(function (iso, i) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "day-chip";
      btn.setAttribute("role", "tab");
      btn.dataset.day = iso;
      btn.setAttribute("aria-selected", String(iso === state.day));
      btn.textContent = dayLabel(iso, i);
      btn.addEventListener("click", function () {
        setState({ day: iso });
      });
      els.dayStrip.appendChild(btn);
    });
  }

  function updateDayStripSelection() {
    var chips = els.dayStrip.querySelectorAll(".day-chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].setAttribute("aria-selected", String(chips[i].dataset.day === state.day));
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
      btn.setAttribute("role", "tab");
      btn.dataset.sede = item.code;
      btn.setAttribute("aria-selected", String(item.code === state.sede));
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
      chips[i].setAttribute("aria-selected", String(chips[i].dataset.sede === state.sede));
    }
  }

  function renderCicloSelect(ciclos) {
    els.cicloSelect.innerHTML = '<option value="">todos los ciclos</option>';
    ciclos.forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      if (c === state.ciclo) opt.selected = true;
      els.cicloSelect.appendChild(opt);
    });
  }

  function renderUpdatedKicker() {
    var hours = hoursSince(DATA.generated_at);
    var label;
    if (hours < 1) label = "actualizado hace unos minutos";
    else if (hours === 1) label = "actualizado hace 1 h";
    else label = "actualizado hace " + hours + " h";
    els.updatedKicker.textContent = label;
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
    renderAgenda();
  }

  function syncUrl() {
    var params = new URLSearchParams();
    if (state.day && state.day !== defaultDay) params.set("dia", state.day);
    if (state.sede !== "000") params.set("sede", state.sede);
    if (state.ciclo) params.set("ciclo", state.ciclo);
    if (state.q.trim()) params.set("q", state.q.trim());
    var qsStr = params.toString();
    var url = location.pathname + (qsStr ? "?" + qsStr : "");
    history.replaceState(null, "", url);
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

  function timeToMinutes(t) {
    var parts = t.split(":");
    return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
  }

  function renderAgenda() {
    var bySede = buildAgendaData();
    var sedeCodes = state.sede === "000" ? SEDE_ORDER : [state.sede];
    var total = sedeCodes.reduce(function (sum, c) {
      return sum + bySede[c].length;
    }, 0);

    els.agenda.className = state.sede === "000" ? "mode-all" : "mode-single";

    var filtersActive = Boolean(state.q.trim() || state.ciclo);

    if (total === 0) {
      els.agenda.innerHTML = "";
      els.agenda.hidden = true;
      els.globalEmpty.hidden = false;
      els.globalEmpty.querySelector(".empty-state-title").textContent = filtersActive
        ? "ningún resultado para tu búsqueda"
        : "sin funciones este día";
      els.clearFiltersBtn.hidden = !filtersActive;
      return;
    }

    els.agenda.hidden = false;
    els.globalEmpty.hidden = true;
    els.agenda.innerHTML = "";

    var isToday = state.day === todayISO();
    var nowMin = isToday ? nowMinutesCDMX() : -1;

    sedeCodes.forEach(function (code) {
      var sedeInfo = DATA.sedes[code];
      if (!sedeInfo) return;
      var rows = bySede[code];

      var column = document.createElement("section");
      column.className = "sede-column";
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
        '<span class="sede-count">' +
        rows.length +
        " " +
        (rows.length === 1 ? "función" : "funciones") +
        "</span>";
      column.appendChild(header);

      var rule = document.createElement("div");
      rule.className = "sede-rule";
      column.appendChild(rule);

      if (rows.length === 0) {
        var empty = document.createElement("p");
        empty.className = "sede-empty";
        empty.textContent = "sin funciones este día";
        column.appendChild(empty);
      } else {
        var list = document.createElement("div");
        list.className = "show-list";
        var placedDivider = false;
        rows.forEach(function (entry) {
          var minutes = timeToMinutes(entry.st.time);
          var isPast = isToday && minutes < nowMin;
          if (isToday && !placedDivider && minutes >= nowMin) {
            var divider = document.createElement("div");
            divider.className = "now-divider";
            divider.textContent = "ahora";
            divider.dataset.nowDivider = "true";
            list.appendChild(divider);
            placedDivider = true;
          }
          list.appendChild(buildShowRow(entry.film, entry.st, isPast));
        });
        column.appendChild(list);
      }

      els.agenda.appendChild(column);
    });

    if (!hasAutoScrolled && isToday) {
      hasAutoScrolled = true;
      requestAnimationFrame(function () {
        var marker = els.agenda.querySelector("[data-now-divider]");
        if (marker) marker.scrollIntoView({ block: "center", behavior: "auto" });
      });
    }
  }

  function buildShowRow(film, st, isPast) {
    var row = document.createElement("article");
    row.className = "show-row";
    row.dataset.past = String(isPast);

    var link = document.createElement("a");
    link.className = "show-row-link";
    link.href = film.official_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    if (isPast) link.tabIndex = -1;

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

    link.appendChild(info);
    row.appendChild(link);

    var actions = document.createElement("div");
    actions.className = "show-row-actions";

    var buyBtn = document.createElement("a");
    buyBtn.className = "pill-btn buy-btn";
    buyBtn.href = st.buy_url;
    buyBtn.target = "_blank";
    buyBtn.rel = "noopener noreferrer";
    buyBtn.textContent = "boletos";
    if (isPast) buyBtn.tabIndex = -1;
    actions.appendChild(buyBtn);

    var hasDetail = Boolean(film.synopsis || film.cast || film.trailer_youtube_id);
    if (hasDetail) {
      var detailId = "detail-" + st.session_id;
      var chevron = document.createElement("button");
      chevron.type = "button";
      chevron.className = "chevron-btn";
      chevron.setAttribute("aria-expanded", "false");
      chevron.setAttribute("aria-controls", detailId);
      chevron.setAttribute("aria-label", "ver más detalles de " + film.title);
      chevron.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
        '<path d="m6 9 6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      actions.appendChild(chevron);
      row.appendChild(actions);

      var detail = document.createElement("div");
      detail.className = "show-detail";
      detail.id = detailId;
      detail.hidden = true;
      if (film.synopsis) {
        var syn = document.createElement("p");
        syn.textContent = film.synopsis;
        detail.appendChild(syn);
      }
      if (film.cast) {
        var cast = document.createElement("p");
        cast.className = "show-detail-cast";
        cast.textContent = "con " + film.cast;
        detail.appendChild(cast);
      }
      if (film.trailer_youtube_id) {
        var trailer = document.createElement("a");
        trailer.className = "show-detail-trailer";
        trailer.href = "https://www.youtube.com/watch?v=" + film.trailer_youtube_id;
        trailer.target = "_blank";
        trailer.rel = "noopener noreferrer";
        trailer.textContent = "ver tráiler ↗";
        detail.appendChild(trailer);
      }
      row.appendChild(detail);

      chevron.addEventListener("click", function () {
        var expanded = chevron.getAttribute("aria-expanded") === "true";
        chevron.setAttribute("aria-expanded", String(!expanded));
        detail.hidden = expanded;
      });
    } else {
      row.appendChild(actions);
    }

    return row;
  }

  // ---------- about modal (manual trigger; about-modal.js owns the
  // first-visit auto-show, ported unchanged from croquis) ----------

  function bindAboutModalTrigger() {
    var modal = qs("about-modal");
    if (!modal || !els.infoBtn) return;
    var content = modal.querySelector(".about-content");
    var startBtn = modal.querySelector(".about-start-btn");
    var appRoot = qs("app");

    function openModal() {
      modal.hidden = false;
      if (appRoot) appRoot.inert = true;
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
