"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const local = {
  get(k, d) { try { const v = localStorage.getItem("kino:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("kino:" + k, JSON.stringify(v)); } catch {} },
};

// ---------------------------------------------------------------- regions + time (data is in the region's local time)
const REGIONS = [
  { key: "oslo", name: "Oslo", file: "films.json", tz: "Europe/Oslo" },
  { key: "westman", name: "Westman", file: "westman.json", tz: "America/Winnipeg" },
  { key: "costadelsol", name: "Costa del Sol", file: "costadelsol.json", tz: "Europe/Madrid" },
];
let clockFmt = null;
function setClock(tz) {
  clockFmt = new Intl.DateTimeFormat("sv-SE", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}
setClock("Europe/Oslo");
const localNow = () => clockFmt.format(new Date()).replace(" ", "T"); // "2026-10-01T10:40" in the region's time zone
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const asDate = (t) => { const [y, m, d] = t.slice(0, 10).split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const addDays = (day, n) => { const d = asDate(day); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
function dayLabel(t, { short = false } = {}) {
  const day = t.slice(0, 10), today = localNow().slice(0, 10);
  if (day === today) return "Today";
  if (day === addDays(today, 1)) return "Tomorrow";
  const d = asDate(day);
  const base = `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  return short || day.slice(0, 4) === today.slice(0, 4) ? base : `${base} ${day.slice(0, 4)}`;
}
const hhmm = (t) => t.slice(11, 16);

// ---------------------------------------------------------------- state
// One grid, three ways to read it. Each view groups films under section headers.
const VIEWS = [["when", "Playing when", "Grouped by when it starts"], ["sale", "Newly on sale", "Tickets that just went on sale"],
  ["ann", "Newly announced", "Films that just got a date here"]];  // the Sort menu
const TIX = [["all", "All"], ["on", "On sale"], ["off", "Not on sale yet"]];
// Order of films inside each section. "date" means the view's natural order (by time, or newest first).
const WITHIN = [["rating", "Best rated first"], ["date", "By date"], ["fewest", "Fewest showings first"]];
const DEFAULT_HIDE = ["short", "stage", "talk"];  // by default only films are shown
const DEFAULTS_V = 2;                             // bump to re-apply new defaults to saved settings
const DEFAULT_PREFS = { cinemas: [], hideDubbed: false, englishSubs: false, audioEnNo: false, hidden: {}, watchlistAlways: true, announcements: true, regions: ["oslo"], frequency: "daily", hideKinds: DEFAULT_HIDE, defaultsV: DEFAULTS_V };
// Settings saved before these defaults existed get the new "films only" default once.
const withDefaults = (saved) => ({ ...DEFAULT_PREFS, ...saved, ...(saved.defaultsV === DEFAULTS_V ? {} : { hideKinds: DEFAULT_HIDE, defaultsV: DEFAULTS_V }) });
const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
const KINDS = [["film", "Films"], ["short", "Shorts"], ["stage", "Live & stage"], ["talk", "Talks & events"]];
const KIND_BADGE = { short: "Shorts", stage: "Live & stage", talk: "Talk / event" };
const kindShown = (f) => !(state.prefs.hideKinds || []).includes(f.kind || "film");
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const urlRegion = new URLSearchParams(location.search).get("r");
const state = {
  data: null,
  region: REGIONS.some((r) => r.key === urlRegion) ? urlRegion : local.get("region", "oslo"),
  view: VIEWS.some(([v]) => v === local.get("view", "when")) ? local.get("view", "when") : "when",
  tix: local.get("tix", "all"),
  within: local.get("within2", "rating"),
  onlyWatch: false,
  day: "",              // "Playing when": the day its first section shows; "" = today. Not remembered across reloads
  calMonth: "",         // the month the date picker shows ("2026-10")
  q: "",
  prefs: withDefaults(local.get("prefs", {})),
  watchlist: new Set(local.get("watchlist", [])),
  collapsed: new Set(), // "view:sectionKey" of collapsed sections; deliberately not remembered across reloads
  showAll: false,       // film sheet: show showings hidden by filters
  sheetCinemas: new Set(), // film sheet: cinema tags clicked to narrow its showings
  user: null,           // { id, email }
  pendingEmail: (() => { try { return sessionStorage.getItem("kino:pendingEmail") || ""; } catch { return ""; } })(),
  profile: null,        // { subscribed, ... }
};

// ---------------------------------------------------------------- filters (keep in step with scraper/digest.py: show_ok)
// "My cinemas" is one list across regions; only the ones in the region being viewed apply.
const myCinemas = () => state.prefs.cinemas.filter((c) => state.data.cinemas.includes(c));
const UNDERSTOOD_AUDIO = ["en", "no", "nb"];  // audio languages that need no Spanish (Costa del Sol)
const DUBBING_REGIONS = ["oslo", "costadelsol"];  // where the data says which showings are dubbed
function showMatches(s, now) {
  if (s.t < now) return false;
  const p = state.prefs, mine = myCinemas();
  if (mine.length && !mine.includes(s.cinema)) return false;
  if (state.region === "costadelsol" && p.audioEnNo && !UNDERSTOOD_AUDIO.includes(s.lang)) return false;  // English or Norwegian audio
  if (DUBBING_REGIONS.includes(state.region) && p.hideDubbed && s.dub) return false;  // "Original language only"
  if (state.region === "oslo" && p.englishSubs && !s.en) return false;
  return true;
}
const isWatched = (f) => f.ids.some((id) => state.watchlist.has(id));
// The watchlist is one list across regions (ids are per cinema listing); only the films in the region being viewed count.
const watchedHere = () => state.data.films.filter(isWatched).length;

// Hiding a film: prefs.hidden maps film id -> the film's onSaleSince when it was hidden. It stays hidden while that same
// run continues; if the film goes off sale and comes back later (a new onSaleSince, the same rule as "newly on sale"),
// it shows again. Hiding is never permanent.
const eyeOn = `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/></svg>`;
const eyeOff = `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4 4l16 16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
function isHidden(f) {
  const since = f.ids.map((i) => state.prefs.hidden?.[i]).find((v) => v !== undefined);
  if (since === undefined) return false;
  return !(since && f.onSaleSince && f.onSaleSince > since);
}
function unhideFilm(f) { for (const i of f.ids) delete state.prefs.hidden?.[i]; }
// Hiding or unhiding moves the card to its new place in the section; slide it there instead of jumping.
let flipFrom = null;
function flipBegin(id) { flipFrom = { id, rects: new Map([...document.querySelectorAll(".card")].map((c) => [c.dataset.id, c.getBoundingClientRect()])) }; }
function flipRun(fadeFrom = null) {
  const prev = flipFrom; flipFrom = null;
  if (!prev || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  for (const c of document.querySelectorAll(".card")) {
    const a = prev.rects.get(c.dataset.id);
    if (!a) continue;
    const b = c.getBoundingClientRect(), dx = a.left - b.left, dy = a.top - b.top;
    if (dx || dy) c.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 450, easing: "cubic-bezier(.2, .8, .2, 1)" });
    if (fadeFrom !== null && c.dataset.id === prev.id) for (const el of c.querySelectorAll(".poster img, .poster .ph, .m, h3")) el.animate([{ opacity: fadeFrom }, {}], { duration: 450 });
  }
}
// A press first shows its own result right on the button (and the dimming/filling), and only a moment later does the card
// slide to its new place, so the press clearly registered before anything moves.
const SETTLE_MS = 450;
let renderTimer = null;
const pop = (el) => { el.classList.remove("pop"); void el.offsetWidth; el.classList.add("pop"); };  // a quick "it registered" bounce
const cardEl = (id) => document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
function settleThenRender(id) {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(() => { renderTimer = null; flipBegin(id); render(); flipRun(); }, SETTLE_MS);
}
function flushRender() { if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; render(); } }
function toggleHidden(f) {
  flushRender();
  const wasHidden = isHidden(f);
  if (wasHidden) unhideFilm(f);
  else state.prefs.hidden = { ...(state.prefs.hidden || {}), [f.id]: f.onSaleSince || "" };
  savePrefs();
  const card = cardEl(f.id), btn = card?.querySelector(".hide");
  if (card) card.classList.toggle("hid", !wasHidden);  // dims (or undims) with a fade
  if (btn) { btn.innerHTML = wasHidden ? eyeOff : eyeOn; btn.title = wasHidden ? "Hide this film" : "Show this film again"; btn.setAttribute("aria-label", btn.title); pop(btn); }
  if ($("film").open) { if (wasHidden) openFilm(f.id); else $("film").close(); }
  if (!wasHidden) toast(`Hidden ${titleOf(f)}`, () => toggleHidden(f));
  settleThenRender(f.id);
}
// "New" = went on sale (or, for announced films, first got a date) in the last 7 days, after tracking began.
function isRecent(since) {
  if (!since || since <= state.data.baseline) return false;
  return since.slice(0, 10) >= addDays(localNow().slice(0, 10), -7);
}
const isNew = (f) => isRecent(f.status === "on_sale" ? f.onSaleSince : f.announcedSince);
function textMatch(f, q) {
  return !q || `${f.title} ${f.alt} ${f.ext?.en || ""} ${f.director} ${f.series.join(" ")}`.toLowerCase().includes(q);
}
// English title when scraper/external.py found a confident, genuine one; otherwise the Norwegian title.
const titleOf = (f) => f.ext?.en || f.title;
const otherTitles = (f) => [...new Set([f.title, f.ext?.en, f.alt].filter((t) => t && t !== titleOf(f)))];
const byTitle = (a, b) => titleOf(a.f).localeCompare(titleOf(b.f), "nb");

// When a film "starts" here: its first showing, or its (confirmed) premiere if that's earlier,
// e.g. it premiered last week and is still playing. No showings: the confirmed premiere, else none.
function startDay(f, shows) {
  const first = shows[0]?.t.slice(0, 10);
  const prem = f.premiereConfirmed ? f.premiere : "";
  if (first) return prem && prem < first ? prem : first;
  return prem || "";
}

// The day the first "Playing when" section shows: the one picked, or today (also once a picked day has passed).
const chosenDay = (today) => (state.day > today ? state.day : today);
const DAY_KEY = "0000";
const dayTitle = (chosen, today) => (chosen === today ? "Playing today" : `Playing ${dayLabel(chosen)}`);
// Section labels for the "When it's playing" view. The first section is the chosen day (today unless another is
// picked); the rest group films by their next showing after it, so picking a day shows the page as it will be then.
function whenSection(row, today, chosen = today) {
  // A film's day = its next showing; films without showings use their confirmed premiere.
  const day = row.shows[0]?.t.slice(0, 10) || row.start;
  if (!day) return { key: "9999", label: "Date not set" };
  if (row.shows.length && day === chosen) return { key: DAY_KEY, label: dayTitle(chosen, today) };
  const dow = asDate(today).getUTCDay();                    // 0 = Sunday
  const weekEnd = addDays(today, (7 - dow) % 7);            // this coming Sunday
  if (day <= weekEnd) return { key: "0001", label: "This week" };
  if (day <= addDays(weekEnd, 7)) return { key: "0002", label: "Next week" };
  const [y, m] = day.split("-").map(Number);
  const [ty, tm] = today.split("-").map(Number);
  const label = y === ty && m === tm ? `Later in ${MONTH_NAMES[m - 1]}` : `${MONTH_NAMES[m - 1]}${y !== ty ? " " + y : ""}`;
  return { key: day.slice(0, 7), label };
}

// Section labels for the two "newly" views, by when it happened.
function sinceSection(since, today, before) {
  if (!since || since <= state.data.baseline) return { key: "9", label: before };
  const day = since.slice(0, 10);
  if (day === today) return { key: "0", label: "Today" };
  if (day === addDays(today, -1)) return { key: "1", label: "Yesterday" };
  if (day >= addDays(today, -6)) return { key: "2", label: "Earlier this week" };
  if (day >= addDays(today, -13)) return { key: "3", label: "Last week" };
  return { key: "4", label: "Earlier" };
}

// A film's showings from `from` on that pass the filters, and whether it's on sale; null when the filters leave it out.
function filmShows(f, now, from, q = "") {
  if (!textMatch(f, q) || (!q && !kindShown(f))) return null;  // searching finds every type
  if (state.onlyWatch && !isWatched(f)) return null;
  const shows = f.shows.filter((s) => s.t >= from && showMatches(s, now));
  if (f.shows.length && !shows.length) return null; // has showings, none match the filters
  const bookable = shows.filter((s) => s.ticket);
  const onSale = f.status === "on_sale" && bookable.length > 0;
  if (state.tix === "on" && !onSale) return null;
  if (state.tix === "off" && onSale) return null;
  return { shows: onSale ? bookable : shows, onSale, all: shows };
}

// Whether "Playing when" shows the chosen-day section with its day buttons (searching always covers every date).
const dayMode = () => state.view === "when" && !state.q.trim();

function buildRows() {
  const now = localNow(), today = now.slice(0, 10), q = state.q.trim().toLowerCase();
  const b = state.data.baseline ? asDate(state.data.baseline) : null;
  const started = b ? `Before ${b.getUTCDate()} ${MONTHS[b.getUTCMonth()]}` : "Before tracking began";
  const chosen = dayMode() ? chosenDay(today) : today, later = chosen !== today;
  const rows = [];
  for (const f of state.data.films) {
    // A picked day hides everything before it: only showings from that day on count.
    const m = filmShows(f, now, later ? chosen + "T00:00" : now, q);
    if (!m) continue;
    const row = { f, shows: m.shows, onSale: m.onSale, hidden: isHidden(f), watched: isWatched(f), start: startDay(f, m.all) };
    if (state.view === "when") {
      if (!row.start && !q) continue; // undated films only turn up when you search for them
      if (later && !row.shows.length && row.start < chosen) continue;  // premiered before the picked day, nothing since
      row.sec = whenSection(row, today, chosen);
      if (later && row.sec.key === DAY_KEY) row.dayShows = row.shows.filter((s) => s.t.startsWith(chosen));
    } else if (state.view === "sale") {
      if (!onSale) continue;
      row.sec = sinceSection(f.onSaleSince, today, started);
      row.since = f.onSaleSince;
    } else {
      if (f.status !== "announced" || !f.announcedSince) continue;
      row.sec = sinceSection(f.announcedSince, today, started);
      row.since = f.announcedSince;
    }
    rows.push(row);
  }
  const t0 = (r) => r.shows[0]?.t || (r.start ? r.start + "T00:00" : "9999");
  const rating = (r) => r.f.ext?.lbRating ?? -1;
  rows.sort((a, b) => {
    if (a.sec.key !== b.sec.key) return a.sec.key.localeCompare(b.sec.key);
    if (a.hidden !== b.hidden) return a.hidden ? 1 : -1;  // hidden films are dimmed and always last in their section
    if (a.watched !== b.watched) return a.watched ? -1 : 1;  // watchlist films come first
    if (state.within === "rating") return rating(b) - rating(a) || t0(a).localeCompare(t0(b)) || byTitle(a, b);
    if (state.within === "fewest") { // one-off screenings first; films with no showings yet (just a premiere) last
      const n = (r) => r.shows.length || Infinity;
      return n(a) - n(b) || t0(a).localeCompare(t0(b)) || byTitle(a, b);
    }
    if (state.view !== "when") return (b.since || "").localeCompare(a.since || "") || t0(a).localeCompare(t0(b)) || byTitle(a, b);
    return t0(a).localeCompare(t0(b)) || byTitle(a, b);
  });
  return rows;
}

// ---------------------------------------------------------------- render: controls + filter sidebar
function activeFilters() {
  return (state.tix !== "all" ? 1 : 0)
    + (sameSet(state.prefs.hideKinds || [], DEFAULT_HIDE) ? 0 : 1) + (myCinemas().length ? 1 : 0)
    + (DUBBING_REGIONS.includes(state.region) && state.prefs.hideDubbed ? 1 : 0)
    + (state.region === "oslo" && state.prefs.englishSubs ? 1 : 0)
    + (state.region === "costadelsol" && state.prefs.audioEnNo ? 1 : 0);
}

function renderControls() {
  const cur = VIEWS.find(([v]) => v === state.view);
  $("sortBtn").innerHTML = `<span class="k">Sort</span> ${cur[1]} <span class="chev" aria-hidden="true">▾</span>`;
  $("sortBtn").setAttribute("aria-label", `Sort: ${cur[1]}`);
  $("sortMenu").innerHTML = VIEWS.map(([v, l, d]) =>
    `<button role="menuitemradio" aria-checked="${v === state.view}" data-pick="view" data-value="${v}">${l}<small>${d}</small></button>`).join("");
  const reg = REGIONS.find((r) => r.key === state.region);
  $("regionBtn").innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="10" r="2.5" fill="currentColor"/></svg>${reg.name} <span class="chev" aria-hidden="true">▾</span>`;
  $("regionBtn").setAttribute("aria-label", `Location: ${reg.name}`);
  $("regionMenu").innerHTML = REGIONS.map((r) =>
    `<button role="menuitemradio" aria-checked="${r.key === state.region}" data-pick="region" data-value="${r.key}">${r.name}</button>`).join("");
  document.querySelectorAll("#listSwitch [data-list]").forEach((b) => b.setAttribute("aria-pressed", (b.dataset.list === "watch") === state.onlyWatch));
  const wl = watchedHere();
  $("wlCount").textContent = wl ? ` · ${wl}` : "";
  const n = activeFilters();
  $("filtersBtn").textContent = n ? `Filters · ${n}` : "Filters";
  $("filtersBtn").setAttribute("aria-pressed", n > 0);
  renderFilters();
}

function renderFilters() {
  const now = localNow(), kindCounts = {}, cinemaCounts = {};
  for (const f of state.data.films) kindCounts[f.kind || "film"] = (kindCounts[f.kind || "film"] || 0) + 1;
  for (const f of state.data.films) for (const s of f.shows) if (s.ticket && s.t >= now) cinemaCounts[s.cinema] = (cinemaCounts[s.cinema] || 0) + 1;
  const hide = new Set(state.prefs.hideKinds || []), mine = myCinemas();
  // Every option is a chip: a real checkbox/radio inside a label, styled as a tappable pill.
  const ck = (attrs, checked, label, n, type = "checkbox") =>
    `<label class="opt-chip"><input type="${type}" ${attrs}${checked ? " checked" : ""}><span>${label}</span>${n != null ? `<span class="n">${n}</span>` : ""}</label>`;
  const focused = document.activeElement?.closest?.("#filtersBody") ? document.activeElement.dataset.f + "|" + (document.activeElement.value || "") : "";
  $("filtersBody").innerHTML = `
    <fieldset class="fg"><legend>Order within sections</legend><div class="chips">
      ${WITHIN.map(([v, l]) => ck(`name="within" data-f="within" value="${v}"`, state.within === v, v === "date" && state.view !== "when" ? "Newest first" : l, null, "radio")).join("")}
    </div></fieldset>
    <fieldset class="fg"><legend>Tickets</legend>
      <div class="chips">${TIX.map(([v, l]) => ck(`name="tix" data-f="tix" value="${v}"`, state.tix === v, l, null, "radio")).join("")}</div>
    </fieldset>
    <fieldset class="fg"><legend>Types <span class="hint">${sameSet([...hide], DEFAULT_HIDE) ? "Films only" : `${KINDS.length - hide.size} of ${KINDS.length}`}</span></legend><div class="chips">
      ${KINDS.map(([k, l]) => ck(`data-f="kind" value="${k}"`, !hide.has(k), l, kindCounts[k] || 0)).join("")}
    </div></fieldset>
    <fieldset class="fg"${DUBBING_REGIONS.includes(state.region) ? "" : " hidden"}><legend>Language</legend>
      <div class="chips">${ck('data-f="dub"', state.prefs.hideDubbed, "Original language only")}
      ${state.region === "oslo" ? ck('data-f="en"', state.prefs.englishSubs, "English subtitles only") : ""}
      ${state.region === "costadelsol" ? ck('data-f="audio"', state.prefs.audioEnNo, "English or Norwegian audio") : ""}</div>
    </fieldset>
    <fieldset class="fg"><legend>Cinemas <span class="hint">${mine.length ? `${mine.length} chosen` : "All"}</span></legend>
      <div class="chips">${state.data.cinemas.map((c) => ck(`data-f="cinema" value="${esc(c)}"`, mine.includes(c), esc(c), cinemaCounts[c] || 0)).join("")}</div>
      ${mine.length ? `<button class="linkbtn" data-f="cinemas-clear">Show all cinemas</button>` : ""}
    </fieldset>
`;
  $("filtersReset").hidden = !activeFilters();
  if (focused) { // keep keyboard focus on the control that was just changed
    const [f, v] = focused.split("|");
    [...$("filtersBody").querySelectorAll(`[data-f="${f}"]`)].find((el) => (el.value || "") === v)?.focus();
  }
}

// ---------------------------------------------------------------- render: grid
// "Last chance" when only 1 or 2 showings are listed in the next two weeks. There is deliberately no "leaving soon" for
// films with more showings: cinemas often add showings later. Only when the cinema publishes beyond the film's last
// showing, so a short schedule window isn't mistaken for the end.
const ms = (t) => Date.parse(t + ":00Z");
function endingNote(shows) {
  const last = shows[shows.length - 1];
  const horizon = state.horizon?.[last?.cinema];
  if (!last || !horizon || ms(horizon) - ms(last.t) < 3 * 864e5) return "";
  const days = (ms(last.t) - ms(localNow())) / 864e5;
  return shows.length <= 2 && days <= 14 ? "Last chance" : "";
}

const whereList = (shows) => {
  const cinemas = [...new Set(shows.map((s) => s.cinema))];
  return cinemas.length > 2 ? `${cinemas.slice(0, 2).join(", ")} +${cinemas.length - 2}` : cinemas.join(", ");
};

function cardMeta(row) {
  const { f, shows, onSale, start, dayShows } = row;
  const today = localNow().slice(0, 10);
  if (dayShows) { // a picked day: that day's cinemas and times
    // each time stays whole; the line may wrap between them on narrow cards
    const times = dayShows.slice(0, 3).map((s) => `<span class="nw">${hhmm(s.t)}</span>`).join(" · ") + (dayShows.length > 3 ? ` <span class="nw">+${dayShows.length - 3}</span>` : "");
    if (!onSale) return `${times}<br><span class="nosaletag">Not on sale yet</span>`;
    const ending = endingNote(shows);
    return `<b>${esc(whereList(dayShows))}</b><br>${times}` + (ending ? `<br><span class="leave">${esc(ending)}</span>` : "");
  }
  if (onSale) {
    const where = whereList(shows);
    const first = shows[0];
    const cls = first.t.slice(0, 10) === today ? ' class="today"' : "";
    // keep "Tomorrow 10:15" and "11 shows" whole on narrow cards
    const ending = endingNote(shows);
    return `<b>${esc(where)}</b><br><span class="nw${cls ? " today" : ""}">${dayLabel(first.t, { short: true })} ${hhmm(first.t)}</span> · <span class="nw">${shows.length} show${shows.length > 1 ? "s" : ""}</span>`
      + (ending ? `<br><span class="leave">${esc(ending)}</span>` : "");
  }
  if (shows.length) return `${dayLabel(shows[0].t, { short: true })} ${hhmm(shows[0].t)}<br><span class="nosaletag">Not on sale yet</span>`;
  if (f.scope === "Canada" && f.premiere) return `Opens in Canada ${dayLabel(f.premiere)}<br><span class="nosaletag">Not scheduled here yet</span>`;
  if (f.premiere) {
    const d = asDate(f.premiere);
    const when = f.premiereConfirmed ? `Premiere ${dayLabel(f.premiere)}` : `Expected ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    return `${when}<br><span class="nosaletag">Not on sale yet</span>`;
  }
  return `<span class="nosaletag">Date not set</span>`;
}

function posterHtml(f) {
  return `<div class="poster">${f.poster
    ? `<img loading="lazy" src="${esc(f.poster)}" alt="" onerror="${f.poster2 ? `this.onerror=function(){this.remove()};this.src='${esc(f.poster2)}'` : "this.remove()"}">`
    : ""}<div class="ph"${f.poster ? ' aria-hidden="true" style="z-index:-1"' : ""}>${esc(titleOf(f))}</div></div>`;
}

// Letterboxd rating on the poster: the number, then a star (the star only ever means a rating).
function ratingHtml(f) {
  const r = f.ext?.lbRating;
  if (r == null) return "";
  const label = `Letterboxd rating ${r.toFixed(1)} out of 5`;
  return `<span class="lbr pillr" title="${label}" aria-label="${label}">${r.toFixed(1)}<i aria-hidden="true">★</i></span>`;
}
// Watchlist heart (inline SVG so it's crisp and the same everywhere).
const heart = (filled) => `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-7.5-4.7-9.6-9.3C.9 8.3 3 4.5 6.7 4.5c2 0 3.6 1 5.3 3 1.7-2 3.3-3 5.3-3 3.7 0 5.8 3.8 4.3 7.2C19.5 16.3 12 21 12 21z" fill="${filled ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>`;

function cardHtml(row) {
  const { f } = row;
  const flag = (isNew(f) ? `<span class="flag">New</span>` : "") + (KIND_BADGE[f.kind] ? `<span class="kind">${KIND_BADGE[f.kind]}</span>` : "")
    + ratingHtml(f)
    + `<button type="button" class="hide" data-hide="${esc(f.id)}" aria-label="${row.hidden ? "Show" : "Hide"} this film" title="${row.hidden ? "Show this film again" : "Hide this film"}">${row.hidden ? eyeOn : eyeOff}</button>`;
  const on = isWatched(f);
  return `<li class="card${row.onSale ? "" : " nosale"}${row.hidden ? " hid" : ""}" data-id="${esc(f.id)}">
    <a href="#film/${esc(f.id)}">${posterHtml(f).replace('<div class="poster">', `<div class="poster">${flag}`)}
      <h3>${esc(titleOf(f))}</h3><div class="m">${cardMeta(row)}</div></a>
    <button class="star${on ? " on" : ""}" data-star="${esc(f.id)}" aria-pressed="${on}" aria-label="${on ? "Remove from" : "Add to"} watchlist" title="${on ? "On your watchlist" : "Add to watchlist"}">${heart(on)}</button>
  </li>`;
}

function renderGrid() {
  const rows = buildRows();
  const sections = [];
  for (const r of rows) {
    if (!sections.length || sections.at(-1).key !== r.sec.key) sections.push({ key: r.sec.key, label: r.sec.label, rows: [] });
    sections.at(-1).rows.push(r);
  }
  const isCollapsed = (s) => state.collapsed.has(`${state.view}:${s.key}`);
  let empty = "No films match these filters.";
  if (state.q.trim()) empty = `Nothing matching “${esc(state.q.trim())}” in ${esc(state.data.location)}'s listings yet.`;
  else if (state.onlyWatch && !state.watchlist.size) empty = "Your watchlist is empty. Tap the heart on any poster to add it; it'll be highlighted when tickets go on sale.";
  else if (state.onlyWatch && !watchedHere()) empty = `Nothing on your watchlist is in ${esc(state.data.location)}'s listings. Tap the heart on any poster to add it; it'll be highlighted when tickets go on sale.`;
  // The chosen-day section is always there in "Playing when", even empty, so its day buttons never go missing.
  const today = localNow().slice(0, 10), chosen = chosenDay(today);
  const withDay = dayMode() && !(state.onlyWatch && !watchedHere());
  if (withDay && sections[0]?.key !== DAY_KEY) sections.unshift({ key: DAY_KEY, label: dayTitle(chosen, today), rows: [] });
  const dayEmpty = `${state.onlyWatch ? "Nothing on your watchlist is" : "Nothing is"} playing ${chosen === today ? "for the rest of today" : `on ${dayLabel(chosen)}`}${activeFilters() ? " with these filters" : ""}.`;
  state.sections = sections;
  $("filtersShow").textContent = `Show ${rows.length} film${rows.length === 1 ? "" : "s"}`;
  $("grid").innerHTML = (withDay ? dayPickerHtml(today, chosen) : "") + (sections.length ? sections.map((s, i) => {
    const shut = isCollapsed(s), isDay = withDay && s.key === DAY_KEY;
    const peek = shut ? `<span class="peek">${esc(s.rows.slice(0, 4).map((r) => titleOf(r.f)).join(" · "))}${s.rows.length > 4 ? " …" : ""}</span>` : "";
    return `<section class="sec${shut ? " shut" : ""}" id="sec-${i}">
      <h2 class="sech"><button data-sec="${i}" aria-expanded="${!shut}" aria-controls="secgrid-${i}">
        <span class="lbl">${esc(s.label)}</span> <span class="n">${s.rows.length}</span>${peek}<span class="chev" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
      </button></h2>
      ${shut ? "" : isDay && !s.rows.length ? `<p class="dayempty">${esc(dayEmpty)}</p>` : `<ul class="grid" id="secgrid-${i}">${s.rows.map(cardHtml).join("")}</ul>`}</section>`;
  }).join("") : `<p class="empty">${empty}</p>`);
}

// ---------------------------------------------------------------- render: the day buttons and the date picker
const shortDay = (day) => { const d = asDate(day); return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()}`; };
const weekdayName = new Intl.DateTimeFormat("en-GB", { weekday: "long", timeZone: "UTC" });
const calIcon = `<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3.5 10h17M8 3v4M16 3v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
function dayPickerHtml(today, chosen) {
  const quick = [today, addDays(today, 1), addDays(today, 2)];
  const picked = !quick.includes(chosen);
  return `<div class="daypick" role="group" aria-label="Day to show">
    ${quick.map((d, i) => `<button class="daybtn" data-dayset="${i ? d : ""}" aria-pressed="${chosen === d}">${["Today", "Tomorrow", weekdayName.format(asDate(d))][i]}</button>`).join("")}
    <div class="dd"><button class="daybtn${picked ? " on" : ""}" id="calBtn" aria-haspopup="dialog" aria-expanded="false" aria-label="${picked ? `${longDate.format(asDate(chosen))}, pick another date` : "Pick a date"}">${calIcon}${picked ? `${shortDay(chosen)}<span class="wide"> ${MONTHS[asDate(chosen).getUTCMonth()]}</span>` : `<span class="wide">Pick a date</span><span class="narrow">Date</span>`}</button>
      <div class="menu cal" id="calMenu" role="dialog" aria-label="Pick a date" hidden></div></div>
  </div>`;
}

// The last day anything is listed, from the cinemas' published horizons.
const lastListedDay = () => Object.values(state.horizon || {}).reduce((a, t) => (t > a ? t : a), "").slice(0, 10);
// How many films the chosen-day section would hold on each day of a month (only today onwards).
function dayCounts(month, today, last) {
  const now = localNow(), counts = {};
  const [y, m] = month.split("-").map(Number), len = new Date(Date.UTC(y, m, 0)).getUTCDate();
  for (let i = 1; i <= len; i++) {
    const day = `${month}-${String(i).padStart(2, "0")}`;
    if (day < today || day > last) continue;
    counts[day] = 0;
    for (const f of state.data.films) {
      const r = filmShows(f, now, day === today ? now : day + "T00:00");
      if (r?.shows[0]?.t.startsWith(day)) counts[day]++;
    }
  }
  return counts;
}
const longDate = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
// A month grid (weeks start on Monday). Days with nothing playing under the current filters can't be picked.
function calHtml() {
  const today = localNow().slice(0, 10), chosen = chosenDay(today), last = lastListedDay() || today;
  const month = state.calMonth, [y, m] = month.split("-").map(Number);
  const lead = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7, counts = dayCounts(month, today, last);
  const cells = Array.from({ length: lead }, () => `<span></span>`);
  for (let i = 1, len = new Date(Date.UTC(y, m, 0)).getUTCDate(); i <= len; i++) {
    const day = `${month}-${String(i).padStart(2, "0")}`, n = counts[day] || 0;
    const label = `${longDate.format(asDate(day))}, ${n ? `${n} film${n > 1 ? "s" : ""}` : "nothing playing"}`;
    cells.push(`<button class="cday${day === today ? " now" : ""}" data-dayset="${day === today ? "" : day}" data-cal="${day}" aria-pressed="${day === chosen}" aria-label="${label}"${n ? "" : " disabled"}>${+day.slice(8)}</button>`);
  }
  return `<div class="calhead">
      <button class="calnav" data-calnav="-1" aria-label="Previous month"${month <= today.slice(0, 7) ? " disabled" : ""}>‹</button>
      <b>${MONTH_NAMES[m - 1]} ${y}</b>
      <button class="calnav" data-calnav="1" aria-label="Next month"${month >= last.slice(0, 7) ? " disabled" : ""}>›</button></div>
    <div class="calgrid">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => `<span class="wd" aria-hidden="true">${d.slice(0, 2)}</span>`).join("")}${cells.join("")}</div>`;
}
function openCal() {
  const menu = $("calMenu");
  if (menu.hidden) { state.calMonth = chosenDay(localNow().slice(0, 10)).slice(0, 7); menu.innerHTML = calHtml(); }
  toggleMenu($("calBtn"), menu);
  if (!menu.hidden) (menu.querySelector('.cday[aria-pressed="true"]:not(:disabled)') || menu.querySelector(".cday:not(:disabled)"))?.focus();
}
function setDay(day) {
  closeMenus();
  state.day = day;
  renderGrid();
  ($("grid").querySelector('.daybtn[aria-pressed="true"]') || $("calBtn"))?.focus({ preventScroll: true });
}
document.addEventListener("click", (e) => {
  const t = e.target.closest("#calBtn,[data-calnav],[data-dayset]");
  if (!t) return;
  if (t.id === "calBtn") { openCal(); return; }
  if (t.dataset.calnav) {
    const [y, m] = state.calMonth.split("-").map(Number), d = new Date(Date.UTC(y, m - 1 + +t.dataset.calnav, 1));
    state.calMonth = d.toISOString().slice(0, 7);
    $("calMenu").innerHTML = calHtml();
    $("calMenu").querySelector(`[data-calnav="${t.dataset.calnav}"]:not(:disabled)`)?.focus();
    return;
  }
  setDay(t.dataset.dayset);
});
// Arrow keys move through the month's days (a week up or down), skipping days with nothing playing.
document.addEventListener("keydown", (e) => {
  const from = e.target.closest?.(".cday");
  const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
  if (!from || !step) return;
  e.preventDefault();
  for (let day = addDays(from.dataset.cal, step); day.slice(0, 7) === state.calMonth; day = addDays(day, step)) {
    const b = $("calMenu").querySelector(`[data-cal="${day}"]`);
    if (b && !b.disabled) { b.focus(); return; }
  }
});

function render() { renderControls(); renderGrid(); }

// ---------------------------------------------------------------- film sheet
function findFilm(id) { return state.data.films.find((f) => f.id === id || f.ids.includes(id)); }

function stubHtml(s) {
  const screen = s.screen && s.screen !== s.cinema ? s.screen.replace(s.cinema, "").trim() : "";
  const tags = [...s.tags.filter((t) => !["Norsk tekst"].includes(t)), s.dub ? "Dubbed" : ""].filter(Boolean);
  const inner = `<span class="t">${hhmm(s.t)}</span><span class="c">${esc(s.cinema)}${screen ? ` · ${esc(screen)}` : ""}</span>`
    + (tags.length ? `<span class="tg">${esc(tags.join(" · "))}</span>` : "")
    + (s.note ? `<span class="nt">${esc(s.note)}</span>` : "")
    + (!s.ticket ? `<span class="c">${esc(s.status || "Not on sale yet")}</span>` : "");
  return s.ticket
    ? `<a class="stub" href="${esc(s.ticket)}" target="_blank" rel="noopener" title="Buy tickets">${inner}</a>`
    : `<span class="stub nosale">${inner}</span>`;
}

// Letterboxd / IMDb / RT / Metacritic links. A direct link only when the scraper matched the film
// confidently (scraper/external.py); otherwise a Letterboxd search, which can't point at the wrong film.
function extLinks(f) {
  const x = f.ext || {}, out = [];
  const lb = x.lb ? `https://letterboxd.com/film/${encodeURIComponent(x.lb)}/`
    : x.imdb ? `https://letterboxd.com/imdb/${x.imdb}/` : x.tmdb ? `https://letterboxd.com/tmdb/${x.tmdb}/` : "";
  if (lb) out.push(`<a class="ext lb" href="${esc(lb)}" target="_blank" rel="noopener">Letterboxd${x.lbRating ? ` <b>${x.lbRating.toFixed(1)} ★</b>` : ""}</a>`);
  else out.push(`<a class="ext lb" href="https://letterboxd.com/search/films/${encodeURIComponent((f.alt || f.title) + (f.year ? " " + f.year : ""))}/" target="_blank" rel="noopener">Search Letterboxd</a>`);
  return `<div class="exts">${out.join("")}</div>`;
}

function openFilm(id) {
  const f = findFilm(id);
  if (!f) return;
  const dlg = $("film"), now = localNow(), today = now.slice(0, 10);
  const picked = dayMode() && chosenDay(today) !== today ? chosenDay(today) : "";  // the day picked in "Playing when"
  const all = f.shows.filter((s) => s.t >= now);
  const filtered = state.showAll ? all : all.filter((s) => showMatches(s, now));
  const shown = state.sheetCinemas.size ? filtered.filter((s) => state.sheetCinemas.has(s.cinema)) : filtered;
  const hidden = all.length - filtered.length;
  const byDay = {};
  for (const s of shown) (byDay[s.t.slice(0, 10)] ||= []).push(s);
  const meta = [f.year, f.runtime ? `${f.runtime} min` : "", f.director ? `Directed by ${f.director}` : "", f.countries.slice(0, 3).join(", "), f.genres.slice(0, 3).join(", ")].filter(Boolean).join(" · ");
  const cinemas = Object.entries(all.reduce((a, s) => ((a[s.cinema] = (a[s.cinema] || 0) + 1), a), {}));
  const on = isWatched(f);
  const days = Object.entries(byDay).map(([day, shows]) => `
    <div class="day${day === today ? " today" : ""}${day === picked ? " picked" : ""}"><h4>${dayLabel(day + "T00:00")}<small>${day.split("-").reverse().join(".")}</small></h4>
    <div class="stubs">${shows.map(stubHtml).join("")}</div></div>`).join("");
  let empty = "";
  if (!all.length && f.scope === "Canada") empty = `<p class="hiddenNote">Opens in Canadian cinemas ${dayLabel(f.premiere)}. No ${esc(state.data.location)} cinema has scheduled it yet; it moves to On sale as soon as one lists showtimes.${on ? "" : " Add it to your watchlist to have it highlighted then."}</p>`;
  else if (!all.length && f.elsewhere?.length) empty = `<p class="hiddenNote">Premiere ${dayLabel(f.premiere)}. Showings so far only in ${esc(f.elsewhere.join(", "))}; none in ${esc(state.data.location)} yet.</p>`;
  else if (!all.length) empty = `<p class="hiddenNote">${f.premiere ? `Premiere ${dayLabel(f.premiere)}${f.premiereConfirmed ? "" : " (not confirmed)"}. ` : ""}No showings announced in ${esc(state.data.location)} yet.${on ? " You'll see it marked as new when tickets go on sale." : " Add it to your watchlist to have it highlighted when tickets go on sale."}</p>`;
  const hiddenNote = hidden ? `<p class="hiddenNote">${hidden} showing${hidden > 1 ? "s" : ""} hidden by your filters. <button class="linkbtn" data-showall="1">Show all</button></p>`
    : state.showAll && all.some((s) => !showMatches(s, now)) ? `<p class="hiddenNote"><button class="linkbtn" data-showall="0">Apply my filters</button></p>` : "";
  dlg.innerHTML = `<form method="dialog" class="dlg-close"><button class="x" aria-label="Close">×</button></form>
    <div class="fhead">${posterHtml(f)}<div>
      <h2 id="filmTitle">${esc(titleOf(f))}</h2>
      ${otherTitles(f).length ? `<div class="alt">${esc(otherTitles(f).join(" · "))}</div>` : ""}
      <div class="meta">${esc(meta)}</div>
      ${f.blurb ? `<p>${esc(f.blurb)}</p>` : ""}
    </div></div>
    <div class="fmore">
      <div class="badges">${KIND_BADGE[f.kind] ? `<span class="badge line">${KIND_BADGE[f.kind]}</span>` : ""}${isNew(f) ? `<span class="badge new">${f.status === "on_sale" ? "New on sale" : "Newly announced"}</span>` : ""}${cinemas.map(([c, n]) => `<button class="badge pick${state.sheetCinemas.has(c) ? " on" : ""}" data-sheetcinema="${esc(c)}" aria-pressed="${state.sheetCinemas.has(c)}" title="Show only ${esc(c)}">${esc(c)} · ${n}</button>`).join("")}${f.series.map((s) => `<span class="badge line">${esc(s)}</span>`).join("")}</div>
      ${extLinks(f)}
      <div class="actions"><button class="btn${on ? "" : " accent"}" data-star="${esc(f.id)}">${heart(on)} ${on ? "On your watchlist" : "Add to watchlist"}</button>
      <button class="btn ghost" data-hide="${esc(f.id)}">${isHidden(f) ? eyeOn : eyeOff} ${isHidden(f) ? "Show this film again" : "Hide this film"}</button>
      ${f.links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join("")}</div>
    </div>
    <div class="days">${days}${hiddenNote}${empty}</div>
    <form method="dialog" class="sheetbar"><button class="btn ghost">Close</button></form>`;
  if (!dlg.open) {
    dlg.showModal();
    dlg.querySelector(".day.picked")?.scrollIntoView({ block: "center" });  // opened from a picked day: start there
  }
}

function route() {
  // The heart in the daily email: add the film to the watchlist, then show it (its button now reads "On your watchlist").
  const w = location.hash.match(/^#watch\/(.+)$/);
  if (w) {
    const id = decodeURIComponent(w[1]), f = findFilm(id);
    if (f && !isWatched(f) && requestWatch(f)) { savePrefs(); render(); }
    history.replaceState(null, "", location.pathname + location.search + "#film/" + encodeURIComponent(id));
  }
  const m = location.hash.match(/^#film\/(.+)$/);
  if (m) { openFilm(decodeURIComponent(m[1])); return; }
  if ($("film").open) $("film").close();
}

$("film").addEventListener("close", () => {
  state.showAll = false;
  state.sheetCinemas.clear();
  if (location.hash.startsWith("#film/")) history.pushState("", document.title, location.pathname + location.search);
});
$("film").addEventListener("click", (e) => { if (e.target === $("film")) $("film").close(); });
$("account").addEventListener("click", (e) => { if (e.target === $("account")) $("account").close(); });

// ---------------------------------------------------------------- events
function savePrefs() { local.set("prefs", state.prefs); local.set("watchlist", [...state.watchlist]); queueSync(); }

function setCollapsed(key, shut) {
  shut ? state.collapsed.add(key) : state.collapsed.delete(key);
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-sec],[data-collapseall],[data-star],[data-hide],[data-showall],[data-sheetcinema],button[data-f]");
  if (!t) return;
  if (t.dataset.sec) { // collapse / expand; keep the header in view if it was pinned
    const i = +t.dataset.sec, s = state.sections[i], key = `${state.view}:${s.key}`;
    // The header pins just below the top bar, so "pinned" means above that line, not above the screen.
    const stick = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--stick-top")) || 0;
    const pinned = $("sec-" + i).getBoundingClientRect().top < stick - 1;
    setCollapsed(key, !state.collapsed.has(key));
    renderGrid();
    if (pinned) window.scrollBy(0, $("sec-" + i).getBoundingClientRect().top - stick);  // keep this header where it was pinned
    $("grid").querySelector(`[data-sec="${i}"]`)?.focus({ preventScroll: true });
    return;
  }
  if (t.dataset.collapseall) {
    for (const s of state.sections) setCollapsed(`${state.view}:${s.key}`, t.dataset.collapseall === "1");
    closeMenus(); renderGrid(); window.scrollTo({ top: 0 }); return;
  }
  if (t.dataset.f === "reset") {
    state.tix = "all";
    state.prefs.hideKinds = [...DEFAULT_HIDE]; state.prefs.hideDubbed = false; state.prefs.englishSubs = false; state.prefs.audioEnNo = false;
    state.prefs.cinemas = state.prefs.cinemas.filter((c) => !state.data.cinemas.includes(c));
    local.set("tix", "all"); savePrefs(); render(); return;
  }
  if (t.dataset.f === "cinemas-clear") {
    setCinemas(state.prefs.cinemas.filter((c) => !state.data.cinemas.includes(c)));
    savePrefs(); render(); return;
  }
  if (t.dataset.hide) { e.preventDefault(); toggleHidden(findFilm(t.dataset.hide)); return; }
  if (t.dataset.star) {
    e.preventDefault();
    const f = findFilm(t.dataset.star);
    flushRender();
    const was = isWatched(f);
    if (was) { f.ids.forEach((id) => state.watchlist.delete(id)); savePrefs(); }
    else if (requestWatch(f)) savePrefs();
    const now = isWatched(f), star = cardEl(f.id)?.querySelector(".star");
    if (now !== was) {  // show the heart's new state at once, then let the card slide to its new place (watchlist films come first)
      if (star) {
        star.classList.toggle("on", now); star.setAttribute("aria-pressed", now); star.innerHTML = heart(now);
        star.title = now ? "On your watchlist" : "Add to watchlist"; star.setAttribute("aria-label", `${now ? "Remove from" : "Add to"} watchlist`);
        pop(star);
      }
      settleThenRender(f.id);
    }
    if ($("film").open) openFilm(f.id);
    return;
  } else if (t.dataset.sheetcinema) {
    const c = t.dataset.sheetcinema;
    state.sheetCinemas.has(c) ? state.sheetCinemas.delete(c) : state.sheetCinemas.add(c);
    openFilm(location.hash.slice(6)); return;
  } else if (t.dataset.showall) { state.showAll = t.dataset.showall === "1"; openFilm(location.hash.slice(6)); return; }
  render();
  flipRun();
});

// Picking a specific cinema switches Tickets to "On sale"; going back to all cinemas switches it back to "All".
function setCinemas(next) {
  const here = state.data.cinemas, had = state.prefs.cinemas.some((c) => here.includes(c)), has = next.some((c) => here.includes(c));
  state.prefs.cinemas = next;
  if (!had && has) state.tix = "on"; else if (had && !has) state.tix = "all";
  local.set("tix", state.tix);
}

// Filter sidebar: checkboxes and radios.
$("filtersBody").addEventListener("change", (e) => {
  const el = e.target, f = el.dataset.f;
  if (f === "within") { state.within = el.value; local.set("within2", state.within); render(); return; }
  if (f === "tix") { state.tix = el.value; local.set("tix", state.tix); }
  else if (f === "kind") {
    // Ticked types are shown; stored as the hidden ones.
    const hidden = new Set(state.prefs.hideKinds || []);
    el.checked ? hidden.delete(el.value) : hidden.add(el.value);
    state.prefs.hideKinds = KINDS.map(([x]) => x).filter((x) => hidden.has(x));
  } else if (f === "cinema") {
    const list = state.prefs.cinemas;
    setCinemas(el.checked ? [...new Set([...list, el.value])] : list.filter((x) => x !== el.value));
  } else if (f === "dub") state.prefs.hideDubbed = el.checked;
  else if (f === "en") state.prefs.englishSubs = el.checked;
  else if (f === "audio") state.prefs.audioEnNo = el.checked;
  if (f !== "tix") savePrefs();
  render();
});
const setFiltersOpen = (open) => {
  document.body.classList.toggle("filters-open", open);
  $("scrim").hidden = !open;
  $("filtersBtn").setAttribute("aria-expanded", open);
  if (open) $("filtersDone").focus();
};
$("filtersBtn").addEventListener("click", () => setFiltersOpen(!document.body.classList.contains("filters-open")));
$("filtersDone").addEventListener("click", () => { setFiltersOpen(false); $("filtersBtn").focus(); });
$("scrim").addEventListener("click", () => setFiltersOpen(false));
$("filtersShow").addEventListener("click", () => { setFiltersOpen(false); window.scrollTo({ top: 0 }); });

// Search: an icon on phones that opens a full-width box; always open on wide screens.
const setSearching = (on) => {
  document.body.classList.toggle("searching", on);
  $("searchBtn").setAttribute("aria-expanded", on);
  if (on) $("q").focus();
};
$("searchBtn").addEventListener("click", () => setSearching(true));
$("searchClose").addEventListener("click", () => {
  $("q").value = ""; state.q = ""; renderGrid();
  setSearching(false); $("searchBtn").focus();
});
$("q").addEventListener("keydown", (e) => { if (e.key === "Escape") $("searchClose").click(); });
// Tapping anywhere outside an empty, open search box closes it, like the × does.
document.addEventListener("pointerdown", (e) => {
  if (!document.body.classList.contains("searching") || $("q").value) return;
  if (e.target.closest("#searchBox, #searchBtn")) return;
  setSearching(false);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("filters-open")) setFiltersOpen(false); });
$("q").addEventListener("input", (e) => { state.q = e.target.value; renderGrid(); });
// Pill menus (Sort, Location): a button that opens a small list of choices.
function closeMenus() {
  document.querySelectorAll(".menu").forEach((m) => { m.hidden = true; });
  document.querySelectorAll("[aria-haspopup]").forEach((b) => b.setAttribute("aria-expanded", "false"));
}
function toggleMenu(btn, menu) {
  const open = menu.hidden;
  closeMenus();
  if (!open) return;
  menu.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  (menu.querySelector('[aria-checked="true"]') || menu.querySelector("button"))?.focus();
}
$("sortBtn").addEventListener("click", () => toggleMenu($("sortBtn"), $("sortMenu")));
$("regionBtn").addEventListener("click", () => toggleMenu($("regionBtn"), $("regionMenu")));
document.addEventListener("click", (e) => {
  const pick = e.target.closest("[data-pick]");
  if (pick) {
    closeMenus();
    if (pick.dataset.pick === "view" && pick.dataset.value !== state.view) {
      state.view = pick.dataset.value; state.day = ""; local.set("view", state.view); render(); window.scrollTo({ top: 0 });
    } else if (pick.dataset.pick === "region" && pick.dataset.value !== state.region) loadRegion(pick.dataset.value);
    return;
  }
  if (!e.target.closest(".dd")) closeMenus();
});
document.addEventListener("keydown", (e) => {
  const menu = e.target.closest?.(".menu:not(.cal)");  // the date picker has its own arrow keys
  if (e.key === "Escape" && document.querySelector(".menu:not([hidden])")) {
    const btn = document.querySelector('[aria-haspopup][aria-expanded="true"]');
    closeMenus(); btn?.focus(); return;
  }
  if (menu && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    const items = [...menu.querySelectorAll("button")], i = items.indexOf(document.activeElement);
    items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
  }
});
$("listSwitch").addEventListener("click", (e) => {
  const b = e.target.closest("[data-list]");
  if (!b) return;
  state.onlyWatch = b.dataset.list === "watch"; render(); window.scrollTo({ top: 0 });
});

// Section headers stick just below the controls bar; keep that offset in a CSS variable.
const syncStickTop = () => {
  const c = $("controls"), sticky = getComputedStyle(c).position === "sticky";
  document.documentElement.style.setProperty("--stick-top", sticky ? c.offsetHeight + "px" : "0px");
};
new ResizeObserver(syncStickTop).observe($("controls"));
window.addEventListener("resize", syncStickTop);
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- saving to the watchlist needs an account
// With accounts available, a signed-out tap on a heart asks the visitor to sign in first, then adds the film.
function requestWatch(f) {
  if (!sb || state.user) { state.watchlist.add(f.id); unhideFilm(f); return true; }
  state.pendingWatch = f.id;
  if (state.authReady) promptSignIn();   // otherwise decided once the saved session has been checked
  return false;
}
function promptSignIn() {
  renderAccount();
  if (!$("account").open) $("account").showModal();
}
// Called once we know who's signed in: finish a watchlist add that was waiting on it.
function settlePendingWatch() {
  const id = state.pendingWatch;
  if (!id) return;
  if (!state.user) { promptSignIn(); return; }
  state.pendingWatch = null;
  const f = findFilm(id);
  if (!f) return;
  state.watchlist.add(f.id);
  unhideFilm(f);
  state.profile ? savePrefs() : local.set("watchlist", [...state.watchlist]);  // profile not loaded yet: the merge will save it
  if ($("account").open) $("account").close();
  render();
  if ($("film").open) openFilm(f.id);
  toast(`Added ${titleOf(f)} to your watchlist`);
}
function toast(msg, undo) {
  const t = $("toast");
  t.textContent = "";
  const m = document.createElement("span"); m.textContent = msg; t.append(m);
  if (undo) {
    const b = document.createElement("button");
    b.className = "linkbtn"; b.textContent = "Undo";
    b.onclick = () => { undo(); try { t.hidePopover(); } catch { t.hidden = true; } };
    t.append(b);
  }
  try { t.showPopover(); } catch { t.hidden = false; }
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { try { t.hidePopover(); } catch { t.hidden = true; } }, undo ? 7000 : 3500);
}
$("account").addEventListener("close", () => { if (!state.user) state.pendingWatch = null; });

// ---------------------------------------------------------------- accounts (Supabase magic link)
const cfg = window.KINO_CONFIG || {};
const sb = cfg.supabaseUrl && cfg.supabaseKey && window.supabase ? window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseKey) : null;
let syncTimer = null;

