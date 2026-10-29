/* ==========================================================================
   THE HYPERTEXT TRIOMNIBUS — GRAPH OBSERVATORY ENGINE
   Zero-CDN Self-Contained Interactive Graph Studio, Moretti Ablation Lab,
   Cross-Resource Connector (Calibre Scribrarium + Grist + WordNet), & Q&A Oracle
   ========================================================================== */

(function () {
  "use strict";

  const STATE = {
    data: null,
    nodeMap: new Map(),
    layout: "force", // force | strata | chronicle | ego | ledger
    theme: "light",
    maxDay: 35,
    playTimer: null,
    search: "",
    minThemeSets: 2,
    colorBy: "type", // type | method | difficulty | betweenness
    sizeBy: "betweenness", // betweenness | degree | quotes | uniform
    labelDensity: 2, // 1=minimal, 2=smart hubs, 3=dense, 4=all
    repulsion: 1.0,
    horatioMode: false,
    layers: {
      triad: true,
      shadow: true,
      book: true,
      person: true,
      theme: true,
      ghost_book: true,
    },
    edgeRules: {
      in_set: true,
      credit_author: true,
      credit_translator: true,
      shadow: true,
      has_theme: true,
      wordnet_kin: true,
      ghost_bridge: true,
      shared_theme: false,
      sequence: false,
    },
    ablated: new Set(),
    selectedId: "week:1",
    hoveredId: null,
    draggingId: null,
    spotlightIds: null, // Set of highlighted IDs from Oracle or Pathfinder
    pathIds: [],
    pathEdges: [],
    ledgerSort: { col: "betweenness", dir: -1 },
    // Viewport transform
    cam: { x: 0, y: 0, k: 1 },
    isPanning: false,
    panStart: { x: 0, y: 0, camX: 0, camY: 0 },
    // Active filtered subgraph + live metrics
    visNodes: [],
    visEdges: [],
    liveMetrics: {
      compCount: 1,
      giantPct: 100,
      liveCutpoints: new Set(),
      liveDeg: new Map(),
    },
    simAlpha: 1.0,
    localBridgeAlive: false,
  };

  const DIFF_COLORS = {
    1: "#52b788",
    2: "#4ea8de",
    3: "#457b9d",
    4: "#e07a5f",
    5: "#c1292e",
  };

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function escHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function resolveSiteHref(slug) {
    if (!slug) return null;
    // Works both inside /static/observatory/index.html on live Quartz site and standalone
    if (window.location.hostname.includes("thegesturalist.com") || window.location.port === "8080") {
      return "/" + slug;
    }
    return "https://triomnibus.thegesturalist.com/" + slug;
  }

  // =========================================================================
  // 1. DATA LOADING (Supports both HTTP fetch and file:// JS global fallback)
  // =========================================================================
  async function loadGraphData() {
    if (window.location.protocol === "file:" && window.TRIOMNIBUS_GRAPH_DATA) {
      return window.TRIOMNIBUS_GRAPH_DATA;
    }
    const candidates = ["../triomnibus-graph.json", "./triomnibus-graph.json", "/static/triomnibus-graph.json"];
    for (const url of candidates) {
      try {
        const res = await fetch(url);
        if (res.ok) return await res.json();
      } catch (_) {
        // continue
      }
    }
    if (window.TRIOMNIBUS_GRAPH_DATA) {
      return window.TRIOMNIBUS_GRAPH_DATA;
    }
    throw new Error("Could not load triomnibus-graph.json or triomnibus-graph-data.js");
  }

  // =========================================================================
  // 2. SUBGRAPH FILTERING & LIVE TARJAN / COMPONENT RECOMPUTATION
  // =========================================================================
  function isNodeLayerEnabled(n) {
    if (n.type === "set") {
      return n.subtype === "triad" ? STATE.layers.triad : STATE.layers.shadow;
    }
    if (n.type === "author_week") return STATE.layers.shadow;
    if (n.type === "book") return STATE.layers.book;
    if (n.type === "person") return STATE.layers.person;
    if (n.type === "theme") {
      if (!STATE.layers.theme) return false;
      return (n.cross_set_count || 1) >= STATE.minThemeSets;
    }
    if (n.type === "ghost_book") return STATE.layers.ghost_book;
    return true;
  }

  function isEdgeRuleEnabled(e) {
    const k = e.kind;
    if (k === "in_set" || k === "week_member") return STATE.edgeRules.in_set;
    if (k === "credit:author") return STATE.edgeRules.credit_author;
    if (k === "credit:translator" || k === "credit:editor" || k === "credit:contributor") {
      return STATE.edgeRules.credit_translator;
    }
    if (k.startsWith("shadow:")) return STATE.edgeRules.shadow;
    if (k === "has_theme" || k === "concept_stem") return STATE.edgeRules.has_theme;
    if (k === "wordnet_kin") return STATE.edgeRules.wordnet_kin;
    if (k === "ghost_bridge") return STATE.edgeRules.ghost_bridge;
    if (k === "shared_theme") return STATE.edgeRules.shared_theme;
    if (k === "sequence") return STATE.edgeRules.sequence;
    return true;
  }

  function matchesSearch(n, q) {
    if (!q) return true;
    const hay = [
      n.title,
      n.author_credit,
      n.thread,
      n.carry_quote,
      n.calibre_id ? "calibre " + n.calibre_id : "",
      (n.roles || []).join(" "),
      (n.connection_kinds || []).join(" "),
      (n.quotes || []).join(" "),
      n.fts_snippet || "",
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  }

  function rebuildSubgraph(reheat) {
    const data = STATE.data;
    if (!data) return;
    const q = STATE.search.trim().toLowerCase();

    const activeSet = new Set();
    const candidateNodes = [];

    for (const n of data.nodes) {
      if (STATE.ablated.has(n.id)) continue;
      if ((n.day_index || 1) > STATE.maxDay) continue;
      if (!isNodeLayerEnabled(n)) continue;
      candidateNodes.push(n);
      activeSet.add(n.id);
    }

    // If search query active, keep matching nodes + immediate 1-hop neighbors
    let searchKeep = null;
    if (q) {
      const seeds = new Set(candidateNodes.filter((n) => matchesSearch(n, q)).map((n) => n.id));
      searchKeep = new Set(seeds);
      for (const e of data.edges) {
        if (!isEdgeRuleEnabled(e)) continue;
        if (!activeSet.has(e.source) || !activeSet.has(e.target)) continue;
        if (seeds.has(e.source)) searchKeep.add(e.target);
        if (seeds.has(e.target)) searchKeep.add(e.source);
      }
    }

    STATE.visNodes = searchKeep
      ? candidateNodes.filter((n) => searchKeep.has(n.id))
      : candidateNodes;
    const visIdSet = new Set(STATE.visNodes.map((n) => n.id));

    STATE.visEdges = data.edges.filter(
      (e) =>
        isEdgeRuleEnabled(e) &&
        (e.day_index || 1) <= STATE.maxDay &&
        visIdSet.has(e.source) &&
        visIdSet.has(e.target)
    );

    // Initialize deterministic seed coordinates for any node lacking x,y
    const N = STATE.visNodes.length || 1;
    STATE.visNodes.forEach((n, idx) => {
      if (typeof n.x !== "number" || Number.isNaN(n.x)) {
        const angle = (idx / N) * Math.PI * 2 * 3.7;
        const rad = 70 + Math.sqrt(idx) * 26;
        n.x = Math.cos(angle) * rad;
        n.y = Math.sin(angle) * rad;
        n.vx = 0;
        n.vy = 0;
      }
    });

    computeLiveTopologyMetrics();
    updateTelemetryUI();
    applyProjectionTargets(reheat);
    if (STATE.layout === "ledger") {
      renderLedgerTable();
    }
  }

  function computeLiveTopologyMetrics() {
    const adj = new Map();
    for (const n of STATE.visNodes) adj.set(n.id, new Set());
    for (const e of STATE.visEdges) {
      if (e.source !== e.target) {
        adj.get(e.source).add(e.target);
        adj.get(e.target).add(e.source);
      }
    }

    const liveDeg = new Map();
    for (const [id, s] of adj.entries()) liveDeg.set(id, s.size);

    // Connected components
    const visited = new Set();
    const compSizes = [];
    for (const n of STATE.visNodes) {
      if (visited.has(n.id)) continue;
      let sz = 0;
      const q = [n.id];
      visited.add(n.id);
      while (q.length) {
        const u = q.shift();
        sz++;
        for (const nb of adj.get(u)) {
          if (!visited.has(nb)) {
            visited.add(nb);
            q.push(nb);
          }
        }
      }
      compSizes.push(sz);
    }

    const total = STATE.visNodes.length || 1;
    const maxComp = compSizes.length ? Math.max(...compSizes) : 0;

    // 2-core peeling + live Tarjan cutpoints ("Horatios")
    const alive = new Set(adj.keys());
    let peeled = true;
    while (peeled) {
      peeled = false;
      for (const u of Array.from(alive)) {
        let deg2 = 0;
        for (const nb of adj.get(u)) if (alive.has(nb)) deg2++;
        if (deg2 <= 1) {
          alive.delete(u);
          peeled = true;
        }
      }
    }

    const disc = new Map();
    const low = new Map();
    const parent = new Map();
    const cuts = new Set();
    let timer = 0;

    function dfs(u) {
      // Iterative-safe recursion depth (~370 nodes max)
      disc.set(u, timer);
      low.set(u, timer);
      timer++;
      let children = 0;
      for (const v of adj.get(u)) {
        if (!alive.has(v)) continue;
        if (!disc.has(v)) {
          children++;
          parent.set(v, u);
          dfs(v);
          low.set(u, Math.min(low.get(u), low.get(v)));
          if (parent.get(u) === null && children > 1) cuts.add(u);
          if (parent.get(u) !== null && low.get(v) >= disc.get(u)) cuts.add(u);
        } else if (v !== parent.get(u)) {
          low.set(u, Math.min(low.get(u), disc.get(v)));
        }
      }
    }

    for (const u of alive) {
      if (!disc.has(u)) {
        parent.set(u, null);
        dfs(u);
      }
    }

    STATE.liveMetrics = {
      compCount: compSizes.length,
      giantPct: Math.round((maxComp / total) * 100),
      liveCutpoints: cuts,
      liveDeg,
      compSizes: compSizes.sort((a, b) => b - a),
    };
  }

  // =========================================================================
  // 3. PROJECTION LAYOUTS (Constellation, Strata, Chronicle Arc, Chavruta Ring)
  // =========================================================================
  function applyProjectionTargets(reheat) {
    const nodes = STATE.visNodes;
    if (!nodes.length) return;

    if (STATE.layout === "force") {
      nodes.forEach((n) => {
        n.tx = null;
        n.ty = null;
      });
      if (reheat) STATE.simAlpha = 1.0;
      return;
    }

    STATE.simAlpha = 0.0; // Deterministic coordinate interpolation

    if (STATE.layout === "strata") {
      // 3-Column Operational Diagram: Col 1 = Readings (Triads & Shadow Days) | Col 2 = Books | Col 3 = People & Themes & Ghosts
      const colSets = nodes
        .filter((n) => n.type === "set" || n.type === "author_week")
        .sort((a, b) => (a.day_index || 0) - (b.day_index || 0));
      const colBooks = nodes
        .filter((n) => n.type === "book" || n.type === "ghost_book")
        .sort((a, b) => (a.day_index || 0) - (b.day_index || 0));
      const colRight = nodes
        .filter((n) => n.type === "person" || n.type === "theme")
        .sort((a, b) => (b.betweenness || 0) - (a.betweenness || 0));

      const placeCol = (arr, xPos, spanH) => {
        const step = arr.length > 1 ? spanH / (arr.length - 1) : 0;
        arr.forEach((n, idx) => {
          n.x = xPos;
          n.y = -spanH / 2 + idx * step;
          n.vx = 0;
          n.vy = 0;
        });
      };

      const H = Math.max(920, colBooks.length * 16);
      placeCol(colSets, -460, H * 0.9);
      placeCol(colBooks, 0, H);
      placeCol(colRight, 460, H);
      fitViewport(false);
    } else if (STATE.layout === "chronicle") {
      // Chronological Spine: Sets 1..35 along y=0, People & Shadow above, Themes & Ghost Shelf below, Books clustered beneath their reading
      const sets = nodes
        .filter((n) => n.type === "set")
        .sort((a, b) => (a.day_index || 0) - (b.day_index || 0));
      const stepX = 52;
      const startX = -((sets.length - 1) * stepX) / 2;
      const setX = new Map();
      sets.forEach((n, idx) => {
        n.x = startX + idx * stepX;
        n.y = 0;
        setX.set(n.set_id, n.x);
      });

      const weeks = nodes.filter((n) => n.type === "author_week");
      weeks.forEach((w, idx) => {
        w.x = startX + Math.max(0, sets.length - 2) * stepX;
        w.y = -210 - idx * 45;
      });

      // Books stacked cleanly right below their primary set
      const bookSlot = new Map();
      nodes
        .filter((n) => n.type === "book")
        .forEach((b) => {
          const sid = (b.sets && b.sets[0]) || 1;
          const slot = (bookSlot.get(sid) || 0) + 1;
          bookSlot.set(sid, slot);
          b.x = (setX.get(sid) ?? 0) + (slot % 2 === 0 ? 7 : -7);
          b.y = 48 + slot * 26;
        });

      // Multi-reading People arched above
      const people = nodes
        .filter((n) => n.type === "person")
        .sort((a, b) => (b.cross_set_count || 1) - (a.cross_set_count || 1));
      people.forEach((p, idx) => {
        const sids = p.sets || [];
        const avgX = sids.length
          ? sids.reduce((acc, s) => acc + (setX.get(s) ?? 0), 0) / sids.length
          : 0;
        const isBridge = (p.cross_set_count || 1) >= 2;
        p.x = avgX + ((idx % 5) - 2) * 14;
        p.y = isBridge ? -155 - (idx % 3) * 34 : -75 - (idx % 3) * 22;
      });

      // Themes & Ghosts below
      const themes = nodes.filter((n) => n.type === "theme" || n.type === "ghost_book");
      themes.forEach((t, idx) => {
        const sids = t.sets || [];
        const avgX = sids.length
          ? sids.reduce((acc, s) => acc + (setX.get(s) ?? 0), 0) / sids.length
          : startX + sets.length * stepX * 0.8;
        t.x = avgX + ((idx % 5) - 2) * 18;
        t.y = t.type === "ghost_book" ? -255 - (idx % 2) * 30 : 255 + (idx % 4) * 34;
      });
      fitViewport(false);
    } else if (STATE.layout === "ego") {
      // Concentric Chavruta / Shadow Rings around selectedId
      const centerId =
        STATE.visNodes.some((n) => n.id === STATE.selectedId)
          ? STATE.selectedId
          : (STATE.visNodes[0] && STATE.visNodes[0].id);
      if (!centerId) return;

      const adj = new Map();
      for (const n of nodes) adj.set(n.id, []);
      for (const e of STATE.visEdges) {
        adj.get(e.source).push(e.target);
        adj.get(e.target).push(e.source);
      }

      const dist = new Map([[centerId, 0]]);
      const q = [centerId];
      while (q.length) {
        const u = q.shift();
        const d = dist.get(u);
        if (d >= 3) continue;
        for (const nb of adj.get(u) || []) {
          if (!dist.has(nb)) {
            dist.set(nb, d + 1);
            q.push(nb);
          }
        }
      }

      const rings = [[], [], []];
      STATE.egoKeep = new Set();
      for (const n of nodes) {
        if (!dist.has(n.id) || dist.get(n.id) > 2) continue;
        STATE.egoKeep.add(n.id);
        rings[dist.get(n.id)].push(n);
      }

      const radii = [0, 128, 255];
      rings.forEach((arr, rIdx) => {
        const R = radii[rIdx];
        if (rIdx === 0) {
          arr.forEach((n) => {
            n.x = 0;
            n.y = 0;
          });
          return;
        }
        arr.forEach((n, idx) => {
          const th = (idx / arr.length) * Math.PI * 2 - Math.PI / 2;
          n.x = Math.cos(th) * R;
          n.y = Math.sin(th) * R;
        });
      });
      STATE.cam.x = 0;
      STATE.cam.y = 0;
      STATE.cam.k = 0.92;
    }
  }

  // =========================================================================
  // 4. FORCE SIMULATION STEP (Velocity-Verlet with Collision & Hub Gravity)
  // =========================================================================
  function stepForceSimulation() {
    if (STATE.layout !== "force" || STATE.simAlpha < 0.004) return;
    const nodes = STATE.visNodes;
    const edges = STATE.visEdges;
    const N = nodes.length;
    if (!N) return;

    const alpha = STATE.simAlpha;
    const repulseK = 1650 * STATE.repulsion;

    // Pairwise repulsion + anti-overlap collision (~370 visible nodes => ~68k pairs = <1.2ms in V8)
    for (let i = 0; i < N; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < N; j++) {
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) {
          dx = (Math.random() - 0.5) * 2;
          dy = (Math.random() - 0.5) * 2;
          d2 = 4;
        }
        if (d2 < 260000) {
          const d = Math.sqrt(d2);
          const f = (repulseK * alpha) / d2;
          const fx = (dx / d) * f;
          const fy = (dy / d) * f;
          if (!a.pinned) {
            a.vx -= fx;
            a.vy -= fy;
          }
          if (!b.pinned) {
            b.vx += fx;
            b.vy += fy;
          }
          const minGap = nodeRadius(a) + nodeRadius(b) + 5;
          if (d < minGap) {
            const push = ((minGap - d) / d) * 0.35;
            if (!a.pinned) {
              a.vx -= dx * push;
              a.vy -= dy * push;
            }
            if (!b.pinned) {
              b.vx += dx * push;
              b.vy += dy * push;
            }
          }
        }
      }
    }

    // Spring attraction along active edges
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const s = STATE.nodeMap.get(e.source);
      const t = STATE.nodeMap.get(e.target);
      if (!s || !t) continue;
      const dx = t.x - s.x;
      const dy = t.y - s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const targetLen =
        e.kind === "in_set" ? 56 : e.kind.startsWith("credit:") ? 46 : e.kind.startsWith("shadow:") ? 72 : 88;
      const k = e.kind === "shared_theme" ? 0.015 : 0.048;
      const f = (d - targetLen) * k * alpha;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      if (!s.pinned) {
        s.vx += fx;
        s.vy += fy;
      }
      if (!t.pinned) {
        t.vx -= fx;
        t.vy -= fy;
      }
    }

    // Gentle centering gravity (keeps disconnected islands visible inside frame)
    for (let i = 0; i < N; i++) {
      const n = nodes[i];
      if (n.pinned || n.id === STATE.draggingId) continue;
      n.vx -= n.x * 0.0085 * alpha;
      n.vy -= n.y * 0.0085 * alpha;
      n.vx *= 0.78;
      n.vy *= 0.78;
      n.x += n.vx;
      n.y += n.vy;
    }

    STATE.simAlpha *= 0.972;
  }

  // =========================================================================
  // 5. VISUAL ENCODING & HIGH-DPI CANVAS RENDERER
  // =========================================================================
  function nodeRadius(n) {
    if (STATE.sizeBy === "uniform") return n.type === "set" || n.type === "author_week" ? 8.5 : 6;
    if (STATE.sizeBy === "quotes") {
      const qc = n.quote_count || (n.carry_quote ? 2 : 0);
      return 5 + Math.min(11, qc * 1.6);
    }
    if (STATE.sizeBy === "degree") {
      const d = STATE.liveMetrics.liveDeg.get(n.id) || n.degree || 1;
      return 4.5 + Math.min(13, Math.sqrt(d) * 2.5);
    }
    // Default: Brandes betweenness + base entity hierarchy
    const base =
      n.type === "author_week"
        ? 11
        : n.type === "set"
        ? 8.2
        : n.type === "ghost_book"
        ? 7.2
        : n.type === "theme"
        ? 6.8
        : n.type === "book"
        ? 6.2
        : 5.5;
    const boost = Math.min(11, Math.sqrt(n.betweenness || 0) * 26);
    return base + boost;
  }

  function nodeColor(n) {
    if (STATE.colorBy === "method") {
      if (n.type === "ghost_book") return cssVar("--node-ghost");
      const isShadow =
        n.subtype === "shadow" ||
        n.type === "author_week" ||
        n.method === "An author and their shadow" ||
        (n.sets && n.sets.some((s) => s >= 33));
      return isShadow ? cssVar("--node-shadow") : cssVar("--node-triad");
    }
    if (STATE.colorBy === "difficulty") {
      return DIFF_COLORS[n.difficulty || 3] || cssVar("--node-book");
    }
    if (STATE.colorBy === "betweenness") {
      const b = Math.min(1, (n.betweenness || 0) * 8);
      return b > 0.55 ? cssVar("--cutpoint") : b > 0.15 ? cssVar("--gold") : cssVar("--node-triad");
    }
    // Entity & Role
    if (n.type === "author_week") return cssVar("--node-week");
    if (n.type === "set") {
      return n.subtype === "shadow" ? cssVar("--node-shadow") : cssVar("--node-triad");
    }
    if (n.type === "book") return cssVar("--node-book");
    if (n.type === "person") {
      return (n.roles || []).includes("translator") ? cssVar("--node-translator") : cssVar("--node-person");
    }
    if (n.type === "theme") return cssVar("--node-theme");
    if (n.type === "ghost_book") return cssVar("--node-ghost");
    return cssVar("--accent");
  }

  function edgeStyle(e, highlighted) {
    const k = e.kind;
    if (highlighted) return { stroke: cssVar("--cutpoint"), width: 2.5, dash: [] };
    if (k === "shadow:argues") return { stroke: cssVar("--warn"), width: 1.9, dash: [5, 3] };
    if (k.startsWith("shadow:")) return { stroke: cssVar("--gold"), width: 1.8, dash: [] };
    if (k === "wordnet_kin") return { stroke: cssVar("--gold"), width: 1.5, dash: [3, 3] };
    if (k === "ghost_bridge") return { stroke: cssVar("--ghost"), width: 1.4, dash: [4, 4] };
    if (k === "credit:translator" || k === "credit:editor") {
      return { stroke: cssVar("--tertiary"), width: 1.35, dash: [2, 2] };
    }
    if (k === "sequence") return { stroke: cssVar("--accent"), width: 1.4, dash: [] };
    return {
      stroke: STATE.theme === "dark" ? "rgba(180,185,198,0.22)" : "rgba(65,72,82,0.20)",
      width: 1.0,
      dash: [],
    };
  }

  const canvas = document.getElementById("graph-canvas");
  const ctx = canvas.getContext("2d");

  function resizeCanvas() {
    const stage = document.getElementById("obs-stage");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(300, stage.clientWidth) * dpr;
    canvas.height = Math.max(300, stage.clientHeight) * dpr;
  }

  function screenToWorld(sx, sy) {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    return {
      x: (sx - w / 2 - STATE.cam.x) / STATE.cam.k,
      y: (sy - h / 2 - STATE.cam.y) / STATE.cam.k,
    };
  }

  function drawFrame() {
    stepForceSimulation();

    if (STATE.layout === "ledger") {
      requestAnimationFrame(drawFrame);
      return;
    }

    const dpr = window.devicePixelRatio || 1;
    const W = canvas.width;
    const H = canvas.height;
    ctx.save();
    ctx.clearRect(0, 0, W, H);
    ctx.scale(dpr, dpr);

    const cw = canvas.clientWidth;
    const ch = canvas.clientHeight;
    ctx.translate(cw / 2 + STATE.cam.x, ch / 2 + STATE.cam.y);
    ctx.scale(STATE.cam.k, STATE.cam.k);

    // Determine active focus neighborhood (hover, path, or spotlight)
    const focusSet = new Set();
    const pathEdgeKeySet = new Set(
      STATE.pathEdges.map((pe) => [pe.source, pe.target].sort().join("||"))
    );

    const activeFocusNode = STATE.hoveredId || null;
    if (activeFocusNode) {
      focusSet.add(activeFocusNode);
      for (const e of STATE.visEdges) {
        if (e.source === activeFocusNode) focusSet.add(e.target);
        if (e.target === activeFocusNode) focusSet.add(e.source);
      }
    } else if (STATE.pathIds.length) {
      STATE.pathIds.forEach((id) => focusSet.add(id));
    } else if (STATE.spotlightIds && STATE.spotlightIds.size) {
      STATE.spotlightIds.forEach((id) => focusSet.add(id));
    }

    const hasFocus = focusSet.size > 0;

    // Optional Ego Concentric Guide Rings
    if (STATE.layout === "ego") {
      ctx.save();
      ctx.strokeStyle = STATE.theme === "dark" ? "rgba(255,255,255,0.11)" : "rgba(40,75,99,0.13)";
      ctx.setLineDash([4, 5]);
      [128, 255].forEach((r) => {
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.stroke();
      });
      ctx.restore();
    }

    const isEgoHidden = (id) => STATE.layout === "ego" && STATE.egoKeep && !STATE.egoKeep.has(id);

    // ---- Draw Edges ----
    for (let i = 0; i < STATE.visEdges.length; i++) {
      const e = STATE.visEdges[i];
      if (isEgoHidden(e.source) || isEgoHidden(e.target)) continue;
      const s = STATE.nodeMap.get(e.source);
      const t = STATE.nodeMap.get(e.target);
      if (!s || !t) continue;

      const eKey = [e.source, e.target].sort().join("||");
      const inPath = pathEdgeKeySet.has(eKey);
      const touchesHover =
        activeFocusNode && (e.source === activeFocusNode || e.target === activeFocusNode);
      const inSpotlight =
        !activeFocusNode &&
        !STATE.pathIds.length &&
        STATE.spotlightIds &&
        STATE.spotlightIds.has(e.source) &&
        STATE.spotlightIds.has(e.target);

      const hi = inPath || touchesHover || inSpotlight;
      const st = edgeStyle(e, hi);

      ctx.save();
      ctx.globalAlpha = hasFocus ? (hi ? 0.96 : 0.08) : 0.85;
      ctx.strokeStyle = st.stroke;
      ctx.lineWidth = st.width / Math.sqrt(Math.max(0.45, STATE.cam.k));
      ctx.setLineDash(st.dash);

      ctx.beginPath();
      if (STATE.layout === "chronicle" && Math.abs(t.x - s.x) > 45 && Math.abs(t.y - s.y) < 40) {
        // Sweeping Bézier arc along the chronological spine
        const midX = (s.x + t.x) / 2;
        const span = Math.min(240, Math.abs(t.x - s.x) * 0.42);
        const arcY = e.kind === "shared_theme" ? Math.max(s.y, t.y) + span : Math.min(s.y, t.y) - span;
        ctx.moveTo(s.x, s.y);
        ctx.quadraticCurveTo(midX, arcY, t.x, t.y);
      } else {
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(t.x, t.y);
      }
      ctx.stroke();
      ctx.restore();
    }

    // ---- Draw Nodes & Smart De-cluttered Labels ----
    const placedLabels = [];
    const fgColor = cssVar("--fg");
    const bgCard = cssVar("--bg-card");
    const cutColor = cssVar("--cutpoint");
    const goldColor = cssVar("--gold");

    // Draw higher-priority nodes on top
    const orderedNodes = STATE.visNodes
      .filter((n) => !isEgoHidden(n.id))
      .sort((a, b) => (a.betweenness || 0) - (b.betweenness || 0));

    for (let i = 0; i < orderedNodes.length; i++) {
      const n = orderedNodes[i];
      const r = nodeRadius(n);
      const col = nodeColor(n);
      const isSelected = n.id === STATE.selectedId;
      const isFocused = !hasFocus || focusSet.has(n.id) || isSelected;
      const isHoratio = STATE.liveMetrics.liveCutpoints.has(n.id) || n.is_macro_cutpoint;

      ctx.save();
      ctx.globalAlpha = isFocused ? 1.0 : 0.16;

      // Calibre Viewer Marginalia Halo
      if (n.reader_highlights > 0) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4.5, 0, Math.PI * 2);
        ctx.strokeStyle = goldColor;
        ctx.lineWidth = 1.6;
        ctx.stroke();
      }

      // Horatio (2-Core Macro Cutpoint) Reticle Ring
      if ((STATE.horatioMode && isHoratio) || isSelected) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + (isSelected ? 5.5 : 4.5), 0, Math.PI * 2);
        ctx.strokeStyle = isSelected ? fgColor : cutColor;
        ctx.lineWidth = isSelected ? 2.2 : 1.9;
        if (!isSelected) ctx.setLineDash([3, 2]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Vertex Geometry by Entity Class
      ctx.fillStyle = col;
      ctx.strokeStyle = bgCard;
      ctx.lineWidth = 1.3;

      if (n.type === "set" || n.type === "author_week") {
        // Diamond
        const dr = r * 1.22;
        ctx.beginPath();
        ctx.moveTo(n.x, n.y - dr);
        ctx.lineTo(n.x + dr, n.y);
        ctx.lineTo(n.x, n.y + dr);
        ctx.lineTo(n.x - dr, n.y);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      } else if (n.type === "person") {
        // Rounded Square (dashed outline if Translator!)
        const sr = r * 0.92;
        ctx.beginPath();
        ctx.rect(n.x - sr, n.y - sr, sr * 2, sr * 2);
        ctx.fill();
        if ((n.roles || []).includes("translator")) {
          ctx.strokeStyle = goldColor;
          ctx.lineWidth = 1.6;
        }
        ctx.stroke();
      } else if (n.type === "ghost_book") {
        // Translucent Ghost Circle with Dashed Rim
        ctx.save();
        ctx.globalAlpha *= 0.25;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
        ctx.beginPath();
        ctx.setLineDash([3, 2.5]);
        ctx.strokeStyle = col;
        ctx.lineWidth = 1.8;
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        // Book or Theme Circle
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }

      ctx.restore();
    }

    // ---- Second Pass: Smart Anti-Collision Labels ----
    const labelCandidates = orderedNodes.slice().reverse();
    ctx.font = '500 11px "Source Serif 4", Georgia, serif';
    ctx.textBaseline = "middle";

    for (let i = 0; i < labelCandidates.length; i++) {
      const n = labelCandidates[i];
      const isSelected = n.id === STATE.selectedId;
      const isHovered = n.id === STATE.hoveredId;
      const inFocus = hasFocus && focusSet.has(n.id);
      const isHoratio = STATE.liveMetrics.liveCutpoints.has(n.id) || n.is_macro_cutpoint;

      let wantLabel = false;
      if (isSelected || isHovered || inFocus || STATE.layout === "ego") {
        wantLabel = true;
      } else if (!hasFocus) {
        if (STATE.labelDensity === 4) wantLabel = true;
        else if (STATE.labelDensity === 3) {
          wantLabel =
            n.type === "set" ||
            n.type === "author_week" ||
            n.type === "ghost_book" ||
            (n.cross_set_count || 0) >= 2 ||
            (n.betweenness || 0) >= 0.015;
        } else if (STATE.labelDensity === 2) {
          wantLabel =
            n.type === "author_week" ||
            n.subtype === "shadow" ||
            isHoratio ||
            (n.type === "person" && (n.cross_set_count || 0) >= 2) ||
            (n.type === "theme" && (n.cross_set_count || 0) >= 3) ||
            (n.type === "set" && ((n.betweenness || 0) >= 0.085 || STATE.cam.k >= 1.15)) ||
            n.type === "ghost_book" ||
            STATE.cam.k >= 1.55;
        } else {
          wantLabel = isHoratio || n.type === "author_week" || (n.betweenness || 0) >= 0.1;
        }
      }

      if (!wantLabel) continue;

      const rawLabel = n.title.length > 36 && !isSelected && !isHovered ? n.title.slice(0, 34) + "…" : n.title;
      const r = nodeRadius(n);
      const lx = n.x + r + 5;
      const ly = n.y;
      const tw = ctx.measureText(rawLabel).width;
      const box = {
        x0: lx - 2,
        y0: ly - 7,
        x1: lx + tw + 4,
        y1: ly + 7,
      };

      if (!isSelected && !isHovered && !inFocus) {
        let collides = false;
        for (let j = 0; j < placedLabels.length; j++) {
          const b = placedLabels[j];
          if (!(box.x1 < b.x0 || box.x0 > b.x1 || box.y1 < b.y0 || box.y0 > b.y1)) {
            collides = true;
            break;
          }
        }
        if (collides) continue;
      }
      placedLabels.push(box);

      ctx.save();
      ctx.fillStyle = STATE.theme === "dark" ? "rgba(22,22,24,0.82)" : "rgba(250,248,248,0.86)";
      ctx.fillRect(box.x0, box.y0, box.x1 - box.x0, box.y1 - box.y0);
      ctx.fillStyle = isSelected || isHovered ? cssVar("--accent") : fgColor;
      if (isSelected || isHovered || (STATE.horatioMode && isHoratio)) {
        ctx.font = '600 11.5px "Source Serif 4", Georgia, serif';
      } else {
        ctx.font = '400 11px "Source Serif 4", Georgia, serif';
      }
      ctx.fillText(rawLabel, lx, ly);
      ctx.restore();
    }

    ctx.restore();
    requestAnimationFrame(drawFrame);
  }

  function fitViewport(animate) {
    if (!STATE.visNodes.length) return;
    let minX = Infinity,
      maxX = -Infinity,
      minY = Infinity,
      maxY = -Infinity;
    for (const n of STATE.visNodes) {
      if (n.x < minX) minX = n.x;
      if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.y > maxY) maxY = n.y;
    }
    const pad = 70;
    const spanW = Math.max(180, maxX - minX + pad * 2);
    const spanH = Math.max(180, maxY - minY + pad * 2);
    const cw = Math.max(320, canvas.clientWidth);
    const ch = Math.max(320, canvas.clientHeight);
    const k = Math.max(0.28, Math.min(1.65, Math.min(cw / spanW, ch / spanH)));
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    STATE.cam.k = k;
    STATE.cam.x = -cx * k;
    STATE.cam.y = -cy * k;
    if (animate) STATE.simAlpha = Math.max(STATE.simAlpha, 0.15);
  }

  // =========================================================================
  // 6. TELEMETRY, INSPECTOR & EXTERNAL RESOURCE CONNECTORS
  // =========================================================================
  function updateTelemetryUI() {
    document.getElementById("stat-nodes").textContent = STATE.visNodes.length;
    document.getElementById("stat-edges").textContent = STATE.visEdges.length;
    document.getElementById("stat-comps").textContent = STATE.liveMetrics.compCount;
    document.getElementById("stat-giant").textContent = STATE.liveMetrics.giantPct + "%";
    document.getElementById("stat-horatios").textContent = STATE.liveMetrics.liveCutpoints.size;

    const abPill = document.getElementById("stat-ablated-pill");
    const abBanner = document.getElementById("ablation-banner");
    if (STATE.ablated.size > 0) {
      abPill.style.display = "inline-flex";
      abPill.classList.add("alert");
      document.getElementById("stat-ablated").textContent = STATE.ablated.size;
      abBanner.classList.add("visible");
      const names = Array.from(STATE.ablated)
        .map((id) => (STATE.nodeMap.get(id) || {}).title || id)
        .join(", ");
      document.getElementById("ablation-summary").textContent =
        `Ablated (${STATE.ablated.size}): ${names} → ${STATE.liveMetrics.compCount} components (${STATE.liveMetrics.giantPct}% giant comp)`;
    } else {
      abPill.style.display = "none";
      abBanner.classList.remove("visible");
    }

    // Update layer counts
    const counts = { triad: 0, shadow: 0, book: 0, person: 0, theme: 0, ghost: 0 };
    for (const n of STATE.visNodes) {
      if (n.type === "set" && n.subtype === "triad") counts.triad++;
      else if ((n.type === "set" && n.subtype === "shadow") || n.type === "author_week") counts.shadow++;
      else if (n.type === "book") counts.book++;
      else if (n.type === "person") counts.person++;
      else if (n.type === "theme") counts.theme++;
      else if (n.type === "ghost_book") counts.ghost++;
    }
    document.getElementById("cnt-triad").textContent = counts.triad;
    document.getElementById("cnt-shadow").textContent = counts.shadow;
    document.getElementById("cnt-book").textContent = counts.book;
    document.getElementById("cnt-person").textContent = counts.person;
    document.getElementById("cnt-theme").textContent = counts.theme;
    document.getElementById("cnt-ghost").textContent = counts.ghost;

    updateAblationLabCard();
  }

  function selectNode(nodeId, panTo) {
    if (!STATE.nodeMap.has(nodeId)) return;
    STATE.selectedId = nodeId;
    if (STATE.layout === "ego") {
      applyProjectionTargets(false);
    } else if (panTo) {
      const n = STATE.nodeMap.get(nodeId);
      if (n && typeof n.x === "number") {
        STATE.cam.x = -n.x * STATE.cam.k;
        STATE.cam.y = -n.y * STATE.cam.k;
      }
    }
    renderInspector();
  }

  function renderNodeChip(id) {
    const n = STATE.nodeMap.get(id);
    if (!n) return "";
    const icon =
      n.type === "set"
        ? "◆"
        : n.type === "author_week"
        ? "❖"
        : n.type === "book"
        ? "●"
        : n.type === "person"
        ? "■"
        : n.type === "ghost_book"
        ? "◌"
        : "◦";
    return `<button class="node-chip" data-focus-node="${escHtml(n.id)}">${icon} ${escHtml(n.title)}</button>`;
  }

  function renderInspector() {
    const box = document.getElementById("inspector-content");
    const n = STATE.nodeMap.get(STATE.selectedId);
    if (!n) {
      box.innerHTML = `<p class="hint-note">Click any vertex in the constellation to inspect its verified quotations, structural centrality, Shadow citations, and external Scribrarium connections.</p>`;
      return;
    }

    const isHoratio = STATE.liveMetrics.liveCutpoints.has(n.id) || n.is_macro_cutpoint;
    const deg = STATE.liveMetrics.liveDeg.get(n.id) ?? n.degree ?? 0;
    const siteHref = resolveSiteHref(n.slug);

    // Collect connected neighbors by relation
    const neighbors = [];
    const shadowCitations = [];
    const wordnetLinks = [];
    for (const e of STATE.data.edges) {
      if (e.source === n.id || e.target === n.id) {
        const otherId = e.source === n.id ? e.target : e.source;
        const other = STATE.nodeMap.get(otherId);
        if (!other) continue;
        neighbors.push({ edge: e, node: other });
        if (e.kind.startsWith("shadow:")) shadowCitations.push({ edge: e, node: other });
        if (e.kind === "wordnet_kin") wordnetLinks.push({ edge: e, node: other });
      }
    }

    let html = `<div class="insp-kicker">`;
    html += `<span class="tag-pill">${escHtml(n.type.replace("_", " ").toUpperCase())}</span>`;
    if (n.method) html += `<span class="tag-pill">${escHtml(n.method)}</span>`;
    if (n.roles && n.roles.length) html += `<span class="tag-pill">${escHtml(n.roles.join(", "))}</span>`;
    if (n.difficulty) html += `<span class="tag-pill">Difficulty ${n.difficulty}</span>`;
    if (isHoratio) html += `<span class="tag-pill horatio">◎ 2-Core Horatio (Cutpoint)</span>`;
    if (n.type === "ghost_book") html += `<span class="tag-pill ghost">◌ Unresurfaced Scribrarium Bridge</span>`;
    if (n.preview) html += `<span class="tag-pill">Staged Grist Sync Preview</span>`;
    html += `</div>`;

    html += `<h2 class="insp-title">${escHtml(n.title)}</h2>`;

    const metaParts = [];
    if (n.date) metaParts.push(n.date + ` (Day ${n.day_index})`);
    if (n.author_credit) metaParts.push(n.author_credit);
    if (n.year) metaParts.push(n.year);
    if (n.calibre_id) metaParts.push(`Calibre ID ${n.calibre_id}`);
    if (n.mentions_total) metaParts.push(`${n.mentions_total} Scribrarium FTS mentions`);
    if (n.cross_set_count > 1) metaParts.push(`Spans ${n.cross_set_count} readings`);
    if (metaParts.length) {
      html += `<div class="insp-meta">${escHtml(metaParts.join(" · "))}</div>`;
    }

    // Action Buttons
    html += `<div style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px">`;
    if (siteHref) {
      html += `<a class="btn btn-primary" href="${escHtml(siteHref)}" target="_blank" rel="noopener" style="text-decoration:none">Open Reading Page ↗</a>`;
    }
    html += `<button class="btn" data-ego-focus="${escHtml(n.id)}">4 · Chavruta Ring</button>`;
    html += `<button class="btn btn-warn" data-ablate-one="${escHtml(n.id)}">✂ Ablate Vertex</button>`;
    html += `<button class="btn" data-park-node="${escHtml(n.id)}" title="Park follow-up item for Nov_PLR1_26 in Grist Parking_Lot">📌 Park (PLR)</button>`;
    html += `</div>`;

    // Hage & Harary Metrics Grid
    html += `<div class="metrics-box">
      <div class="metric-cell"><div class="m-lbl">Degree (k)</div><div class="m-val">${deg}</div></div>
      <div class="metric-cell"><div class="m-lbl">Betweenness</div><div class="m-val">${(n.betweenness || 0).toFixed(4)}</div></div>
      <div class="metric-cell"><div class="m-lbl">Clustering</div><div class="m-val">${(n.clustering || 0).toFixed(3)}</div></div>
    </div>`;

    // Thread / Role / Ghost Explanation
    if (n.thread) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:5px">The Thread / Finding</div><p style="margin:0;font-size:13.5px">${escHtml(n.thread)}</p></div>`;
    }
    if (n.day_plan) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:5px">Week Architecture</div><p style="margin:0;font-size:13px">${escHtml(n.day_plan)}</p></div>`;
    }
    if (n.role_in_set) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:4px">Role in Reading</div><p style="margin:0 0 6px;font-size:13px"><strong>${escHtml(n.role_in_set)}</strong></p>${n.selection_logic ? `<p style="margin:0;font-size:12.5px;color:var(--fg-muted)">${escHtml(n.selection_logic)}</p>` : ""}</div>`;
    }
    if (n.why_bridge) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:4px">Scribrarium FTS5 Discovery</div><p style="margin:0 0 6px;font-size:13px">${escHtml(n.why_bridge)}</p><div class="quote-block">${escHtml(n.fts_snippet)}</div></div>`;
    }

    // Carry Quote & Verified Quotations
    if (n.carry_quote) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:4px">To Carry</div><div class="quote-block">${escHtml(n.carry_quote)}</div></div>`;
    }
    if (n.quotes && n.quotes.length) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:4px">Verified Quotations (${n.quote_count})</div>`;
      n.quotes.slice(0, 4).forEach((q) => {
        html += `<div class="quote-block">${escHtml(q)}</div>`;
      });
      html += `</div>`;
    }

    // Calibre Viewer Reader Highlights (Cross-Resource Enrichment!)
    if (n.reader_highlights > 0 && n.sample_highlight) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:4px;color:var(--gold)">◎ Calibre Viewer Marginalia (${n.reader_highlights} highlights)</div>
        <div class="quote-block highlight-calibre">“${escHtml(n.sample_highlight)}”</div></div>`;
    }

    // Shadow Citations & Disputes
    if (shadowCitations.length) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:5px;color:var(--warn)">Shadow Citations &amp; Disputes</div>`;
      shadowCitations.forEach(({ edge, node }) => {
        html += `<div class="quote-block shadow-dispute">
          <strong>${escHtml(edge.relation || edge.kind)}</strong> ↔ ${renderNodeChip(node.id)}
          ${edge.location ? `<span class="mono" style="font-size:10.5px;color:var(--fg-subtle)"> (${escHtml(edge.location)})</span>` : ""}
          ${edge.quote ? `<div style="margin-top:4px">${escHtml(edge.quote)}</div>` : ""}
        </div>`;
      });
      html += `</div>`;
    }

    // WordNet OEWN Synset Lineage
    if (wordnetLinks.length) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:5px;color:var(--gold)">WordNet OEWN Semantic Kinship</div>`;
      wordnetLinks.forEach(({ edge, node }) => {
        html += `<div class="resource-card">
          <div>${renderNodeChip(node.id)} · <span class="mono" style="font-size:10.5px">Wu-Palmer ${(edge.wu_palmer || 0).toFixed(2)}</span></div>
          <div class="mono" style="font-size:10.5px;color:var(--fg-muted);margin-top:4px">LCA: ${escHtml(edge.meet_at)}</div>
          <div class="mono" style="font-size:10px;color:var(--fg-subtle);margin-top:3px">${escHtml((edge.synset_path || []).join(" → "))}</div>
        </div>`;
      });
      html += `</div>`;
    }

    // Connected Neighbors grouped cleanly
    if (neighbors.length) {
      html += `<div class="panel-sec"><div class="sec-title" style="margin-bottom:6px">Connected Constellation (${neighbors.length})</div><div>`;
      const seen = new Set();
      neighbors.slice(0, 36).forEach(({ node }) => {
        if (seen.has(node.id)) return;
        seen.add(node.id);
        html += renderNodeChip(node.id);
      });
      html += `</div></div>`;
    }

    // Park confirmation drawer placeholder
    html += `<div id="park-toast"></div>`;
    box.innerHTML = html;
  }

  // =========================================================================
  // 7. MORETTI LAB & CHAVRUTA SHORTEST PATHFINDER
  // =========================================================================
  function populatePathDropdowns() {
    const srcSel = document.getElementById("path-src");
    const dstSel = document.getElementById("path-dst");
    const pickable = STATE.data.nodes
      .filter((n) => n.type !== "theme" || (n.cross_set_count || 0) >= 2)
      .slice()
      .sort((a, b) => a.type.localeCompare(b.type) || a.title.localeCompare(b.title));

    const opts = pickable
      .map((n) => `<option value="${escHtml(n.id)}">[${escHtml(n.type)}] ${escHtml(n.title)}</option>`)
      .join("");
    srcSel.innerHTML = opts;
    dstSel.innerHTML = opts;
    if (STATE.nodeMap.has("person:georges-perec")) srcSel.value = "person:georges-perec";
    if (STATE.nodeMap.has("person:giorgio-agamben")) dstSel.value = "person:giorgio-agamben";
  }

  function runShortestPath(srcId, dstId) {
    if (!srcId || !dstId || srcId === dstId) return;

    // Search across ALL non-ablated core + WordNet + Ghost edges (excluding trivial chronological sequence unless enabled)
    const adj = new Map();
    for (const n of STATE.data.nodes) {
      if (!STATE.ablated.has(n.id)) adj.set(n.id, []);
    }
    for (const e of STATE.data.edges) {
      if (e.kind === "sequence" && !STATE.edgeRules.sequence) continue;
      if (!adj.has(e.source) || !adj.has(e.target)) continue;
      adj.get(e.source).push({ to: e.target, edge: e });
      adj.get(e.target).push({ to: e.source, edge: e });
    }

    const prev = new Map([[srcId, null]]);
    const prevEdge = new Map();
    const q = [srcId];
    let found = false;

    while (q.length) {
      const u = q.shift();
      if (u === dstId) {
        found = true;
        break;
      }
      for (const { to, edge } of adj.get(u) || []) {
        if (!prev.has(to)) {
          prev.set(to, u);
          prevEdge.set(to, edge);
          q.push(to);
        }
      }
    }

    const detailBox = document.getElementById("path-detail-box");
    const banner = document.getElementById("path-banner");
    const chainEl = document.getElementById("path-banner-chain");

    if (!found) {
      STATE.pathIds = [];
      STATE.pathEdges = [];
      banner.classList.remove("visible");
      detailBox.innerHTML = `<div class="resource-card" style="border-color:var(--warn)">No path exists between these vertices under current ablations! (They sit in disconnected components.)</div>`;
      return;
    }

    const pathNodes = [];
    const pathEdges = [];
    let cur = dstId;
    while (cur) {
      pathNodes.unshift(cur);
      if (prevEdge.has(cur)) pathEdges.unshift(prevEdge.get(cur));
      cur = prev.get(cur);
    }

    // Ensure any theme node on the path is visible even if minThemeSets hid it
    pathNodes.forEach((id) => {
      const nd = STATE.nodeMap.get(id);
      if (nd && nd.type === "theme" && (nd.cross_set_count || 1) < STATE.minThemeSets) {
        STATE.minThemeSets = 1;
        document.getElementById("min-theme-sets").value = "1";
        document.getElementById("min-theme-val").textContent = "≥ 1 reading (all)";
      }
    });
    rebuildSubgraph(false);

    STATE.pathIds = pathNodes;
    STATE.pathEdges = pathEdges;

    banner.classList.add("visible");
    document.getElementById("path-banner-title").textContent =
      `Chavruta Dialectical Path (${pathEdges.length} hops)`;
    chainEl.innerHTML = pathNodes
      .map((id, idx) => {
        const chip = renderNodeChip(id);
        if (idx === pathNodes.length - 1) return chip;
        const ek = pathEdges[idx].label || pathEdges[idx].kind;
        return `${chip} <span style="color:var(--fg-subtle)">—[${escHtml(ek)}]→</span> `;
      })
      .join("");

    let dHtml = `<div class="resource-card"><h4>${pathEdges.length}-Hop Chain</h4><ol style="margin:6px 0 0;padding-left:18px">`;
    for (let i = 0; i < pathEdges.length; i++) {
      const a = STATE.nodeMap.get(pathNodes[i]);
      const b = STATE.nodeMap.get(pathNodes[i + 1]);
      const e = pathEdges[i];
      const qQuote = e.quote || b.carry_quote || (b.quotes && b.quotes[0]) || "";
      dHtml += `<li style="margin-bottom:6px">
        <strong>${escHtml(a.title)}</strong> → <em>${escHtml(e.label || e.kind)}</em> → <strong>${escHtml(b.title)}</strong>
        ${qQuote ? `<div class="quote-block" style="margin:4px 0;font-size:12px">${escHtml(qQuote.slice(0, 190))}${qQuote.length > 190 ? "…" : ""}</div>` : ""}
      </li>`;
    }
    dHtml += `</ol></div>`;
    detailBox.innerHTML = dHtml;
  }

  function updateAblationLabCard() {
    const box = document.getElementById("ablation-report-box");
    if (!box || !STATE.data) return;
    const cuts = Array.from(STATE.liveMetrics.liveCutpoints)
      .map((id) => STATE.nodeMap.get(id))
      .filter(Boolean)
      .sort((a, b) => (b.betweenness || 0) - (a.betweenness || 0));

    let html = `<div><strong>Network Stability:</strong> ${STATE.liveMetrics.compCount} connected component(s); Giant Component holds <strong>${STATE.liveMetrics.giantPct}%</strong> of active vertices.</div>`;
    if (STATE.ablated.size) {
      html += `<div style="margin-top:6px;color:var(--warn)"><strong>Ablated (${STATE.ablated.size}):</strong> `;
      html += Array.from(STATE.ablated)
        .map((id) => escHtml((STATE.nodeMap.get(id) || {}).title || id))
        .join(", ");
      html += `</div>`;
    }
    html += `<div style="margin-top:7px"><span class="mono" style="font-size:10.5px;color:var(--fg-subtle)">Active 2-Core Horatios (${cuts.length}):</span><div style="margin-top:4px">`;
    html += cuts.length
      ? cuts.slice(0, 8).map((c) => renderNodeChip(c.id)).join("")
      : `<em>None in current filter</em>`;
    html += `</div></div>`;
    box.innerHTML = html;
  }

  function renderExternalConnectorsCard() {
    const box = document.getElementById("external-connectors-box");
    const m = STATE.data.meta;
    box.innerHTML = `
      <div class="resource-card">
        <h4>1 · Scribrarium Calibre FTS5 (${m.scribrarium_total_volumes.toLocaleString()} vols)</h4>
        <p style="margin:0 0 6px">${m.scribrarium_fts_indexed.toLocaleString()} full-text indexed formats · ${m.scribrarium_annotations_total} viewer highlights · <strong>${m.canetti_mentions_total}</strong> books mention Elias Canetti.</p>
        <div>${STATE.data.nodes.filter((n) => n.type === "ghost_book").map((g) => renderNodeChip(g.id)).join("")}</div>
      </div>
      <div class="resource-card">
        <h4>2 · Grist Multi-Table Live Schema</h4>
        <p style="margin:0">Connected tables: <code>Sets</code> (${m.total_sets}), <code>Books</code> (${m.book_count}), <code>Themes</code> (${m.canonical_theme_count}), <code>Methods</code> (6), <code>Author_Weeks</code>, <code>Shadow_Edges</code>, <code>Mentions</code>, plus <em>Lexicon</em> (<code>jZT77mQfrBpwsy5Fkcn5AS</code>) &amp; <em>Parking Lot</em> (<code>fjWnpuU31w9r2YnnGVMygW</code>).</p>
      </div>
      <div class="resource-card">
        <h4>3 · WordNet OEWN Semantic Lineage</h4>
        <p style="margin:0">${m.wordnet_kinship_count} Lowest-Common-Ancestor synset bridges linking oblique theme pairs across readings.</p>
      </div>
    `;
  }

  // =========================================================================
  // 8. MODE 5: HAGE & HARARY STRUCTURAL METRICS LEDGER
  // =========================================================================
  function renderLedgerTable() {
    const tbody = document.getElementById("ledger-tbody");
    const { col, dir } = STATE.ledgerSort;
    const rows = STATE.visNodes.slice().sort((a, b) => {
      if (col === "title") return dir * a.title.localeCompare(b.title);
      if (col === "type") return dir * a.type.localeCompare(b.type);
      if (col === "sets") return dir * ((a.cross_set_count || (a.sets || []).length || 1) - (b.cross_set_count || (b.sets || []).length || 1));
      if (col === "degree") return dir * ((STATE.liveMetrics.liveDeg.get(a.id) || 0) - (STATE.liveMetrics.liveDeg.get(b.id) || 0));
      if (col === "clustering") return dir * ((a.clustering || 0) - (b.clustering || 0));
      if (col === "horatio") return dir * ((STATE.liveMetrics.liveCutpoints.has(a.id) ? 1 : 0) - (STATE.liveMetrics.liveCutpoints.has(b.id) ? 1 : 0));
      return dir * ((a.betweenness || 0) - (b.betweenness || 0));
    });

    tbody.innerHTML = rows
      .slice(0, 260)
      .map((n) => {
        const deg = STATE.liveMetrics.liveDeg.get(n.id) || 0;
        const isH = STATE.liveMetrics.liveCutpoints.has(n.id) || n.is_macro_cutpoint;
        const rCount = n.cross_set_count || (n.sets ? n.sets.length : 1);
        const roleSub = n.roles ? ` (${n.roles.join(", ")})` : n.subtype ? ` · ${n.subtype}` : "";
        return `<tr data-focus-node="${escHtml(n.id)}">
          <td><strong>${escHtml(n.title)}</strong>${n.reader_highlights ? ` <span class="tag-pill" style="color:var(--gold)">◎ ${n.reader_highlights}</span>` : ""}</td>
          <td class="mono" style="font-size:11px">${escHtml(n.type + roleSub)}</td>
          <td class="mono">${rCount}</td>
          <td class="mono">${deg}</td>
          <td class="mono"><strong>${(n.betweenness || 0).toFixed(4)}</strong></td>
          <td class="mono">${(n.clustering || 0).toFixed(3)}</td>
          <td>${isH ? `<span class="tag-pill horatio">◎ Horatio</span>` : "—"}</td>
          <td><button class="btn btn-warn" style="padding:2px 6px;font-size:10px" data-ablate-one="${escHtml(n.id)}">✂ Ablate</button></td>
        </tr>`;
      })
      .join("");
  }

  // =========================================================================
  // 9. THE TRIOMNIBUS ORACLE — SMART CORPUS Q&A ENGINE (+ OPTIONAL GEMINI)
  // =========================================================================
  async function answerOracleQuestion(rawQuery) {
    const q = (rawQuery || "").trim();
    if (!q) return;
    const ql = q.toLowerCase();
    const outBox = document.getElementById("oracle-output");
    const nodes = STATE.data.nodes;
    const edges = STATE.data.edges;

    let title = "Corpus Structural Synthesis";
    let bodyHtml = "";
    const citedIds = new Set();

    // Intent 1: Cutpoints / Horatios / Betweenness Bridges
    if (/(horatio|cutpoint|articulation|betweenness|central|hub|bridge.*who|stability)/.test(ql) && !/translat/.test(ql) && !/scribrarium|ghost|unresurfaced/.test(ql)) {
      title = "The Horatios of the Triomnibus (Cutpoints & Top Betweenness Bridges)";
      const horatios = nodes
        .filter((n) => n.is_macro_cutpoint)
        .sort((a, b) => (b.betweenness || 0) - (a.betweenness || 0));
      const topPeople = nodes
        .filter((n) => n.type === "person")
        .sort((a, b) => (b.betweenness || 0) - (a.betweenness || 0))
        .slice(0, 5);
      const topThemes = nodes
        .filter((n) => n.type === "theme")
        .sort((a, b) => (b.betweenness || 0) - (a.betweenness || 0))
        .slice(0, 5);

      horatios.forEach((n) => citedIds.add(n.id));
      topPeople.forEach((n) => citedIds.add(n.id));

      bodyHtml += `<p>Applying Franco Moretti’s vertex-removal criterion and Hage &amp; Harary’s articulation-point theorem (from ${renderNodeChip("set:32")}) to the 2-core structural backbone reveals <strong>${horatios.length} true macro cutpoints (“Horatios”)</strong> whose removal fractures multi-reading clusters:</p><ul>`;
      horatios.forEach((h) => {
        bodyHtml += `<li>${renderNodeChip(h.id)} — <span class="mono">C_B = ${h.betweenness.toFixed(4)}, k = ${h.degree}</span></li>`;
      });
      bodyHtml += `</ul><p><strong>Highest-Betweenness Human Bridges:</strong> Notice that #1 overall is <em>not</em> a primary author, but translator ${renderNodeChip("person:david-bellos")} (<span class="mono">C_B = 0.0602</span>), followed by:</p><div>`;
      topPeople.forEach((p) => {
        bodyHtml += renderNodeChip(p.id);
      });
      bodyHtml += `</div><p style="margin-top:8px"><strong>Highest-Betweenness Conceptual Hubs:</strong></p><div>`;
      topThemes.forEach((t) => {
        citedIds.add(t.id);
        bodyHtml += renderNodeChip(t.id);
      });
      bodyHtml += `</div>`;
      STATE.horatioMode = true;
      document.getElementById("btn-horatio-mode").classList.add("active");
    }
    // Intent 2: Translators & Editors as Structural Bridges
    else if (/translat|editor|bellos|credit/.test(ql)) {
      title = "Translators & Editors as Hidden Structural Bridges";
      const trans = nodes
        .filter((n) => n.type === "person" && (n.roles || []).some((r) => r === "translator" || r === "editor"))
        .sort((a, b) => (b.cross_set_count || 0) - (a.cross_set_count || 0) || (b.betweenness || 0) - (a.betweenness || 0));
      const multi = trans.filter((t) => (t.cross_set_count || 0) >= 2);
      trans.slice(0, 8).forEach((t) => citedIds.add(t.id));

      bodyHtml += `<p>Because <code>credits.py</code> parses translators, editors, and contributors into typed edges rather than flattening credit strings, translators emerge as critical cross-reading bridges:</p><ul>`;
      multi.forEach((t) => {
        const books = (t.book_cids || [])
          .map((cid) => STATE.nodeMap.get(`book:${cid}`))
          .filter(Boolean);
        books.forEach((b) => citedIds.add(b.id));
        bodyHtml += `<li>${renderNodeChip(t.id)} (<span class="mono">${t.roles.join(", ")} · C_B = ${t.betweenness.toFixed(4)}</span>) bridges <strong>${t.cross_set_count} readings</strong> via ${books.map((b) => renderNodeChip(b.id)).join(" ")}.</li>`;
      });
      bodyHtml += `</ul><p>Notably, ${renderNodeChip("person:david-bellos")} silently stitches Georges Perec (${renderNodeChip("set:3")} &amp; ${renderNodeChip("set:32")}) to Ismail Kadare &amp; Giorgio Agamben (${renderNodeChip("set:31")}). Notable single-set translators/contributors include ${renderNodeChip("person:samuel-beckett")} (on Geulincx), ${renderNodeChip("person:hannah-arendt")} (on Kafka), and ${renderNodeChip("person:carol-stewart")} (on Canetti’s <em>Crowds and Power</em>).</p>`;
    }
    // Intent 3: Unresurfaced Scribrarium Ghost-Shelf Bridges
    else if (/scribrarium|ghost|unresurfaced|13,2|calasso|sebald|fts/.test(ql)) {
      title = "Unresurfaced Scribrarium Ghost-Shelf Bridges (Live FTS5)";
      const ghosts = nodes.filter((n) => n.type === "ghost_book");
      ghosts.forEach((g) => citedIds.add(g.id));
      bodyHtml += `<p>Querying your 13,268-volume <em>Scribrarium</em> full-text index uncovered <strong>234 volumes</strong> naming Elias Canetti. Six unresurfaced volumes act as immediate high-value bridges between Volume 2 (Canetti) and Volume 1 thinkers:</p><ul>`;
      ghosts.forEach((g) => {
        (g.bridges_people || []).forEach((p) => {
          const pid = "person:" + p.toLowerCase().replace(/[^a-z0-9]+/g, "-");
          if (STATE.nodeMap.has(pid)) citedIds.add(pid);
        });
        bodyHtml += `<li>${renderNodeChip(g.id)} — <em>${escHtml(g.author_credit)}</em> (Calibre ID <code>${g.calibre_id}</code>): bridges <strong>${escHtml((g.bridges_people || []).join(" ↔ "))}</strong>.<div class="quote-block" style="font-size:12px;margin:4px 0">${escHtml(g.fts_snippet)}</div></li>`;
      });
      bodyHtml += `</ul>`;
    }
    // Intent 4: Elias Canetti Shadow Week <-> Volume 1 Bridges
    else if (/canetti|shadow|volume 2|crowd/.test(ql)) {
      title = "How Elias Canetti’s Shadow Week Connects to Volume 1 (Triads)";
      ["week:1", "set:33", "set:34", "set:35", "person:elias-canetti", "person:george-steiner", "book:11442", "set:22", "set:25", "theme:crowd", "theme:memory", "theme:silence", "theme:translation"].forEach((id) => {
        if (STATE.nodeMap.has(id)) citedIds.add(id);
      });
      bodyHtml += `<p>Sets #33–#35 (${renderNodeChip("set:33")} ${renderNodeChip("set:34")} ${renderNodeChip("set:35")}) inaugurate <strong>Method II: An Author and Their Shadow</strong> (${renderNodeChip("week:1")}), shifting from 3-book Triads to a directed citation-and-dispute graph across 234 <em>Scribrarium</em> mentions:</p>
      <ul>
        <li><strong>Direct Author Bridge (Vol. 2 ↔ Vol. 1):</strong> On Day 3, ${renderNodeChip("person:george-steiner")} disputes Canetti in ${renderNodeChip("book:11442")} (<em>“A Tale of Three Cities”</em>), directly linking Canetti’s week back to Steiner’s Volume 1 appearances in ${renderNodeChip("set:22")} and ${renderNodeChip("set:25")}.</li>
        <li><strong>Quotation &amp; Dispute Ring:</strong> Day 2 traces quotations in ${renderNodeChip("book:3123")} (Földényi) and ${renderNodeChip("book:926")} (Gass); Day 3 pits Canetti against ${renderNodeChip("book:3632")} (Carey) and ${renderNodeChip("book:9962")} (Malabou on plasticity vs. <em>Verwandlung</em>).</li>
        <li><strong>Conceptual Bridges into Vol. 1:</strong> Joins Volume 1 along ${renderNodeChip("theme:crowd")} (linking back to Triad #11), ${renderNodeChip("theme:memory")}, ${renderNodeChip("theme:silence")}, ${renderNodeChip("theme:commonplace")}, and ${renderNodeChip("theme:translation")}.</li>
      </ul>`;
    }
    // Intent 5: WordNet Semantic Lineage
    else if (/wordnet|synset|semantic|lineage|lca|ancestor/.test(ql)) {
      title = "WordNet OEWN Semantic Lineage Across Oblique Themes";
      const wks = STATE.data.wordnet_kinships || [];
      bodyHtml += `<p>WordNet Open English WordNet (OEWN) taxonomy connects themes that never co-occur on the same page by finding their <strong>Lowest Common Ancestor (LCA)</strong> and Wu-Palmer depth similarity:</p><ul>`;
      wks.forEach((w) => {
        const ta = `theme:${w.theme_a.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
        const tb = `theme:${w.theme_b.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
        if (STATE.nodeMap.has(ta)) citedIds.add(ta);
        if (STATE.nodeMap.has(tb)) citedIds.add(tb);
        bodyHtml += `<li>${STATE.nodeMap.has(ta) ? renderNodeChip(ta) : escHtml(w.theme_a)} ↔ ${STATE.nodeMap.has(tb) ? renderNodeChip(tb) : escHtml(w.theme_b)} — meet at <code>${escHtml(w.meet_at)}</code> (<span class="mono">Wu-Palmer ${w.wu_palmer.toFixed(2)}, ${w.edges} edges</span>)<div class="mono" style="font-size:10.5px;color:var(--fg-subtle)">${escHtml(w.path.join(" → "))}</div></li>`;
      });
      bodyHtml += `</ul>`;
    }
    // Intent 6: Conflict + Shared form or Connection_Kind queries
    else if (/conflict/.test(ql) && /form|method|kind/.test(ql)) {
      title = "Readings Pairing ‘Conflict’ with ‘Shared Form or Method’";
      const matches = nodes.filter(
        (n) =>
          n.type === "set" &&
          (n.connection_kinds || []).includes("Conflict") &&
          (n.connection_kinds || []).some((k) => k.toLowerCase().includes("form"))
      );
      matches.forEach((m) => citedIds.add(m.id));
      bodyHtml += `<p>Across all 35 readings, <strong>${matches.length} sets</strong> simultaneously exhibit <em>Conflict</em> and <em>Shared form or method</em>—books built by the same procedure that reach opposite verdicts on what that procedure does:</p><ul>`;
      matches.forEach((m) => {
        bodyHtml += `<li>${renderNodeChip(m.id)} (<span class="mono">${m.date} · Difficulty ${m.difficulty}</span>): ${escHtml((m.thread || "").slice(0, 210))}…${m.carry_quote ? `<div class="quote-block" style="font-size:12px">${escHtml(m.carry_quote)}</div>` : ""}</li>`;
      });
      bodyHtml += `</ul>`;
    }
    // Intent 7: General Free-Text BM25 / Keyword Retrieval Across All Threads, Books & Verified Quotes
    else {
      const terms = ql
        .replace(/[^a-z0-9à-ÿ\s]/g, " ")
        .split(/\s+/)
        .filter((w) => w.length >= 3 && !["what", "where", "which", "how", "does", "pass", "passes", "between", "across", "the", "and", "with", "from", "about", "that", "books", "triads"].includes(w));

      const scored = [];
      for (const n of nodes) {
        const text = [
          n.title,
          n.title, // double weight on title
          n.author_credit,
          n.thread,
          n.carry_quote,
          n.role_in_set,
          n.selection_logic,
          (n.quotes || []).join(" "),
          n.fts_snippet,
          n.why_bridge,
        ]
          .join(" ")
          .toLowerCase();

        let score = 0;
        for (const tm of terms) {
          const stem = tm.replace(/(ing|ies|es|s)$/, "");
          if (stem.length >= 3 && text.includes(stem)) {
            score += n.title.toLowerCase().includes(stem) ? 5 : 2;
          }
        }
        if (score > 0) scored.push({ node: n, score: score + (n.betweenness || 0) * 6 });
      }
      scored.sort((a, b) => b.score - a.score);
      const top = scored.slice(0, 7);
      top.forEach(({ node }) => citedIds.add(node.id));

      title = `Corpus Retrieval: “${escHtml(q)}”`;
      if (!top.length) {
        bodyHtml = `<p>No exact matches found for those terms. Try asking about <em>cutpoints / Horatios</em>, <em>translators</em>, <em>Canetti</em>, <em>gesture</em>, <em>forgery</em>, <em>oath</em>, <em>clocks</em>, or <em>Scribrarium Ghost Shelf</em>.</p>`;
      } else {
        bodyHtml = `<p>Found <strong>${scored.length} matching vertices</strong> across threads, roles, and verified quotations. Top structural anchors:</p><ul>`;
        top.forEach(({ node }) => {
          const snippet =
            node.carry_quote ||
            (node.quotes && node.quotes[0]) ||
            node.thread ||
            node.role_in_set ||
            node.why_bridge ||
            "";
          bodyHtml += `<li>${renderNodeChip(node.id)} <span class="mono" style="font-size:10.5px;color:var(--fg-subtle)">(${escHtml(node.type)}${node.date ? " · " + node.date : ""})</span>
            ${snippet ? `<div class="quote-block" style="font-size:12px;margin:4px 0">${escHtml(snippet.slice(0, 230))}${snippet.length > 230 ? "…" : ""}</div>` : ""}
          </li>`;
        });
        bodyHtml += `</ul>`;
      }
    }

    // Highlight cited subgraph on the canvas!
    if (citedIds.size > 0) {
      STATE.spotlightIds = citedIds;
    }

    outBox.innerHTML = `
      <div class="oracle-answer">
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <h4>${title}</h4>
          ${citedIds.size ? `<button class="btn" id="btn-clear-spotlight" style="padding:2px 6px;font-size:10px">Clear Spotlight (${citedIds.size})</button>` : ""}
        </div>
        ${bodyHtml}
        <div id="gemini-synth-slot"></div>
      </div>
    `;

    const clearBtn = document.getElementById("btn-clear-spotlight");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        STATE.spotlightIds = null;
        clearBtn.remove();
      });
    }

    // Optional BYOK Gemini API synthesis over retrieved subgraph
    const apiKey = (document.getElementById("gemini-api-key").value || "").trim();
    if (apiKey && citedIds.size > 0) {
      const slot = document.getElementById("gemini-synth-slot");
      slot.innerHTML = `<p class="mono" style="font-size:11px;color:var(--gold)">Synthesizing with Gemini over ${citedIds.size} retrieved corpus nodes…</p>`;
      try {
        const ctxLines = Array.from(citedIds)
          .slice(0, 12)
          .map((id) => {
            const nd = STATE.nodeMap.get(id);
            return `- [${nd.id}] ${nd.title} (${nd.type}): ${nd.thread || nd.role_in_set || ""} | Quote: ${nd.carry_quote || (nd.quotes && nd.quotes[0]) || nd.fts_snippet || ""}`;
          })
          .join("\n");
        const resp = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${encodeURIComponent(apiKey)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              contents: [
                {
                  parts: [
                    {
                      text: `You are the scholarly oracle of Johnny's Hypertext Triomnibus. Answer concisely in direct, unornate literary-philosophical prose grounded strictly in the retrieved corpus nodes below, citing exact titles and verbatim quotes:\n\nQuestion: ${q}\n\nRetrieved Subgraph:\n${ctxLines}`,
                    },
                  ],
                },
              ],
            }),
          }
        );
        const json = await resp.json();
        const txt =
          json?.candidates?.[0]?.content?.parts?.[0]?.text ||
          json?.error?.message ||
          "No response returned.";
        slot.innerHTML = `<div style="margin-top:10px;padding-top:8px;border-top:1px dashed var(--border)"><div class="sec-title" style="color:var(--gold);margin-bottom:4px">✦ Gemini Grounded Synthesis</div><p style="white-space:pre-wrap">${escHtml(txt)}</p></div>`;
      } catch (err) {
        slot.innerHTML = `<p class="mono" style="font-size:10.5px;color:var(--warn)">Gemini synthesis unavailable (${escHtml(err.message)}).</p>`;
      }
    }
  }

  // =========================================================================
  // 10. EXPORTERS (Vector SVG & Markdown Chavruta Reading Bench)
  // =========================================================================
  function downloadBlob(filename, mime, content) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportCurrentSVG() {
    if (!STATE.visNodes.length) return;
    let minX = Infinity,
      maxX = -Infinity,
      minY = Infinity,
      maxY = -Infinity;
    for (const n of STATE.visNodes) {
      minX = Math.min(minX, n.x - 40);
      maxX = Math.max(maxX, n.x + 180);
      minY = Math.min(minY, n.y - 40);
      maxY = Math.max(maxY, n.y + 40);
    }
    const w = Math.round(maxX - minX);
    const h = Math.round(maxY - minY);
    const bg = cssVar("--bg");
    const fg = cssVar("--fg");

    const lines = [
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${Math.round(minX)} ${Math.round(minY)} ${w} ${h}" width="${w}" height="${h}">`,
      `<rect x="${Math.round(minX)}" y="${Math.round(minY)}" width="${w}" height="${h}" fill="${bg}"/>`,
    ];
    for (const e of STATE.visEdges) {
      const s = STATE.nodeMap.get(e.source);
      const t = STATE.nodeMap.get(e.target);
      if (!s || !t) continue;
      const st = edgeStyle(e, false);
      lines.push(
        `<line x1="${s.x.toFixed(1)}" y1="${s.y.toFixed(1)}" x2="${t.x.toFixed(1)}" y2="${t.y.toFixed(1)}" stroke="${st.stroke}" stroke-width="${st.width}" />`
      );
    }
    for (const n of STATE.visNodes) {
      const r = nodeRadius(n);
      const c = nodeColor(n);
      lines.push(
        `<circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${r.toFixed(1)}" fill="${c}" stroke="#fff" stroke-width="1"/>`
      );
      if ((n.betweenness || 0) >= 0.02 || n.type === "set" || n.type === "author_week" || n.type === "ghost_book") {
        lines.push(
          `<text x="${(n.x + r + 5).toFixed(1)}" y="${(n.y + 3.5).toFixed(1)}" fill="${fg}" font-family="Georgia, serif" font-size="11">${escHtml(n.title)}</text>`
        );
      }
    }
    lines.push(`</svg>`);
    downloadBlob(`triomnibus-${STATE.layout}-day${STATE.maxDay}.svg`, "image/svg+xml", lines.join("\n"));
  }

  function exportReadingBenchMarkdown() {
    const focusIds =
      STATE.pathIds.length
        ? STATE.pathIds
        : STATE.spotlightIds && STATE.spotlightIds.size
        ? Array.from(STATE.spotlightIds)
        : [STATE.selectedId].filter(Boolean);

    const lines = [
      `# Triomnibus Reading Bench (${new Date().toISOString().slice(0, 10)})`,
      ``,
      `- **Projection:** ${STATE.layout} · **Accretion Horizon:** Day ${STATE.maxDay}`,
      `- **Active Topology:** ${STATE.visNodes.length} vertices, ${STATE.visEdges.length} edges, ${STATE.liveMetrics.compCount} components (${STATE.liveMetrics.giantPct}% giant component)`,
      ``,
      `## Selected Constellation / Dialectical Chain`,
      ``,
    ];

    focusIds.forEach((id) => {
      const n = STATE.nodeMap.get(id);
      if (!n) return;
      lines.push(`### ${n.title} (\`${n.type}\`${n.calibre_id ? ` · Calibre ID ${n.calibre_id}` : ""})`);
      if (n.thread) lines.push(n.thread);
      if (n.role_in_set) lines.push(`- **Role:** ${n.role_in_set}`);
      if (n.carry_quote) lines.push(`> ${n.carry_quote}`);
      if (n.quotes && n.quotes.length) {
        n.quotes.slice(0, 3).forEach((q) => lines.push(`> ${q}`));
      }
      if (n.fts_snippet) lines.push(`> *(Scribrarium FTS)* ${n.fts_snippet}`);
      lines.push(``);
    });

    downloadBlob("triomnibus-reading-bench.md", "text/markdown", lines.join("\n"));
  }

  // =========================================================================
  // 11. INTERACTION WIRING (Pointer, Wheel, Tabs, Presets, Parking Lot Hook)
  // =========================================================================
  function hitTestNode(sx, sy) {
    const pt = screenToWorld(sx, sy);
    let best = null;
    let bestD = Infinity;
    for (let i = 0; i < STATE.visNodes.length; i++) {
      const n = STATE.visNodes[i];
      const r = nodeRadius(n) + 5 / STATE.cam.k;
      const dx = pt.x - n.x;
      const dy = pt.y - n.y;
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d <= r && d < bestD) {
        best = n;
        bestD = d;
      }
    }
    return best;
  }

  function switchRightTab(tabName) {
    document.querySelectorAll(".rtab-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.rtab === tabName);
    });
    document.querySelectorAll(".rtab-pane").forEach((p) => {
      p.classList.toggle("active", p.id === "pane-" + tabName);
    });
    document.getElementById("obs-workspace").classList.toggle("oracle-open", tabName === "oracle");
    resizeCanvas();
  }

  async function parkNodeToPLR(nodeId) {
    const n = STATE.nodeMap.get(nodeId);
    if (!n) return;
    const toast = document.getElementById("park-toast");
    const itemTitle = `Follow up Triomnibus ${n.type}: ${n.title}${n.calibre_id ? ` (Calibre #${n.calibre_id})` : ""}`;
    if (STATE.localBridgeAlive) {
      try {
        const res = await fetch("http://127.0.0.1:18770/api/parking-lot", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            item: itemTitle,
            notes: n.thread || n.role_in_set || n.why_bridge || "",
            links: resolveSiteHref(n.slug) || "",
          }),
        });
        const j = await res.json();
        toast.innerHTML = `<div class="resource-card" style="border-color:var(--tertiary);margin-top:8px">✓ Parked to Grist <code>Parking_Lot</code> for session <strong>${escHtml(j.target_session || "Nov_PLR1_26")}</strong>!</div>`;
        return;
      } catch (_) {
        // fall through to CLI command snippet
      }
    }
    const cmd = `python3 ~/.gemini/antigravity/scratch/parking-lot-review/plr_grist.py add --item "${itemTitle.replace(/"/g, '\\"')}" --session Nov_PLR1_26`;
    toast.innerHTML = `<div class="resource-card" style="border-color:var(--gold);margin-top:8px">
      <div class="mono" style="font-size:10.5px;color:var(--gold);margin-bottom:4px">📌 Ready for Parking Lot (Nov_PLR1_26)</div>
      <div style="font-size:11.5px;margin-bottom:4px">Start <code>local_corpus_api.py</code> for 1-click live Grist write, or run:</div>
      <code style="display:block;font-size:10px;word-break:break-all;padding:5px;background:var(--bg-elev);border-radius:3px">${escHtml(cmd)}</code>
    </div>`;
  }

  function wireEvents() {
    window.addEventListener("resize", resizeCanvas);

    // Canvas Mouse / Wheel
    let downPos = { x: 0, y: 0 };
    canvas.addEventListener("mousedown", (ev) => {
      const rect = canvas.getBoundingClientRect();
      const sx = ev.clientX - rect.left;
      const sy = ev.clientY - rect.top;
      downPos = { x: sx, y: sy };
      const hit = hitTestNode(sx, sy);
      if (hit) {
        STATE.draggingId = hit.id;
        hit.pinned = true;
      } else {
        STATE.isPanning = true;
        STATE.panStart = { x: ev.clientX, y: ev.clientY, camX: STATE.cam.x, camY: STATE.cam.y };
      }
    });

    window.addEventListener("mousemove", (ev) => {
      const rect = canvas.getBoundingClientRect();
      const sx = ev.clientX - rect.left;
      const sy = ev.clientY - rect.top;
      if (STATE.draggingId) {
        const nd = STATE.nodeMap.get(STATE.draggingId);
        if (nd) {
          const pt = screenToWorld(sx, sy);
          nd.x = pt.x;
          nd.y = pt.y;
          if (STATE.layout === "force") STATE.simAlpha = Math.max(STATE.simAlpha, 0.25);
        }
        return;
      }
      if (STATE.isPanning) {
        STATE.cam.x = STATE.panStart.camX + (ev.clientX - STATE.panStart.x);
        STATE.cam.y = STATE.panStart.camY + (ev.clientY - STATE.panStart.y);
        return;
      }
      if (sx >= 0 && sy >= 0 && sx <= rect.width && sy <= rect.height) {
        const hit = hitTestNode(sx, sy);
        STATE.hoveredId = hit ? hit.id : null;
        canvas.style.cursor = hit ? "pointer" : "grab";
      }
    });

    window.addEventListener("mouseup", (ev) => {
      const rect = canvas.getBoundingClientRect();
      const sx = ev.clientX - rect.left;
      const sy = ev.clientY - rect.top;
      const moved = Math.hypot(sx - downPos.x, sy - downPos.y);
      if (STATE.draggingId) {
        const clickedId = STATE.draggingId;
        const nd = STATE.nodeMap.get(clickedId);
        if (moved < 5) {
          if (nd) nd.pinned = false;
          selectNode(clickedId, false);
          switchRightTab("inspector");
        }
        STATE.draggingId = null;
      }
      STATE.isPanning = false;
    });

    canvas.addEventListener(
      "wheel",
      (ev) => {
        ev.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const sx = ev.clientX - rect.left - rect.width / 2;
        const sy = ev.clientY - rect.top - rect.height / 2;
        const factor = ev.deltaY < 0 ? 1.12 : 0.89;
        const nextK = Math.max(0.18, Math.min(4.5, STATE.cam.k * factor));
        const ratio = nextK / STATE.cam.k;
        STATE.cam.x = sx - (sx - STATE.cam.x) * ratio;
        STATE.cam.y = sy - (sy - STATE.cam.y) * ratio;
        STATE.cam.k = nextK;
      },
      { passive: false }
    );

    // Projection Mode Buttons
    document.querySelectorAll(".mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        STATE.layout = btn.dataset.layout;
        const isLedger = STATE.layout === "ledger";
        document.getElementById("ledger-view").classList.toggle("visible", isLedger);
        canvas.style.display = isLedger ? "none" : "block";
        if (isLedger) renderLedgerTable();
        else applyProjectionTargets(true);
      });
    });

    // Right Tabs
    document.querySelectorAll(".rtab-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchRightTab(btn.dataset.rtab));
    });

    // Top Actions
    document.getElementById("btn-horatio-mode").addEventListener("click", (ev) => {
      STATE.horatioMode = !STATE.horatioMode;
      ev.currentTarget.classList.toggle("active", STATE.horatioMode);
    });

    document.getElementById("btn-open-oracle").addEventListener("click", () => {
      switchRightTab("oracle");
      document.getElementById("oracle-query").focus();
    });

    document.getElementById("btn-theme-toggle").addEventListener("click", () => {
      STATE.theme = STATE.theme === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", STATE.theme);
      try {
        localStorage.setItem("triomnibus-obs-theme", STATE.theme);
      } catch (_) {}
    });

    document.getElementById("btn-export-svg").addEventListener("click", exportCurrentSVG);
    document.getElementById("btn-export-md").addEventListener("click", exportReadingBenchMarkdown);

    // Zoom Controls
    document.getElementById("btn-zoom-in").addEventListener("click", () => {
      STATE.cam.k = Math.min(4.5, STATE.cam.k * 1.22);
    });
    document.getElementById("btn-zoom-out").addEventListener("click", () => {
      STATE.cam.k = Math.max(0.2, STATE.cam.k / 1.22);
    });
    document.getElementById("btn-zoom-fit").addEventListener("click", () => fitViewport(true));
    document.getElementById("btn-reheat").addEventListener("click", () => {
      STATE.visNodes.forEach((n) => (n.pinned = false));
      STATE.simAlpha = 1.0;
    });

    // Search & Filters
    document.getElementById("search-box").addEventListener("input", (ev) => {
      STATE.search = ev.target.value;
      rebuildSubgraph(false);
    });

    document.querySelectorAll("[data-layer]").forEach((cb) => {
      cb.addEventListener("change", () => {
        STATE.layers[cb.dataset.layer] = cb.checked;
        rebuildSubgraph(true);
      });
    });

    document.querySelectorAll("[data-edge]").forEach((cb) => {
      cb.addEventListener("change", () => {
        STATE.edgeRules[cb.dataset.edge] = cb.checked;
        rebuildSubgraph(true);
      });
    });

    document.getElementById("min-theme-sets").addEventListener("input", (ev) => {
      const v = parseInt(ev.target.value, 10);
      STATE.minThemeSets = v;
      document.getElementById("min-theme-val").textContent =
        v === 1 ? "≥ 1 reading (all)" : `≥ ${v} readings`;
      document.getElementById("theme-filter-note").textContent =
        v === 1
          ? "Showing all 1,098 canonical themes (including single-reading leaves)."
          : `Filtering to cross-reading bridge themes (≥ ${v} sets).`;
      rebuildSubgraph(true);
    });

    document.getElementById("sel-color-by").addEventListener("change", (ev) => {
      STATE.colorBy = ev.target.value;
    });
    document.getElementById("sel-size-by").addEventListener("change", (ev) => {
      STATE.sizeBy = ev.target.value;
    });
    document.getElementById("label-density").addEventListener("input", (ev) => {
      const v = parseInt(ev.target.value, 10);
      STATE.labelDensity = v;
      const names = { 1: "Minimal", 2: "Smart Hubs", 3: "Dense", 4: "All Labels" };
      document.getElementById("label-density-val").textContent = names[v] || "Smart Hubs";
    });
    document.getElementById("repulsion-slider").addEventListener("input", (ev) => {
      STATE.repulsion = parseInt(ev.target.value, 10) / 100;
      document.getElementById("repulsion-val").textContent = STATE.repulsion.toFixed(1) + "×";
      if (STATE.layout === "force") STATE.simAlpha = Math.max(STATE.simAlpha, 0.55);
    });

    // Temporal Day Scrubber & Play/Pause
    const scrubber = document.getElementById("day-scrubber");
    const updateDayLabel = () => {
      const setForDay = STATE.data.nodes.find((n) => n.type === "set" && n.day_index === STATE.maxDay);
      const dt = setForDay ? setForDay.date.slice(5) : "";
      document.getElementById("day-scrubber-label").textContent =
        `Day ${STATE.maxDay} / ${STATE.data.meta.total_sets}${dt ? " (" + dt + ")" : ""}`;
    };
    scrubber.addEventListener("input", () => {
      STATE.maxDay = parseInt(scrubber.value, 10);
      updateDayLabel();
      rebuildSubgraph(false);
    });

    document.getElementById("btn-play-days").addEventListener("click", (ev) => {
      if (STATE.playTimer) {
        clearInterval(STATE.playTimer);
        STATE.playTimer = null;
        ev.currentTarget.textContent = "▶";
        return;
      }
      if (STATE.maxDay >= STATE.data.meta.total_sets) STATE.maxDay = 1;
      ev.currentTarget.textContent = "⏸";
      STATE.playTimer = setInterval(() => {
        if (STATE.maxDay >= STATE.data.meta.total_sets) {
          clearInterval(STATE.playTimer);
          STATE.playTimer = null;
          document.getElementById("btn-play-days").textContent = "▶";
          return;
        }
        STATE.maxDay++;
        scrubber.value = String(STATE.maxDay);
        updateDayLabel();
        rebuildSubgraph(false);
      }, 360);
    });

    // Reset All & Restore Ablation
    document.getElementById("btn-restore-ablation").addEventListener("click", () => {
      STATE.ablated.clear();
      rebuildSubgraph(true);
      renderInspector();
    });
    document.getElementById("btn-clear-path").addEventListener("click", () => {
      STATE.pathIds = [];
      STATE.pathEdges = [];
      document.getElementById("path-banner").classList.remove("visible");
    });
    document.getElementById("btn-reset-all").addEventListener("click", () => {
      STATE.ablated.clear();
      STATE.pathIds = [];
      STATE.pathEdges = [];
      STATE.spotlightIds = null;
      STATE.search = "";
      STATE.maxDay = STATE.data.meta.total_sets;
      STATE.minThemeSets = 2;
      document.getElementById("search-box").value = "";
      document.getElementById("min-theme-sets").value = "2";
      document.getElementById("min-theme-val").textContent = "≥ 2 readings";
      scrubber.value = String(STATE.maxDay);
      updateDayLabel();
      document.getElementById("path-banner").classList.remove("visible");
      rebuildSubgraph(true);
      fitViewport(true);
    });

    // Pathfinder & Ablation Presets
    document.getElementById("btn-run-path").addEventListener("click", () => {
      runShortestPath(
        document.getElementById("path-src").value,
        document.getElementById("path-dst").value
      );
    });

    document.querySelectorAll("[data-path-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const [a, b] = btn.dataset.pathPreset.split("|");
        document.getElementById("path-src").value = a;
        document.getElementById("path-dst").value = b;
        runShortestPath(a, b);
      });
    });

    document.querySelectorAll("[data-ablate-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        btn.dataset.ablatePreset.split(",").forEach((id) => STATE.ablated.add(id.trim()));
        rebuildSubgraph(true);
      });
    });

    // Oracle Q&A Controls
    const oracleInput = document.getElementById("oracle-query");
    document.getElementById("btn-ask-oracle").addEventListener("click", () => {
      answerOracleQuestion(oracleInput.value);
    });
    oracleInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") answerOracleQuestion(oracleInput.value);
    });
    document.querySelectorAll("#oracle-presets [data-q]").forEach((btn) => {
      btn.addEventListener("click", () => {
        oracleInput.value = btn.dataset.q;
        answerOracleQuestion(btn.dataset.q);
      });
    });

    // Ledger Sorting
    document.querySelectorAll("#ledger-table th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const col = th.dataset.sort;
        if (STATE.ledgerSort.col === col) STATE.ledgerSort.dir *= -1;
        else STATE.ledgerSort = { col, dir: col === "title" || col === "type" ? 1 : -1 };
        renderLedgerTable();
      });
    });

    // Delegated clicks for Node Chips, Ego Focus, Single-Vertex Ablation & Park to PLR
    document.body.addEventListener("click", (ev) => {
      const chip = ev.target.closest("[data-focus-node]");
      const abBtn = ev.target.closest("[data-ablate-one]");
      const egoBtn = ev.target.closest("[data-ego-focus]");
      const parkBtn = ev.target.closest("[data-park-node]");

      if (abBtn) {
        ev.stopPropagation();
        const id = abBtn.dataset.ablateOne;
        STATE.ablated.add(id);
        rebuildSubgraph(true);
        if (STATE.selectedId === id) {
          STATE.selectedId = STATE.visNodes[0] ? STATE.visNodes[0].id : null;
        }
        renderInspector();
        return;
      }
      if (egoBtn) {
        const id = egoBtn.dataset.egoFocus;
        STATE.selectedId = id;
        document.querySelectorAll(".mode-btn").forEach((b) => {
          b.classList.toggle("active", b.dataset.layout === "ego");
        });
        STATE.layout = "ego";
        document.getElementById("ledger-view").classList.remove("visible");
        canvas.style.display = "block";
        applyProjectionTargets(false);
        renderInspector();
        return;
      }
      if (parkBtn) {
        parkNodeToPLR(parkBtn.dataset.parkNode);
        return;
      }
      if (chip) {
        const id = chip.dataset.focusNode;
        selectNode(id, true);
      }
    });

    // Persist optional Gemini API key in localStorage
    const gemInput = document.getElementById("gemini-api-key");
    try {
      const savedKey = localStorage.getItem("triomnibus-gemini-key");
      if (savedKey) gemInput.value = savedKey;
      const savedTheme = localStorage.getItem("triomnibus-obs-theme");
      if (savedTheme === "dark" || savedTheme === "light") {
        STATE.theme = savedTheme;
        document.documentElement.setAttribute("data-theme", savedTheme);
      }
    } catch (_) {}
    gemInput.addEventListener("change", () => {
      try {
        localStorage.setItem("triomnibus-gemini-key", gemInput.value.trim());
      } catch (_) {}
    });
  }

  async function probeLocalBridge() {
    const el = document.getElementById("local-bridge-status");
    try {
      const ctrl = new AbortController();
      setTimeout(() => ctrl.abort(), 900);
      const res = await fetch("http://127.0.0.1:18770/api/health", { signal: ctrl.signal });
      if (res.ok) {
        STATE.localBridgeAlive = true;
        el.textContent = "Local Scribrarium Bridge (127.0.0.1:18770): ● CONNECTED (live Calibre + Grist)";
        el.style.color = "var(--tertiary)";
        return;
      }
    } catch (_) {}
    el.textContent = "Local Scribrarium Bridge (127.0.0.1:18770): offline (using prebuilt static snapshot)";
  }

  // =========================================================================
  // 12. BOOTSTRAP
  // =========================================================================
  async function init() {
    resizeCanvas();
    wireEvents();

    const data = await loadGraphData();
    STATE.data = data;
    for (const n of data.nodes) STATE.nodeMap.set(n.id, n);

    STATE.maxDay = data.meta.total_sets || 35;
    const scrubber = document.getElementById("day-scrubber");
    scrubber.max = String(STATE.maxDay);
    scrubber.value = String(STATE.maxDay);
    const latestSet = data.nodes.find((n) => n.type === "set" && n.day_index === STATE.maxDay);
    const latestDt = latestSet && latestSet.date ? latestSet.date.slice(5) : "";
    document.getElementById("day-scrubber-label").textContent =
      `Day ${STATE.maxDay} / ${STATE.maxDay}${latestDt ? " (" + latestDt + ")" : ""}`;
    document.getElementById("corpus-subtitle").textContent =
      `${data.meta.triad_count} Triads + ${data.meta.shadow_set_count} Canetti Shadow Days · ${data.meta.book_count} Books · ${data.meta.person_count} People · ${data.meta.scribrarium_total_volumes.toLocaleString()}-Vol Scribrarium`;

    // Support URL hash / query deep-linking from any Quartz reading page (?focus=slug)
    const params = new URLSearchParams(window.location.search);
    const focusSlug = params.get("focus");
    if (focusSlug) {
      const match = data.nodes.find((n) => n.slug === focusSlug || n.id === focusSlug);
      if (match) STATE.selectedId = match.id;
    }
    if (!STATE.nodeMap.has(STATE.selectedId)) {
      const activeWeeks = data.nodes.filter((n) => n.type === "author_week");
      STATE.selectedId = activeWeeks.length ? activeWeeks[activeWeeks.length - 1].id : "set:32";
    }

    populatePathDropdowns();
    renderExternalConnectorsCard();
    rebuildSubgraph(true);

    // Pre-settle 160 physics ticks synchronously so initial render is already balanced & legible!
    for (let t = 0; t < 160; t++) stepForceSimulation();
    fitViewport(false);

    renderInspector();
    answerOracleQuestion("Who are the structural cutpoints (Horatios) and top betweenness bridges?");
    // Clear initial spotlight so default canvas isn't dimmed until user clicks an Oracle question
    STATE.spotlightIds = null;

    probeLocalBridge();
    requestAnimationFrame(drawFrame);
  }

  init().catch((err) => {
    console.error("Observatory init error:", err);
    document.getElementById("inspector-content").innerHTML =
      `<div class="resource-card" style="border-color:var(--warn)">Failed to initialize Graph Observatory: ${escHtml(err.message)}</div>`;
  });
})();
