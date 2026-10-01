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
const VIEWS = [["when", "Playing when"], ["sale", "Newly on sale"], ["ann", "Newly announced"]];
const TIX = [["all", "All"], ["on", "On sale"], ["off", "Not on sale yet"]];
// Order of films inside each section. "date" means the view's natural order (by time, or newest first).
const WITHIN = [["date", "By date"], ["rating", "Best rated first"], ["fewest", "Fewest showings first"]];
const DEFAULT_PREFS = { cinemas: [], hideDubbed: false, englishSubs: false, watchlistAlways: true, announcements: true, regions: ["oslo"], hideKinds: [] };
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
  within: local.get("within", "date"),
  onlyWatch: false,
  q: "",
  prefs: { ...DEFAULT_PREFS, ...local.get("prefs", {}) },
  watchlist: new Set(local.get("watchlist", [])),
  collapsed: new Set(local.get("collapsed", [])), // "view:sectionKey" of collapsed sections
  showAll: false,       // film sheet: show showings hidden by filters
  sheetCinemas: new Set(), // film sheet: cinema tags clicked to narrow its showings
  user: null,           // { id, email }
  pendingEmail: (() => { try { return sessionStorage.getItem("kino:pendingEmail") || ""; } catch { return ""; } })(),
  profile: null,        // { subscribed, ... }
};

// ---------------------------------------------------------------- filters (keep in step with scraper/digest.py: show_ok)
// "My cinemas" is one list across regions; only the ones in the region being viewed apply.
const myCinemas = () => state.prefs.cinemas.filter((c) => state.data.cinemas.includes(c));
function showMatches(s, now) {
  if (s.t < now) return false;
  const p = state.prefs, mine = myCinemas();
  if (mine.length && !mine.includes(s.cinema)) return false;
  if (state.region === "oslo" && p.hideDubbed && s.dub) return false;
  if (state.region === "oslo" && p.englishSubs && !s.en) return false;
  return true;
}
const isWatched = (f) => f.ids.some((id) => state.watchlist.has(id));
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