function queueSync() {
  if (!sb || !state.user || !state.profile) return;  // before the account's saved list has loaded, changes stay local and get merged in
  clearTimeout(syncTimer);
  syncTimer = setTimeout(pushProfile, 600);
}
async function pushProfile(extra = {}) {
  if (!sb || !state.user) return;
  const row = { prefs: state.prefs, watchlist: [...state.watchlist], updated_at: new Date().toISOString(), ...extra };
  const { error } = await sb.from("profiles").update(row).eq("id", state.user.id);
  if (error) console.error("Saving settings failed:", error.message);
}

async function loadProfile() {
  state.profileFailed = false;
  const { data, error } = await sb.from("profiles").select("subscribed,prefs,watchlist").eq("id", state.user.id).maybeSingle();
  if (error || !data) {
    console.error("Loading profile failed:", error?.message);
    state.profileFailed = true;
    if ($("account").open) renderAccount();
    return;
  }
  state.profile = data;
  // Merge: the account's settings win if it has any; local watchlist items are added to the account.
  const remote = data.prefs || {};
  const localHidden = state.prefs.hidden || {};
  if (Object.keys(remote).length) state.prefs = withDefaults(remote);
  state.prefs.hidden = { ...localHidden, ...(remote.hidden || {}) };  // hidden films from this device join the account's
  const merged = new Set([...(data.watchlist || []), ...state.watchlist]);
  const changed = merged.size !== (data.watchlist || []).length || !Object.keys(remote).length
    || Object.keys(state.prefs.hidden).length !== Object.keys(remote.hidden || {}).length;
  state.watchlist = merged;
  local.set("prefs", state.prefs); local.set("watchlist", [...merged]);
  if (changed) await pushProfile();
  render();
  if ($("account").open) renderAccount();  // the dialog may already be open: show the loaded settings
}

