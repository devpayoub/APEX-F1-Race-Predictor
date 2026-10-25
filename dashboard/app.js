/* ============================================================
   APEX — dashboard renderer + job controller
   Data comes from GET /api/prediction when served by app.py,
   or from window.PREDICTION when opened as a static file.
   ============================================================ */
(function () {
  "use strict";

  var D = null;
  var state = { sortKey: "position", sortAsc: true, active: null };
  var API = { live: false, races: [], defaultRound: null, busy: false };
  var cdTimer = null;

  /* ---------------------------------------------------------- helpers --- */
  function el(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }
  function pct(v, d) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v * 100).toFixed(d === undefined ? 1 : d) + "%";
  }
  function pWin(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    if (v <= 0) return "—";
    if (v < 0.001) return "<0.1%";
    return (v * 100).toFixed(1) + "%";
  }
  function num(v, d) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toFixed(d === undefined ? 2 : d);
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function byId(id) { return document.getElementById(id); }
  function esc(s) { return String(s === null || s === undefined ? "" : s); }

  /* -------------------------------------------------------------- api --- */
  function apiGet(path) {
    return fetch(path, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function detectServer() {
    return apiGet("/api/health").then(function (h) {
      API.live = true;
      API.health = h;
    }).catch(function () { API.live = false; });
  }

  /* ------------------------------------------------------------- boot --- */
  function boot() {
    byId("btn-reload").addEventListener("click", function () { location.reload(); });
    byId("btn-run").addEventListener("click", runPrediction);
    byId("sel-season").addEventListener("change", function () { loadRaces(+this.value); });
    byId("sel-race").addEventListener("change", function () { highlightSelection(); });

    detectServer().then(function () {
      if (API.live) {
        return loadRaces(+byId("sel-season").value).then(loadPrediction);
      }
      setStatus("Static mode — start the server for live predictions.", "warn");
      byId("btn-run").disabled = true;
      byId("sel-race").innerHTML = "";
      byId("sel-race").appendChild(new Option("Static file mode", ""));
      loadPrediction();
    });
  }

  /* ------------------------------------------------------------ races --- */
  function loadRaces(season) {
    return apiGet("/api/races?season=" + season).then(function (data) {
      API.races = data.races || [];
      API.defaultRound = data.default_round;
      var sel = byId("sel-race");
      sel.innerHTML = "";
      API.races.forEach(function (r) {
        var tag = "R" + r.round + " · " + (r.name || "").replace(" Grand Prix", " GP") +
          " · " + shortDate(r.date) + (r.completed ? "" : "  (upcoming)");
        var o = new Option(tag, String(r.round));
        o.title = (r.name || "") + " — " + (r.circuit || "") + ", " + (r.location || "");
        sel.appendChild(o);
      });
      if (API.defaultRound) sel.value = String(API.defaultRound);
      sel.disabled = false;
      highlightSelection();
    }).catch(function (e) {
      byId("sel-race").innerHTML = "";
      byId("sel-race").appendChild(new Option("Schedule unavailable", ""));
      setStatus("Could not load the " + season + " schedule: " + e.message, "error");
    });
  }

  function shortDate(iso) {
    if (!iso) return "";
    var d = new Date(iso + "T00:00:00Z");
    if (isNaN(d)) return iso;
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
  }

  function highlightSelection() {
    var rnd = +byId("sel-race").value;
    var race = API.races.filter(function (r) { return r.round === rnd; })[0];
    if (race) setStatus("Selected: " + race.name + " — " + (race.circuit || "") + ", " + (race.location || ""), "");
  }

  /* ------------------------------------------------------------ status --- */
  function setStatus(text, kind) {
    var s = byId("ctrl-status");
    s.textContent = text || "";
    s.className = "ctrl-status" + (kind ? " is-" + kind : "");
  }

  function setProgress(v) {
    var bar = byId("job-progress");
    var fill = byId("job-progress-fill");
    if (v === null) {
      bar.classList.remove("active");
      fill.style.width = "0%";
      return;
    }
    bar.classList.add("active");
    fill.style.width = clamp(v * 100, 0, 100) + "%";
  }

  function setBusy(busy) {
    API.busy = busy;
    var btn = byId("btn-run");
    btn.disabled = busy || !API.live;
    btn.classList.toggle("is-busy", busy);
    byId("btn-run-label").textContent = busy ? "Predicting…" : "Run prediction";
    byId("sel-race").disabled = busy || !API.live;
    byId("sel-season").disabled = busy || !API.live;
    byId("sel-sims").disabled = busy || !API.live;
    if (!busy) setProgress(null);
  }

  /* --------------------------------------------------------- prediction --- */
  function loadPrediction() {
    var fromApi = API.live
      ? apiGet("/api/prediction").catch(function () { return null; })
      : Promise.resolve(null);

    return fromApi.then(function (payload) {
      if (!payload && window.PREDICTION) payload = window.PREDICTION;
      if (payload && payload.drivers && payload.drivers.length) render(payload);
      else renderEmpty();
    });
  }

  function runPrediction() {
    if (!API.live) {
      setStatus("Live predictions need the server: python app.py", "error");
      return;
    }
    if (API.busy) return;

    var body = {
      season: +byId("sel-season").value,
      round: +byId("sel-race").value,
      sims: +byId("sel-sims").value
    };
    if (!body.round) { setStatus("Pick a Grand Prix first.", "error"); return; }

    setBusy(true);
    setProgress(0.02);
    setStatus("Queued…", "");

    fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (r.status === 429) throw new Error("server busy — try again in a moment");
      if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || ("HTTP " + r.status)); });
      return r.json();
    }).then(function (job) {
      pollJob(job.job_id);
    }).catch(function (e) {
      setBusy(false);
      setStatus("Could not start the job: " + e.message, "error");
    });
  }

  function pollJob(jobId) {
    var started = Date.now();
    var timer = setInterval(function () {
      apiGet("/api/jobs/" + jobId).then(function (job) {
        if (job.status === "running" || job.status === "queued") {
          setProgress(job.progress || 0.02);
          setStatus((job.stage || job.status) + " · " + Math.round((job.progress || 0) * 100) + "%", "");
          return;
        }
        clearInterval(timer);
        setBusy(false);

        if (job.status === "error") {
          setStatus("Prediction failed: " + (job.error || "unknown error"), "error");
          return;
        }
        var secs = ((Date.now() - started) / 1000).toFixed(1);
        setStatus("Prediction complete in " + secs + " s", "ok");
        if (job.result) render(job.result);
      }).catch(function (e) {
        clearInterval(timer);
        setBusy(false);
        setStatus("Lost contact with the server: " + e.message, "error");
      });
    }, 900);
  }

  /* ------------------------------------------------------------ render --- */
  function render(payload) {
    D = payload;
    var app = byId("app");
    app.innerHTML = "";
    app.appendChild(byId("tpl-dashboard").content.cloneNode(true));

    renderHeader();
    renderHero();
    renderPodium();
    renderBars();
    renderTable();
    renderScatter();
    renderStrategy();
    renderMethod();
    renderFooter();
    startCountdown();
    wireCrossFilter();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function renderEmpty() {
    var app = byId("app");
    app.innerHTML = "";
    var box = el("div", "empty-state");
    box.appendChild(el("h2", null, API.live ? "No prediction loaded" : "Static preview"));

    var p = el("p");
    if (API.live) {
      p.innerHTML = "Pick a Grand Prix above and run the model. A run fetches timing data, " +
        "simulates several thousand races, then scores the historical prior — about 15 seconds.";
    } else {
      p.innerHTML = "This page is open as a plain file, so live predictions are unavailable. " +
        "Start the server with <code>python app.py</code> (or <code>run.bat</code>) and open " +
        "<code>http://localhost:5000</code>.";
    }
    box.appendChild(p);

    if (API.live) {
      var btn = el("button", "btn-primary", "Run prediction");
      btn.addEventListener("click", runPrediction);
      box.appendChild(btn);
    }
    app.appendChild(box);
  }

  /* ----------------------------------------------------------- header --- */
  function renderHeader() {
    var r = D.meta.race;
    byId("tb-race").textContent = r.name + " " + r.season;
    byId("tb-meta").textContent = "Round " + r.round + " · " + r.circuit;
    document.title = "APEX — " + r.name + " " + r.season;
  }

  /* ------------------------------------------------------------- hero --- */
  function renderHero() {
    var r = D.meta.race, m = D.meta.model, c = D.meta.conditions;

    byId("hero-season").textContent = r.season;
    byId("hero-round").textContent = "Round " + r.round;
    byId("hero-title").textContent = r.name;
    byId("hero-location").textContent = r.location + " · " + r.circuit;
    byId("hero-sims").textContent = (m.simulations || 0).toLocaleString("en-US");

    var g = new Date(m.generated_at || Date.now());
    byId("hero-generated").textContent = g.toLocaleTimeString("en-GB", {
      hour: "2-digit", minute: "2-digit", timeZone: "UTC"
    }) + " UTC";

    var conf = (m.confidence || "medium").toLowerCase();
    var badge = byId("hero-confidence");
    badge.className = "confidence " + conf;
    badge.lastElementChild.textContent = conf + " confidence";

    var chips = byId("hero-chips");
    chips.innerHTML = "";
    function chip(label, value, cls) {
      var d = el("span", "chip");
      d.appendChild(el("span", "dot " + (cls || "")));
      d.appendChild(document.createTextNode(label + " "));
      d.appendChild(el("b", null, value));
      chips.appendChild(d);
    }
    chip("Laps", r.laps);
    chip("Length", num(r.circuit_length_km, 3) + " km");
    if (c.air_temp_c !== null && c.air_temp_c !== undefined) chip("Air", num(c.air_temp_c, 1) + "°C");
    if (c.track_temp_c !== null && c.track_temp_c !== undefined) chip("Track", num(c.track_temp_c, 0) + "°C");
    chip("Rain", Math.round((c.rain_prob || 0) * 100) + "%", (c.rain_prob || 0) > 0.2 ? "warn" : "ok");
    chip("Safety car", Math.round((c.sc_probability || 0) * 100) + "%", "warn");

    if (conf === "low" || conf === "medium") {
      var b = byId("banner");
      b.className = "banner show";
      b.textContent = conf === "low"
        ? "Limited data — this forecast leans on qualifying and priors. Treat margins as indicative."
        : "Partial telemetry — some drivers have no clean long-run data; their race pace is inferred.";
    }
  }

  /* ----------------------------------------------------------- podium --- */
  function renderPodium() {
    var top = D.drivers.slice(0, 3);
    var wrap = byId("podium");
    wrap.innerHTML = "";
    var order = [1, 0, 2];

    order.forEach(function (idx) {
      var d = top[idx];
      if (!d) return;
      var pos = idx + 1;
      var step = el("div", "podium-step p" + pos);
      step.style.setProperty("--team", d.team_colour);
      step.dataset.code = d.code;

      step.appendChild(el("div", "podium-pos", "P" + pos));
      step.appendChild(el("div", "podium-code", d.code));
      step.appendChild(el("div", "podium-name", d.name));

      var team = el("div", "podium-team");
      var bar = el("span", "bar");
      bar.style.background = d.team_colour;
      team.appendChild(bar);
      team.appendChild(document.createTextNode(d.team));
      step.appendChild(team);

      var prob = el("div", "podium-prob");
      prob.appendChild(el("div", "podium-prob-label", "Win probability"));
      prob.appendChild(el("div", "podium-prob-value", pWin(d.win_prob)));
      step.appendChild(prob);

      var meta = el("div", "podium-meta");
      meta.innerHTML =
        "POD <b>" + pct(d.podium_prob, 1) + "</b>" +
        " · EXP <b>" + num(d.expected_finish, 2) + "</b>" +
        " · " + (d.expected_gap_to_winner === null ? "leader" :
          "<b>+" + num(d.expected_gap_to_winner, 1) + "s</b>");
      step.appendChild(meta);

      wrap.appendChild(step);
    });

    var fav = D.favourite;
    byId("podium-hint").textContent = fav
      ? "Favourite " + fav.code + " · " + pWin(fav.win_prob)
      : "";

    renderAnalysis();
    renderMovers();
  }

  /* --------------------------------------------------------- analysis --- */
  function renderAnalysis() {
    var host = byId("analysis");
    if (!host) return;
    var lines = D.analysis || [];
    if (!lines.length) return;

    var codes = {};
    D.drivers.forEach(function (d) { codes[d.code] = 1; });

    host.appendChild(el("div", "analysis-title", "Model read"));

    lines.forEach(function (text) {
      var p = el("p");
      var safe = esc(text).replace(/[&<>"]/g, function (c) {
        return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c];
      });
      p.innerHTML = safe.replace(/\b([A-Z]{3})\b/g, function (m) {
        return codes[m] ? "<b>" + m + "</b>" : m;
      });
      host.appendChild(p);
    });
  }

  /* ---------------------------------------------------------- movers --- */
  function renderMovers() {
    var host = byId("movers");
    if (!host) return;

    var all = D.drivers.slice();
    var gains = all.filter(function (d) { return d.grid_delta > 0; })
      .sort(function (a, b) { return b.grid_delta - a.grid_delta; }).slice(0, 3);
    var losses = all.filter(function (d) { return d.grid_delta < 0; })
      .sort(function (a, b) { return a.grid_delta - b.grid_delta; }).slice(0, 3);

    host.appendChild(el("div", "analysis-title", "Biggest predicted movers"));

    var grid = el("div", "movers-grid");
    grid.appendChild(column("Gains", gains, "up"));
    grid.appendChild(column("Losses", losses, "down"));
    host.appendChild(grid);

    function column(title, rows, dir) {
      var c = el("div");
      c.appendChild(el("h5", null, title));
      if (!rows.length) {
        c.appendChild(el("div", "mover-empty", "No movement projected"));
        return c;
      }
      rows.forEach(function (d) {
        var r = el("div", "mover-row");
        r.dataset.code = d.code;
        r.appendChild(el("div", "mover-code", d.code));
        r.appendChild(el("div", "mover-path", "P" + d.grid + " → P" + d.position));
        r.appendChild(el("div", "mover-delta " + dir,
          (d.grid_delta > 0 ? "+" : "") + d.grid_delta));
        c.appendChild(r);
      });
      return c;
    }
  }

  /* ------------------------------------------------------------- bars --- */
  function renderBars() {
    var wrap = byId("bars");
    wrap.innerHTML = "";
    var max = Math.max.apply(null, D.drivers.map(function (d) { return d.win_prob || 0; })) || 1;

    D.drivers.forEach(function (d, i) {
      var row = el("div", "bar-row" + (i === 0 ? " is-fav" : ""));
      row.dataset.code = d.code;
      row.appendChild(el("div", "bar-pos", d.position));
      row.appendChild(el("div", "bar-code", d.code));

      var track = el("div", "bar-track");
      var fill = el("div", "bar-fill");
      fill.style.setProperty("--team", d.team_colour);
      track.appendChild(fill);
      row.appendChild(track);

      row.appendChild(el("div", "bar-val", pWin(d.win_prob)));
      wrap.appendChild(row);

      fill.style.width = clamp((d.win_prob / max) * 100, 0, 100) + "%";
    });
  }

  /* ------------------------------------------------------------ table --- */
  function renderTable() {
    var body = byId("grid-body");
    body.innerHTML = "";

    var rows = D.drivers.slice().sort(function (a, b) {
      var k = state.sortKey;
      var av = a[k], bv = b[k];
      if (k === "code" || k === "strategy") {
        return state.sortAsc ? String(av).localeCompare(String(bv))
                             : String(bv).localeCompare(String(av));
      }
      return state.sortAsc ? (av - bv) : (bv - av);
    });

    rows.forEach(function (d) {
      var tr = el("tr");
      tr.dataset.code = d.code;

      tr.appendChild(el("td", "cell-pos", d.position));

      var tdD = el("td");
      var cell = el("div", "cell-driver");
      var bar = el("span", "driver-bar");
      bar.style.background = d.team_colour;
      cell.appendChild(bar);
      cell.appendChild(el("span", "driver-code", d.code));
      cell.appendChild(el("span", "driver-name", d.team));
      tdD.appendChild(cell);
      tr.appendChild(tdD);

      tr.appendChild(el("td", "num", d.grid));

      var dd = d.grid_delta;
      var cls = dd > 0 ? "up" : dd < 0 ? "down" : "flat";
      var arrow = dd > 0 ? "▲ " + dd : dd < 0 ? "▼ " + Math.abs(dd) : "—";
      tr.appendChild(el("td", "delta " + cls, arrow));

      [["win_prob", d.win_prob, true], ["podium_prob", d.podium_prob, false],
       ["points_prob", d.points_prob, false]]
        .forEach(function (pair) {
          var td = el("td");
          var wrap = el("span", "prob-cell");
          var mini = el("span", "prob-mini");
          var sp = el("span");
          sp.style.width = clamp(pair[1] * 100, 0, 100) + "%";
          sp.style.background = d.team_colour;
          mini.appendChild(sp);
          wrap.appendChild(mini);
          wrap.appendChild(document.createTextNode(pair[2] ? pWin(pair[1]) : pct(pair[1], 1)));
          td.appendChild(wrap);
          tr.appendChild(td);
        });

      tr.appendChild(el("td", "num", num(d.expected_finish, 2)));
      tr.appendChild(el("td", "num", num(d.deg_rate, 3)));
      tr.appendChild(el("td", null, d.strategy));
      tr.appendChild(el("td", "num", pct(d.dnf_prob, 1)));

      body.appendChild(tr);
    });

    var ths = byId("grid-table").querySelectorAll("thead th");
    Array.prototype.forEach.call(ths, function (th) {
      th.classList.toggle("sorted", th.dataset.key === state.sortKey);
      th.classList.toggle("asc", th.dataset.key === state.sortKey && state.sortAsc);
      th.addEventListener("click", function () {
        if (state.sortKey === th.dataset.key) state.sortAsc = !state.sortAsc;
        else { state.sortKey = th.dataset.key; state.sortAsc = true; }
        renderTable();
        wireCrossFilter();
      });
    });
  }

  /* ---------------------------------------------------------- scatter --- */
  function renderScatter() {
    var svg = byId("scatter");
    var NS = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";

    var pad = { l: 46, r: 16, t: 18, b: 38 };
    var W = 620, H = 340;
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;

    var xs = D.drivers.map(function (d) { return d.qualifying_gap || 0; });
    var ys = D.drivers.map(function (d) { return d.race_pace_gap || 0; });
    var xMax = Math.max.apply(null, xs) * 1.12 || 1;
    var yMax = Math.max.apply(null, ys) * 1.12 || 1;

    function sx(v) { return pad.l + (v / xMax) * iw; }
    function sy(v) { return pad.t + ih - (v / yMax) * ih; }

    function add(tag, attrs, parent) {
      var n = document.createElementNS(NS, tag);
      for (var k in attrs) if (attrs.hasOwnProperty(k)) n.setAttribute(k, attrs[k]);
      (parent || svg).appendChild(n);
      return n;
    }

    for (var i = 0; i <= 4; i++) {
      var gy = pad.t + (ih / 4) * i;
      add("line", { x1: pad.l, y1: gy, x2: W - pad.r, y2: gy, class: "axis" });
      add("text", { x: pad.l - 8, y: gy + 3, class: "tick", "text-anchor": "end" })
        .textContent = (yMax - (yMax / 4) * i).toFixed(2);
      var gx = pad.l + (iw / 4) * i;
      add("line", { x1: gx, y1: pad.t, x2: gx, y2: pad.t + ih, class: "axis" });
      add("text", { x: gx, y: H - pad.b + 15, class: "tick", "text-anchor": "middle" })
        .textContent = ((xMax / 4) * i).toFixed(2);
    }

    add("text", { x: pad.l + iw / 2, y: H - 4, class: "axis-label", "text-anchor": "middle" })
      .textContent = "Qualifying gap to pole (s)";
    var yl = add("text", { x: 12, y: pad.t + ih / 2, class: "axis-label", "text-anchor": "middle",
      transform: "rotate(-90 12 " + (pad.t + ih / 2) + ")" });
    yl.textContent = "Race-pace gap (s)";

    add("text", { x: pad.l + 8, y: pad.t + 14, class: "quad" }).textContent = "quick in quali · strong in race";
    add("text", { x: W - pad.r - 8, y: pad.t + 14, class: "quad", "text-anchor": "end" })
      .textContent = "slow in quali · weak in race";

    var labelled = {};
    D.drivers.slice(0, 10).forEach(function (d) { labelled[d.code] = 1; });

    D.drivers.forEach(function (d) {
      var cx = sx(d.qualifying_gap || 0), cy = sy(d.race_pace_gap || 0);
      var g = add("g", { class: "pt", "data-code": d.code });

      var title = document.createElementNS(NS, "title");
      title.textContent = d.code + " — quali +" + num(d.qualifying_gap, 3) +
        "s, race trim +" + num(d.race_pace_gap, 3) + "s, win " + pWin(d.win_prob);
      g.appendChild(title);

      add("circle", { cx: cx, cy: cy, r: labelled[d.code] ? 5.5 : 4.2,
        fill: d.team_colour, "fill-opacity": labelled[d.code] ? 0.95 : 0.55,
        stroke: "#0A0C10", "stroke-width": 1.4 }, g);

      if (labelled[d.code]) {
        add("text", { x: cx + 9, y: cy + 3.2, class: "pt-label" }, g).textContent = d.code;
      }
    });
  }

  /* --------------------------------------------------------- strategy --- */
  function renderStrategy() {
    var wrap = byId("stints");
    wrap.innerHTML = "";
    var rows = D.drivers.slice(0, 8);
    var COMPOUND = { S: "#E8002D", M: "#FFD400", H: "#F0F0F0" };

    rows.forEach(function (d) {
      var parts = d.strategy.replace(/^\d-stop\s*/, "").split("-").map(function (s) { return s.trim(); });
      var row = el("div", "stint-row");
      row.dataset.code = d.code;
      row.appendChild(el("div", "stint-code", d.code));

      var track = el("div", "stint-track");
      var cuts = parts.length === 2 ? [0.47] : parts.length === 3 ? [0.30, 0.64] : [1];
      var prev = 0;
      parts.forEach(function (c, i) {
        var end = i < cuts.length ? cuts[i] : 1;
        var seg = el("div", "stint-seg");
        seg.style.width = ((end - prev) * 100) + "%";
        seg.style.background = COMPOUND[c.charAt(0).toUpperCase()] || "#555";
        seg.title = c + " · laps " + Math.round(prev * D.meta.race.laps) + "–" + Math.round(end * D.meta.race.laps);
        track.appendChild(seg);
        prev = end;
      });
      row.appendChild(track);
      row.appendChild(el("div", "stint-label", d.strategy.replace(/^\d-stop\s*/, "")));
      wrap.appendChild(row);
    });

    byId("strategy-hint").textContent = "Top 8 · " + (D.meta.conditions.expected_strategy || "");

    var cond = byId("conditions");
    cond.innerHTML = "";
    var c = D.meta.conditions;
    function tile(label, value, note, gauge) {
      var t = el("div", "cond");
      t.appendChild(el("div", "cond-label", label));
      t.appendChild(el("div", "cond-value", value));
      if (note) t.appendChild(el("div", "cond-note", note));
      if (gauge !== undefined) {
        var g = el("div", "gauge");
        var s = el("span");
        s.style.width = clamp(gauge * 100, 0, 100) + "%";
        g.appendChild(s);
        t.appendChild(g);
      }
      cond.appendChild(t);
    }
    tile("Air", num(c.air_temp_c, 1) + "°", "ambient");
    tile("Track", num(c.track_temp_c, 0) + "°", "asphalt");
    tile("Rain", Math.round((c.rain_prob || 0) * 100) + "%", "forecast", c.rain_prob);
    tile("Safety car", Math.round((c.sc_probability || 0) * 100) + "%", "per race", c.sc_probability);
  }

  /* -------------------------------------------------------- methodology -- */
  function renderMethod() {
    var notes = byId("method-notes");
    (D.methodology_notes || []).forEach(function (n) {
      notes.appendChild(el("li", null, n));
    });

    var m = D.meta.model;
    var list = byId("method-model");
    function item(html) { var li = el("li"); li.innerHTML = html; list.appendChild(li); }
    item("<b>Simulations</b> — " + (m.simulations || 0).toLocaleString("en-US") + " full races");
    item("<b>Ensemble</b> — " + Math.round(m.blend.monte_carlo * 100) + "% Monte Carlo / " +
         Math.round(m.blend.ml * 100) + "% historical prior");
    item("<b>Calibration</b> — temperature " + m.calibration_temperature);
    if (m.ml_rows) item("<b>Prior corpus</b> — " + m.ml_rows.toLocaleString("en-US") +
      " classified race results (2022–2026)");
    if (m.ml_cv_logloss) item("<b>Prior CV log-loss</b> — " + m.ml_cv_logloss);
    item("<b>Confidence</b> — " + esc(m.confidence));
    if (m.fastf1 && m.fastf1.loaded === false) {
      item("<b>FastF1</b> — unavailable: " + esc(m.fastf1.reason));
    }

    var tags = byId("source-tags");
    (D.meta.sources || []).forEach(function (s) { tags.appendChild(el("span", "source-tag", s)); });
  }

  /* ------------------------------------------------------------ footer --- */
  function renderFooter() {
    byId("foot-warn").textContent =
      "Model output for analysis and entertainment. Probabilities are not guarantees — " +
      "strategy calls, incidents and weather can invalidate any forecast. " +
      "Verify data against official FIA classifications before acting on it.";
    byId("foot-stamp").textContent =
      "APEX v" + (D.meta.model.version || "1.0") + " · " +
      new Date(D.meta.generated_at).toISOString().replace(".000", "");
  }

  /* --------------------------------------------------------- countdown --- */
  function startCountdown() {
    if (cdTimer) { clearInterval(cdTimer); cdTimer = null; }
    var start = new Date(D.meta.race.start_utc);
    var label = byId("cd-label");
    var value = byId("cd-value");
    if (!label || !value) return;

    function tick() {
      var diff = start - new Date();
      if (isNaN(diff)) { value.textContent = "—"; return; }
      if (diff <= 0) {
        var live = diff > -2 * 3600 * 1000;
        label.textContent = live ? "Session" : "Completed";
        value.textContent = live ? "LIVE" : "—";
        value.classList.toggle("live", live);
        return;
      }
      var h = Math.floor(diff / 3600000);
      var m = Math.floor((diff % 3600000) / 60000);
      var s = Math.floor((diff % 60000) / 1000);
      value.textContent = String(h).padStart(2, "0") + ":" +
        String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }
    tick();
    cdTimer = setInterval(tick, 1000);
  }

  /* ------------------------------------------------------ cross-filter --- */
  function wireCrossFilter() {
    var rows = document.querySelectorAll("[data-code]");
    var scatter = byId("scatter");

    function activate(code) {
      if (state.active === code) return;
      state.active = code;
      Array.prototype.forEach.call(rows, function (r) {
        r.classList.toggle("is-active", r.dataset.code === code);
      });
      if (scatter) {
        scatter.classList.toggle("dimmed", !!code);
        Array.prototype.forEach.call(scatter.querySelectorAll(".pt"), function (p) {
          p.classList.toggle("is-active", p.dataset.code === code);
        });
      }
    }
    function clear() {
      state.active = null;
      Array.prototype.forEach.call(rows, function (r) { r.classList.remove("is-active"); });
      if (scatter) {
        scatter.classList.remove("dimmed");
        Array.prototype.forEach.call(scatter.querySelectorAll(".pt"), function (p) {
          p.classList.remove("is-active");
        });
      }
    }

    Array.prototype.forEach.call(rows, function (r) {
      r.addEventListener("mouseenter", function () { activate(r.dataset.code); });
      r.addEventListener("mouseleave", clear);
    });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