// Section labels for the "When it's playing" view.
function whenSection(row, today) {
  if (!row.start) return { key: "9999", label: "Date not set" };
  // Playing now = already started and on again within a week. Something that premiered long ago with
  // one special screening in November belongs under November, like the one-off it is.
  if (row.shows.length && row.start <= today && row.shows[0].t.slice(0, 10) <= addDays(today, 6))
    return { key: "0000", label: "Playing now" };
  if (row.shows.length && row.start <= today) row = { ...row, start: row.shows[0].t.slice(0, 10) };
  const dow = asDate(today).getUTCDay();                    // 0 = Sunday
  const weekEnd = addDays(today, (7 - dow) % 7);            // this coming Sunday
  if (row.start <= weekEnd) return { key: "0001", label: "This week" };
  if (row.start <= addDays(weekEnd, 7)) return { key: "0002", label: "Next week" };
  const [y, m] = row.start.split("-").map(Number);
  const [ty, tm] = today.split("-").map(Number);
  const label = y === ty && m === tm ? `Later in ${MONTH_NAMES[m - 1]}` : `${MONTH_NAMES[m - 1]}${y !== ty ? " " + y : ""}`;
  return { key: row.start.slice(0, 7), label };
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

function buildRows() {
  const now = localNow(), today = now.slice(0, 10), q = state.q.trim().toLowerCase();
  const b = state.data.baseline ? asDate(state.data.baseline) : null;
  const started = b ? `Before ${b.getUTCDate()} ${MONTHS[b.getUTCMonth()]}` : "Before tracking began";
  const rows = [];
  for (const f of state.data.films) {
    if (!textMatch(f, q) || !kindShown(f)) continue;
    if (state.onlyWatch && !isWatched(f)) continue;
    const shows = f.shows.filter((s) => showMatches(s, now));
    if (f.shows.length && !shows.length) continue; // has showings, none match the filters
    const bookable = shows.filter((s) => s.ticket);
    const onSale = f.status === "on_sale" && bookable.length > 0;
    const row = { f, shows: onSale ? bookable : shows, onSale, start: startDay(f, shows) };
    if (state.view === "when") {
      if (state.tix === "on" && !onSale) continue;
      if (state.tix === "off" && onSale) continue;
      if (!row.start && !q) continue; // undated films only turn up when you search for them
      row.sec = whenSection(row, today);
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
    if (state.within === "rating") return rating(b) - rating(a) || t0(a).localeCompare(t0(b)) || byTitle(a, b);
    if (state.within === "fewest") { // one-off screenings first; films with no showings yet (just a premiere) last
      const n = (r) => r.shows.length || Infinity;
      return n(a) - n(b) || t0(a).localeCompare(t0(b)) || byTitle(a, b);
    }
    if (state.view !== "when") return (b.since || "").localeCompare(a.since || "") || t0(a).localeCompare(t0(b)) || byTitle(a, b);
    if (a.sec.key === "0000") // playing now: newly on sale, then newest premieres, then repertory by time
      return (b.f.onSaleSince || "").localeCompare(a.f.onSaleSince || "")
        || (b.f.premiere || "").localeCompare(a.f.premiere || "") || t0(a).localeCompare(t0(b)) || byTitle(a, b);
    return t0(a).localeCompare(t0(b)) || byTitle(a, b);
  });
  return rows;
}

// ---------------------------------------------------------------- render: controls + filter sidebar
function activeFilters() {
  return (state.view === "when" && state.tix !== "all" ? 1 : 0)
    + ((state.prefs.hideKinds || []).length ? 1 : 0) + (myCinemas().length ? 1 : 0)
    + (state.region === "oslo" ? (state.prefs.hideDubbed ? 1 : 0) + (state.prefs.englishSubs ? 1 : 0) : 0);
}

function renderControls() {
  $("view").innerHTML = VIEWS.map(([v, l]) => `<button class="chip${v === state.view ? " on" : ""}" data-view="${v}" aria-pressed="${v === state.view}">${l}</button>`).join("");
  $("region").innerHTML = REGIONS.map((r) => `<option value="${r.key}"${r.key === state.region ? " selected" : ""}>${r.name}</option>`).join("");
  $("watchBtn").setAttribute("aria-pressed", state.onlyWatch);
  $("watchBtn").textContent = `★ Watchlist${state.watchlist.size ? " " + state.watchlist.size : ""}`;
  $("watchBtn").setAttribute("aria-label", `Watchlist${state.watchlist.size ? `, ${state.watchlist.size} films` : ""}${state.onlyWatch ? ", showing only these" : ""}`);
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
  const ck = (attrs, checked, label, n) =>
    `<label class="ck"><input type="checkbox" ${attrs}${checked ? " checked" : ""}><span>${label}</span>${n != null ? `<span class="n">${n}</span>` : ""}</label>`;
  const focused = document.activeElement?.closest?.("#filtersBody") ? document.activeElement.dataset.f + "|" + (document.activeElement.value || "") : "";
  $("filtersBody").innerHTML = `
    <fieldset class="fg"><legend>Sections</legend>
      <div class="row"><button class="btn ghost small" data-collapseall="1">Collapse all</button>
      <button class="btn ghost small" data-collapseall="0">Expand all</button></div>
    </fieldset>
    <fieldset class="fg"><legend>Order within sections</legend>
      ${WITHIN.map(([v, l]) => `<label class="ck"><input type="radio" name="within" data-f="within" value="${v}"${state.within === v ? " checked" : ""}><span>${v === "date" && state.view !== "when" ? "Newest first" : l}</span></label>`).join("")}
    </fieldset>
    <fieldset class="fg"${state.view === "when" ? "" : " hidden"}><legend>Tickets</legend>
      ${TIX.map(([v, l]) => `<label class="ck"><input type="radio" name="tix" data-f="tix" value="${v}"${state.tix === v ? " checked" : ""}><span>${l}</span></label>`).join("")}
    </fieldset>
    <fieldset class="fg"><legend>Types</legend><div class="cols">
      ${KINDS.map(([k, l]) => ck(`data-f="kind" value="${k}"`, !hide.has(k), l, kindCounts[k] || 0)).join("")}
    </div></fieldset>
    <fieldset class="fg"><legend>Cinemas <span class="hint">${mine.length ? `${mine.length} chosen` : "none ticked = all"}</span></legend>
      <div class="cols">${state.data.cinemas.map((c) => ck(`data-f="cinema" value="${esc(c)}"`, mine.includes(c), esc(c), cinemaCounts[c] || 0)).join("")}</div>
      ${mine.length ? `<button class="linkbtn" data-f="cinemas-clear">Show all cinemas</button>` : ""}
    </fieldset>
    <fieldset class="fg"${state.region === "oslo" ? "" : " hidden"}><legend>Language</legend>
      ${ck('data-f="dub"', state.prefs.hideDubbed, "Hide Norwegian dubs")}
      ${ck('data-f="en"', state.prefs.englishSubs, "English subtitles only")}
    </fieldset>`;
  $("filtersReset").hidden = !activeFilters();
  if (focused) { // keep keyboard focus on the control that was just changed
    const [f, v] = focused.split("|");
    [...$("filtersBody").querySelectorAll(`[data-f="${f}"]`)].find((el) => (el.value || "") === v)?.focus();
  }
}

// ---------------------------------------------------------------- render: grid
function cardMeta(row) {
  const { f, shows, onSale, start } = row;
  const today = localNow().slice(0, 10);
  if (onSale) {
    const cinemas = [...new Set(shows.map((s) => s.cinema))];
    const where = cinemas.length > 2 ? `${cinemas.slice(0, 2).join(", ")} +${cinemas.length - 2}` : cinemas.join(", ");
    const first = shows[0];
    const cls = first.t.slice(0, 10) === today ? ' class="today"' : "";
    const lead = start > today ? "Starts " : "";
    return `<b>${esc(where)}</b><br><span${cls}>${lead}${dayLabel(first.t, { short: true })} ${hhmm(first.t)}</span> · ${shows.length} show${shows.length > 1 ? "s" : ""}`;
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
    ? `<img loading="lazy" src="${esc(f.poster)}" alt="" onerror="this.remove()">`
    : ""}<div class="ph"${f.poster ? ' aria-hidden="true" style="z-index:-1"' : ""}>${esc(titleOf(f))}</div></div>`;
}

function cardHtml(row) {
  const { f } = row;
  const flag = (isNew(f) ? `<span class="flag">New</span>` : "") + (KIND_BADGE[f.kind] ? `<span class="kind">${KIND_BADGE[f.kind]}</span>` : "");
  const on = isWatched(f);
  return `<li class="card${row.onSale ? "" : " nosale"}">
    <a href="#film/${esc(f.id)}">${posterHtml(f).replace('<div class="poster">', `<div class="poster">${flag}`)}
      <h3>${esc(titleOf(f))}</h3><div class="m">${cardMeta(row)}</div></a>
    <button class="star${on ? " on" : ""}" data-star="${esc(f.id)}" aria-pressed="${on}" aria-label="${on ? "Remove from" : "Add to"} watchlist" title="${on ? "On your watchlist" : "Add to watchlist"}">${on ? "★" : "☆"}</button>
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
  else if (state.onlyWatch && !state.watchlist.size) empty = "Your watchlist is empty. Tap ☆ on any poster to add it; it'll be highlighted when tickets go on sale.";
  state.sections = sections;
  $("filtersShow").textContent = `Show ${rows.length} film${rows.length === 1 ? "" : "s"}`;
  $("grid").innerHTML = sections.length ? sections.map((s, i) => {
    const shut = isCollapsed(s);
    const peek = shut ? `<span class="peek">${esc(s.rows.slice(0, 4).map((r) => titleOf(r.f)).join(" · "))}${s.rows.length > 4 ? " …" : ""}</span>` : "";
    return `<section class="sec${shut ? " shut" : ""}" id="sec-${i}">
      <h2 class="sech"><button data-sec="${i}" aria-expanded="${!shut}" aria-controls="secgrid-${i}">
        <span class="lbl">${esc(s.label)}</span> <span class="n">${s.rows.length}</span>${peek}<span class="chev" aria-hidden="true">${shut ? "▸" : "▾"}</span>
      </button></h2>
      ${shut ? "" : `<ul class="grid" id="secgrid-${i}">${s.rows.map(cardHtml).join("")}</ul>`}</section>`;
  }).join("") : `<p class="empty">${empty}</p>`;
}

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
  if (lb) out.push(`<a class="ext lb" href="${esc(lb)}" target="_blank" rel="noopener">Letterboxd${x.lbRating ? ` <b>★ ${x.lbRating.toFixed(1)}</b>` : ""}</a>`);
  else out.push(`<a class="ext lb" href="https://letterboxd.com/search/films/${encodeURIComponent((f.alt || f.title) + (f.year ? " " + f.year : ""))}/" target="_blank" rel="noopener">Search Letterboxd</a>`);
  if (x.imdb) out.push(`<a class="ext" href="https://www.imdb.com/title/${esc(x.imdb)}/" target="_blank" rel="noopener">IMDb</a>`);
  if (x.rt) out.push(`<a class="ext" href="https://www.rottentomatoes.com/${esc(x.rt)}" target="_blank" rel="noopener">Rotten Tomatoes</a>`);
  if (x.mc) out.push(`<a class="ext" href="https://www.metacritic.com/${esc(x.mc)}/" target="_blank" rel="noopener">Metacritic</a>`);
  return `<div class="exts">${out.join("")}</div>`;
}