function renderAccount(message = "", isErr = false) {
  const body = $("accountBody");
  const msg = message ? `<div class="msg${isErr ? " err" : ""}">${esc(message)}</div>` : "";
  if (!state.user && state.pendingEmail) { // step 2: type the code from the email
    body.innerHTML = `<h2 id="accountTitle">Enter your code</h2>
      <p>We sent a sign-in code to <b>${esc(state.pendingEmail)}</b>. It works on any device, so you can read the email on your phone and type the code here.</p>
      <form class="signin" id="codeForm"><input id="code" required inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6,10}"
        maxlength="10" placeholder="123456" aria-label="Sign-in code">
      <button class="btn accent" type="submit">Sign in</button></form>
      <div class="row" style="margin-top:10px"><button class="linkbtn" id="resendCode">Send a new code</button>
      <button class="linkbtn" id="otherEmail">Use a different email</button></div>${msg}`;
    setTimeout(() => $("code")?.focus(), 0);
    return;
  }
  if (!state.user) { // step 1: email
    const want = state.pendingWatch && findFilm(state.pendingWatch);
    body.innerHTML = want ? `<h2 id="accountTitle">Sign in to save to your watchlist</h2>
      <p><b>${esc(titleOf(want))}</b> will be added as soon as you're in. Your watchlist is saved to your account, so it follows you to other devices and you can get an email when films on it go on sale.</p>
      <p>Enter your email and we'll send a one-time code. No password, and new emails get an account automatically.</p>
      <form class="signin" id="signinForm"><input type="email" id="email" required placeholder="you@example.com" autocomplete="email">
      <button class="btn accent" type="submit">Send code</button></form>${msg}` : `<h2 id="accountTitle">Sign in or sign up</h2>
      <p>Enter your email and we'll send you a one-time code. No password. Signing in syncs your watchlist and filters across devices, and lets you get the daily email of films newly on sale.</p>
      <form class="signin" id="signinForm"><input type="email" id="email" required placeholder="you@example.com" autocomplete="email">
      <button class="btn accent" type="submit">Send code</button></form>${msg}`;
    return;
  }
  if (!state.profile) { // signed in, but this account's saved settings haven't arrived yet
    body.innerHTML = `<h2 id="accountTitle">Your account</h2><div class="who">${esc(state.user.email)}</div>
      <p>${state.profileFailed ? "Couldn't load your saved settings. Close this and try again in a moment." : "Loading your settings…"}</p>${msg}`;
    return;
  }
  const p = state.profile;
  const n = state.prefs.cinemas.length;
  const filters = [n ? `${n} chosen cinema${n > 1 ? "s" : ""}` : "all cinemas", state.prefs.hideDubbed ? "original language only" : "", state.prefs.englishSubs ? "English subtitles only (Oslo)" : "", state.prefs.audioEnNo ? "English or Norwegian audio only (Costa del Sol)" : ""].filter(Boolean).join(", ");
  body.innerHTML = `<h2 id="accountTitle">Your account</h2>
    <div class="who">${esc(state.user.email)}</div>
    <label class="opt"><input type="checkbox" id="optSub"${p.subscribed ? " checked" : ""}>
      <span>Email me about new films<small>Only sent when there is something new to tell you, and never more than once a day. Uses your filters: ${esc(filters)}.</small></span></label>
    <div class="opt regionsopt"><span></span><span>How often<small>Daily: at most one email a day, sent in the morning (after 9:00 local time), and none on days with nothing new. Weekly: one email on Fridays with everything new that week, if there is any.</small>
      <span class="row">${[["daily", "Daily, if there's news"], ["weekly", "Weekly, on Fridays"]].map(([v, l]) => `<label class="toggle"><input type="radio" name="freq" value="${v}"${(state.prefs.frequency || "daily") === v ? " checked" : ""}> ${l}</label>`).join("")}</span></span></div>
    <div class="opt regionsopt"><span></span><span>Regions<small>One email per region, at 9:00 local time.</small>
      <span class="row">${REGIONS.map((r) => `<label class="toggle"><input type="checkbox" data-optregion="${r.key}"${(state.prefs.regions || ["oslo"]).includes(r.key) ? " checked" : ""}> ${r.name}</label>`).join("")}</span></span></div>
    <label class="opt"><input type="checkbox" id="optAnn"${state.prefs.announcements ? " checked" : ""}>
      <span>Include newly announced films<small>Films that just got a Norwegian release date or showings, before tickets are on sale.</small></span></label>
    <label class="opt"><input type="checkbox" id="optWatch"${state.prefs.watchlistAlways ? " checked" : ""}>
      <span>Always include watchlist films<small>Email me when a film on my watchlist goes on sale, even if it doesn't match my filters.</small></span></label>
    <div class="row" style="margin-top:12px"><button class="btn ghost" id="signOut">Sign out</button></div>${msg}`;
}

