// Browser test of the app's "Not showing" section (Watchlist tab) and the film page of a film this location doesn't list.
//   node scripts/test-app.js [screenshot dir]
// Serves a copy of site/ with the programme data (site/data/ if it's there, else today's from the live site) and a fake Supabase:
// resolve_films answers from FILMS below, watch_options from WATCH. Needs Playwright with Chromium (CI installs it; scripts/test.sh skips this without it).
const { chromium } = require("playwright");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const LIVE = (process.env.SITE_URL || "https://belacmu.github.io/kinoprogram/").replace(/\/?$/, "/");
const shots = process.argv[2] || "";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json" };

async function site() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cinecrab-app-"));
  fs.cpSync(path.join(ROOT, "site"), dir, { recursive: true, filter: (p) => !/[\\/]site[\\/](data|f)([\\/]|$)/.test(p) });
  fs.writeFileSync(path.join(dir, "config.js"), 'window.KINO_CONFIG = { supabaseUrl: "https://fake.supabase.co", supabaseKey: "sb_publishable_test" };\n');
  fs.mkdirSync(path.join(dir, "data"));
  for (const f of ["films.json", "winnipeg.json"]) {
    const local = path.join(ROOT, "site", "data", f);
    if (fs.existsSync(local)) fs.copyFileSync(local, path.join(dir, "data", f));
    else {
      const r = await fetch(LIVE + "data/" + f);
      if (!r.ok) throw new Error(`Couldn't download ${f} from ${LIVE}: ${r.status}`);
      fs.writeFileSync(path.join(dir, "data", f), Buffer.from(await r.arrayBuffer()));
    }
  }
  const server = http.createServer((req, res) => {
    const p = path.join(dir, decodeURIComponent(new URL(req.url, "http://x").pathname).replace(/\/$/, "/index.html"));
    if (!p.startsWith(dir) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(p)] || "application/octet-stream" });
    fs.createReadStream(p).pipe(res);
  });
  await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
  return { dir, server, base: `http://127.0.0.1:${server.address().port}/` };
}