function openFilm(id) {
  const f = findFilm(id);
  if (!f) return;
  const dlg = $("film"), now = localNow(), today = now.slice(0, 10);
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
    <div class="day${day === today ? " today" : ""}"><h4>${dayLabel(day + "T00:00")}<small>${day.split("-").reverse().join(".")}</small></h4>
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
      <div class="badges">${KIND_BADGE[f.kind] ? `<span class="badge line">${KIND_BADGE[f.kind]}</span>` : ""}${isNew(f) ? `<span class="badge new">${f.status === "on_sale" ? "New on sale" : "Newly announced"}</span>` : ""}${cinemas.map(([c, n]) => `<button class="badge pick${state.sheetCinemas.has(c) ? " on" : ""}" data-sheetcinema="${esc(c)}" aria-pressed="${state.sheetCinemas.has(c)}" title="Show only ${esc(c)}">${esc(c)} · ${n}</button>`).join("")}${f.series.map((s) => `<span class="badge line">${esc(s)}</span>`).join("")}</div>
      ${extLinks(f)}
      <div class="actions"><button class="btn${on ? "" : " accent"}" data-star="${esc(f.id)}">${on ? "★ On your watchlist" : "☆ Add to watchlist"}</button>
      ${f.links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join("")}</div>
    </div></div>
    <div class="days">${days}${hiddenNote}${empty}</div>`;
  if (!dlg.open) dlg.showModal();
}

function route() {
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
  local.set("collapsed", [...state.collapsed]);
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-sec],[data-collapseall],[data-star],[data-showall],[data-sheetcinema],button[data-f]");
  if (!t) return;
  if (t.dataset.sec) { // collapse / expand; keep the header in view if it was pinned
    const i = +t.dataset.sec, s = state.sections[i], key = `${state.view}:${s.key}`;
    const pinned = $("sec-" + i).getBoundingClientRect().top < 0;
    setCollapsed(key, !state.collapsed.has(key));
    renderGrid();
    if (pinned) $("sec-" + i)?.scrollIntoView({ block: "start" });
    $("grid").querySelector(`[data-sec="${i}"]`)?.focus();
    return;
  }
  if (t.dataset.collapseall) {
    for (const s of state.sections) setCollapsed(`${state.view}:${s.key}`, t.dataset.collapseall === "1");
    renderGrid(); setFiltersOpen(false); window.scrollTo({ top: 0 }); return;
  }
  if (t.dataset.f === "reset") {
    state.tix = "all";
    state.prefs.hideKinds = []; state.prefs.hideDubbed = false; state.prefs.englishSubs = false;
    state.prefs.cinemas = state.prefs.cinemas.filter((c) => !state.data.cinemas.includes(c));
    local.set("tix", "all"); savePrefs(); render(); return;
  }
  if (t.dataset.f === "cinemas-clear") {
    state.prefs.cinemas = state.prefs.cinemas.filter((c) => !state.data.cinemas.includes(c));
    savePrefs(); render(); return;
  }
  if (t.dataset.star) {
    e.preventDefault();
    const f = findFilm(t.dataset.star);
    if (isWatched(f)) f.ids.forEach((id) => state.watchlist.delete(id)); else state.watchlist.add(f.id);
    savePrefs();
    if ($("film").open) openFilm(f.id);
  } else if (t.dataset.sheetcinema) {
    const c = t.dataset.sheetcinema;
    state.sheetCinemas.has(c) ? state.sheetCinemas.delete(c) : state.sheetCinemas.add(c);
    openFilm(location.hash.slice(6)); return;
  } else if (t.dataset.showall) { state.showAll = t.dataset.showall === "1"; openFilm(location.hash.slice(6)); return; }
  render();
});

// Filter sidebar: checkboxes and radios.
$("filtersBody").addEventListener("change", (e) => {
  const el = e.target, f = el.dataset.f;
  if (f === "within") { state.within = el.value; local.set("within", state.within); render(); return; }
  if (f === "tix") { state.tix = el.value; local.set("tix", state.tix); }
  else if (f === "kind") {
    const cur = new Set(state.prefs.hideKinds || []);
    el.checked ? cur.delete(el.value) : cur.add(el.value);
    state.prefs.hideKinds = KINDS.map(([x]) => x).filter((x) => cur.has(x));
  } else if (f === "cinema") {
    const list = state.prefs.cinemas;
    state.prefs.cinemas = el.checked ? [...new Set([...list, el.value])] : list.filter((x) => x !== el.value);
  } else if (f === "dub") state.prefs.hideDubbed = el.checked;
  else if (f === "en") state.prefs.englishSubs = el.checked;
  if (f !== "tix") savePrefs();
  render();
});
const setFiltersOpen = (open) => {
  document.body.classList.toggle("filters-open", open);
  $("filtersBtn").setAttribute("aria-expanded", open);
  if (open) $("filtersDone").focus();
};
$("filtersBtn").addEventListener("click", () => setFiltersOpen(!document.body.classList.contains("filters-open")));
$("filtersDone").addEventListener("click", () => { setFiltersOpen(false); $("filtersBtn").focus(); });
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
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("filters-open")) setFiltersOpen(false); });
$("q").addEventListener("input", (e) => { state.q = e.target.value; renderGrid(); });
$("watchBtn").addEventListener("click", () => { state.onlyWatch = !state.onlyWatch; render(); window.scrollTo({ top: 0 }); });
$("region").addEventListener("change", (e) => { if (e.target.value !== state.region) loadRegion(e.target.value); });
$("view").addEventListener("click", (e) => {
  const b = e.target.closest("[data-view]");
  if (!b || b.dataset.view === state.view) return;
  state.view = b.dataset.view; local.set("view", state.view); render(); window.scrollTo({ top: 0 });
});

// Section headers stick just below the controls bar; keep that offset in a CSS variable.
const syncStickTop = () => {
  const c = $("controls"), sticky = getComputedStyle(c).position === "sticky";
  document.documentElement.style.setProperty("--stick-top", sticky ? c.offsetHeight + "px" : "0px");
};
new ResizeObserver(syncStickTop).observe($("controls"));
window.addEventListener("resize", syncStickTop);
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- accounts (Supabase magic link)
const cfg = window.KINO_CONFIG || {};
const sb = cfg.supabaseUrl && cfg.supabaseKey && window.supabase ? window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseKey) : null;
let syncTimer = null;

function queueSync() {
  if (!sb || !state.user) return;
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
  const { data, error } = await sb.from("profiles").select("subscribed,prefs,watchlist").eq("id", state.user.id).maybeSingle();
  if (error || !data) { console.error("Loading profile failed:", error?.message); return; }
  state.profile = data;
  // Merge: the account's settings win if it has any; local watchlist items are added to the account.
  const remote = data.prefs || {};
  if (Object.keys(remote).length) state.prefs = { ...DEFAULT_PREFS, ...remote };
  const merged = new Set([...(data.watchlist || []), ...state.watchlist]);
  const changed = merged.size !== (data.watchlist || []).length || !Object.keys(remote).length;
  state.watchlist = merged;
  local.set("prefs", state.prefs); local.set("watchlist", [...merged]);
  if (changed) await pushProfile();
  render();
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
    body.innerHTML = `<h2 id="accountTitle">Sign in or sign up</h2>
      <p>Enter your email and we'll send you a one-time code. No password. Signing in syncs your watchlist and filters across devices, and lets you get the daily email of films newly on sale.</p>
      <form class="signin" id="signinForm"><input type="email" id="email" required placeholder="you@example.com" autocomplete="email">
      <button class="btn accent" type="submit">Send code</button></form>${msg}`;
    return;
  }
  const p = state.profile || {};
  const n = state.prefs.cinemas.length;
  const filters = [n ? `${n} chosen cinema${n > 1 ? "s" : ""}` : "all cinemas", state.prefs.hideDubbed ? "no Norwegian dubs" : "", state.prefs.englishSubs ? "English subtitles only" : ""].filter(Boolean).join(", ");
  body.innerHTML = `<h2 id="accountTitle">Your account</h2>
    <div class="who">${esc(state.user.email)}</div>
    <label class="opt"><input type="checkbox" id="optSub"${p.subscribed ? " checked" : ""}>
      <span>Daily email at 9:00<small>Only sent on days with something new. Uses your filters: ${esc(filters)}.</small></span></label>
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
  $("accountBtn").textContent = state.user ? "Account" : "Sign in";
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
      renderAccount(e.target.checked ? "Subscribed. You'll get an email after 9:00 on days with new films." : "Unsubscribed.");
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
    setUser(session);
    if (state.user && state.user.id !== had) setTimeout(loadProfile, 0);
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
    const r = await fetch(`data/${reg.file}`, { cache: "no-cache" });
    state.data = await r.json();
  } catch (e) {
    $("sub").textContent = `Couldn't load the ${reg.name} programme. Reload to try again.`;
    return;
  }
  const g = state.data.generated;
  $("sub").textContent = `${state.data.location} · ${state.data.sources || "Filmweb + Cinemateket"} · updated ${dayLabel(g, { short: true })} ${hhmm(g)}`;
  $("foot").innerHTML = reg.key === "oslo"
    ? `Data from <a href="https://www.filmweb.no" target="_blank" rel="noopener">Filmweb</a> and <a href="https://www.cinemateket.no" target="_blank" rel="noopener">Cinemateket</a>, refreshed several times a day. Tickets are bought on the cinemas' own sites.`
    : `Data from <a href="https://www.landmarkcinemas.com" target="_blank" rel="noopener">Landmark Cinemas</a>, <a href="https://www.cinemaclock.com" target="_blank" rel="noopener">CinemaClock</a> and the <a href="https://evanstheatre.ca" target="_blank" rel="noopener">Evans Theatre</a>. Small theatres sell tickets at the door. Times are Manitoba time.`;
  document.title = `Cinecrab · ${reg.name}`;
  render();
  route();
}

(async function boot() {
  await loadRegion(state.region);
  handleUnsubscribe();
})();