function setUser(session) {
  state.user = session?.user ? { id: session.user.id, email: session.user.email } : null;
  // Signed out: a filled "Sign in" button. Signed in: a round avatar with the email's first letter.
  const ab = $("accountBtn");
  ab.textContent = state.user ? state.user.email[0].toUpperCase() : "Sign in";
  ab.classList.toggle("primary", !state.user);
  ab.classList.toggle("avatar", !!state.user);
  ab.setAttribute("aria-label", state.user ? `Account (${state.user.email})` : "Sign in");
  if (!state.user) state.profile = null;
  renderAccount();
}

if (sb) {
  $("accountBtn").hidden = false;
  $("accountBtn").addEventListener("click", () => { renderAccount(); $("account").showModal(); });
  const sendCode = async (email) => {
    const { error } = await sb.auth.signInWithOtp({ email, options: { shouldCreateUser: true } });
    if (error) return error.status === 429 || /rate|seconds/i.test(error.message)
      ? "Wait a minute before asking for another code." : `Couldn't send a code: ${error.message}`;
    state.pendingEmail = email;
    try { sessionStorage.setItem("kino:pendingEmail", email); } catch {}
    return "";
  };
  $("account").addEventListener("submit", async (e) => {
    if (e.target.id !== "signinForm" && e.target.id !== "codeForm") return;  // the × button's own form must be allowed to close the dialog
    e.preventDefault();
    const btn = e.target.querySelector("button[type=submit]");
    if (e.target.id === "signinForm") {
      btn.disabled = true;
      const err = await sendCode($("email").value.trim());
      btn.disabled = false;
      renderAccount(err, !!err);
    } else if (e.target.id === "codeForm") {
      btn.disabled = true;
      const { error } = await sb.auth.verifyOtp({ email: state.pendingEmail, token: $("code").value.trim(), type: "email" });
      btn.disabled = false;
      if (error) { renderAccount("That code didn't work. Check it, or send a new one (codes expire after an hour).", true); return; }
      state.pendingEmail = "";
      try { sessionStorage.removeItem("kino:pendingEmail"); } catch {}
      renderAccount("Signed in. Your watchlist and filters now sync across devices.");
    }
  });
  $("account").addEventListener("change", async (e) => {
    if (e.target.id === "optSub") {
      state.profile = { ...state.profile, subscribed: e.target.checked };
      await pushProfile({ subscribed: e.target.checked });
      renderAccount(e.target.checked ? "Subscribed. You'll get an email in the morning on days when there is something new." : "Unsubscribed.");
    } else if (e.target.name === "freq") {
      state.prefs.frequency = e.target.value; savePrefs(); renderAccount();
    } else if (e.target.dataset.optregion) {
      const k = e.target.dataset.optregion, cur = new Set(state.prefs.regions || ["oslo"]);
      e.target.checked ? cur.add(k) : cur.delete(k);
      state.prefs.regions = REGIONS.map((r) => r.key).filter((x) => cur.has(x));
      savePrefs(); renderAccount();
    } else if (e.target.id === "optAnn") {
      state.prefs.announcements = e.target.checked; savePrefs(); renderAccount();
    } else if (e.target.id === "optWatch") {
      state.prefs.watchlistAlways = e.target.checked; savePrefs(); renderAccount();
    }
  });
  $("account").addEventListener("click", async (e) => {
    if (e.target.id === "signOut") { await sb.auth.signOut(); renderAccount("Signed out."); }
    else if (e.target.id === "resendCode") {
      const err = await sendCode(state.pendingEmail);
      renderAccount(err || `New code sent to ${state.pendingEmail}.`, !!err);
    } else if (e.target.id === "otherEmail") {
      state.pendingEmail = "";
      try { sessionStorage.removeItem("kino:pendingEmail"); } catch {}
      renderAccount();
    }
  });
  sb.auth.onAuthStateChange((event, session) => {
    const had = state.user?.id;
    state.authReady = true;
    setUser(session);
    if (state.user && state.user.id !== had) setTimeout(loadProfile, 0);
    settlePendingWatch();
  });
}