(async () => {
  const { dir, server, base } = await site();
  const oslo = JSON.parse(fs.readFileSync(path.join(dir, "data", "films.json")));
  const wpg = JSON.parse(fs.readFileSync(path.join(dir, "data", "winnipeg.json")));
  const osloIds = new Set(oslo.films.flatMap((f) => f.ids));
  const here = oslo.films.find((f) => f.shows.length);                                  // a film Oslo lists
  const there = wpg.films.find((f) => f.shows.length && !f.ids.some((i) => osloIds.has(i)));  // one only Winnipeg lists
  if (!here || !there) throw new Error("The test data has no Oslo film, or no Winnipeg-only film");
  const Y = new Date().getUTCFullYear();  // dates in this year show without the year
  const FILMS = {
    "cm-gone": { id: "cm-gone", title: "The Last Picture Show", year: "1971", tmdb: 25188, imdb: "tt0067328",
      meta: { en: "The Last Picture Show", director: "Peter Bogdanovich", runtime: 118, lb: "the-last-picture-show", lbRating: 3.94,
        blurb: "A group of 1950s high schoolers come of age in a bleak, isolated, atrophied West Texas town.", last: { oslo: `${Y}-01-02` } },
      gone_at: `${Y}-01-03T00:00:00Z`, regions: [] },
    [there.id]: { id: there.id, title: there.title, year: there.year || "", meta: { last: { winnipeg: `${Y}-01-20` } }, gone_at: null, regions: ["winnipeg"] },
    "fw-nomatch": { id: "fw-nomatch", title: "Ukjent kortfilmprogram", year: "", meta: { last: { winnipeg: `${Y}-01-12` } }, gone_at: `${Y}-01-13T00:00:00Z`, regions: [] },
  };
  const ALIASES = { "fw-old-listing": there.id };  // an old listing id of a film only Winnipeg lists (playing there: never said here)
  const calls = [], watchAsked = [], marks = [];
  // Where to watch, as the update stores it: Klara-like film checked with services in Norway (and Canada, never shown on an Oslo
  // page), the short checked with nothing; the Winnipeg film never checked (no row).
  const WATCH = [
    { film_id: "cm-gone", country: "NO", link: "https://www.themoviedb.org/movie/25188/watch?locale=NO", checked_at: `${Y}-01-05T10:00:00+00:00`,
      options: { stream: [{ name: "Netflix", logo: "/nf.jpg" }, { name: "Max", logo: "" }], rent: [{ name: "Apple TV", logo: "/apple.jpg" }] } },
    { film_id: "cm-gone", country: "CA", link: "https://www.themoviedb.org/movie/25188/watch?locale=CA", checked_at: `${Y}-01-05T10:00:00+00:00`,
      options: { stream: [{ name: "Crave", logo: "" }] } },
    { film_id: "fw-nomatch", country: "NO", link: "", checked_at: `${Y}-01-05T10:00:00+00:00`, options: {} },
  ];

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route(/^https?:\/\/(?!127\.0\.0\.1|cdn\.jsdelivr\.net)/, async (route) => {
    const url = route.request().url();
    if (!url.startsWith("https://fake.supabase.co/")) return route.abort();  // posters and the like: not needed
    if (url.includes("/rpc/mark_watchlisted")) {
      marks.push(JSON.parse(route.request().postData()).ids);
      return route.fulfill({ status: 204, body: "" });
    }
    if (url.includes("/rpc/resolve_films")) {
      const ids = JSON.parse(route.request().postData()).ids;
      calls.push(ids);
      const rows = ids.map((a) => { const f = FILMS[ALIASES[a] || a]; return f && { asked: a, ...f }; }).filter(Boolean);
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(rows) });
    }
    if (url.includes("/rest/v1/watch_options")) {
      const q = new URL(url).searchParams, film = (q.get("film_id") || "").replace(/^eq\./, ""), country = (q.get("country") || "").replace(/^eq\./, "");
      watchAsked.push(`${film}|${country}`);
      const rows = WATCH.filter((w) => w.film_id === film && w.country === country).map(({ options, link, checked_at }) => ({ options, link, checked_at }));
      const one = /vnd\.pgrst\.object/.test(route.request().headers()["accept"] || "");
      if (one && !rows.length) return route.fulfill({ status: 406, contentType: "application/json", body: JSON.stringify({ code: "PGRST116", message: "0 rows" }) });
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(one ? rows[0] : rows) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  const watch = [here.id, "cm-gone", "fw-old-listing", "fw-nomatch", "nobody-knows-this"];
  await page.addInitScript((w) => {
    if (sessionStorage.getItem("seeded")) return;
    sessionStorage.setItem("seeded", "1");
    localStorage.setItem("kino:region", JSON.stringify("oslo"));
    localStorage.setItem("kino:watchlist", JSON.stringify(w));
  }, watch);
  let failed = 0;
  const check = (ok, what) => { console.log(`${ok ? "ok  " : "FAIL"} ${what}`); if (!ok) failed++; };
  const shot = (name) => shots && page.screenshot({ path: path.join(shots, name) });

  try {
    await page.goto(base);
    await page.waitForSelector("#grid .card");
    await page.click('[data-list="watch"]');
    await page.waitForFunction(() => document.querySelector("#grid .sec .lbl")?.textContent.startsWith("Not showing"), null, { timeout: 10000 });
    const first = await page.$eval("#grid .sec", (s) => ({ label: s.querySelector(".lbl").textContent, n: s.querySelector(".n").textContent,
      shut: s.classList.contains("shut") }));
    check(first.label === "Not showing in Oslo", `the first section is "Not showing in Oslo" (got "${first.label}")`);
    check(first.n === "3", `it holds the 3 films found; an id no film is known by is left out (got ${first.n})`);
    check(first.shut, "it is shut by default");
    check(marks.length === 1 && JSON.stringify([...marks[0]].sort()) === JSON.stringify([...watch].sort()), `the hearts this browser had are marked as wanted, once (${JSON.stringify(marks)})`);
    check(calls.length === 1 && calls[0].length === 4 && !calls[0].includes(here.id), `one lookup, only for the ids Oslo doesn't list: ${JSON.stringify(calls[0])}`);
    await shot("1-shut.png");

    await page.click("#grid .sec:first-of-type h2 button");
    await page.waitForSelector("#grid .sec:first-of-type .card");
    const cards = await page.$$eval("#grid .sec:first-of-type .card", (cs) => cs.map((c) => ({ id: c.dataset.id, meta: c.querySelector(".m").textContent,
      hide: !!c.querySelector("[data-hide]"), on: c.querySelector(".star").getAttribute("aria-pressed") })));
    const meta = (id) => cards.find((c) => c.id === id)?.meta;
    check(cards[0]?.id === "cm-gone" && meta("cm-gone") === "Last shown 2 Jan", `the film last shown here comes first, saying when (${JSON.stringify(cards[0])})`);
    check(meta(there.id) === "No showings here" && meta("fw-nomatch") === "No showings here", `films never shown here say only that (got "${meta(there.id)}", "${meta("fw-nomatch")}")`);
    const sectionText = await page.$eval("#grid .sec:first-of-type", (e) => e.textContent);
    check(!/Winnipeg|Westman|Costa del Sol/.test(sectionText), "nothing in the section mentions another location");
    check(cards.every((c) => !c.hide && c.on === "true"), "hearts on, no hide button");
    await shot("2-open.png");

    await page.click('#grid .card[data-id="cm-gone"] a');
    await page.waitForSelector("#film[open] #filmTitle");
    const sheet = await page.$eval("#film", (d) => ({ title: d.querySelector("#filmTitle").textContent, meta: d.querySelector(".meta").textContent,
      note: d.querySelector(".ghostnote p")?.textContent || "", menu: !!d.querySelector("[data-sheetmenu]"), blurb: d.querySelector("#blurbText")?.textContent || "" }));
    check(sheet.title === "The Last Picture Show" && sheet.meta.includes("1971 · 118 min · Directed by Peter Bogdanovich"), `the header comes from the film's card (${sheet.meta})`);
    check(sheet.note.startsWith("No showings in Oslo right now. Last shown here 2 Jan."), `says there are no showings and when it last played ("${sheet.note}")`);
    check(!sheet.menu && sheet.blurb.includes("bleak"), "its description; no share or hide menu");
    const pill = await page.$eval("#film .lbpill", (a) => ({ text: a.textContent.trim(), href: a.href }));
    check(pill.text === "3.9" && pill.href === "https://letterboxd.com/film/the-last-picture-show/", `Letterboxd: its rating, linking to the film (${JSON.stringify(pill)})`);
    await page.waitForSelector("#film .watchopts", { timeout: 5000 }).catch(() => {});
    const wo = await page.$eval("#film", (d) => {
      const box = d.querySelector(".watchopts");
      return box && { title: box.querySelector("h3").textContent, groups: [...box.querySelectorAll(".worow")].map((g) => g.querySelector("h4").firstChild.textContent + ": "
        + [...g.querySelectorAll(".wostub .t")].map((n) => n.textContent).join(", ")), link: box.querySelector(".wofoot a")?.href || "", text: box.textContent };
    });
    check(wo?.title === "Watch at home in Norway", `where to watch, in this location's country (${wo?.title})`);
    check(JSON.stringify(wo?.groups) === JSON.stringify(["Stream: Netflix, Max", "Rent: Apple TV"]), `grouped as the update stored them (${JSON.stringify(wo?.groups)})`);
    check(wo?.link.startsWith("https://www.themoviedb.org/") && /Data from JustWatch/.test(wo?.text || ""), "links to all options, credits JustWatch");
    check(!/Crave|Canada/.test(wo?.text || ""), "nothing from another country");
    check(watchAsked.includes("cm-gone|NO") && !watchAsked.some((a) => !a.endsWith("|NO")), `asked only for Norway (${JSON.stringify(watchAsked)})`);
    await shot("3-sheet.png");

    await page.click("#film .btn.wl");
    await page.waitForTimeout(900);
    check(!(await page.evaluate(() => JSON.parse(localStorage.getItem("kino:watchlist")))).includes("cm-gone"), "its heart takes it off the watchlist");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(900);
    check((await page.$eval("#grid .sec:first-of-type .n", (e) => e.textContent)) === "2", "and out of the section");

    await page.goto(base + "#film/fw-nomatch");
    await page.waitForSelector("#film[open] #filmTitle");
    check((await page.$eval("#filmTitle", (e) => e.textContent)) === "Ukjent kortfilmprogram", "a link to a film no location lists opens its page");
    const pill2 = await page.$eval("#film .lbpill", (a) => ({ text: a.textContent.trim(), href: a.href }));
    check(pill2.text.startsWith("Search") && pill2.href.includes("/search/films/"), `Letterboxd: nothing known, a search (${JSON.stringify(pill2)})`);
    await page.waitForSelector("#film .watchopts", { timeout: 5000 }).catch(() => {});
    const none = await page.$eval("#film", (d) => d.querySelector(".watchopts .wonone")?.textContent || "");
    check(none.startsWith("Not available to stream, rent or buy in Norway as far as we know (checked 5 Jan)"), `checked, nothing found, says so ("${none}")`);

    await page.goto(base + "#film/fw-old-listing");
    await page.waitForSelector("#film[open] .ghostnote");
    const other = await page.$eval("#film", (d) => ({ title: d.querySelector("#filmTitle").textContent, text: d.textContent }));
    check(other.title === there.title, `an old id finds its film (${other.title})`);
    check(!/Winnipeg|Westman|Costa del Sol/.test(other.text) && !/See its showings/.test(other.text), "its page doesn't mention the location where it is playing");
    await page.waitForTimeout(800);
    check(!(await page.$("#film .watchopts")), "a film never checked shows no where-to-watch block");
    await shot("4-other.png");

    if (shots) {  // for looking at: a phone, in dark mode
      await page.setViewportSize({ width: 390, height: 844 });
      await page.emulateMedia({ colorScheme: "dark" });
      await page.goto(base + "?r=oslo#film/cm-gone");
      await page.evaluate(() => localStorage.setItem("kino:region", JSON.stringify("oslo")));
      await page.reload();
      await page.waitForSelector("#film .watchopts", { timeout: 8000 }).catch(() => {});
      await page.$eval("#film .watchopts", (e) => e.scrollIntoView({ block: "center" })).catch(() => {});
      await shot("5-phone-dark.png");
    }
    // Films listed here with no showings. Before its cinema premiere: the date (and a film Filmweb has showings for only in other
    // cities never names them). After it: no date at all. Where to watch either way, or its digital release if nothing yet.
    const sheetOf = async (setup, watchRow) => {
      await page.goto(base);
      await page.waitForSelector("#grid .card");
      const id = await page.evaluate(setup);
      if (watchRow) WATCH.push({ film_id: id, country: "NO", link: "https://www.themoviedb.org/movie/1/watch?locale=NO", checked_at: `${Y}-01-05T10:00:00+00:00`, ...watchRow });
      await page.evaluate((i) => { location.hash = "#film/" + encodeURIComponent(i); }, id);
      await page.waitForSelector("#film[open] .hiddenNote");
      if (watchRow) await page.waitForSelector("#film .watchopts", { timeout: 5000 }).catch(() => {});
      const r = await page.$eval("#film", (d) => ({ text: d.textContent, note: d.querySelector(".days .hiddenNote").textContent,
        line: getComputedStyle(d.querySelector(".days .hiddenNote")).borderTopWidth }));
      await page.keyboard.press("Escape");
      return r;
    };
    const coming = await sheetOf(() => {
      const f = state.data.films[0];
      f.shows = []; f.scope = ""; f.premiere = "2099-01-29"; f.premiereConfirmed = true; f.elsewhere = ["Bergen", "Trondheim"];
      return f.id;
    }, { options: { rent: [{ name: "Viaplay", logo: "" }] } });
    check(/^Cinema premiere Thu 29 Jan 2099\. No showings announced in Oslo yet\./.test(coming.note), `before its cinema premiere: the date ("${coming.note}")`);
    check(!/Bergen|Trondheim/.test(coming.text), "a film showing only in other cities doesn't name them");
    check(/Watch at home in Norway/.test(coming.text) && /Viaplay/.test(coming.text), "a listed film with no showings yet also says where to watch it");
    check(coming.line === "0px", `no second line under the header (${coming.line})`);
    const past = await sheetOf(() => {
      const f = state.data.films[1];
      f.shows = []; f.scope = ""; f.premiere = "2020-03-01"; f.premiereConfirmed = true;
      return f.id;
    }, { options: { digital: "2099-12-12" } });
    check(/^No showings in Oslo right now\./.test(past.note) && !/premiere|2020/i.test(past.text), `after its cinema premiere: no date ("${past.note}")`);
    check(/Not available yet\. Digital release in Norway: 12 Dec 2099, usually to rent or buy first\./.test(past.text), "nothing to watch yet: its digital release date");

    // Marks are sent once per browser; a new heart sends its film's.
    const before = marks.length;
    await page.goto(base);
    await page.waitForSelector("#grid .card");
    await page.waitForTimeout(500);
    check(marks.length === before, "marks already sent aren't sent again");
    const fresh = await page.$eval("#grid .card .star[aria-pressed='false']", (b) => b.dataset.star);
    await page.click(`#grid .card .star[data-star="${fresh}"]`);
    await page.waitForTimeout(500);
    check(JSON.stringify(marks.at(-1)) === JSON.stringify([fresh]), `a new heart marks its film (${JSON.stringify(marks.at(-1))})`);

    check(!errors.length, `no errors on the page ${errors.length ? JSON.stringify(errors) : ""}`);
  } finally {
    await browser.close();
    server.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(failed ? `${failed} failed` : "app tests passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
