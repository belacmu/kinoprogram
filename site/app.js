"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const local = {
  get(k, d) { try { const v = localStorage.getItem("kino:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("kino:" + k, JSON.stringify(v)); } catch {} },
};

// ---------------------------------------------------------------- time (all data is Oslo local time)
const osloFmt = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Oslo", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const osloNow = () => osloFmt.format(new Date()).replace(" ", "T"); // "2026-10-01T10:40"
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const asDate = (t) => { const [y, m, d] = t.slice(0, 10).split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const addDays = (day, n) => { const d = asDate(day); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
function dayLabel(t, { short = false } = {}) {
  const day = t.slice(0, 10), today = osloNow().slice(0, 10);
  if (day === today) return "Today";
  if (day === addDays(today, 1)) return "Tomorrow";
  const d = asDate(day);
  const base = `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  return short || day.slice(0, 4) === today.slice(0, 4) ? base : `${base} ${day.slice(0, 4)}`;
}
const hhmm = (t) => t.slice(11, 16);

// ---------------------------------------------------------------- state
const SORTS = {
  onsale: [["newest", "Newest in cinemas"], ["next", "Next showing"], ["az", "A–Z"], ["most", "Most showings"], ["last", "Last chance"]],
  coming: [["soonest", "Soonest first"], ["announced", "Newly announced"], ["az", "A–Z"]],
  watch: [["next", "Soonest first"], ["az", "A–Z"]],
};
const WHEN = [["all", "Any time"], ["today", "Today"], ["7", "7 days"], ["14", "2 weeks"], ["30", "30 days"]];
const DEFAULT_PREFS = { cinemas: [], hideDubbed: false, englishSubs: false, watchlistAlways: true, announcements: true };

const state = {
  data: null,
  tab: "onsale",
  q: "",
  sort: local.get("sort", { onsale: "newest", coming: "soonest", watch: "next" }),
  when: local.get("when", "all"),
  prefs: { ...DEFAULT_PREFS, ...local.get("prefs", {}) },
  watchlist: new Set(local.get("watchlist", [])),
  cinemasOpen: false,
  showAll: false,       // film sheet: show showings hidden by filters
  user: null,           // { id, email }
  profile: null,        // { subscribed, ... }
};

// ---------------------------------------------------------------- filters (keep in step with scraper/digest.py: show_ok)
function showMatches(s, now) {
  if (s.t < now) return false;
  const p = state.prefs;
  if (p.cinemas.length && !p.cinemas.includes(s.cinema)) return false;
  if (p.hideDubbed && s.dub) return false;
  if (p.englishSubs && !s.en) return false;
  return true;
}
function windowEnd() {
  if (state.when === "all") return "9999";
  return addDays(osloNow().slice(0, 10), state.when === "today" ? 1 : Number(state.when));
}
const isWatched = (f) => f.ids.some((id) => state.watchlist.has(id));
// "New" = went on sale (or, for announced films, first got a date) in the last 7 days, after tracking began.
function isRecent(since) {
  if (!since || since <= state.data.baseline) return false;
  return since.slice(0, 10) >= addDays(osloNow().slice(0, 10), -7);
}
const isNew = (f) => isRecent(f.status === "on_sale" ? f.onSaleSince : f.announcedSince);
function textMatch(f, q) {
  return !q || `${f.title} ${f.alt} ${f.director} ${f.series.join(" ")}`.toLowerCase().includes(q);
}

function rowsFor(tab) {
  const now = osloNow(), end = windowEnd(), q = state.q.trim().toLowerCase();
  const rows = [];
  for (const f of state.data.films) {
    if (!textMatch(f, q)) continue;
    const shows = f.shows.filter((s) => showMatches(s, now) && s.t < end);
    const bookable = shows.filter((s) => s.ticket);
    if (tab === "onsale") {
      if (f.status === "on_sale" && bookable.length) rows.push({ f, shows: bookable });
    } else if (tab === "coming") {
      if (f.status !== "announced") continue;
      if (f.shows.length && !shows.length) continue; // has showings, none match filters
      rows.push({ f, shows });
    } else if (isWatched(f)) {
      rows.push({ f, shows: f.status === "on_sale" ? bookable : shows });
    }
  }
  return rows;
}

const startOf = (r) => r.shows[0]?.t || (r.f.premiere ? r.f.premiere + "T00:00" : "9999");
const byTitle = (a, b) => a.f.title.localeCompare(b.f.title, "nb");
const SORTERS = {
  newest: (a, b) => (b.f.onSaleSince || "").localeCompare(a.f.onSaleSince || "") || startOf(a).localeCompare(startOf(b)),
  next: (a, b) => startOf(a).localeCompare(startOf(b)) || byTitle(a, b),
  soonest: (a, b) => startOf(a).localeCompare(startOf(b)) || byTitle(a, b),
  announced: (a, b) => (b.f.announcedSince || "").localeCompare(a.f.announcedSince || "") || startOf(a).localeCompare(startOf(b)),
  az: byTitle,
  most: (a, b) => b.shows.length - a.shows.length || byTitle(a, b),
  last: (a, b) => a.shows.at(-1).t.localeCompare(b.shows.at(-1).t),
};

// ---------------------------------------------------------------- render: controls
function renderControls() {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === state.tab));
  const sort = state.sort[state.tab];
  $("sort").innerHTML = SORTS[state.tab].map(([v, l]) => `<option value="${v}"${v === sort ? " selected" : ""}>Sort: ${l}</option>`).join("");
  $("when").hidden = state.tab === "coming";
  $("when").innerHTML = WHEN.map(([v, l]) => `<button class="chip${state.when === v ? " on" : ""}" data-when="${v}">${l}</button>`).join("");
  $("dubBtn").setAttribute("aria-pressed", state.prefs.hideDubbed);
  $("enBtn").setAttribute("aria-pressed", state.prefs.englishSubs);
  const n = state.prefs.cinemas.length;
  $("cinemaBtn").textContent = (n ? `${n} cinema${n > 1 ? "s" : ""}` : "All cinemas") + (state.cinemasOpen ? " ▴" : " ▾");
  $("cinemaBtn").setAttribute("aria-pressed", n > 0);
  $("cinemaBtn").setAttribute("aria-expanded", state.cinemasOpen);
  $("cinemas").hidden = !state.cinemasOpen;
  if (state.cinemasOpen) {
    const now = osloNow(), counts = {};
    for (const f of state.data.films) for (const s of f.shows) if (s.ticket && s.t >= now) counts[s.cinema] = (counts[s.cinema] || 0) + 1;
    $("cinemas").innerHTML = state.data.cinemas.map((c) =>
      `<button class="chip${state.prefs.cinemas.includes(c) ? " on" : ""}" data-cinema="${esc(c)}">${esc(c)}<span class="n">${counts[c] || 0}</span></button>`).join("")
      + `<button class="linkbtn" data-cinema="">${n ? "Show all cinemas" : "Pick the cinemas you go to"}</button>`;
  }
}

// ---------------------------------------------------------------- render: grid
function cardMeta(f, shows) {
  const today = osloNow().slice(0, 10);
  if (f.status === "on_sale" && shows.length) {
    const cinemas = [...new Set(shows.map((s) => s.cinema))];
    const where = cinemas.length > 2 ? `${cinemas.slice(0, 2).join(", ")} +${cinemas.length - 2}` : cinemas.join(", ");
    const first = shows[0];
    const cls = first.t.slice(0, 10) === today ? ' class="today"' : "";
    return `<b>${esc(where)}</b><br><span${cls}>${dayLabel(first.t, { short: true })} ${hhmm(first.t)}</span> · ${shows.length} show${shows.length > 1 ? "s" : ""}`;
  }
  if (f.shows.length) return `Tickets not on sale yet<br>From ${dayLabel(f.shows[0].t, { short: true })}`;
  if (f.premiere) {
    const d = asDate(f.premiere);
    return f.premiereConfirmed ? `Premiere ${dayLabel(f.premiere)}` : `Expected ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
  return "Date not announced";
}

function posterHtml(f) {
  return `<div class="poster">${f.poster
    ? `<img loading="lazy" src="${esc(f.poster)}" alt="" onerror="this.remove()">`
    : ""}<div class="ph"${f.poster ? ' aria-hidden="true" style="z-index:-1"' : ""}>${esc(f.title)}</div></div>`;
}

function cardHtml({ f, shows }) {
  const flag = isNew(f) ? `<span class="flag">New</span>` : "";
  const on = isWatched(f);
  return `<li class="card">
    <a href="#film/${esc(f.id)}">${posterHtml(f).replace('<div class="poster">', `<div class="poster">${flag}`)}
      <h3>${esc(f.title)}</h3><div class="m">${cardMeta(f, shows)}</div></a>
    <button class="star${on ? " on" : ""}" data-star="${esc(f.id)}" aria-pressed="${on}" aria-label="${on ? "Remove from" : "Add to"} watchlist" title="${on ? "On your watchlist" : "Add to watchlist"}">${on ? "★" : "☆"}</button>
  </li>`;
}

function renderGrid() {
  const rows = rowsFor(state.tab).sort(SORTERS[state.sort[state.tab]] || SORTERS.next);
  const counts = { onsale: rowsFor("onsale").length, coming: rowsFor("coming").length, watch: rowsFor("watch").length };
  $("nOnsale").textContent = counts.onsale;
  $("nComing").textContent = counts.coming;
  $("nWatch").textContent = counts.watch || "";
  const nShows = rows.reduce((a, r) => a + r.shows.filter((s) => s.ticket).length, 0);
  $("count").textContent = state.tab === "onsale" ? `${rows.length} films · ${nShows} bookable showings` : `${rows.length} films`;
  let empty = "No films match these filters.";
  if (state.tab === "watch" && !state.watchlist.size) empty = "Your watchlist is empty. Tap ☆ on any poster to add it; it'll be highlighted when tickets go on sale.";
  $("grid").innerHTML = rows.length ? rows.map(cardHtml).join("") : `<li class="empty">${empty}</li>`;
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
  const lb = x.lb ? `https://letterboxd.com/film/${encodeURIComponent(x.lb)}/` : x.imdb ? `https://letterboxd.com/imdb/${x.imdb}/` : "";
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
  const dlg = $("film"), now = osloNow(), today = now.slice(0, 10);
  const all = f.shows.filter((s) => s.t >= now);
  const shown = state.showAll ? all : all.filter((s) => showMatches(s, now));
  const hidden = all.length - shown.length;
  const byDay = {};
  for (const s of shown) (byDay[s.t.slice(0, 10)] ||= []).push(s);
  const meta = [f.year, f.runtime ? `${f.runtime} min` : "", f.director ? `Directed by ${f.director}` : "", f.countries.slice(0, 3).join(", "), f.genres.slice(0, 3).join(", ")].filter(Boolean).join(" · ");
  const cinemas = Object.entries(all.reduce((a, s) => ((a[s.cinema] = (a[s.cinema] || 0) + 1), a), {}));
  const on = isWatched(f);
  const days = Object.entries(byDay).map(([day, shows]) => `
    <div class="day${day === today ? " today" : ""}"><h4>${dayLabel(day + "T00:00")}<small>${day.split("-").reverse().join(".")}</small></h4>
    <div class="stubs">${shows.map(stubHtml).join("")}</div></div>`).join("");
  let empty = "";
  if (!all.length) empty = `<p class="hiddenNote">${f.premiere ? `Premiere ${dayLabel(f.premiere)}${f.premiereConfirmed ? "" : " (not confirmed)"}. ` : ""}No showings announced in Oslo yet.${on ? " You'll see it marked as new when tickets go on sale." : " Add it to your watchlist to have it highlighted when tickets go on sale."}</p>`;
  const hiddenNote = hidden ? `<p class="hiddenNote">${hidden} showing${hidden > 1 ? "s" : ""} hidden by your filters. <button class="linkbtn" data-showall="1">Show all</button></p>`
    : state.showAll && all.some((s) => !showMatches(s, now)) ? `<p class="hiddenNote"><button class="linkbtn" data-showall="0">Apply my filters</button></p>` : "";
  dlg.innerHTML = `<form method="dialog" class="dlg-close"><button class="x" aria-label="Close">×</button></form>
    <div class="fhead">${posterHtml(f)}<div>
      <h2 id="filmTitle">${esc(f.title)}</h2>
      ${f.alt ? `<div class="alt">${esc(f.alt)}</div>` : ""}
      <div class="meta">${esc(meta)}</div>
      ${f.blurb ? `<p>${esc(f.blurb)}</p>` : ""}
      <div class="badges">${isNew(f) ? `<span class="badge new">${f.status === "on_sale" ? "New on sale" : "Newly announced"}</span>` : ""}${cinemas.map(([c, n]) => `<span class="badge">${esc(c)} · ${n}</span>`).join("")}${f.series.map((s) => `<span class="badge line">${esc(s)}</span>`).join("")}</div>
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
  if (location.hash.startsWith("#film/")) history.pushState("", document.title, location.pathname + location.search);
});
$("film").addEventListener("click", (e) => { if (e.target === $("film")) $("film").close(); });
$("account").addEventListener("click", (e) => { if (e.target === $("account")) $("account").close(); });

// ---------------------------------------------------------------- events
function savePrefs() { local.set("prefs", state.prefs); local.set("watchlist", [...state.watchlist]); queueSync(); }

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-tab],[data-when],[data-cinema],[data-star],[data-showall]");
  if (!t) return;
  if (t.dataset.tab) { state.tab = t.dataset.tab; }
  else if (t.dataset.when) { state.when = t.dataset.when; local.set("when", state.when); }
  else if (t.dataset.cinema !== undefined) {
    const c = t.dataset.cinema, list = state.prefs.cinemas;
    if (!c) state.prefs.cinemas = list.length ? [] : list;
    else state.prefs.cinemas = list.includes(c) ? list.filter((x) => x !== c) : [...list, c];
    savePrefs();
  } else if (t.dataset.star) {
    e.preventDefault();
    const f = findFilm(t.dataset.star);
    if (isWatched(f)) f.ids.forEach((id) => state.watchlist.delete(id)); else state.watchlist.add(f.id);
    savePrefs();
    if ($("film").open) openFilm(f.id);
  } else if (t.dataset.showall) { state.showAll = t.dataset.showall === "1"; openFilm(location.hash.slice(6)); return; }
  render();
});
$("dubBtn").addEventListener("click", () => { state.prefs.hideDubbed = !state.prefs.hideDubbed; savePrefs(); render(); });
$("enBtn").addEventListener("click", () => { state.prefs.englishSubs = !state.prefs.englishSubs; savePrefs(); render(); });
$("cinemaBtn").addEventListener("click", () => { state.cinemasOpen = !state.cinemasOpen; render(); });
$("q").addEventListener("input", (e) => { state.q = e.target.value; renderGrid(); });
$("sort").addEventListener("change", (e) => { state.sort[state.tab] = e.target.value; local.set("sort", state.sort); renderGrid(); });
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
  if (!state.user) {
    body.innerHTML = `<h2 id="accountTitle">Sign in or sign up</h2>
      <p>Enter your email and we'll send you a sign-in link. No password. Signing in syncs your filters and watchlist across devices, and lets you get the daily email of films newly on sale.</p>
      <form class="signin" id="signinForm"><input type="email" id="email" required placeholder="you@example.com" autocomplete="email">
      <button class="btn accent" type="submit">Send link</button></form>${msg}`;
    return;
  }
  const p = state.profile || {};
  const n = state.prefs.cinemas.length;
  const filters = [n ? `${n} cinema${n > 1 ? "s" : ""}` : "all cinemas", state.prefs.hideDubbed ? "no Norwegian dubs" : "", state.prefs.englishSubs ? "English subtitles only" : ""].filter(Boolean).join(", ");
  body.innerHTML = `<h2 id="accountTitle">Your account</h2>
    <div class="who">${esc(state.user.email)}</div>
    <label class="opt"><input type="checkbox" id="optSub"${p.subscribed ? " checked" : ""}>
      <span>Daily email at 9:00<small>Only sent on days with something new. Uses your filters: ${esc(filters)}.</small></span></label>
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
  $("account").addEventListener("submit", async (e) => {
    if (e.target.id !== "signinForm") return;
    e.preventDefault();
    const email = $("email").value.trim();
    const btn = e.target.querySelector("button"); btn.disabled = true;
    const { error } = await sb.auth.signInWithOtp({ email, options: { emailRedirectTo: location.origin + location.pathname } });
    btn.disabled = false;
    renderAccount(error ? `Couldn't send the link: ${error.message}` : `Link sent to ${email}. Open it on this device to sign in.`, !!error);
  });
  $("account").addEventListener("change", async (e) => {
    if (e.target.id === "optSub") {
      state.profile = { ...state.profile, subscribed: e.target.checked };
      await pushProfile({ subscribed: e.target.checked });
      renderAccount(e.target.checked ? "Subscribed. You'll get an email after 9:00 on days with new films." : "Unsubscribed.");
    } else if (e.target.id === "optAnn") {
      state.prefs.announcements = e.target.checked; savePrefs(); renderAccount();
    } else if (e.target.id === "optWatch") {
      state.prefs.watchlistAlways = e.target.checked; savePrefs(); renderAccount();
    }
  });
  $("account").addEventListener("click", async (e) => {
    if (e.target.id === "signOut") { await sb.auth.signOut(); renderAccount("Signed out."); }
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
(async function boot() {
  try {
    const r = await fetch("data/films.json", { cache: "no-cache" });
    state.data = await r.json();
  } catch (e) {
    $("sub").textContent = "Couldn't load the programme. Reload to try again.";
    return;
  }
  const g = state.data.generated;
  $("sub").textContent = `${state.data.location} · Filmweb + Cinemateket · updated ${dayLabel(g, { short: true })} ${hhmm(g)}`;
  state.prefs.cinemas = state.prefs.cinemas.filter((c) => state.data.cinemas.includes(c));
  render();
  route();
  handleUnsubscribe();
})();
