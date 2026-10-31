"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const local = {
  get(k, d) { try { const v = localStorage.getItem("kino:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("kino:" + k, JSON.stringify(v)); } catch {} },
};

// The current tab is marked from the first paint (before the programme arrives), so it doesn't pop in.
(() => {
  const feed = /^#(feed|new)$/.test(location.hash);
  document.querySelector(`#mainNav [data-page="${feed ? "feed" : "films"}"]`)?.setAttribute("aria-current", "");
  document.body.classList.toggle("page-feed", feed);
})();

// ---------------------------------------------------------------- regions + time (data is in the region's local time)
const REGIONS = [
  { key: "oslo", name: "Oslo", file: "films.json", tz: "Europe/Oslo" },
  { key: "westman", name: "Westman", file: "westman.json", tz: "America/Winnipeg" },
  { key: "winnipeg", name: "Winnipeg", file: "winnipeg.json", tz: "America/Winnipeg" },
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
// One grid of films grouped by when they play; what went on sale or was announced lately is in "What's new".
const TIX = [["all", "All"], ["on", "On sale"], ["off", "Not on sale yet"]];
// Order of films inside each section.
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
  tix: local.get("tix", "all"),
  within: local.get("within2", "rating"),
  onlyWatch: false,
  friendSel: [],        // Friends tab, in the filters: ids of the friends picked (none = all friends)
  page: "films",        // the films list (default) or the feed
  friendEvents: [],     // what friends added to their watchlists: {id, name, film, at}
  feedOpen: new Set(),  // posts expanded to show all their films
  feedKinds: local.get("feedKinds", []).map((k) => (k === "showings" ? "sale" : k)),  // kinds of updates picked in the feed's filter (none = all)
  onlyFriends: false,   // the Friends tab: films on your friends' watchlists
  day: "",              // the day the first section shows; "" = today. Not remembered across reloads
  calMonth: "",         // the month the date picker shows ("2026-10")
  q: "",
  prefs: withDefaults(local.get("prefs", {})),
  watchlist: new Set(local.get("watchlist", [])),
  collapsed: new Set(), // "view:sectionKey" of collapsed sections; deliberately not remembered across reloads
  showAll: false,       // film sheet: show showings hidden by filters
  sheetCinemas: new Set(), // film sheet: cinema tags clicked to narrow its showings
  sheetTags: new Set(),    // film sheet: format tags (IMAX, Engelsk tekst, …) picked to narrow its showings, by tagKey
  sheetDay: "",            // film sheet: the one day whose showings are listed; "" = all days
  user: null,           // { id, email }
  pendingEmail: (() => { try { return sessionStorage.getItem("kino:pendingEmail") || ""; } catch { return ""; } })(),
  profile: null,        // { subscribed, ... }
  blurbOpen: "",        // film sheet: the film whose description is expanded
  cinemasOpen: false,   // filter panel: all cinemas listed, not just the busiest
  friends: [],          // [{ id, name, list: Set of film ids }]: people who added each other by code
  friendsAt: 0,         // when the friends were last loaded
  friendsError: false,  // the last load failed
  acctView: "",         // account dialog: "" = settings, "friends" = the friends screen
  pendingFriend: (() => { try { return sessionStorage.getItem("kino:pendingFriend") || ""; } catch { return ""; } })(),  // a code from an invite link, waiting for sign-in
};

// ---------------------------------------------------------------- filters (keep in step with scraper/digest.py: show_ok)
// "My cinemas" is one list across regions; only the ones in the region being viewed apply.
const myCinemas = () => state.prefs.cinemas.filter((c) => state.data?.cinemas.includes(c));
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
const friendsHere = () => state.data.films.filter((f) => friendsShown(f).length).length;  // films here that a friend has on their watchlist

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
// A film hidden while it was only announced: once it has a sale date, remember it so a later return can show it again. It only
// counts as a change to save once the account's own list is loaded (before that, a stale local entry would win over the account's).
function adoptSaleDates() {
  let changed = false;
  for (const f of state.data.films) for (const i of f.ids) if (state.prefs.hidden?.[i] === "" && f.onSaleSince) {
    state.prefs.hidden[i] = f.onSaleSince; if (state.profile) hidChange(i, f.onSaleSince); changed = true;
  }
  return changed;
}
function unhideFilm(f) { for (const i of f.ids) if (state.prefs.hidden && i in state.prefs.hidden) { delete state.prefs.hidden[i]; hidChange(i); } }
// Hiding or unhiding moves the card to its new place in the section; slide it there instead of jumping.
let flipFrom = null;
function flipBegin(id) { flipFrom = { id, rects: new Map([...document.querySelectorAll("#grid .card")].map((c) => [c.dataset.id, c.getBoundingClientRect()])) }; }
function flipRun(fadeFrom = null) {
  const prev = flipFrom; flipFrom = null;
  if (!prev || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  for (const c of document.querySelectorAll("#grid .card")) {
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
// The card that was pressed: in the feed or in the grid.
const cardEl = (id) => (state.page === "feed" ? $("feedPage") : $("grid")).querySelector(`.card[data-id="${CSS.escape(id)}"]`);
function settleThenRender(id) {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(() => { renderTimer = null; flipBegin(id); render(); flipRun(); }, SETTLE_MS);
}
function flushRender() { if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; render(); } }
function toggleHidden(f) {
  flushRender();
  const wasHidden = isHidden(f);
  if (wasHidden) unhideFilm(f);
  else { state.prefs.hidden = { ...(state.prefs.hidden || {}), [f.id]: f.onSaleSince || "" }; hidChange(f.id, f.onSaleSince || ""); }
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

// The day the first section shows: the one picked; otherwise today, or, once nothing more is on today
// (under the filters), the next day something is. A picked day that has passed counts as not picked.
const pickedDay = (today) => (state.day > today ? state.day : "");
function chosenDay(today) {
  if (pickedDay(today)) return state.day;
  const now = localNow();
  let next = "";
  for (const f of state.data.films) {
    const t = filmShows(f, now, now)?.shows[0]?.t;
    if (!t) continue;
    if (t.startsWith(today)) return today;
    if (!next || t < next) next = t;
  }
  return next ? next.slice(0, 10) : today;
}
const DAY_KEY = "0000";
// "from", since the section is when these films start playing
const dayTitle = (chosen, today) => `Playing from ${chosen === today || chosen === addDays(today, 1) ? dayLabel(chosen).toLowerCase() : dayLabel(chosen)}`;
// Section labels for the "When it's playing" view. The first section is the chosen day (today unless another is
// picked); the rest group films by their next showing after it, so picking a day shows the page as it will be then.
function whenSection(row, today, chosen = today) {
  // A film's day = its next showing; films without showings use their confirmed premiere.
  const day = row.shows[0]?.t.slice(0, 10) || row.start;
  if (!day) return { key: "9999", label: "Date not set" };
  if (row.shows.length && day === chosen) return { key: DAY_KEY, label: dayTitle(chosen, today) };
  const dow = asDate(today).getUTCDay();                    // 0 = Sunday
  const weekEnd = addDays(today, (7 - dow) % 7);            // this coming Sunday
  if (day <= weekEnd) return { key: "0001", label: "From later this week" };
  if (day <= addDays(weekEnd, 7)) return { key: "0002", label: "From next week" };
  const [y, m] = day.split("-").map(Number);
  const [ty, tm] = today.split("-").map(Number);
  const label = y === ty && m === tm ? `Later in ${MONTH_NAMES[m - 1]}` : `${MONTH_NAMES[m - 1]}${y !== ty ? " " + y : ""}`;
  return { key: day.slice(0, 7), label };
}

// "What's new" groups, by when it happened.
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
// `all` ignores the Tickets filter and the Watchlist switch ("What's new" has neither).
function filmShows(f, now, from, q = "", all = false) {
  if (!textMatch(f, q) || (!q && !kindShown(f))) return null;  // searching finds every type
  if (!all && state.onlyWatch && !isWatched(f)) return null;
  if (!all && state.onlyFriends && !friendsShown(f).length) return null;
  const shows = f.shows.filter((s) => s.t >= from && showMatches(s, now));
  if (f.shows.length && !shows.length) return null; // has showings, none match the filters
  const bookable = shows.filter((s) => s.ticket);
  const onSale = f.status === "on_sale" && bookable.length > 0;
  if (!all && state.tix === "on" && !onSale) return null;
  if (!all && state.tix === "off" && onSale) return null;
  return { shows: onSale ? bookable : shows, onSale, all: shows };
}

// Whether the list starts with the chosen-day section (searching always covers every date).
const dayMode = () => !state.q.trim();

function buildRows() {
  const now = localNow(), today = now.slice(0, 10), q = state.q.trim().toLowerCase();
  const chosen = dayMode() ? chosenDay(today) : today, later = chosen !== today;
  const rows = [];
  for (const f of state.data.films) {
    // A picked day hides everything before it: only showings from that day on count.
    const m = filmShows(f, now, later ? chosen + "T00:00" : now, q);
    if (!m) continue;
    const row = { f, shows: m.shows, onSale: m.onSale, hidden: isHidden(f), watched: isWatched(f), start: startDay(f, m.all) };
    if (!row.start && !q) continue; // undated films only turn up when you search for them
    if (pickedDay(today) && !row.shows.length && row.start < chosen) continue;  // premiered before the picked day, nothing since
    row.sec = whenSection(row, today, chosen);
    if (later && row.sec.key === DAY_KEY) row.dayShows = row.shows.filter((s) => s.t.startsWith(chosen));
    rows.push(row);
  }
  rows.sort((a, b) => {
    if (a.sec.key !== b.sec.key) return a.sec.key.localeCompare(b.sec.key);
    if (a.hidden !== b.hidden) return a.hidden ? 1 : -1;  // hidden films are dimmed and always last in their section
    if (a.watched !== b.watched) return a.watched ? -1 : 1;  // watchlist films come first
    if (state.onlyFriends) { const d = friendsShown(b.f).length - friendsShown(a.f).length; if (d) return d; }  // Friends tab: the ones most friends want first
    return byWithin(a, b);
  });
  return rows;
}

// The "Order within sections" choice, for two rows ({ f, shows, start }); the grid and "What's new" share it.
const t0 = (r) => r.shows[0]?.t || (r.start ? r.start + "T00:00" : "9999");
const rating = (r) => r.f.ext?.lbRating ?? -1;
function byWithin(a, b) {
  if (state.within === "rating") return rating(b) - rating(a) || t0(a).localeCompare(t0(b)) || byTitle(a, b);
  if (state.within === "fewest") { // one-off screenings first; films with no showings yet (just a premiere) last
    const n = (r) => r.shows.length || Infinity;
    return n(a) - n(b) || t0(a).localeCompare(t0(b)) || byTitle(a, b);
  }
  return t0(a).localeCompare(t0(b)) || byTitle(a, b);
}

// ---------------------------------------------------------------- render: controls + filter sidebar
function activeFilters() {
  return (state.tix !== "all" ? 1 : 0)
    + (myCinemas().length ? 1 : 0)
    + (DUBBING_REGIONS.includes(state.region) && state.prefs.hideDubbed ? 1 : 0)
    + (state.region === "oslo" && state.prefs.englishSubs ? 1 : 0)
    + (state.region === "costadelsol" && state.prefs.audioEnNo ? 1 : 0)
    + (state.onlyFriends && state.friendSel.length ? 1 : 0);
}

// The tabs: All films, Watchlist, Friends. The selected one says how many films are listed (the number the list below shows).
const clip = async (text) => { try { await navigator.clipboard.writeText(text); return true; } catch { return false; } };
// A code pasted on its own, inside an invite link ("?friend=K7QM4P") or inside the invite message ("... code K7Q-M4P ...").
const extractCode = (t) => {
  t = String(t || "").trim();
  return t.match(/[?&]friend=([A-Za-z0-9-]+)/)?.[1] || (t.length <= 12 ? t : t.match(/code\s+([A-Za-z0-9-]{6,8})/i)?.[1] || t);
};
const inviteLink = () => location.href.split(/[?#]/)[0] + "?friend=" + state.profile.friend_code;
const inviteText = () => `Add me on Cinecrab: ${inviteLink()} or enter my code ${fmtCode(state.profile.friend_code)} under Friends`;
// The Friends tab's panel: share your code and add a friend's; with friends, a line of them (open the panel to add more). Rebuilt
// only when something it shows changed, so a half-typed code isn't lost.
// Watchlist tab, signed out: the list lives on this device only; an account keeps it safe and syncs it.
function renderWatchInvite() {
  const el = $("wlInvite");
  el.hidden = !(state.onlyWatch && sb && state.authReady && !state.user);
  if (el.hidden || el.firstChild) return;
  el.innerHTML = `<svg class="wlico" width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8.5a4.75 4.75 0 0 1-.5 9.5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>
    <div class="wlmsg"><strong>Keep your watchlist safe</strong><span>It's saved on this device only. Register or sign in to open it from any device.</span></div>
    <button type="button" class="btn accent small" data-wl="signin">Sign in or register</button>`;
}
let friendsPanelSig = "";
function renderFriendsPanel(force = false) {
  const el = $("friendsPanel");
  el.hidden = !state.onlyFriends || !!(state.user && state.friends.length);  // with friends, adding one is the button's dialog
  if (!state.onlyFriends) return;
  const p = state.profile;
  const sig = JSON.stringify([!!state.user, !!p, p?.friend_code, state.friends.map((f) => [f.id, f.name]), state.profileFailed, typeof sb !== "undefined" && !!sb]);
  if (!force && sig === friendsPanelSig) return;
  if (!force && el.contains(document.activeElement) && document.activeElement.value) return;
  friendsPanelSig = sig;
  state.friendSel = state.friendSel.filter((id) => state.friends.some((x) => x.id === id));
  const html = friendsNeedHtml();
  el.innerHTML = html;
  el.hidden = !html;
}
// What the Friends view shows when there's nothing to list yet: no accounts, the sign-in prompt, loading, or the first-friend card.
// "" once there are friends. Used by the Friends tab on Films and by the Friends filter in the Feed.
function friendsNeedHtml() {
  const p = state.profile;
  if (typeof sb === "undefined" || !sb) return `<h2 class="fh">Friends</h2><p class="fp">Friends need an account, which isn't available right now.</p>`;
  if (!state.user) return `<h2 class="fh">See what your friends want to watch</h2><p class="fp">Sign in, add your friends with a code, and the films on their watchlists show up here, and on the posters.</p>
      <button type="button" class="btn accent" data-fr="signin">Sign in</button>`;
  if (!p || !p.friend_code) return `<p class="fp">${state.profileFailed ? "Couldn't load your account. Try again in a moment." : "Loading…"}</p>`;
  return state.friends.length ? "" : `<h2 class="fh">Add your first friend</h2><p class="fp">Swap codes to see each other's watchlists, here and on the posters.</p>${friendsBody(p)}`;
}
// Your code, the box for a friend's, and your friends with Remove: on the Friends tab while there are no friends, else in the dialog.
function friendsBody(p) {
  const manage = state.friends.length ? `<div class="fmanage"><div class="subh">Your friends (${state.friends.length})</div>${state.friends.map((fr) => `<div class="frow"><span>${esc(fr.name)}</span><button type="button" class="linkbtn" data-fr="remove" data-id="${esc(fr.id)}">Remove</button></div>`).join("")}</div>` : "";
  return `<div class="fcols">
      <div class="fcol"><div class="subh">Your code</div>
        <div class="fline"><div class="fcode" aria-label="Your friend code">${esc(fmtCode(p.friend_code))}</div><button type="button" class="btn dark small" data-fr="share">Copy invite link</button></div></div>
      <form class="fcol" data-fr-form><label class="subh">Enter a friend's code</label>
        <div class="fline"><input type="text" name="code" placeholder="Enter their code" autocapitalize="characters" autocomplete="off" autocorrect="off" spellcheck="false" maxlength="200">
          <button type="submit" class="btn accent small" disabled>Add friend</button></div></form>
    </div>${manage}`;
}
function renderFriendsDialog() {
  const p = state.profile;
  $("addFriendBtn").hidden = !(state.onlyFriends && sb);  // always there on the Friends tab, so it's the one familiar way in
  if (!$("friendsAdd").open) return;
  if (!p?.friend_code) { $("friendsAdd").close(); return; }
  const body = $("friendsAddBody");
  if (body.contains(document.activeElement) && document.activeElement.value) return;  // don't lose a half-typed code
  body.innerHTML = `<h2>Add a friend</h2><p>Swap codes to see each other's watchlists.</p>${friendsBody(p)}`;
}
function renderListSwitch(count = null) {
  const cur = state.onlyFriends ? "friends" : state.onlyWatch ? "watch" : "all";
  for (const b of $("listSwitch").querySelectorAll("[data-list]")) {
    const on = b.dataset.list === cur;
    b.setAttribute("aria-pressed", on);
    b.querySelector(".cnt").textContent = on && count !== null && !(cur === "friends" && !state.user) ? ` · ${count}` : "";
  }
}
function setList(name) {
  state.onlyWatch = name === "watch"; state.onlyFriends = name === "friends";
  if (state.page !== "films") { setPage("films"); return; }
  render(); window.scrollTo({ top: 0 });
}
function showFriendsTab() { if ($("account").open) $("account").close(); setList("friends"); }
function renderRegionButton() {
  const reg = REGIONS.find((r) => r.key === state.region);
  $("regionBtn").innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="10" r="2.5" fill="currentColor"/></svg>${reg.name} <span class="chev" aria-hidden="true">▾</span>`;
  $("regionBtn").setAttribute("aria-label", `Location: ${reg.name}`);
}
function renderControls() {
  renderDayControl();
  const reg = REGIONS.find((r) => r.key === state.region);
  renderRegionButton();
  $("regionMenu").innerHTML = REGIONS.map((r) =>
    `<button role="menuitemradio" aria-checked="${r.key === state.region}" data-pick="region" data-value="${r.key}">${r.name}</button>`).join("");
  fitHeader();  // the location's name changes the header's width
  renderListSwitch();
  const n = activeFilters();
  $("filtersBtn").textContent = n ? `Filters · ${n}` : "Filters";
  $("filtersBtn").setAttribute("aria-pressed", n > 0);
  renderFilters();
}

const CINEMAS_COLLAPSE_AT = 8, CINEMAS_SHOWN = 6;  // more cinemas than this: only the busiest few are listed until "Show more"
function renderFilters() {
  const now = localNow(), cinemaCounts = {};
  for (const f of state.data?.films || []) for (const s of f.shows) if (s.ticket && s.t >= now) cinemaCounts[s.cinema] = (cinemaCounts[s.cinema] || 0) + 1;
  const mine = myCinemas();
  // One choice: a list of radio buttons. Any number: pills with a check mark. (Both are real inputs inside labels.)
  const radio = (attrs, checked, label) => `<label class="radio"><input type="radio" ${attrs}${checked ? " checked" : ""}><span>${label}</span></label>`;
  const ck = (attrs, checked, label, n) =>
    `<label class="opt-chip"><input type="checkbox" ${attrs}${checked ? " checked" : ""}><span>${label}</span>${n != null ? `<span class="n">${n}</span>` : ""}</label>`;
  // Cinemas, busiest first. With many, only the busiest few (and any that are picked) show until "Show more".
  const order = [...(state.data?.cinemas || [])].sort((a, b) => (cinemaCounts[b] || 0) - (cinemaCounts[a] || 0) || a.localeCompare(b));
  const collapsed = order.length > CINEMAS_COLLAPSE_AT && !state.cinemasOpen;
  const listed = collapsed ? order.filter((c, i) => i < CINEMAS_SHOWN || mine.includes(c)) : order, more = order.length - listed.length;
  // Spoken language and subtitles are separate choices (they can be combined). Spoken: any, original language only (not dubbed)
  // and, in Costa del Sol, English or Norwegian audio. Subtitles (Oslo): any, or only showings with English subtitles.
  const spokenNow = state.region === "costadelsol" && state.prefs.audioEnNo ? "audio" : state.prefs.hideDubbed ? "original" : "any";
  const spokenOptions = [["any", "Any", ""], ["original", "Original language only", state.region === "costadelsol" ? "Not dubbed into Spanish" : "Not dubbed into Norwegian"],
    ...(state.region === "costadelsol" ? [["audio", "English or Norwegian audio", "Spoken in English or Norwegian"]] : [])];
  const subsOptions = [["any", "Any", ""], ["subs", "English subtitles", "Only showings with English subtitles"]];
  const radioD = (attrs, checked, label, desc) => `<label class="radio"><input type="radio" ${attrs}${checked ? " checked" : ""}><span>${label}${desc ? `<small>${desc}</small>` : ""}</span></label>`;
  const focused = document.activeElement?.closest?.("#filtersBody") ? document.activeElement.dataset.f + "|" + (document.activeElement.value || "") : "";
  $("filtersBody").innerHTML = `
    <fieldset class="fg"${state.onlyFriends && state.friends.length ? "" : " hidden"}><legend>Friends <span class="hint">${state.friendSel.length ? `${state.friendSel.length} chosen` : "All"}</span></legend>
      <div class="fghint">Pick one or more. The numbers are films here on their watchlist.</div>
      <div class="chips">${state.friends.map((fr) => ck(`data-f="friend" value="${esc(fr.id)}"`, state.friendSel.includes(fr.id), esc(shortName(fr.name)), (state.data?.films || []).filter((f) => f.ids.some((i) => fr.list.has(i))).length)).join("")}</div>
      ${state.friendSel.length ? `<div class="fgrow"><button class="linkbtn" data-f="friends-clear">Clear selection</button></div>` : ""}
    </fieldset>
    <fieldset class="fg"><legend>Sort films by</legend>
      <div class="fghint">Within each group of films.</div>
      ${WITHIN.map(([v, l]) => radio(`name="within" data-f="within" value="${v}"`, state.within === v, l)).join("")}
    </fieldset>
    <fieldset class="fg"><legend>Tickets</legend>
      ${TIX.map(([v, l]) => radio(`name="tix" data-f="tix" value="${v}"`, state.tix === v, l)).join("")}
    </fieldset>
    <fieldset class="fg"${DUBBING_REGIONS.includes(state.region) ? "" : " hidden"}><legend>Spoken language</legend>
      ${spokenOptions.map(([v, l, d]) => radioD(`name="spoken" data-f="spoken" value="${v}"`, spokenNow === v, l, d)).join("")}
    </fieldset>
    <fieldset class="fg"${state.region === "oslo" ? "" : " hidden"}><legend>Subtitles</legend>
      ${subsOptions.map(([v, l, d]) => radioD(`name="subs" data-f="subs" value="${v}"`, (state.prefs.englishSubs ? "subs" : "any") === v, l, d)).join("")}
    </fieldset>
    <fieldset class="fg"><legend>Cinemas <span class="hint">${mine.length ? `${mine.length} chosen` : "All"}</span></legend>
      <div class="fghint">${state.data ? "Pick one or more. The numbers are showings on sale." : "Loading the cinemas…"}</div>
      <div class="chips">${listed.map((c) => ck(`data-f="cinema" value="${esc(c)}"`, mine.includes(c), esc(c), cinemaCounts[c] || 0)).join("")}</div>
      <div class="fgrow">${more ? `<button class="linkbtn" data-f="cinemas-more">Show ${more} more</button>` : state.cinemasOpen && order.length > CINEMAS_COLLAPSE_AT ? `<button class="linkbtn" data-f="cinemas-more">Show fewer</button>` : ""}
      ${mine.length ? `<button class="linkbtn" data-f="cinemas-clear">Clear selection</button>` : ""}</div>
    </fieldset>
`;
  $("filtersReset").hidden = !activeFilters();
  if (focused) { // keep keyboard focus on the control that was just changed
    const [f, v] = focused.split("|");
    [...$("filtersBody").querySelectorAll(`[data-f="${f}"]`)].find((el) => (el.value || "") === v)?.focus();
  }
}

// ---------------------------------------------------------------- render: grid
// "Last chance": from the time the build sets for a film about to stop playing (scraper/build.py: mark_last_chance).
const endingNote = (f) => (f.lastChance && f.lastChance <= localNow() ? "Last chance" : "");

const whereList = (shows) => {
  const cinemas = [...new Set(shows.map((s) => s.cinema))];
  return cinemas.length > 2 ? `${cinemas.slice(0, 2).join(", ")} +${cinemas.length - 2}` : cinemas.join(", ");
};

// The tag for a film with no bookable showings: the showings' own status when they all share one ("Sold out"),
// otherwise "Not on sale yet".
function noSaleTag(shows) {
  const st = new Set(shows.map((s) => s.status));
  return `<span class="nosaletag">${esc(st.size === 1 && shows[0].status || "Not on sale yet")}</span>`;
}

// What a card says about its showings (the times themselves are on the film's page):
//   several days:        "Fri 30 Oct – 6 Nov" and, on its own line, "6 shows"
//   several on one day:  "Today · 3 shows"
//   one showing:         "Tomorrow 19:00" (the time says it; no "1 show")
function showsLine(shows) {
  const today = localNow().slice(0, 10), first = shows[0], firstDay = first.t.slice(0, 10), lastDay = shows.at(-1).t.slice(0, 10);
  const endLabel = (day) => day === today ? "Today" : day === addDays(today, 1) ? "Tomorrow" : `${asDate(day).getUTCDate()} ${MONTHS[asDate(day).getUTCMonth()]}`;
  const cls = firstDay === today ? " today" : "";
  const count = `<span class="nw">${shows.length} shows</span>`;  // keep "11 shows" and the dates whole on narrow cards
  if (shows.length === 1) return `<span class="nw${cls}">${dayLabel(first.t, { short: true })} ${hhmm(first.t)}</span>`;
  if (lastDay === firstDay) return `<span class="nw${cls}">${dayLabel(first.t, { short: true })}</span> · ${count}`;
  return `<span class="nw${cls}">${dayLabel(first.t, { short: true })} – ${endLabel(lastDay)}</span><br>${count}`;
}
function cardMeta(row) {
  const { f, shows, onSale, dayShows } = row;
  if (onSale) {
    const ending = endingNote(f);
    // a picked day (or today): that day's cinemas, with the range of showings from then on
    return `<b>${esc(whereList(dayShows || shows))}</b><br>${showsLine(shows)}` + (ending ? `<br><span class="leave">${esc(ending)}</span>` : "");
  }
  if (shows.length) return `${showsLine(shows)}<br>${noSaleTag(dayShows || shows)}`;
  if (f.scope === "Canada" && f.premiere) return `Opens in Canada ${dayLabel(f.premiere)}<br><span class="nosaletag">Not scheduled here yet</span>`;
  if (f.premiere) {
    const d = asDate(f.premiere);
    const when = f.premiereConfirmed ? `Premiere ${dayLabel(f.premiere)}` : `Expected ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    return `${when}<br><span class="nosaletag">Not on sale yet</span>`;
  }
  return `<span class="nosaletag">Date not set</span>`;
}

let posterEager = 0;  // the first posters of a render load at once; the rest as they near the screen
function posterHtml(f) {
  return `<div class="poster">${f.poster
    ? `<img ${posterEager-- > 0 ? 'loading="eager" fetchpriority="high"' : 'loading="lazy"'} src="${esc(f.poster)}" alt="" onerror="${f.poster2 ? `this.onerror=function(){this.remove()};this.src='${esc(f.poster2)}'` : "this.remove()"}">`
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
const moreIcon = `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.9" fill="currentColor"/><circle cx="12" cy="12" r="1.9" fill="currentColor"/><circle cx="19" cy="12" r="1.9" fill="currentColor"/></svg>`;
const shareIcon = `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V3m0 0L7.5 7.5M12 3l4.5 4.5M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const linkIcon = `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
// The system share sheet on phones and tablets (touch); on a computer a plain "Copy link", as most sites do.
const canShare = !!navigator.share && matchMedia("(pointer: coarse)").matches;
const heart = (filled) => `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-7.5-4.7-9.6-9.3C.9 8.3 3 4.5 6.7 4.5c2 0 3.6 1 5.3 3 1.7-2 3.3-3 5.3-3 3.7 0 5.8 3.8 4.3 7.2C19.5 16.3 12 21 12 21z" fill="${filled ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>`;

// ---------------------------------------------------------------- friends on films
// Friends' watchlists come from the account (friends_watchlists); a film shows the initials of those who want to see it.
const shortName = (n) => /^[^@\s]+@[^@\s]+$/.test(n) ? n.split("@")[0] : String(n || "");  // a name that's still an email shows its first part
const fmtCode = (c) => String(c || "").replace(/^(.{3})(.+)$/, "$1-$2");
// On the Friends tab with friends picked in the filters, only they count.
const friendsShown = (f) => friendsOn(f).filter((fr) => !state.friendSel.length || state.friendSel.includes(fr.id));
const friendsOn = (f) => state.friends.filter((fr) => f.ids.some((i) => fr.list.has(i)));
function initialsOf(name) {
  const w = shortName(name).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  return ((w[0]?.[0] || "?") + (w.length > 1 ? w[1][0] : "")).toUpperCase();
}
const friendHue = (id) => [...String(id)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const nameList = (names) => names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
const friendsLine = (fs) => `On ${nameList(fs.map((fr) => shortName(fr.name) + "’s"))} watchlist${fs.length > 1 ? "s" : ""}`;
function friendsStack(f) {
  const fs = friendsOn(f);
  if (!fs.length) return "";
  const dots = fs.slice(0, 3).map((fr) => `<span class="fdot" style="--h:${friendHue(fr.id)}">${esc(initialsOf(fr.name))}</span>`).join("")
    + (fs.length > 3 ? `<span class="fdot more">+${fs.length - 3}</span>` : "");
  return `<span class="fstack" role="img" aria-label="${esc(friendsLine(fs))}" title="${esc(friendsLine(fs))}">${dots}</span>`;
}

function cardHtml(row) {
  const { f } = row;
  const flag = (isNew(f) ? `<span class="flag">New</span>` : "") + (KIND_BADGE[f.kind] ? `<span class="kind">${KIND_BADGE[f.kind]}</span>` : "")
    + ratingHtml(f)
    + `<button type="button" class="hide" data-hide="${esc(f.id)}" aria-label="${row.hidden ? "Show" : "Hide"} this film" title="${row.hidden ? "Show this film again" : "Hide this film"}">${row.hidden ? eyeOn : eyeOff}</button>`;
  const on = isWatched(f);
  return `<li class="card${row.onSale ? "" : " nosale"}${row.hidden ? " hid" : ""}" data-id="${esc(f.id)}">
    <a href="#film/${esc(f.id)}">${posterHtml(f).replace('<div class="poster">', `<div class="poster">${flag}${friendsStack(f)}`)}
      <h3>${esc(titleOf(f))}</h3><div class="m">${row.meta ?? cardMeta(row)}</div></a>
    <button class="star${on ? " on" : ""}" data-star="${esc(f.id)}" aria-pressed="${on}" aria-label="${on ? "Remove from" : "Add to"} watchlist" title="${on ? "On your watchlist" : "Add to watchlist"}">${heart(on)}</button>
  </li>`;
}

function renderGrid() {
  posterEager = 12;
  $("qClear").hidden = !state.q;
  const rows = buildRows();
  const sections = [];
  for (const r of rows) {
    if (!sections.length || sections.at(-1).key !== r.sec.key) sections.push({ key: r.sec.key, label: r.sec.label, rows: [] });
    sections.at(-1).rows.push(r);
  }
  const isCollapsed = (s) => state.collapsed.has(s.key);
  let empty = "No films match these filters.", offerClear = true;  // "Clear filters" only when the filters are why nothing shows
  if (state.q.trim()) empty = `Nothing matching “${esc(state.q.trim())}” in ${esc(state.data.location)}'s listings yet.`;
  else if (state.onlyWatch && !state.watchlist.size) { empty = "Your watchlist is empty. Tap the heart on any poster to add it; it'll be highlighted when tickets go on sale."; offerClear = false; }
  else if (state.onlyFriends && !state.user) empty = "";
  else if (state.onlyFriends && !state.friends.length) { empty = "Once your friends add films to their watchlists, they'll show up here."; offerClear = false; }
  else if (state.onlyFriends && !friendsHere()) { empty = `Nothing on your friends' watchlists is in ${esc(state.data.location)}'s listings yet.`; offerClear = !!state.friendSel.length; }
  else if (state.onlyWatch && !watchedHere()) { empty = `Nothing on your watchlist is in ${esc(state.data.location)}'s listings. Tap the heart on any poster to add it; it'll be highlighted when tickets go on sale.`; offerClear = false; }
  // The chosen-day section is always there (unless searching), even empty, so it says plainly that nothing's on that day.
  const today = localNow().slice(0, 10), chosen = chosenDay(today);
  const withDay = dayMode() && !(state.onlyWatch && !watchedHere()) && !(state.onlyFriends && !friendsHere());
  if (withDay && sections[0]?.key !== DAY_KEY) sections.unshift({ key: DAY_KEY, label: dayTitle(chosen, today), rows: [] });
  const dayEmpty = `${state.onlyWatch ? "Nothing on your watchlist is" : state.onlyFriends ? "Nothing on your friends' watchlists is" : "Nothing is"} playing ${chosen === today ? "for the rest of today" : `on ${dayLabel(chosen)}`}${activeFilters() ? " with these filters" : ""}.`;
  // When nothing (or nothing on the day) turns up, offer a way back out of the search and filters.
  const q = state.q.trim(), nf = activeFilters();
  const clearBtn = (withSearch) => (withSearch && q) || nf
    ? ` <button class="linkbtn" data-clearall="${withSearch ? "q" : "f"}">${withSearch && q && nf ? "Clear search and filters" : withSearch && q ? "Clear search" : "Clear filters"}</button>` : "";
  state.sections = sections;
  $("filtersShow").textContent = `Show ${rows.length} film${rows.length === 1 ? "" : "s"}`;
  renderListSwitch(rows.length);
  renderFriendsPanel();
  renderFriendsDialog();
  renderWatchInvite();
  renderActiveChips();
  $("grid").innerHTML = sections.length ? sections.map((s, i) => {
    const shut = isCollapsed(s), isDay = withDay && s.key === DAY_KEY;
    const peek = shut ? `<span class="peek">${esc(s.rows.slice(0, 4).map((r) => titleOf(r.f)).join(" · "))}${s.rows.length > 4 ? " …" : ""}</span>` : "";
    return `<section class="sec${shut ? " shut" : ""}" id="sec-${i}">
      <h2 class="sech"><button data-sec="${i}" aria-expanded="${!shut}" aria-controls="secgrid-${i}">
        <span class="lbl">${esc(s.label)}</span> <span class="n">${s.rows.length}</span>${peek}<span class="chev" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
      </button></h2>
      ${shut ? "" : isDay && !s.rows.length ? `<p class="dayempty">${esc(dayEmpty)}${clearBtn(false)}</p>` : `<ul class="grid" id="secgrid-${i}">${s.rows.map(cardHtml).join("")}</ul>`}</section>`;
  }).join("") : empty ? `<p class="empty">${empty}${offerClear || q ? clearBtn(true) : ""}</p>` : "";
  const hk = hiddenKindsLine();
  $("hiddenKinds").hidden = !hk;
  $("hiddenKinds").innerHTML = hk;
}

// ---------------------------------------------------------------- render: the day control in the bar
// Today is the unfiltered default, so the button just says "Date"; once another day is picked
// it names that day and is highlighted, like active filters. Narrow bars show only the icon.
const calIcon = `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3.5 10h17M8 3v4M16 3v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
function renderDayControl() {
  // goes by what was picked: rolling on to tomorrow because today is over still counts as the default
  const today = localNow().slice(0, 10), picked = pickedDay(today), d = picked && asDate(picked);
  const name = !picked ? "Date" : picked === addDays(today, 1) ? "Tomorrow" : `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  $("dayBtn").innerHTML = `${calIcon}<span class="lbl">${name}</span><span class="chev" aria-hidden="true">▾</span>`;
  $("dayBtn").classList.toggle("on", !!picked);
  $("dayBtn").setAttribute("aria-label", picked ? `Date: ${longDate.format(d)}` : "Pick a date");
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
  // Header like most calendars: the month, then Today (always there, even when today has nothing left) and the arrows.
  return `<div class="calhead">
      <b>${MONTH_NAMES[m - 1]} ${y}</b>
      <button class="caltoday" data-dayset="">Today</button>
      <button class="calnav" data-calnav="-1" aria-label="Previous month"${month <= today.slice(0, 7) ? " disabled" : ""}>‹</button>
      <button class="calnav" data-calnav="1" aria-label="Next month"${month >= last.slice(0, 7) ? " disabled" : ""}>›</button></div>
    <div class="calgrid">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => `<span class="wd" aria-hidden="true">${d.slice(0, 2)}</span>`).join("")}${cells.join("")}</div>`;
}
function openCal() {
  const menu = $("calMenu");
  if (menu.hidden) { state.calMonth = chosenDay(localNow().slice(0, 10)).slice(0, 7); menu.innerHTML = calHtml(); }
  toggleMenu($("dayBtn"), menu);
  if (!menu.hidden) (menu.querySelector('.cday[aria-pressed="true"]:not(:disabled)') || menu.querySelector(".cday:not(:disabled)") || menu.querySelector(".caltoday"))?.focus();
}
// Bring the first section up to just under the pinned bar (the page title stays scrolled away); never scrolls down.
function scrollToFirstSection() {
  // the bar's exact height (--stick-top is rounded), rounded up so not even a sliver of the title shows
  const target = Math.max(0, Math.ceil($("grid").getBoundingClientRect().top + window.scrollY - $("controls").getBoundingClientRect().height));
  if (window.scrollY > target) window.scrollTo({ top: target });
}
// Picking a day (Today too, even if it already was) shows the first section.
function setDay(day) {
  closeMenus();
  if (day !== state.day) { state.day = day; render(); }
  scrollToFirstSection();
  $("dayBtn").focus({ preventScroll: true });
}
$("dayBtn").addEventListener("click", openCal);
$("calMenu").addEventListener("click", (e) => {
  const t = e.target.closest("[data-calnav],[data-dayset]");
  if (!t) return;
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

function render() {
  if (!state.data) return;  // the programme hasn't arrived yet (an account finishing loading can ask for a redraw first)
  renderNav();
  if (state.page === "feed") { renderFeed(); return; }
  renderControls(); renderGrid();
}

// ---------------------------------------------------------------- the feed
// The default page: what happened, as posts. A post is one kind of thing with its films: "3 new films on sale", "16 new films
// announced", "More showings for Dune", "Santi added 4 films to their watchlist". Events come from the programme data
// (state.data.events, written by the scraper) and from friends (state.friendEvents); the same filters as the list apply.
const FEED_DAYS = 30;
const postShown = () => (innerWidth >= 700 ? 5 : 6);  // one row on desktop, two rows of three on phones
const FEED_ICONS = {
  sale: `<path d="M3 9a2 2 0 0 0 0 4v3a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-3a2 2 0 0 1 0-4V6a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1z M14 5v12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-dasharray="0"/>`,
  announced: `<path d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>`,
  showings: `<rect x="4" y="5" width="16" height="15" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M4 10h16M12 13v4M10 15h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>`,
};
// A UTC stamp ("2026-10-31T08:28", as the database keeps them) as this location's local time.
const utcToLocal = (s) => {
  try { return new Intl.DateTimeFormat("sv-SE", { timeZone: state.data.tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(s + ":00Z")).replace(" ", "T"); }
  catch { return s; }
};
// The programme's events. Data written before the scraper kept an event log has none: rebuild the two main kinds from the films' stamps.
const catalogEvents = () => state.data.events || state.data.films.flatMap((f) => [
  f.onSaleSince && f.status === "on_sale" ? { type: "sale", film: f.id, at: f.onSaleSince } : null,
  f.announcedSince && f.status === "announced" ? { type: "announced", film: f.id, at: f.announcedSince } : null].filter(Boolean));
// Under a poster in the feed: only what the post is about (the post's heading already says what happened).
function feedMeta(e, f, m) {
  if (e.type === "sale") return `<b class="cin">${esc(whereList(m.shows))}</b><span class="cin dt">${showsLine(m.shows).split("<br>")[0]}</span>`;  // cinemas on one line, then the usual date range
  if (e.type === "showings") return `+${e.n || 1} showing${(e.n || 1) === 1 ? "" : "s"}${e.cinemas?.length ? " · " + esc(whereList(e.cinemas.map((c) => ({ cinema: c })))) : ""}`;
  if (m.shows.length) return `From ${esc(dayLabel(m.shows[0].t.slice(0, 10)))}`;
  if (f.premiere) return f.premiereConfirmed ? `Premiere ${esc(dayLabel(f.premiere))}` : `Expected ${MONTHS[asDate(f.premiere).getUTCMonth()]} ${asDate(f.premiere).getUTCFullYear()}`;
  return "Date not set";
}
function feedEvents() {
  const now = localNow(), oldest = addDays(now.slice(0, 10), 1 - FEED_DAYS), out = [];
  const row = (f, m, at, meta) => ({ f, at, shows: m.shows, onSale: m.onSale, start: startDay(f, m.all), hidden: false, watched: isWatched(f), meta });
  for (const e of catalogEvents()) {
    const f = findFilm(e.film);
    if (!f || !FEED_ICONS[e.type] || e.at <= state.data.baseline || e.at.slice(0, 10) < oldest || isHidden(f)) continue;  // kinds the feed doesn't show yet are skipped
    const m = filmShows(f, now, now, "", true);
    if (!m) continue;
    if (e.type === "sale" && !m.onSale) continue;
    if (e.type === "announced" && f.status !== "announced") continue;
    if (e.type === "showings" && !m.shows.length) continue;
    out.push({ type: e.type, at: e.at, key: e.type, row: row(f, m, e.at, feedMeta(e, f, m)) });
  }
  for (const e of state.friendEvents) {
    const f = findFilm(e.film);
    if (!f || e.at.slice(0, 10) < oldest || isHidden(f)) continue;
    const m = filmShows(f, now, now, "", true);
    if (m) out.push({ type: "friend", at: e.at, key: "friend|" + e.id, actor: { id: e.id, name: e.name }, row: row(f, m, e.at, "") });
  }
  return out;
}
// Events of one kind (and, for friends, one friend) on one day become one post, newest first.
function feedPosts(filtered = true) {
  const by = new Map();
  for (const e of feedEvents()) {
    if (filtered && state.feedKinds.length && !state.feedKinds.includes(e.type === "showings" ? "sale" : e.type)) continue;  // more showings count as "On sale"
    const k = `${e.key}|${e.at.slice(0, 10)}`;
    if (!by.has(k)) by.set(k, { key: k, type: e.type, actor: e.actor, at: e.at, rows: [] });
    const p = by.get(k);
    if (e.at > p.at) p.at = e.at;
    if (!p.rows.some((r) => r.f.id === e.row.f.id)) p.rows.push(e.row);
  }
  const posts = [...by.values()].sort((a, b) => b.at.localeCompare(a.at));
  for (const p of posts) p.rows.sort((a, b) => b.watched - a.watched || b.at.localeCompare(a.at) || byWithin(a, b));
  return posts;
}
function postText(p) {
  const n = p.rows.length, one = titleOf(p.rows[0].f), films = n === 1 ? "one film" : `${n} films`;
  if (p.type === "friend") return `${esc(shortName(p.actor.name))} added ${n === 1 ? `<b>${esc(one)}</b>` : films} to their watchlist`;
  if (p.type === "sale") return n === 1 ? "One new film on sale" : `${n} new films on sale`;
  if (p.type === "announced") return n === 1 ? "One new film announced" : `${n} new films announced`;
  return n === 1 ? `More showings added for <b>${esc(one)}</b>` : `More showings added for ${films}`;
}
function ago(at) {
  const mins = Math.round((Date.parse(localNow() + "Z") - Date.parse(at + "Z")) / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.floor(mins / 60)} h ago`;
  return dayLabel(at.slice(0, 10));
}
const feedSeen = () => local.get("feedSeen", "");
let feedLatest = "";
function markFeedSeen() { if (feedLatest) local.set("feedSeen", feedLatest); }
function postHtml(p) {
  const open = state.feedOpen.has(p.key), n = p.rows.length, shown = open ? p.rows : p.rows.slice(0, postShown());
  const icon = p.type === "friend"
    ? `<span class="fdot" style="--h:${friendHue(p.actor.id)}">${esc(initialsOf(p.actor.name))}</span>`
    : `<span class="pico ${p.type}"><svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">${FEED_ICONS[p.type]}</svg></span>`;
  return `<article class="post">
    <header class="phead">${icon}<div class="ptext"><strong>${postText(p)}</strong><time>${esc(ago(p.at))}</time></div></header>
    <ul class="grid">${shown.map(cardHtml).join("")}</ul>
    ${n > postShown() ? `<button type="button" class="linkbtn postmore" data-post="${esc(p.key)}" aria-expanded="${open}">${open ? "Show fewer" : `Show ${n - postShown()} more`}</button>` : ""}
  </article>`;
}
// The row of kinds at the top of the feed (needs no data, so it's there before the programme arrives).
function feedFilterHtml() {
  const KINDS = [...(sb ? [["friend", "Friends"]] : []), ["sale", "On sale"], ["announced", "Announced"]];
  const chip = (k, label) => `<button type="button" class="chip" data-fk="${k}" aria-pressed="${k ? state.feedKinds[0] === k : !state.feedKinds.length}">${label}</button>`;
  return `<div class="feedfilter" role="group" aria-label="Kinds of updates">${chip("", "All")}${KINDS.map(([k, l]) => chip(k, l)).join("")}</div>`;
}
// Until the programme arrives: the page's own top (the feed's filter row) and grey placeholders where the posts / films will be.
function renderSkeleton() {
  state.page = /^#(feed|new)$/.test(location.hash) ? "feed" : "films";  // the page we open on, known from the address
  const cards = (n) => `<ul class="grid">${'<li class="card sk"><div class="poster"></div><h3></h3><div class="m"></div></li>'.repeat(n)}</ul>`;
  if (/^#(feed|new)$/.test(location.hash)) {
    const post = `<article class="post sk" aria-hidden="true"><header class="phead"><span class="pico"></span><div class="ptext"><strong class="skbar"></strong><time class="skbar short"></time></div></header>${cards(6)}</article>`;
    $("feedPage").hidden = false;
    $("feedPage").innerHTML = feedFilterHtml() + post + post;
  } else {
    $("grid").innerHTML = `<section class="sec sk" aria-hidden="true"><h2 class="sech"><span class="skbar"></span></h2>${cards(12)}</section>`;
  }
}
function renderFeed() {
  if (!state.data) return;  // the skeleton stays until the programme arrives
  posterEager = 12;
  const el = $("feedPage"), seen = feedSeen(), posts = feedPosts();
  feedLatest = [...catalogEvents().map((e) => e.at), ...state.friendEvents.map((e) => e.at)].sort().at(-1) || "";
  const parts = [];
  let marked = !seen || !posts.some((p) => p.at > seen) || !posts.some((p) => p.at <= seen);
  for (const p of posts) {
    if (!marked && p.at <= seen) { parts.push(`<div class="newmark"><span>New since your last visit</span></div>`); marked = true; }
    parts.push(postHtml(p));
  }
  const filter = feedFilterHtml();
  const nudge = sb && state.authReady && !state.user
    ? `<div class="fpanel feednudge"><div class="wlmsg"><strong>See what your friends add</strong><span>Sign in and add friends to get their watchlist picks here.</span></div><button type="button" class="btn accent small" data-feed="signin">Sign in or register</button></div>` : "";
  const needFriends = state.feedKinds[0] === "friend" ? friendsNeedHtml() : "";  // Friends filter, nobody to show: the Friends view's own prompt
  if (needFriends && el.contains(document.activeElement) && document.activeElement.value) return;  // a code is being typed
  if (needFriends) {
    el.innerHTML = `${filter}<div class="fpanel">${needFriends}</div>${state.user && state.profile?.friend_code ? `<p class="nempty">Once your friends add films to their watchlists, they'll show up here.</p>` : ""}`;
    return;
  }
  el.innerHTML = `${filter}${parts.length ? parts.join("")
      : state.feedKinds.length ? `<p class="nempty">No updates of this kind yet. <button type="button" class="linkbtn" data-fk="">Show all</button></p>`
      : `<p class="nempty">Nothing new yet. Films show up here as soon as they get a date or tickets go on sale${state.user ? ", and when your friends add films to their watchlists" : ""}.</p>`}
    ${nudge}`;
}
$("feedPage").addEventListener("click", (e) => {
  const fk = e.target.closest("[data-fk]");
  if (fk) {
    const k = fk.dataset.fk;
    state.feedKinds = k ? [k] : [];  // one kind at a time, or All
    local.set("feedKinds", state.feedKinds);
    renderFeed();
    return;
  }
  const more = e.target.closest("[data-post]");
  if (more) { const k = more.dataset.post; state.feedOpen.has(k) ? state.feedOpen.delete(k) : state.feedOpen.add(k); renderFeed(); return; }
  if (e.target.closest("[data-feed=signin]")) promptSignIn();
});
// Navigation: Feed and Films. A bar along the bottom on phones and tablets, links in the header on desktop.
function renderNav() {
  $("feedPage").hidden = state.page !== "feed";
  document.body.classList.toggle("page-feed", state.page === "feed");
  for (const a of $("mainNav").querySelectorAll("[data-page]")) a.toggleAttribute("aria-current", a.dataset.page === state.page);
  const seen = feedSeen(), fresh = state.page === "feed" || !seen || !state.data ? 0 : feedPosts(false).filter((p) => p.at > seen).length;
  const dot = $("feedDot");
  dot.hidden = !fresh;
  dot.textContent = fresh > 9 ? "9+" : fresh;
}
function setPage(name) {
  if (state.page === name) return;
  if (state.page === "feed") markFeedSeen();
  state.page = name;
  if (!$("film").open) history.replaceState(null, "", location.pathname + location.search + (name === "feed" ? "#feed" : "#films"));
  render();
  window.scrollTo({ top: 0 });
}
$("mainNav").addEventListener("click", (e) => {
  const a = e.target.closest("[data-page]");
  if (a && a.dataset.page === state.page) { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); }  // the current tab again: back to the top
});
const leaveFeed = () => { if (state.page === "feed" && document.visibilityState === "hidden") markFeedSeen(); };
document.addEventListener("visibilitychange", leaveFeed);
window.addEventListener("pagehide", () => { if (state.page === "feed") markFeedSeen(); });

// ---------------------------------------------------------------- film sheet
// ---------------------------------------------------------------- the poster, enlarged
// A bigger version of the poster where the image service allows it (TMDB: w342 -> w780; Cinemateket's: 360x540 -> 900x1350);
// otherwise the one already loaded, shown larger. If an upgraded image fails to load, the next best is tried.
const bigPoster = (url) => String(url).replace("/t/p/w342/", "/t/p/w780/").replace(/([?&])w=\d+&h=\d+/, "$1w=900&h=1350");
function openZoom(id, from) {
  const f = findFilm(id), dlg = $("zoom");
  if (!f || !f.poster || !from?.querySelector("img")) return;  // no poster image (it failed to load, or there's only the title card)
  const urls = [...new Set([bigPoster(f.poster), f.poster, f.poster2].filter(Boolean))];
  dlg.innerHTML = `<form method="dialog" class="dlg-close zclose"><button class="x" aria-label="Close">×</button></form><img alt="Poster of ${esc(titleOf(f))}">`;
  dlg.setAttribute("aria-label", `Poster of ${titleOf(f)}`);
  const img = dlg.querySelector("img");
  let n = 0;
  img.onerror = () => { if (++n < urls.length) img.src = urls[n]; else dlg.close(); };
  img.src = urls[0];
  dlg.showModal();
}
$("zoom").addEventListener("click", () => $("zoom").close());  // a tap anywhere closes it (Escape and the × do too)
$("zoom").addEventListener("close", () => { $("zoom").innerHTML = ""; });

// A long description is cut to a few lines with "Show more"; the button only appears when something is actually cut.
function fitBlurb() {
  const b = $("film").querySelector(".blurb");
  if (!b) return;
  const p = b.querySelector("p"), btn = b.querySelector("button");
  btn.hidden = !b.classList.contains("open") && p.scrollHeight <= p.clientHeight + 1;
}
window.addEventListener("resize", () => { if ($("film").open) fitBlurb(); });

function findFilm(id) { return state.data.films.find((f) => f.id === id || f.ids.includes(id)); }

// The tags a showing's stub shows. "Norsk tekst" is left out: nearly every Oslo showing has it.
const showTags = (s) => [...s.tags.filter((t) => t !== "Norsk tekst"), s.dub ? "Dubbed" : ""].filter(Boolean);
const tagKey = (t) => t.toLowerCase();  // Filmweb spells some tags several ways ("MIRAGE", "Mirage")
const hasTag = (s, k) => showTags(s).some((t) => tagKey(t) === k);

// Format tags worth filtering a film's showings by: those on some of its showings but not all, most common first,
// each under its most used spelling. A picked tag stays even if it no longer narrows anything, so it can be unpicked.
function sheetTagList(all) {
  const by = new Map();
  for (const s of all) for (const t of new Set(showTags(s))) {
    const k = tagKey(t), e = by.get(k) || { k, n: 0, spell: {} };
    e.n++; e.spell[t] = (e.spell[t] || 0) + 1; by.set(k, e);
  }
  return [...by.values()].filter((e) => e.n < all.length || state.sheetTags.has(e.k))
    .map((e) => ({ ...e, label: Object.entries(e.spell).sort((a, b) => b[1] - a[1])[0][0] }))
    .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
}

const MEAL_TAG = "Meal + film";  // dinner/brunch packages (Sommerro, sometimes Vega): priced far above a cinema ticket

function stubHtml(s) {
  const screen = s.screen && s.screen !== s.cinema ? s.screen.replace(s.cinema, "").trim() : "";
  const tags = showTags(s);
  const inner = `<span class="t">${hhmm(s.t)}</span><span class="c">${esc(s.cinema)}${screen ? ` · ${esc(screen)}` : ""}</span>`
    + (tags.length ? `<span class="tg">${esc(tags.map((t) => (t === MEAL_TAG ? "🍽 " : "") + t).join(" · "))}</span>` : "")
    + (s.note ? `<span class="nt">${esc(s.note)}</span>` : "")
    + (!s.ticket ? `<span class="c">${esc(s.status || "Not on sale yet")}</span>` : "");
  return s.ticket
    ? `<a class="stub" href="${esc(s.ticket)}" target="_blank" rel="noopener" title="Buy tickets">${inner}</a>`
    : `<span class="stub nosale">${inner}</span>`;
}

// Letterboxd / IMDb / RT / Metacritic links. A direct link only when the scraper matched the film
// confidently (scraper/external.py); otherwise a Letterboxd search, which can't point at the wrong film.
// The Letterboxd link is always at the top, with the rating when there is one; the cinema listing's own links (Filmweb, ...)
// go in the "more" menu.
function extLinks(f) {
  const x = f.ext || {};
  const lb = x.lb ? `https://letterboxd.com/film/${encodeURIComponent(x.lb)}/`
    : x.imdb ? `https://letterboxd.com/imdb/${x.imdb}/` : x.tmdb ? `https://letterboxd.com/tmdb/${x.tmdb}/` : "";
  const url = lb || `https://letterboxd.com/search/films/${encodeURIComponent((f.alt || f.title) + (f.year ? " " + f.year : ""))}/`;
  const label = (lb && x.lbRating ? `<b>★ ${x.lbRating.toFixed(1)}</b> ` : "") + `<span>${lb ? "Letterboxd ↗" : "Search Letterboxd ↗"}</span>`;  // the underlined part is the link's look
  return {
    top: `<a class="rate" href="${esc(url)}" target="_blank" rel="noopener"${lb && x.lbRating ? ' title="Letterboxd rating"' : ""}>${label}</a>`,
    menu: f.links.map((l) => `<a role="menuitem" href="${esc(l.url)}" target="_blank" rel="noopener"><span class="mi">${linkIcon}</span>${esc(l.label)} ↗</a>`).join(""),
  };
}

function openFilm(id) {
  const f = findFilm(id);
  if (!f) return;
  const dlg = $("film"), now = localNow(), today = now.slice(0, 10);
  const picked = dayMode() && chosenDay(today) !== today ? chosenDay(today) : "";  // the day picked
  const all = f.shows.filter((s) => s.t >= now);
  // Opened while a day is picked on the main page: list that day (if the film plays then), otherwise all days.
  if (!dlg.open) state.sheetDay = dayMode() && pickedDay(today) && all.some((s) => s.t.startsWith(pickedDay(today))) ? pickedDay(today) : "";
  const filtered = state.showAll ? all : all.filter((s) => showMatches(s, now));
  const atPicked = state.sheetCinemas.size ? filtered.filter((s) => state.sheetCinemas.has(s.cinema)) : filtered;
  const withTags = (keys) => atPicked.filter((s) => keys.every((k) => hasTag(s, k)));
  const shown = withTags([...state.sheetTags]);  // picked tags all apply: "IMAX" + "Engelsk tekst" = IMAX with English subtitles
  const listed = state.sheetDay ? shown.filter((s) => s.t.startsWith(state.sheetDay)) : shown;
  const hidden = all.length - filtered.length;
  const byDay = {};
  for (const s of listed) (byDay[s.t.slice(0, 10)] ||= []).push(s);
  const meta = [KIND_BADGE[f.kind], f.year, f.runtime ? `${f.runtime} min` : "", f.director ? `Directed by ${f.director}` : "", f.countries.slice(0, 3).join(", "), f.genres.slice(0, 3).join(", ")].filter(Boolean).join(" · ");
  const on = isWatched(f);
  const days = Object.entries(byDay).map(([day, shows]) => `
    <div class="day${day === today ? " today" : ""}${day === picked ? " picked" : ""}"><h4>${dayLabel(day + "T00:00")}<small>${day.split("-").reverse().join(".")}</small></h4>
    <div class="stubs">${shows.map(stubHtml).join("")}</div></div>`).join("");
  let empty = "";
  if (!all.length && f.scope === "Canada") empty = `<p class="hiddenNote">Opens in Canadian cinemas ${dayLabel(f.premiere)}. No ${esc(state.data.location)} cinema has scheduled it yet; it moves to On sale as soon as one lists showtimes.${on ? "" : " Add it to your watchlist to have it highlighted then."}</p>`;
  else if (!all.length && f.elsewhere?.length) empty = `<p class="hiddenNote">Premiere ${dayLabel(f.premiere)}. Showings so far only in ${esc(f.elsewhere.join(", "))}; none in ${esc(state.data.location)} yet.</p>`;
  else if (!all.length) empty = `<p class="hiddenNote">${f.premiere ? `Premiere ${dayLabel(f.premiere)}${f.premiereConfirmed ? "" : " (not confirmed)"}. ` : ""}No showings announced in ${esc(state.data.location)} yet.${on ? " You'll see it marked as new when tickets go on sale." : " Add it to your watchlist to have it highlighted when tickets go on sale."}</p>`;
  if (all.length && !listed.length && (state.sheetCinemas.size || state.sheetTags.size || state.sheetDay)) empty = `<p class="hiddenNote">No showings match what you picked. <button class="linkbtn" data-sheetclear="1">Show all</button></p>`;
  const hiddenNote = hidden ? `<p class="hiddenNote">${hidden} showing${hidden > 1 ? "s" : ""} hidden by your filters. <button class="linkbtn" data-showall="1">Show all</button></p>`
    : state.showAll && all.some((s) => !showMatches(s, now)) ? `<p class="hiddenNote"><button class="linkbtn" data-showall="0">Apply my filters</button></p>` : "";
  // Badges that only describe the film; under the poster, the menus and day strip that narrow its showings.
  const info = isNew(f) ? `<span class="badge new">${f.status === "on_sale" ? "New on sale" : "Newly announced"}</span>` : "";
  const ext = extLinks(f), picks = sheetPicks(all, shown, withTags, today);
  const stripAt = dlg.open ? dlg.querySelector(".dstrip")?.scrollLeft : undefined;  // re-rendering: keep the strip where it was
  dlg.innerHTML = `<form method="dialog" class="dlg-close"><button class="x" aria-label="Close">×</button></form>
    <div class="fhead">${f.poster ? `<button type="button" class="pzoom" data-zoom="${esc(f.id)}" aria-label="Enlarge the poster of ${esc(titleOf(f))}">${posterHtml(f)}</button>` : posterHtml(f)}<div class="ftext">
      ${info ? `<div class="badges">${info}</div>` : ""}
      <h2 id="filmTitle">${esc(titleOf(f))}</h2>
      ${otherTitles(f).length ? `<div class="alt">${esc(otherTitles(f).join(" · "))}</div>` : ""}
      <div class="meta">${esc(meta)}</div>
      ${ext.top}
      ${f.blurb ? `<div class="blurb${state.blurbOpen === f.id ? " open" : ""}"><p id="blurbText">${esc(f.blurb)}</p><button type="button" class="linkbtn" data-blurb="${esc(f.id)}" aria-controls="blurbText" aria-expanded="${state.blurbOpen === f.id}" hidden>${state.blurbOpen === f.id ? "Show less" : "Show more"}</button></div>` : ""}
    </div>
    <div class="fmore">
      <div class="actions">
        <button class="btn primary${on ? "" : " accent"}" data-star="${esc(f.id)}">${heart(on)} ${on ? "On your watchlist" : "Add to watchlist"}</button>
        <button class="btn ghost share" data-share="${esc(f.id)}" aria-label="${canShare ? "Share" : "Copy link"}">${canShare ? shareIcon : linkIcon}<span class="lbl">${canShare ? "Share" : "Copy link"}</span></button>
        <div class="dd"><button class="btn ghost icon" aria-haspopup="menu" aria-expanded="false" aria-controls="sheetMoreMenu" data-sheetmenu="sheetMoreMenu" aria-label="More" title="More">${moreIcon}</button>
          <div class="menu right" id="sheetMoreMenu" role="menu" hidden>${ext.menu}${ext.menu ? '<div class="menusep"></div>' : ""}<button role="menuitem" data-hide="${esc(f.id)}"><span class="mi">${isHidden(f) ? eyeOn : eyeOff}</span>${isHidden(f) ? "Show this film again" : "Hide this film"}</button></div></div>
      </div>
      ${friendsOn(f).length ? `<div class="friendline">${friendsStack(f)}<span>${esc(friendsLine(friendsOn(f)))}</span></div>` : ""}
    </div>
    </div>
    ${picks.menus || picks.strip ? `<div class="showings"><div class="showhead"><h3>Showings</h3>${picks.menus}</div>${picks.strip}</div>` : ""}
    <div class="days">${days}${hiddenNote}${empty}</div>
    <form method="dialog" class="sheetbar"><button class="btn ghost">Close</button></form>`;
  if (!dlg.open) { dlg.showModal(); dlg.scrollTop = 0; }  // always start at the top, with the title and poster
  fitBlurb();
  const strip = dlg.querySelector(".dstrip"), dayOn = strip?.querySelector('[aria-pressed="true"]');
  if (!strip) return;
  // Just opened: the picked day in view (centred), only if it's off the end, so "All days" stays visible when it can.
  if (stripAt !== undefined) strip.scrollLeft = stripAt;
  else if (dayOn && dayOn.offsetLeft + dayOn.offsetWidth > strip.clientWidth) strip.scrollLeft = dayOn.offsetLeft - (strip.clientWidth - dayOn.offsetWidth) / 2;
  fitDayStrip(strip);
  strip.addEventListener("scroll", () => fitDayStrip(strip), { passive: true });
}

// The strip of days is one row. On wider screens it has arrows at both ends (hidden when every day fits), each dimmed
// once the strip is scrolled to that end; on phones it is swiped.
function fitDayStrip(strip) {
  const max = strip.scrollWidth - strip.clientWidth, [prev, next] = strip.parentElement.querySelectorAll(".dnav");
  strip.parentElement.classList.toggle("fits", max <= 1);
  prev.disabled = strip.scrollLeft <= 1;
  next.disabled = strip.scrollLeft >= max - 1;
  strip.classList.toggle("more-l", !prev.disabled);  // fades the edge that has more days beyond it
  strip.classList.toggle("more-r", !next.disabled && max > 1);
}
addEventListener("resize", () => { const strip = $("film").querySelector(".dstrip"); if (strip) fitDayStrip(strip); });

// The film sheet's Cinemas and Format menus (pick several in each) and its strip of days (one, or all).
// Menus only appear when they can narrow something: two or more cinemas, a tag on some showings but not all.
function sheetPicks(all, shown, withTags, today) {
  const cinemas = Object.entries(all.reduce((a, s) => ((a[s.cinema] = (a[s.cinema] || 0) + 1), a), {}));
  const tags = sheetTagList(all), cin = [...state.sheetCinemas], fmt = tags.filter((e) => state.sheetTags.has(e.k));
  const item = (attr, on, label, n, extra = "") => `<button role="menuitemcheckbox" aria-checked="${on}" ${attr}${extra}>${esc(label)}${n ? ` <span class="n">${n}</span>` : ""}</button>`;
  const dd = (id, name, picked, items) => `<div class="dd"><button class="pill sm${picked ? " on" : ""}" aria-haspopup="menu" aria-expanded="false" aria-controls="${id}" data-sheetmenu="${id}"><span class="lbl">${esc(name)}</span><span class="chev" aria-hidden="true">▾</span></button>
    <div class="menu" id="${id}" role="menu" hidden>${items}</div></div>`;
  let menus = "";
  if (cinemas.length > 1) menus += dd("sheetCinMenu", !cin.length ? "All cinemas" : cin.length === 1 ? cin[0] : `${cin[0]} +${cin.length - 1}`, cin.length,
    item('data-sheetclear="cinemas"', !cin.length, "All cinemas", 0, ' class="plain"') + '<div class="menusep"></div>'
    + cinemas.map(([c, n]) => item(`data-sheetcinema="${esc(c)}"`, state.sheetCinemas.has(c), c, n)).join(""));
  // Picked tags all apply ("IMAX" + "Engelsk tekst" = IMAX with English subtitles). One that no showing has together
  // with those picked is dimmed; picking it picks it alone.
  if (tags.length) menus += dd("sheetFmtMenu", fmt.length ? fmt.map((e) => e.label).join(" + ") : "Any format", fmt.length,
    item('data-sheetclear="formats"', !fmt.length, "Any format", 0, ' class="plain"') + '<div class="menusep"></div>'
    + tags.map((e) => {
      const on = state.sheetTags.has(e.k), alone = !on && !withTags([...state.sheetTags, e.k]).length;
      return item(`data-sheettag="${esc(e.k)}"`, on, e.label, e.n, alone ? ` class="alone" title="None of these is also ${esc(fmt.map((x) => x.label).join(" + "))}; picks ${esc(e.label)} alone"` : "");
    }).join(""));
  // Days with showings under what's picked; a picked day stays even if nothing is left on it, so it can be unpicked.
  const days = [...new Set(shown.map((s) => s.t.slice(0, 10)).concat(state.sheetDay || []))].sort();
  const dayBtn = (day, top, bottom) => `<button data-sheetday="${day || "all"}" aria-pressed="${state.sheetDay === day}"><span>${top}</span><b>${bottom}</b></button>`;
  const strip = days.length > 1 || state.sheetDay ? `<div class="dwrap"><button class="dnav" data-daynav="-1" aria-label="Earlier days">‹</button>
    <div class="dstrip" role="group" aria-label="Day">${dayBtn("", "All", "days")}${days.map((day) => {
    const d = asDate(day), name = day === today ? "Today" : day === addDays(today, 1) ? "Tmrw" : WEEKDAYS[d.getUTCDay()];
    return dayBtn(day, name, `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`);
  }).join("")}</div><button class="dnav" data-daynav="1" aria-label="Later days">›</button></div>` : "";
  return { menus: menus ? `<div class="pmenus">${menus}</div>` : "", strip: strip ? `<div class="picks">${strip}</div>` : "" };
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
  const first = !state.routed;
  state.routed = true;
  if (m) {
    if (first && state.page !== "films") { state.page = "films"; render(); }  // a link straight to a film: the films list is behind it
    openFilm(decodeURIComponent(m[1]));
    return;
  }
  if ($("film").open) $("film").close();
  if (location.hash === "#feed" || location.hash === "#new") setPage("feed");  // #new: the old "What's new" link
  else setPage("films");
}

$("film").addEventListener("close", () => {
  state.showAll = false;
  state.blurbOpen = "";
  state.sheetCinemas.clear();
  state.sheetTags.clear();
  state.sheetDay = "";
  if (location.hash.startsWith("#film/")) history.pushState("", document.title, location.pathname + location.search + (state.page === "films" ? "#films" : "#feed"));
  render();  // back in the feed or list: show any heart or hide changes
});
$("film").addEventListener("click", (e) => { if (e.target === $("film")) $("film").close(); });
$("account").addEventListener("click", (e) => { if (e.target === $("account")) $("account").close(); });

// ---------------------------------------------------------------- events
// The watchlist syncs as changes (films added, films removed), not as a whole list: sending a whole list from a device that
// is behind would bring back films removed elsewhere. Changes not yet saved to the account are remembered here (and across
// reloads) until they are, and applied on top of the account's list.
const wlPending = { add: new Set(local.get("wlAdd", [])), del: new Set(local.get("wlDel", [])) };
function wlChange(ids, adding) {
  for (const i of ids) { (adding ? wlPending.add : wlPending.del).add(i); (adding ? wlPending.del : wlPending.add).delete(i); }
  local.set("wlAdd", [...wlPending.add]); local.set("wlDel", [...wlPending.del]);
}
const wlApply = (base) => { const w = new Set(base); wlPending.add.forEach((i) => w.add(i)); wlPending.del.forEach((i) => w.delete(i)); return w; };
// Hidden films (prefs.hidden) sync the same way: films hidden (id -> value) and films un-hidden, not the whole map.
const hidPending = { set: local.get("hidSet", {}), del: new Set(local.get("hidDel", [])) };
function hidChange(id, value) {  // value undefined: un-hidden
  if (value === undefined) { hidPending.del.add(id); delete hidPending.set[id]; } else { hidPending.set[id] = value; hidPending.del.delete(id); }
  local.set("hidSet", hidPending.set); local.set("hidDel", [...hidPending.del]);
}
const hidApply = (base) => { const h = { ...base, ...hidPending.set }; hidPending.del.forEach((i) => delete h[i]); return h; };
const sameHidden = (a, b) => Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((k) => a[k] === b[k]);
function savePrefs() { local.set("prefs", state.prefs); local.set("watchlist", [...state.watchlist]); queueSync(); }

function setCollapsed(key, shut) {
  shut ? state.collapsed.add(key) : state.collapsed.delete(key);
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-sec],[data-collapseall],[data-star],[data-hide],[data-share],[data-showall],[data-sheetcinema],[data-sheettag],[data-sheetclear],[data-sheetday],[data-sheetmenu],[data-daynav],[data-clearall],[data-zoom],[data-blurb],[data-chip],[data-open-prefs],button[data-f]");
  if (!t) return;
  if (t.dataset.zoom) { openZoom(t.dataset.zoom, t); return; }
  if (t.dataset.blurb) {
    const b = t.closest(".blurb"), open = b.classList.toggle("open");
    state.blurbOpen = open ? t.dataset.blurb : "";
    t.textContent = open ? "Show less" : "Show more"; t.setAttribute("aria-expanded", open);
    return;
  }
  if (t.dataset.sec) { // collapse / expand; keep the header in view if it was pinned
    const i = +t.dataset.sec, s = state.sections[i], key = s.key;
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
    for (const s of state.sections) setCollapsed(s.key, t.dataset.collapseall === "1");
    closeMenus(); renderGrid(); window.scrollTo({ top: 0 }); return;
  }
  if (t.dataset.clearall === "q") { clearSearch(); if (!activeFilters()) return; }
  if (t.dataset.f === "reset" || t.dataset.clearall) { resetFilters(); return; }
  if (t.dataset.f === "cinemas-more") { state.cinemasOpen = !state.cinemasOpen; renderFilters(); return; }
  if (t.dataset.chip) { removeChip(t.dataset.chip); return; }
  if (t.hasAttribute("data-open-prefs")) { openPrefs(); return; }
  if (t.dataset.f === "friends-clear") { state.friendSel = []; render(); return; }
  if (t.dataset.f === "cinemas-clear") {
    setCinemas(state.prefs.cinemas.filter((c) => !state.data.cinemas.includes(c)));
    savePrefs(); render(); return;
  }
  if (t.dataset.share) { e.preventDefault(); shareFilm(findFilm(t.dataset.share)); return; }
  if (t.dataset.hide) { e.preventDefault(); toggleHidden(findFilm(t.dataset.hide)); return; }
  if (t.dataset.star) {
    e.preventDefault();
    const f = findFilm(t.dataset.star);
    flushRender();
    const was = isWatched(f);
    if (was) { f.ids.forEach((id) => state.watchlist.delete(id)); wlChange(f.ids, false); savePrefs(); }
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
  } else if (t.dataset.sheetmenu) { toggleMenu(t, $(t.dataset.sheetmenu)); return;
  } else if (t.dataset.daynav) {
    const strip = t.parentElement.querySelector(".dstrip");
    strip.scrollBy({ left: Math.sign(+t.dataset.daynav) * strip.clientWidth * 0.8, behavior: "smooth" }); return;
  } else if (t.dataset.sheetcinema || t.dataset.sheettag || t.dataset.sheetclear || t.dataset.sheetday) {
    const { sheetcinema: c, sheettag: k, sheetclear: clear, sheetday: day } = t.dataset;
    if (c) state.sheetCinemas.has(c) ? state.sheetCinemas.delete(c) : state.sheetCinemas.add(c);
    else if (k) {
      if (state.sheetTags.has(k)) state.sheetTags.delete(k);
      else if (t.classList.contains("alone")) state.sheetTags = new Set([k]);
      else state.sheetTags.add(k);
    } else if (day) state.sheetDay = day === "all" ? "" : day;
    else {
      if (clear !== "formats") state.sheetCinemas.clear();
      if (clear !== "cinemas") state.sheetTags.clear();
      if (clear === "1") state.sheetDay = "";
    }
    // A menu stays open while picking in it, with the same item focused.
    const menu = t.closest(".menu")?.id, focus = menu && [...t.attributes].find((a) => a.name.startsWith("data-sheet"));
    openFilm(location.hash.slice(6));
    if (menu && $(menu)) {
      $(menu).hidden = false;
      $("film").querySelector(`[data-sheetmenu="${menu}"]`).setAttribute("aria-expanded", "true");
      $(menu).querySelector(`[${focus.name}="${CSS.escape(focus.value)}"]`)?.focus();
    }
    return;
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

function resetFilters() {
  state.tix = "all"; state.friendSel = [];
  state.prefs.hideDubbed = false; state.prefs.englishSubs = false; state.prefs.audioEnNo = false;
  state.prefs.cinemas = state.prefs.cinemas.filter((c) => !state.data.cinemas.includes(c));
  local.set("tix", "all"); savePrefs(); render();
}
// The filters in force, as removable chips above the list (nothing shows when none are).
function activeChips() {
  const chips = [];
  if (state.tix !== "all") chips.push(["tix", state.tix === "on" ? "Tickets on sale" : "Not on sale yet"]);
  if (DUBBING_REGIONS.includes(state.region) && state.prefs.hideDubbed) chips.push(["dub", "Original language only"]);
  if (state.region === "oslo" && state.prefs.englishSubs) chips.push(["en", "English subtitles only"]);
  if (state.region === "costadelsol" && state.prefs.audioEnNo) chips.push(["audio", "English or Norwegian audio"]);
  const mine = myCinemas();  // several cinemas are one chip, to keep the line short (the panel lists them)
  if (mine.length === 1) chips.push(["cinema:" + mine[0], mine[0]]);
  else if (mine.length > 1) chips.push(["cinemas", `${mine.length} cinemas`]);
  return chips;
}
function renderActiveChips() {
  const chips = activeChips(), el = $("activeChips"), btn = $("filtersBtn");
  el.hidden = !chips.length;
  // The chips sit on their own row, to the right of the Filters button, which never moves. "Clear all" stays at the end of the line.
  const list = $("chipList");
  list.querySelectorAll(".achip").forEach((n) => n.remove());
  list.insertAdjacentHTML("beforeend", chips.map(([k, l]) => `<button type="button" class="achip" data-chip="${esc(k)}" aria-label="Remove filter: ${esc(l)}">${esc(l)} <span aria-hidden="true">×</span></button>`).join(""));
  $("chipsClear").hidden = chips.length < 2;
}
function removeChip(k) {
  if (k === "all") { resetFilters(); return; }
  if (k === "tix") { state.tix = "all"; local.set("tix", "all"); }
  else if (k === "dub") state.prefs.hideDubbed = false;
  else if (k === "en") state.prefs.englishSubs = false;
  else if (k === "audio") state.prefs.audioEnNo = false;
  else if (k === "cinemas") setCinemas(state.prefs.cinemas.filter((x) => !state.data.cinemas.includes(x)));  // this region's cinemas
  else if (k.startsWith("cinema:")) setCinemas(state.prefs.cinemas.filter((x) => x !== k.slice(7)));
  savePrefs(); render();
}

// Shorts, live & stage and talks are turned off by default (Preferences); a line under the list says how many that hides.
function hiddenKindsLine() {
  const hide = state.prefs.hideKinds || [];
  if (!hide.length || state.q.trim() || state.onlyWatch || state.onlyFriends) return "";
  state.prefs.hideKinds = [];  // what the list would hold with everything on
  let rows; try { rows = buildRows(); } finally { state.prefs.hideKinds = hide; }
  const n = {}; for (const r of rows) { const k = r.f.kind || "film"; if (hide.includes(k)) n[k] = (n[k] || 0) + 1; }
  const names = { short: ["short", "shorts"], stage: ["live or stage event", "live and stage events"], talk: ["talk or event", "talks and events"] };
  const parts = KINDS.filter(([k]) => n[k]).map(([k]) => `${n[k]} ${names[k][n[k] === 1 ? 0 : 1]}`);
  const one = Object.values(n).reduce((a, b) => a + b, 0) === 1;
  return parts.length ? `${nameList(parts)} ${one ? "is" : "are"} turned off in <button type="button" class="linkbtn" data-open-prefs>Preferences</button>.` : "";
}

// Filter sidebar: checkboxes and radios.
$("filtersBody").addEventListener("change", (e) => {
  const el = e.target, f = el.dataset.f;
  if (f === "within") { state.within = el.value; local.set("within2", state.within); render(); return; }
  if (f === "tix") { state.tix = el.value; local.set("tix", state.tix); }
  else if (f === "friend") state.friendSel = el.checked ? [...new Set([...state.friendSel, el.value])] : state.friendSel.filter((x) => x !== el.value);
  else if (f === "cinema") {
    const list = state.prefs.cinemas;
    setCinemas(el.checked ? [...new Set([...list, el.value])] : list.filter((x) => x !== el.value));
  } else if (f === "spoken") {
    state.prefs.hideDubbed = el.value === "original"; state.prefs.audioEnNo = el.value === "audio";
  } else if (f === "subs") state.prefs.englishSubs = el.value === "subs";
  if (f !== "tix" && f !== "friend") savePrefs();
  render();
});
const setFiltersOpen = (open) => {
  document.body.classList.toggle("filters-open", open);
  $("scrim").hidden = !open;
  $("filtersBtn").setAttribute("aria-expanded", open);
  if (open) $("filtersDone").focus();
};
// On desktop the Filters button toggles the sidebar (remembered); below that it opens the sheet.
// The date button sits beside the search field when the row has room for its name (700px and up), else at the end of the filters row.
const dateInBar = matchMedia("(min-width: 700px)");
const placeDate = () => (dateInBar.matches ? document.querySelector(".bar") : document.querySelector(".filterrow")).append($("dayDD"));
dateInBar.addEventListener("change", placeDate);
placeDate();
const wideScreen = matchMedia("(min-width: 1000px)");
const syncFiltersBtn = () => $("filtersBtn").setAttribute("aria-expanded", wideScreen.matches ? !document.body.classList.contains("filters-off") : document.body.classList.contains("filters-open"));
document.body.classList.toggle("filters-off", local.get("sidebarOff", false));
wideScreen.addEventListener("change", syncFiltersBtn);
syncFiltersBtn();
$("filtersBtn").addEventListener("click", () => {
  if (!wideScreen.matches) { setFiltersOpen(!document.body.classList.contains("filters-open")); return; }
  local.set("sidebarOff", document.body.classList.toggle("filters-off"));
  syncFiltersBtn();
});
$("filtersDone").addEventListener("click", () => { setFiltersOpen(false); $("filtersBtn").focus(); });
$("scrim").addEventListener("click", () => setFiltersOpen(false));
$("filtersShow").addEventListener("click", () => { setFiltersOpen(false); window.scrollTo({ top: 0 }); });

// Search: always a field in the bar. Escape or the × button clears it.
function clearSearch() { $("q").value = ""; state.q = ""; renderGrid(); }
$("q").addEventListener("keydown", (e) => { if (e.key === "Escape" && $("q").value) clearSearch(); });
$("qClear").addEventListener("click", () => { clearSearch(); $("q").focus(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("filters-open")) setFiltersOpen(false); });
$("q").addEventListener("input", (e) => { state.q = e.target.value; renderGrid(); });
// Pill menus (Location, the date picker): a button that opens a small list of choices.
// A menu opens under its button; one that would run off the screen is lined up with the button's other edge instead (and, if
// it's wider than the room either way, nudged back in).
function keepOnScreen(menu) {
  menu.style.left = menu.style.right = "";
  const vw = document.documentElement.clientWidth, edge = 8;
  const host = menu.closest("dialog"), box = host ? host.getBoundingClientRect() : { left: 0, right: vw };  // a menu in a sheet must stay inside it
  const maxRight = Math.min(vw, box.right) - edge, minLeft = Math.max(0, box.left) + edge;
  if (menu.getBoundingClientRect().right <= maxRight) return;
  menu.style.left = "auto"; menu.style.right = "0";
  const left = menu.getBoundingClientRect().left;
  if (left < minLeft) menu.style.right = `${left - minLeft}px`;
}
function closeMenus() {
  document.querySelectorAll(".menu").forEach((m) => { m.hidden = true; m.style.left = m.style.right = ""; });
  document.querySelectorAll("[aria-haspopup]").forEach((b) => b.setAttribute("aria-expanded", "false"));
}
function toggleMenu(btn, menu) {
  const open = menu.hidden;
  closeMenus();
  if (!open) return;
  menu.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  keepOnScreen(menu);
  (menu.querySelector('[aria-checked="true"]') || menu.querySelector("button, a"))?.focus();
}
$("regionBtn").addEventListener("click", () => toggleMenu($("regionBtn"), $("regionMenu")));
document.addEventListener("click", (e) => {
  const pick = e.target.closest("[data-pick]");
  if (pick) {
    closeMenus();
    if (pick.dataset.pick === "region" && pick.dataset.value !== state.region) loadRegion(pick.dataset.value);
    return;
  }
  if (!e.target.closest(".dd")) closeMenus();
});
document.addEventListener("keydown", (e) => {
  const menu = e.target.closest?.(".menu:not(.cal)");  // the date picker has its own arrow keys
  if (e.key === "Escape" && document.querySelector(".menu:not([hidden])")) {
    const btn = document.querySelector('[aria-haspopup][aria-expanded="true"]');
    e.preventDefault();  // in the film sheet, Escape closes the menu, not the sheet
    closeMenus(); btn?.focus(); return;
  }
  if (menu && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    const items = [...menu.querySelectorAll("button, a")], i = items.indexOf(document.activeElement);
    items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
  }
});
$("listSwitch").addEventListener("click", (e) => {
  const b = e.target.closest("[data-list]");
  if (!b) return;
  setList(b.dataset.list);
});

// Section headers stick just below the controls bar; keep that offset in a CSS variable.
const syncStickTop = () => {
  const c = $("controls"), top = document.querySelector(".top");
  const hdr = getComputedStyle(top).position === "sticky" ? top.offsetHeight : 0;  // the header stays on screen from 640px
  const bar = getComputedStyle(c).position === "sticky" ? c.offsetHeight : 0;
  document.documentElement.style.setProperty("--hdr-h", hdr + "px");
  document.documentElement.style.setProperty("--stick-top", hdr + bar + "px");
};
new ResizeObserver(syncStickTop).observe($("controls"));
new ResizeObserver(syncStickTop).observe(document.querySelector(".top"));
window.addEventListener("resize", syncStickTop);
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- the watchlist works without an account
// A signed-out visitor's list stays on this device; signing in later adds it to the account (see wlChange / loadProfile).
function requestWatch(f) { state.watchlist.add(f.id); wlChange([f.id], true); unhideFilm(f); return true; }
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
  wlChange([f.id], true);
  unhideFilm(f);
  state.profile ? savePrefs() : local.set("watchlist", [...state.watchlist]);  // profile not loaded yet: the merge will save it
  if ($("account").open) $("account").close();
  render();
  if ($("film").open) openFilm(f.id);
  toast(`Added ${titleOf(f)} to your watchlist`);
}
// The link to a film's own page (site/f/..., written by scripts/write-film-pages.py): a chat app shows that film's poster
// and title in the preview, which it can't for "#film/..." links (it never sees the fragment).
async function shareFilm(f) {
  const url = new URL(`f/${state.region}/${encodeURIComponent(f.id)}/`, location.href.split(/[?#]/)[0]).href;
  if (canShare) {
    try { await navigator.share({ title: titleOf(f), url }); } catch {}  // closing the sheet counts as an error: nothing to do
    return;
  }
  try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch { toast("Couldn't copy the link"); }
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
$("account").addEventListener("close", () => {
  if (!state.user) {
    state.pendingWatch = null;
    if (!state.pendingEmail) { state.pendingFriend = ""; try { sessionStorage.removeItem("kino:pendingFriend"); } catch {} }  // mid sign-in (code sent) the invite is kept
  }
  state.acctView = "";
});

// ---------------------------------------------------------------- accounts (Supabase magic link)
const cfg = window.KINO_CONFIG || {};
const sb = cfg.supabaseUrl && cfg.supabaseKey && window.supabase ? window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseKey) : null;
let syncTimer = null;

function queueSync() {
  if (!sb || !state.user || !state.profile) return;  // before the account's saved list has loaded, changes stay local and get merged in
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => saveAccount({}, true), 600);
}
// Saving to the account always starts from what the account has now, with this device's unsaved changes (watchlist and hidden
// films) applied on top, so a device that is behind can't bring back what was removed elsewhere. One save at a time, so two
// can't overwrite each other. withPrefs: also write this device's settings (after the person changed one). Otherwise only
// pending changes are saved, and this is also how changes made on another device reach this one (when the app is opened or
// comes back to the front). Returns whether it worked.
let acctQueue = Promise.resolve();
const saveAccount = (extra = {}, withPrefs = false) => (acctQueue = acctQueue.then(() => saveAccountNow(extra, withPrefs), () => saveAccountNow(extra, withPrefs)));
const pushProfile = (extra = {}) => saveAccount(extra, true);
const syncWatchlist = () => saveAccount({}, false);
async function saveAccountNow(extra, withPrefs) {
  if (!sb || !state.user || !state.profile) return false;
  const uid = state.user.id;
  const { data, error } = await sb.from("profiles").select("prefs,watchlist").eq("id", uid).maybeSingle();
  if (error || !data || state.user?.id !== uid) { if (error) console.error("Saving settings failed:", error.message); return false; }
  const remotePrefs = data.prefs || {}, remoteList = data.watchlist || [];
  const sent = { add: [...wlPending.add], del: [...wlPending.del], set: { ...hidPending.set }, hdel: [...hidPending.del] };
  const list = wlApply(remoteList), hidden = hidApply(remotePrefs.hidden || {});
  const row = { ...extra };
  if (withPrefs) row.prefs = { ...state.prefs, hidden };
  else if (!sameHidden(hidden, remotePrefs.hidden || {})) row.prefs = { ...remotePrefs, hidden };
  if (list.size !== remoteList.length || remoteList.some((i) => !list.has(i))) row.watchlist = [...list];
  if (Object.keys(row).length) {
    const { error: e2 } = await sb.from("profiles").update({ ...row, updated_at: new Date().toISOString() }).eq("id", uid);
    if (e2) { console.error("Saving settings failed:", e2.message); return false; }  // the changes stay pending for the next try
  }
  // Saved now (unless changed again meanwhile).
  sent.add.forEach((i) => { if (wlPending.add.has(i)) wlPending.add.delete(i); });
  sent.del.forEach((i) => { if (wlPending.del.has(i)) wlPending.del.delete(i); });
  Object.entries(sent.set).forEach(([k, v]) => { if (hidPending.set[k] === v) delete hidPending.set[k]; });
  sent.hdel.forEach((i) => { if (hidPending.del.has(i)) hidPending.del.delete(i); });
  local.set("wlAdd", [...wlPending.add]); local.set("wlDel", [...wlPending.del]); local.set("hidSet", hidPending.set); local.set("hidDel", [...hidPending.del]);
  // Show the result (plus anything changed while this was running).
  const shownList = wlApply(list), shownHidden = hidApply(hidden);
  const listChanged = shownList.size !== state.watchlist.size || [...shownList].some((i) => !state.watchlist.has(i));
  const hiddenChanged = !sameHidden(shownHidden, state.prefs.hidden || {});
  if (listChanged || hiddenChanged) {
    state.watchlist = shownList; state.prefs.hidden = shownHidden;
    local.set("watchlist", [...shownList]); local.set("prefs", state.prefs);
    render();
    if ($("film").open && location.hash.startsWith("#film/")) openFilm(location.hash.slice(6));
  }
  return true;
}

async function loadProfile() {
  state.profileFailed = false;
  let { data, error } = await sb.from("profiles").select("subscribed,prefs,watchlist,display_name,friend_code").eq("id", state.user.id).maybeSingle();
  // The friends columns arrive with a database update; until it's been applied, load without them.
  if (error) ({ data, error } = await sb.from("profiles").select("subscribed,prefs,watchlist").eq("id", state.user.id).maybeSingle());
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
  // A brand-new account takes on this device's list; after that the account's list is the truth, with this device's unsaved
  // changes on top. (Merging the two would bring back films removed on another device.)
  // The same goes for hidden films.
  const first = !Object.keys(remote).length;
  if (first) { wlChange([...state.watchlist], true); for (const [k, v] of Object.entries(localHidden)) hidChange(k, v); }
  state.watchlist = wlApply(data.watchlist || []);
  state.prefs.hidden = hidApply(remote.hidden || {});
  if (state.data) adoptSaleDates();
  local.set("prefs", state.prefs); local.set("watchlist", [...state.watchlist]);
  if (first || wlPending.add.size || wlPending.del.size || hidPending.del.size || Object.keys(hidPending.set).length) await saveAccount({}, first);
  render();
  refreshAvatar();
  if ($("account").open) renderAccount();  // the dialog may already be open: show the loaded settings
  if (data.friend_code) loadFriends();
}

let friendsSeq = 0;
// What friends added lately, for the feed (friends_feed). Without the database update it is simply empty.
async function loadFriendFeed() {
  const uid = state.user?.id;
  if (!sb || !uid) return;
  const { data, error } = await sb.rpc("friends_feed");
  if (state.user?.id !== uid) return;
  state.friendEvents = error ? [] : (data || []).map((r) => ({ id: r.friend_id, name: r.name, film: r.film, at: utcToLocal(r.added_at) }));
  if (state.page === "feed") renderFeed();
  renderNav();
}
async function loadFriends() {
  if (!sb || !state.user) return;
  const uid = state.user.id, seq = ++friendsSeq;
  const { data, error } = await sb.rpc("friends_watchlists");
  if (state.user?.id !== uid || seq !== friendsSeq) return;  // signed out or in as someone else meanwhile, or a newer load is under way
  if (error) { console.error("Loading friends failed:", error.message); state.friendsError = true; if ($("account").open && !typing()) renderAccount(); return; }
  loadFriendFeed();
  const fresh = (data || []).map((r) => ({ id: r.friend_id, name: r.name, list: new Set(r.watchlist || []) }));
  const sig = (fs) => JSON.stringify(fs.map((f) => [f.id, f.name, [...f.list].sort()]));
  const same = sig(fresh) === sig(state.friends) && !state.friendsError;
  state.friendsError = false;
  state.friendsAt = Date.now();
  if (same) return;
  state.friends = fresh;
  render();
  if ($("account").open && !typing()) renderAccount();  // not while something is being typed in it
  if ($("film").open && location.hash.startsWith("#film/")) openFilm(location.hash.slice(6));  // a film sheet that's already open
}
const typing = () => { const a = document.activeElement; return $("account").contains(a) && a.matches("input") && !!(a.value || a.defaultValue); };  // a box in the account dialog with text in it
document.addEventListener("visibilitychange", () => {  // coming back to the app: friends may have added you or changed their lists
  if (document.visibilityState !== "visible" || !state.user || !state.profile) return;
  syncWatchlist();
  if (state.profile.friend_code && Date.now() - state.friendsAt > 60000) loadFriends();
});

// Preferences: what else shows in the film list besides films. Kept on this device, and in the account when signed in.
const PREF_KINDS = [["short", "Shorts", "Short film programmes."], ["stage", "Live & stage", "Opera, ballet, theatre and concerts."], ["talk", "Talks & events", "Lectures, workshops, quizzes and special evenings."]];
function openPrefs() { state.acctView = "prefs"; renderAccount(); $("account").showModal(); $("account").scrollTop = 0; }
function renderPrefs(msg) {
  const hide = new Set(state.prefs.hideKinds || []);
  $("accountBody").innerHTML = `<h2 id="accountTitle">Preferences</h2>
    <p class="lead">Films are always listed. Choose what else you'd like to see in the list${state.user ? ", and in your email notifications" : ""}.</p>
    <div class="subh">Also show</div>
    ${PREF_KINDS.map(([k, l, d]) => `<label class="opt"><input type="checkbox" data-pref-kind="${k}"${hide.has(k) ? "" : " checked"}><span>${l}<small>${d}</small></span></label>`).join("")}
    <p class="hint">Searching always finds everything, whatever you choose here.</p>${msg}`;
}
$("account").addEventListener("change", (e) => {
  const k = e.target.dataset?.prefKind;
  if (!k) return;
  const hidden = new Set(state.prefs.hideKinds || []);
  e.target.checked ? hidden.delete(k) : hidden.add(k);
  state.prefs.hideKinds = KINDS.map(([x]) => x).filter((x) => hidden.has(x));
  savePrefs(); render(); renderAccount();
});
$("prefsLink").addEventListener("click", openPrefs);


function renderAccount(message = "", isErr = false) {
  const body = $("accountBody");
  $("account").classList.toggle("wide", (!!state.user && !!state.profile) || state.acctView === "prefs");
  const msg = message ? `<div class="msg${isErr ? " err" : ""}">${esc(message)}</div>` : "";
  if (state.acctView === "prefs") { renderPrefs(msg); return; }
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
    const nameField = `<input type="text" id="name" maxlength="40" placeholder="Name, if you're new (optional)" autocomplete="given-name">`;
    if (state.pendingFriend && !want) {
      const c = state.pendingFriend.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
      body.innerHTML = `<h2 id="accountTitle">Sign in to add a friend</h2>
        <p class="lead">You've been invited to Cinecrab. Sign in or sign up and you'll be friends as soon as you're in.</p>
        <form class="signin" id="signinForm"><input type="email" id="email" required placeholder="you@example.com" autocomplete="email">${nameField}
        <button class="btn accent" type="submit">Send code</button></form>
        <p class="alt">Using Cinecrab from your Home Screen? Links open in Safari, which keeps its own sign-in. Instead, copy this code, open Cinecrab from your Home Screen and enter it under Account → Friends.</p>
        <div class="codebox">${esc(fmtCode(c))}</div>
        <div class="row codebtns"><button class="btn ghost" data-friend="copyinvite">Copy code</button></div>${msg}`;
      return;
    }
    body.innerHTML = want ? `<h2 id="accountTitle">Sign in to save to your watchlist</h2>
      <p><b>${esc(titleOf(want))}</b> will be added as soon as you're in. Your watchlist is saved to your account, so it follows you to other devices and you can get an email when films on it go on sale.</p>
      <p>Enter your email and we'll send a one-time code. No password, and new emails get an account automatically.</p>
      <form class="signin" id="signinForm"><input type="email" id="email" required placeholder="you@example.com" autocomplete="email">${nameField}
      <button class="btn accent" type="submit">Send code</button></form>${msg}` : `<h2 id="accountTitle">Sign in or sign up</h2>
      <p>Enter your email and we'll send you a one-time code. No password. Signing in syncs your watchlist and filters across devices, and lets you get the daily email of films newly on sale.</p>
      <form class="signin" id="signinForm"><input type="email" id="email" required placeholder="you@example.com" autocomplete="email">${nameField}
      <button class="btn accent" type="submit">Send code</button></form>${msg}`;
    return;
  }
  if (!state.profile) { // signed in, but this account's saved settings haven't arrived yet
    body.innerHTML = `<h2 id="accountTitle">Your account</h2><div class="who">${esc(state.user.email)}</div>
      <p>${state.profileFailed ? "Couldn't load your saved settings. Close this and try again in a moment." : "Loading your settings…"}</p>${msg}`;
    return;
  }
  const p = state.profile;
  if (state.acctView === "email") { renderEmailSettings(msg); return; }
  renderProfile(msg);
}

// ---- account menu (the avatar's dropdown) and its panels: Profile, Friends, Email notifications
const menuIcon = (d) => `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const MENU_ICONS = {
  profile: menuIcon("M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"),
  prefs: menuIcon("M4 7h9M17 7h3M4 17h3M11 17h9M15 4v6M9 14v6"),
  friends: menuIcon("M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.4c2 .7 3.5 2.4 3.5 5.6"),
  email: menuIcon("M4 6h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zM3.5 7.5l8.5 6 8.5-6"),
  signout: menuIcon("M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l-4 4 4 4M6 12h10"),
};
const emailStatus = () => {
  if (!state.profile) return "";
  if (!state.profile.subscribed) return "Off";
  const names = REGIONS.filter((r) => (state.prefs.regions || ["oslo"]).includes(r.key)).map((r) => r.name);
  return `${state.prefs.frequency === "weekly" ? "Weekly" : "Daily"} · ${names.length > 2 ? names.length + " regions" : names.join(", ") || "no region"}`;
};
const prefsStatus = () => {
  const on = PREF_KINDS.filter(([k]) => !(state.prefs.hideKinds || []).includes(k)).map(([, l]) => l);
  return on.length ? `Also ${on.length > 2 ? on.length + " more" : on.join(", ")}` : "Films only";
};
function renderAcctMenu() {
  const p = state.profile, name = p?.display_name || "";
  const item = (k, label, status) => `<button role="menuitem" data-open="${k}"><span class="mi">${MENU_ICONS[k]}</span>${label}${status ? `<small>${esc(status)}</small>` : ""}</button>`;
  $("acctMenu").innerHTML = `<div class="menuhead">${name ? `<b>${esc(name)}</b>` : ""}<span>${esc(state.user.email)}</span></div><div class="menusep"></div>
    ${item("profile", "Profile")}
    ${p?.friend_code ? item("friends", "Friends", state.friends.length ? `${state.friends.length} friend${state.friends.length > 1 ? "s" : ""}` : "Add friends") : ""}
    ${item("email", "Email notifications", emailStatus())}
    ${item("prefs", "Preferences", prefsStatus())}
    <div class="menusep"></div>
    <button role="menuitem" data-open="signout" class="signout"><span class="mi">${MENU_ICONS.signout}</span>Sign out</button>`;
}

function renderProfile(msg) {
  const p = state.profile;
  $("accountBody").innerHTML = `<h2 id="accountTitle">Profile</h2>
    <div class="subh">Email</div><div class="who">${esc(state.user.email)}</div>
    <p class="hint">What you sign in with, and where notification emails go.</p>
    ${p.friend_code ? `<form class="signin namef" id="nameForm"><label for="dispName" class="subh">Name</label>
      <input type="text" id="dispName" maxlength="40" value="${esc(p.display_name || "")}" placeholder="${esc(state.user.email)}" autocomplete="off">
      <p class="hint">Shown to your friends. Left empty, they see your email.</p>
      <button class="btn ghost" type="submit">Save name</button></form>` : ""}${msg}`;
}

function renderEmailSettings(msg) {
  const p = state.profile, on = !!p.subscribed, freq = state.prefs.frequency || "daily", regs = state.prefs.regions || ["oslo"];
  const n = state.prefs.cinemas.length;
  const filters = [n ? `${n} chosen cinema${n > 1 ? "s" : ""}` : "all cinemas", state.prefs.hideDubbed ? "original language only" : "", state.prefs.englishSubs ? "English subtitles only (Oslo)" : "", state.prefs.audioEnNo ? "English or Norwegian audio only (Costa del Sol)" : ""].filter(Boolean).join(", ");
  const names = REGIONS.filter((r) => regs.includes(r.key)).map((r) => r.name);
  const summary = !on ? "Email is off. You won't get any emails from Cinecrab."
    : !names.length ? "Pick at least one region to get emails."
    : `You'll get ${freq === "weekly" ? "one email on Fridays" : "an email each morning there's news"} for ${nameList(names)}, with films newly on sale${state.prefs.announcements ? " and newly announced films" : ""}${state.prefs.watchlistAlways ? ", plus anything on your watchlist" : ""}.`;
  $("accountBody").innerHTML = `<h2 id="accountTitle">Email notifications</h2>
    <p class="lead">Hear about films as soon as they're on sale, without checking the site. Sent only when there's something new for you, and never more than once a day.</p>
    <label class="opt master"><input type="checkbox" id="optSub"${on ? " checked" : ""}>
      <span>Email me about new films<small>Sent to ${esc(state.user.email)}. Every email has an unsubscribe link.</small></span></label>
    <fieldset class="emailopts"${on ? "" : " disabled"}>
      <div class="subh">How often</div>
      <div class="cardopts">${[["daily", "Daily", "An email each morning, only on days with something new."], ["weekly", "Weekly", "One email on Fridays with everything new that week."]]
        .map(([v, t, d]) => `<label class="cardopt"><input type="radio" name="freq" value="${v}"${freq === v ? " checked" : ""}><b>${t}</b><small>${d}</small></label>`).join("")}</div>
      <div class="subh">Where</div>
      <div class="chips">${REGIONS.map((r) => `<label class="opt-chip"><input type="checkbox" data-optregion="${r.key}"${regs.includes(r.key) ? " checked" : ""}> ${r.name}</label>`).join("")}</div>
      <p class="hint">One email per region, at 9:00 local time.</p>
      <div class="subh">What's included</div>
      <label class="opt"><input type="checkbox" checked disabled><span>Films newly on sale<small>Always included. Your film filters apply: ${esc(filters)}.</small></span></label>
      <label class="opt"><input type="checkbox" id="optAnn"${state.prefs.announcements ? " checked" : ""}>
        <span>Newly announced films<small>Films that just got a Norwegian release date or showings, before tickets are on sale.</small></span></label>
      <label class="opt"><input type="checkbox" id="optWatch"${state.prefs.watchlistAlways ? " checked" : ""}>
        <span>Watchlist films, always<small>Email me when a film on my watchlist goes on sale, even if my filters would hide it.</small></span></label>
    </fieldset>
    <div class="summary">${esc(summary)}</div>${msg}`;
}

const personIcon = `<svg class="ico" width="17" height="17" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
// The header's account button: a filled "Sign in" (a person icon when the header is tight), or a round avatar with an initial.
function paintAccount(signedIn, initial, email = "") {
  const ab = $("accountBtn");
  if (signedIn) ab.textContent = initial; else ab.innerHTML = `${personIcon}<span class="lbl">Sign in</span>`;
  ab.classList.toggle("primary", !signedIn);
  ab.classList.toggle("avatar", signedIn);
  ab.setAttribute("aria-label", signedIn ? `Account${email ? ` (${email})` : ""}` : "Sign in");
  ab.setAttribute("aria-haspopup", signedIn ? "menu" : "dialog");
}
// What was last shown (signed in as whom, with which initial) is remembered, so the next visit paints that straight away instead of
// "Sign in" and then the avatar, and doesn't change again once the saved session and the profile have been checked.
const initialOf = (s) => shortName(s)[0]?.toUpperCase() || "?";
function avatarInitial() {
  if (state.profile) return initialOf(state.profile.display_name || state.user.email);
  const c = local.get("acct", null);  // profile not loaded yet: keep what was shown last time for this account
  return c && c.id === state.user.id && c.i ? c.i : initialOf(state.user.email);
}
function setUser(session) {
  state.user = session?.user ? { id: session.user.id, email: session.user.email } : null;
  if (state.user) {
    paintAccount(true, avatarInitial(), state.user.email);
    if (!local.get("acct", null)) local.set("acct", { id: state.user.id, i: avatarInitial() });
  } else { paintAccount(false); local.set("acct", null); }
  if (!state.user) closeMenus();
  if (!state.user) { state.profile = null; state.friends = []; state.friendEvents = []; state.acctView = ""; }
  renderAccount();
  fitHeader();
}
function refreshAvatar() {
  if (!state.user) return;
  const i = avatarInitial();
  $("accountBtn").textContent = i;
  if (state.profile) local.set("acct", { id: state.user.id, i });
}

// The header keeps the title and its buttons on one row, compacting only as far as it must (how wide it is depends on
// the location's name and on being signed in): the location loses its pin, then
// "Sign in" becomes an icon, then the location loses its arrow, then the title gets a little smaller. If even that
// doesn't fit, the buttons wrap under the title rather than run off the screen.
const FIT_STEPS = ["fit-pin", "fit-account", "fit-chev", "fit-title"];
function fitHeader() {
  const top = document.querySelector(".top"), h1 = top.querySelector("h1"), right = top.querySelector(".topright");
  const gap = parseFloat(getComputedStyle(top).columnGap) || 0;
  const nav = top.querySelector(".mainnav"), navW = nav && getComputedStyle(nav).position !== "fixed" ? nav.offsetWidth + gap : 0;  // the nav sits in the row on tablets and desktop
  const fits = () => h1.offsetWidth + gap + navW + right.offsetWidth <= top.clientWidth;
  top.classList.remove(...FIT_STEPS);
  for (const step of FIT_STEPS) { if (fits()) return; top.classList.add(step); }
}
let topWidth = 0;
new ResizeObserver(([e]) => { if (e.contentRect.width !== topWidth) { topWidth = e.contentRect.width; fitHeader(); } }).observe(document.querySelector(".top"));
document.fonts.ready.then(fitHeader);  // the title's font changes its width once it loads

if (sb) {
  $("acctDD").hidden = false;
  const seen = local.get("acct", null);
  if (seen && seen.i) paintAccount(true, seen.i);  // as last time, until the saved session has been checked (setUser)
  // Signed in: the avatar drops a menu (Profile, Friends, Email notifications, Sign out); each opens its own panel.
  $("accountBtn").addEventListener("click", () => {
    if (!state.user) { renderAccount(); $("account").showModal(); return; }
    renderAcctMenu();
    toggleMenu($("accountBtn"), $("acctMenu"));
  });
  $("acctMenu").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-open]");
    if (!b) return;
    closeMenus();
    if (b.dataset.open === "friends") { showFriendsTab(); return; }
    if (b.dataset.open === "signout") { await sb.auth.signOut(); toast("Signed out"); return; }
    state.acctView = b.dataset.open;
    renderAccount();
    $("accountBtn").focus();  // where focus returns when the panel closes
    $("account").showModal();
    $("account").scrollTop = 0;
  });
  let pendingName = "";  // the optional name typed at sign-up; only used when the email turns out to be new
  const sendCode = async (email) => {
    const { error } = await sb.auth.signInWithOtp({ email, options: { shouldCreateUser: true, ...(pendingName ? { data: { display_name: pendingName } } : {}) } });
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
      pendingName = $("name")?.value.trim().slice(0, 40) || "";
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
      if (!state.pendingFriend && !state.pendingWatch && $("account").open) { $("account").close(); toast("Signed in. Your watchlist and filters now sync across devices."); }
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
    if (e.target.id === "resendCode") {
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
    renderWatchInvite();
    settlePendingFriend();
  });

  // Friends: add by code, share, remove (the Friends tab), and the name (Profile).
  async function redeemFriendCode(raw) {
    const { data, error } = await sb.rpc("redeem_friend_code", { code: extractCode(raw) });
    if (error) return { msg: "Couldn't add the friend. Try again in a moment.", err: true, retry: true };
    const who = shortName(data.name);
    if (data.status === "ok") { await loadFriends(); return { msg: `You and ${who} are now friends.` }; }
    if (data.status === "already") { await loadFriends(); return { msg: `You and ${who} are already friends.` }; }
    if (data.status === "self") return { msg: "That's your own code. Send it to a friend instead.", err: true };
    return { msg: "That code wasn't recognised. Check it and try again.", err: true };
  }
  let settling = false;
  async function settlePendingFriend() {
    const code = state.pendingFriend;
    if (!code || settling) return;
    if (!state.user) { promptSignIn(); return; }
    settling = true;
    const r = await redeemFriendCode(code);
    settling = false;
    if (!r.retry) {  // an answer, good or bad; a network error keeps the invite for the next try
      state.pendingFriend = "";
      try { sessionStorage.removeItem("kino:pendingFriend"); } catch {}
    }
    if ($("account").open) $("account").close();
    toast(r.msg);
    if (!r.err) showFriendsTab();
  }
  async function unfriend(id) {
    const fr = state.friends.find((x) => x.id === id), me = state.user.id;
    if (!fr || !confirm(`Remove ${fr.name} as a friend? You'll stop seeing each other's watchlists.`)) return "";
    const { data, error } = await sb.from("friendships").delete().or(`and(a.eq.${me},b.eq.${id}),and(a.eq.${id},b.eq.${me})`).select();
    if (error || !data?.length) return "Couldn't remove them. Try again in a moment.";
    await loadFriends();
    return `Removed ${fr.name}.`;
  }
  $("wlInvite").addEventListener("click", (e) => { if (e.target.closest("[data-wl=signin]")) promptSignIn(); });
  // The same controls live in the Friends tab's panel (no friends yet) and in the dialog (opened by "Add friend").
  const syncAdd = (host) => { const i = host.querySelector("[name=code]"), b = host.querySelector("[data-fr-form] [type=submit]"); if (b && i) b.disabled = !i.value.trim(); };
  for (const host of [$("friendsPanel"), $("friendsAddBody"), $("feedPage")]) {
    host.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-fr]");
      if (!b) return;
      const what = b.dataset.fr;
      if (what === "signin") promptSignIn();
      else if (what === "share") toast(await clip(inviteLink()) ? "Invite link copied" : "Couldn't copy the link");
      else if (what === "remove") { const m = await unfriend(b.dataset.id); if (m) toast(m); }
    });
    host.addEventListener("input", () => syncAdd(host));
    host.addEventListener("submit", async (e) => {
      const form = e.target.closest("[data-fr-form]");
      if (!form) return;
      e.preventDefault();
      const input = form.querySelector("[name=code]"), btn = form.querySelector("[type=submit]");
      if (!input.value.trim()) { input.focus(); return; }
      btn.disabled = true;
      const r = await redeemFriendCode(input.value);
      btn.disabled = false;
      toast(r.msg);
      if (!r.err) { input.value = ""; if ($("friendsAdd").open) $("friendsAdd").close(); renderFriendsPanel(true); renderFriendsDialog(); } else syncAdd(host);
    });
  }
  $("addFriendBtn").addEventListener("click", () => {
    if (!state.user) { promptSignIn(); return; }
    if (!state.profile?.friend_code) { toast(state.profileFailed ? "Couldn't load your account. Try again in a moment." : "Loading your account…"); return; }
    $("friendsAdd").showModal(); renderFriendsDialog();
  });
  $("friendsAdd").addEventListener("click", (e) => { if (e.target === $("friendsAdd")) $("friendsAdd").close(); });
  $("account").addEventListener("click", async (e) => {  // the invite page's "Copy code" (signed out)
    const b = e.target.closest('[data-friend="copyinvite"]');
    if (!b) return;
    const ok = await clip(fmtCode(state.pendingFriend.replace(/[^A-Za-z0-9]/g, "").toUpperCase()));
    renderAccount(ok ? "Code copied." : "Couldn't copy. Select the code and copy it.", !ok);
  });
  $("account").addEventListener("submit", async (e) => {
    if (e.target.id === "nameForm") {
      e.preventDefault();
      const v = $("dispName").value.trim().slice(0, 40);
      state.profile = { ...state.profile, display_name: v || null };
      const ok = await pushProfile({ display_name: v || null });
      refreshAvatar();
      renderAccount(ok ? "Name saved." : "Couldn't save your name. Try again in a moment.", !ok);
    }
  });
}

// An invite link (?friend=CODE): remember the code; it's redeemed once we know who's signed in (settlePendingFriend).
function takeFriendLink() {
  const u = new URL(location.href), code = u.searchParams.get("friend");
  if (!code) return;
  u.searchParams.delete("friend");
  history.replaceState(null, "", u.pathname + u.search + u.hash);
  if (!sb) return;
  state.pendingFriend = code.slice(0, 20);
  try { sessionStorage.setItem("kino:pendingFriend", state.pendingFriend); } catch {}
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
    if (adoptSaleDates()) savePrefs();
  } catch (e) {
    $("sub").textContent = `Couldn't load the ${reg.name} programme. Reload to try again.`;
    $("grid").innerHTML = ""; $("feedPage").innerHTML = "";  // no placeholders for something that isn't coming
    return;
  }
  const g = state.data.generated;
  $("sub").textContent = `${state.data.location} · ${state.data.sources || "Filmweb + Cinemateket"} · updated ${dayLabel(g, { short: true })} ${hhmm(g)}`;
  $("foot").innerHTML = reg.key === "costadelsol"
    ? `Showtimes from <a href="https://www.carteleracines.es" target="_blank" rel="noopener">CarteleraCines.es</a>, which collects them from the cinemas' ticketing systems, and for mk2 Miramar straight from <a href="https://www.mk2cines.es/miramar-fuengirola/es/miramar-fuengirola/vose" target="_blank" rel="noopener">mk2's own website</a>, refreshed twice a day and covering about two weeks ahead. Some ticket links may pay CarteleraCines a commission; the price doesn't change. Times are Spanish time. Showings in the original language are marked "Original language" (with Spanish subtitles). Some posters come from TMDB; this product uses the TMDB API but is not endorsed or certified by TMDB.`
    : reg.key === "winnipeg"
    ? `Data from <a href="https://moviescout.ca" target="_blank" rel="noopener">MovieScout</a>, <a href="https://www.cinemaclock.com" target="_blank" rel="noopener">CinemaClock</a> and the <a href="https://davebarbercinematheque.com" target="_blank" rel="noopener">Dave Barber Cinematheque</a>, refreshed twice a day. CinemaClock covers about the next week; showings further ahead are the cinemas' advance sales. Times are Manitoba time. Some posters come from TMDB; this product uses the TMDB API but is not endorsed or certified by TMDB.`
    : reg.key === "oslo"
    ? `Data from <a href="https://www.filmweb.no" target="_blank" rel="noopener">Filmweb</a>, <a href="https://www.cinemateket.no" target="_blank" rel="noopener">Cinemateket</a>, <a href="https://www.eventbrite.com/o/revier-117564493841" target="_blank" rel="noopener">Revier Film Club</a> (Eventbrite), <a href="https://deichman.hoopla.no" target="_blank" rel="noopener">Deichman Bjørvika</a> (Hoopla) and <a href="https://www.sommerrohouse.com/kultur/filmopplevelser/" target="_blank" rel="noopener">Sommerro</a>, refreshed several times a day. Tickets are bought on the cinemas' own sites; Revier's and Deichman's screenings are free but need a reserved seat, and Sommerro's are prepaid dinner + film packages. Some posters come from TMDB; this product uses the TMDB API but is not endorsed or certified by TMDB.`
    : `Data from <a href="https://www.landmarkcinemas.com" target="_blank" rel="noopener">Landmark Cinemas</a>, <a href="https://www.cinemaclock.com" target="_blank" rel="noopener">CinemaClock</a> and the <a href="https://evanstheatre.ca" target="_blank" rel="noopener">Evans Theatre</a>. Small theatres sell tickets at the door. Times are Manitoba time. Some posters come from TMDB; this product uses the TMDB API but is not endorsed or certified by TMDB.`;
  document.title = `Cinecrab · ${reg.name}`;
  if (!state.routed) state.page = /^#(feed|new)$/.test(location.hash) ? "feed" : "films";  // which page we open on (Films unless asked for the feed), before anything is drawn
  render();
  route();
}

(async function boot() {
  // The top-right buttons are held back (still taking their space) until what they say and how they fit is settled: the location
  // name needs no data, the account button is painted from last time, and the fit waits for the fonts, so they don't change as the
  // page loads.
  renderRegionButton();
  renderSkeleton();
  renderDayControl();  // the Date button has its name from the first paint, so the search field beside it doesn't resize
  renderFilters();  // the sidebar's options don't need the programme; the cinemas fill in when it arrives
  const headerFonts = Promise.all([document.fonts.load('600 14px "IBM Plex Sans"'), document.fonts.load('800 40px "Big Shoulders Display"')]).catch(() => {});
  Promise.race([headerFonts, new Promise((r) => setTimeout(r, 600))]).then(() => { fitHeader(); document.querySelector(".top").classList.add("ready"); });
  takeFriendLink();
  await loadRegion(state.region);
  handleUnsubscribe();
})();