async function handleUnsubscribe() {
  const token = new URLSearchParams(location.search).get("unsubscribe");
  if (!token) return;
  history.replaceState(null, "", location.pathname + location.hash);
  const banner = $("banner");
  banner.hidden = false;
  if (!sb) { banner.textContent = "Unsubscribing isn't available right now. Try the link again later."; return; }
  const { data, error } = await sb.rpc("unsubscribe", { token });
  banner.textContent = error ? `Couldn't unsubscribe: ${error.message}` : data ? "You're unsubscribed from the daily email. You can turn it back on under Account." : "That unsubscribe link wasn't recognised. Sign in and turn the email off under Account.";
}

// ---------------------------------------------------------------- boot
async function loadRegion(key) {
  const reg = REGIONS.find((r) => r.key === key) || REGIONS[0];
  state.region = reg.key;
  local.set("region", reg.key);
  setClock(reg.tz);
  const url = new URL(location.href);
  if (reg.key === "oslo") url.searchParams.delete("r"); else url.searchParams.set("r", reg.key);
  history.replaceState(null, "", url.pathname + url.search + url.hash);
  $("sub").textContent = `Loading ${reg.name}…`;
  try {
    const r = await fetch(`${window.KINO_BASE || ""}data/${reg.file}`, { cache: "no-cache" });
    state.data = await r.json();
    // How far ahead each cinema has published: a film that stops well before this is really ending.
    state.horizon = {};
    for (const f of state.data.films) for (const s of f.shows) if (s.t > (state.horizon[s.cinema] || "")) state.horizon[s.cinema] = s.t;
    // A film hidden while it was only announced: once it has a sale date, remember it so a later return can show it again.
    let adopted = false;
    for (const f of state.data.films) for (const i of f.ids) if (state.prefs.hidden?.[i] === "" && f.onSaleSince) { state.prefs.hidden[i] = f.onSaleSince; adopted = true; }
    if (adopted) savePrefs();
  } catch (e) {
    $("sub").textContent = `Couldn't load the ${reg.name} programme. Reload to try again.`;
    return;
  }
  const g = state.data.generated;
  $("sub").textContent = `${state.data.location} · ${state.data.sources || "Filmweb + Cinemateket"} · updated ${dayLabel(g, { short: true })} ${hhmm(g)}`;
  $("foot").innerHTML = reg.key === "costadelsol"
    ? `Showtimes from <a href="https://www.carteleracines.es" target="_blank" rel="noopener">CarteleraCines.es</a>, which collects them from the cinemas' ticketing systems, refreshed twice a day and covering about two weeks ahead. Some ticket links may pay CarteleraCines a commission; the price doesn't change. Times are Spanish time. Showings in the original language are marked "Original language" (with Spanish subtitles). Some posters come from TMDB; this product uses the TMDB API but is not endorsed or certified by TMDB.`
    : reg.key === "oslo"
    ? `Data from <a href="https://www.filmweb.no" target="_blank" rel="noopener">Filmweb</a> and <a href="https://www.cinemateket.no" target="_blank" rel="noopener">Cinemateket</a>, refreshed several times a day. Tickets are bought on the cinemas' own sites. Some posters come from TMDB; this product uses the TMDB API but is not endorsed or certified by TMDB.`
    : `Data from <a href="https://www.landmarkcinemas.com" target="_blank" rel="noopener">Landmark Cinemas</a>, <a href="https://www.cinemaclock.com" target="_blank" rel="noopener">CinemaClock</a> and the <a href="https://evanstheatre.ca" target="_blank" rel="noopener">Evans Theatre</a>. Small theatres sell tickets at the door. Times are Manitoba time. Some posters come from TMDB; this product uses the TMDB API but is not endorsed or certified by TMDB.`;
  document.title = `Cinecrab · ${reg.name}`;
  render();
  route();
}

(async function boot() {
  await loadRegion(state.region);
  handleUnsubscribe();
})();
