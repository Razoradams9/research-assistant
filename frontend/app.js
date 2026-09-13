/**
 * Frontend controller + anime.js motion-graphics layer.
 *
 * Data flow (unchanged): POST /api/research -> run_id, then EventSource
 * on /api/research/{run_id}/stream yields ProgressEvents.
 *
 * Motion layer (anime.js v3, global `anime`):
 *   - gradient title shimmer + drifting background blobs
 *   - SVG "spine" that fills between agent cards as stages complete,
 *     with a glowing pulse dot that flows to the next agent on handoff
 *   - active agent: glow ring + looping shimmer progress bar + status
 *     text that cycles ("planning…", "searching…", …)
 *   - particle spark burst when a stage completes
 *   - animated count-up in trail messages ("Found 5 sources")
 *   - sub-question chips stagger in; report reveals with a light sweep
 *
 * Everything degrades gracefully if anime.js failed to load.
 */
(function () {
  const hasAnime = typeof window.anime === "function";

  const form = document.getElementById("ask-form");
  const input = document.getElementById("question");
  const askBtn = document.getElementById("ask-btn");
  const trail = document.getElementById("trail");
  const reportEl = document.getElementById("report");
  const reportSweep = document.getElementById("report-sweep");
  const statusBadge = document.getElementById("pipeline-status");
  const citeBadge = document.getElementById("citation-count");
  const sqContainer = document.getElementById("subquestions");
  const spineFill = document.getElementById("spine-fill");
  const spinePulse = document.getElementById("spine-pulse");

  const STAGE_ORDER = ["manager", "researcher", "synthesizer"];
  // x positions along the 1000-wide spine viewBox, one per stage card center.
  const STAGE_X = { manager: 190, researcher: 500, synthesizer: 810 };
  const SPINE_START = 60;

  const agentCards = {};
  document.querySelectorAll(".agent-card").forEach((c) => { agentCards[c.dataset.stage] = c; });

  const statusVerbs = {
    manager: ["planning", "decomposing", "scoping"],
    researcher: ["searching", "reading", "extracting"],
    synthesizer: ["drafting", "citing", "composing"],
  };
  let statusTimer = null;
  let currentSource = null;

  // ── Background / intro motion ───────────────────────────────────────
  function startAmbient() {
    if (!hasAnime) return;
    document.querySelectorAll(".blob").forEach((blob, i) => {
      anime({
        targets: blob,
        translateX: [0, i % 2 === 0 ? 70 : -70],
        translateY: [0, i % 2 === 0 ? -50 : 60],
        scale: [1, 1.2],
        direction: "alternate", loop: true, easing: "easeInOutSine",
        duration: 8000 + i * 1400,
      });
    });
    // Title gradient shimmer.
    anime({
      targets: ".hero-title",
      backgroundPosition: ["0% 50%", "200% 50%"],
      direction: "alternate", loop: true, easing: "easeInOutSine", duration: 6000,
    });
  }

  function introAnimation() {
    if (!hasAnime) return;
    anime({ targets: ".ask", translateY: [16, 0], opacity: [0, 1], easing: "easeOutExpo", duration: 600 });
    anime({
      targets: ".agent-card",
      translateY: [26, 0], opacity: [0, 1], rotateX: [10, 0],
      delay: anime.stagger(130, { start: 150 }), easing: "easeOutExpo", duration: 750,
    });
  }

  // ── Spine control ───────────────────────────────────────────────────
  function spineTo(stage, { pulse = true } = {}) {
    if (!hasAnime || !spineFill) return;
    const targetX = STAGE_X[stage] || SPINE_START;
    anime({ targets: spineFill, x2: targetX, easing: "easeInOutCubic", duration: 700 });
    if (pulse && spinePulse) {
      anime.remove(spinePulse);
      spinePulse.style.opacity = 1;
      anime({
        targets: spinePulse,
        cx: [{ value: (STAGE_X[prevStage(stage)] || SPINE_START), duration: 0 }, { value: targetX, duration: 700 }],
        easing: "easeInOutCubic",
        complete: () => { anime({ targets: spinePulse, opacity: [1, 0], duration: 400, easing: "linear" }); },
      });
    }
  }
  function prevStage(stage) {
    const i = STAGE_ORDER.indexOf(stage);
    return i > 0 ? STAGE_ORDER[i - 1] : null;
  }
  function resetSpine() {
    if (!spineFill) return;
    spineFill.setAttribute("x2", SPINE_START);
    if (spinePulse) { spinePulse.setAttribute("cx", SPINE_START); spinePulse.style.opacity = 0; }
  }

  // ── Form ────────────────────────────────────────────────────────────
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q) startRun(q);
  });

  async function startRun(question) {
    resetUI();
    setBusy(true);
    setStatus("running", "running");
    let runId;
    try {
      const resp = await fetch("/api/research", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (!resp.ok) throw new Error(`server responded ${resp.status}`);
      runId = (await resp.json()).run_id;
    } catch (err) {
      addTrail({ stage: "error", status: "failed", message: `Could not start run: ${err.message}` });
      setStatus("error", "error"); setBusy(false); return;
    }
    openStream(runId);
  }

  function openStream(runId) {
    if (currentSource) currentSource.close();
    const source = new EventSource(`/api/research/${runId}/stream`);
    currentSource = source;

    source.onmessage = (msg) => {
      let ev;
      try { ev = JSON.parse(msg.data); } catch { return; }
      if (ev.status === "closed") { source.close(); setBusy(false); stopStatusCycle(); return; }

      updateStages(ev);
      addTrail(ev);

      if (ev.stage === "manager" && ev.status === "completed" && ev.data && ev.data.sub_questions) {
        renderSubQuestions(ev.data.sub_questions);
      }
      if (ev.stage === "done" && ev.data && ev.data.report) {
        renderReport(ev.data.report);
        setStatus("done", "done");
        spineTo("synthesizer", { pulse: false });
      }
      if (ev.stage === "error") { setStatus("error", "error"); stopStatusCycle(); }
    };
    source.onerror = () => {
      addTrail({ stage: "error", status: "failed", message: "Connection to the pipeline was lost." });
      setStatus("error", "error"); source.close(); setBusy(false); stopStatusCycle();
    };
  }

  // ── Stage cards ─────────────────────────────────────────────────────
  function updateStages(ev) {
    const card = agentCards[ev.stage];
    if (ev.status === "started" || ev.status === "in_progress") {
      let seen = false;
      Object.keys(agentCards).forEach((key) => {
        if (key === ev.stage) seen = true;
        if (!seen) completeCard(agentCards[key], false);
      });
      if (card && !card.classList.contains("active")) {
        activateCard(card, ev.stage);
        spineTo(ev.stage);
      }
    } else if (ev.status === "completed") {
      if (card) completeCard(card, true);
    } else if (ev.status === "failed") {
      if (card) failCard(card);
    }
    if (ev.stage === "done") {
      Object.values(agentCards).forEach((c) => { if (c.classList.contains("active")) completeCard(c, true); });
    }
  }

  function setCardState(card, label) {
    const s = card.querySelector(".agent-state");
    if (s) s.textContent = label;
  }

  function activateCard(card, stage) {
    card.classList.remove("complete", "failed");
    card.classList.add("active");
    startStatusCycle(card, stage);
    if (hasAnime) {
      anime.remove(card);
      anime({ targets: card, translateY: [-2, -6], scale: [1, 1.035], rotateX: [6, 0], easing: "easeOutBack", duration: 520 });
      const bar = card.querySelector(".agent-bar span");
      if (bar) {
        anime.remove(bar);
        anime({ targets: bar, left: ["-40%", "100%"], loop: true, easing: "easeInOutSine", duration: 1100 });
      }
    } else {
      setCardState(card, "working");
    }
  }

  function completeCard(card, burst) {
    if (!card.classList.contains("active") && !card.classList.contains("complete")) return;
    const wasActive = card.classList.contains("active");
    card.classList.remove("active", "failed");
    card.classList.add("complete");
    if (card === activeStatusCard) stopStatusCycle();
    setCardState(card, "done");
    if (hasAnime) {
      anime.remove(card);
      anime({ targets: card, scale: [1.035, 1], translateY: 0, easing: "easeOutQuad", duration: 340 });
      if (burst && wasActive) sparkBurst(card);
    }
  }

  function failCard(card) {
    card.classList.remove("active", "complete");
    card.classList.add("failed");
    if (card === activeStatusCard) stopStatusCycle();
    setCardState(card, "failed");
    if (hasAnime) anime({ targets: card, translateX: [0, -9, 9, -6, 6, 0], easing: "easeInOutSine", duration: 460 });
  }

  // Status verb cycling on the active card ("planning…" -> "reading…").
  let activeStatusCard = null;
  function startStatusCycle(card, stage) {
    stopStatusCycle();
    activeStatusCard = card;
    const verbs = statusVerbs[stage] || ["working"];
    let i = 0;
    setCardState(card, verbs[0] + "…");
    statusTimer = setInterval(() => { i = (i + 1) % verbs.length; setCardState(card, verbs[i] + "…"); }, 1400);
  }
  function stopStatusCycle() {
    if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
    activeStatusCard = null;
  }

  // ── Particle spark burst ────────────────────────────────────────────
  function sparkBurst(card) {
    const rect = card.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const colors = ["#35d0ff", "#6d8cff", "#b06dff", "#46d68a"];
    for (let i = 0; i < 14; i++) {
      const s = document.createElement("span");
      s.className = "spark";
      s.style.left = cx + "px";
      s.style.top = cy + "px";
      s.style.background = colors[i % colors.length];
      document.body.appendChild(s);
      const angle = (Math.PI * 2 * i) / 14 + Math.random() * 0.4;
      const dist = 40 + Math.random() * 55;
      anime({
        targets: s,
        translateX: Math.cos(angle) * dist,
        translateY: Math.sin(angle) * dist,
        scale: [1, 0], opacity: [1, 0],
        easing: "easeOutExpo", duration: 700 + Math.random() * 300,
        complete: () => s.remove(),
      });
    }
  }

  // ── Sub-question chips ──────────────────────────────────────────────
  function renderSubQuestions(subs) {
    sqContainer.innerHTML = "";
    subs.forEach((sq) => {
      const chip = document.createElement("div");
      chip.className = "sq-chip";
      const id = document.createElement("span"); id.className = "sq-id"; id.textContent = sq.id;
      const text = document.createElement("span"); text.className = "sq-text"; text.textContent = sq.text;
      chip.appendChild(id); chip.appendChild(text);
      sqContainer.appendChild(chip);
    });
    if (hasAnime) {
      anime({ targets: ".sq-chip", translateX: [-18, 0], opacity: [0, 1], delay: anime.stagger(90), easing: "easeOutExpo", duration: 550 });
    } else {
      sqContainer.querySelectorAll(".sq-chip").forEach((c) => (c.style.opacity = 1));
    }
  }

  // ── Live trail (with count-up) ──────────────────────────────────────
  function addTrail(ev) {
    const li = document.createElement("li");
    li.className = `stage-${ev.stage}`;
    const stage = document.createElement("span"); stage.className = "ev-stage"; stage.textContent = ev.stage;
    const msg = document.createElement("span"); msg.className = "ev-msg";
    const time = document.createElement("span"); time.className = "ev-time"; time.textContent = formatTime(ev.timestamp);
    li.appendChild(stage); li.appendChild(msg); li.appendChild(time);
    trail.appendChild(li);
    trail.scrollTop = trail.scrollHeight;

    // Count-up on a leading number in the message ("Found 5 sources …").
    const m = (ev.message || "").match(/^(\D*)(\d+)(.*)$/s);
    if (hasAnime && m) {
      const [, pre, num, post] = m;
      const obj = { n: 0 };
      msg.textContent = `${pre}0${post}`;
      anime({ targets: obj, n: parseInt(num, 10), round: 1, easing: "easeOutExpo", duration: 700,
        update: () => { msg.textContent = `${pre}${obj.n}${post}`; } });
    } else {
      msg.textContent = ev.message || "";
    }

    if (hasAnime) anime({ targets: li, translateX: [-16, 0], opacity: [0, 1], easing: "easeOutExpo", duration: 480 });
    else li.style.opacity = 1;
  }

  // ── Report reveal (sweep + stagger) ─────────────────────────────────
  function renderReport(report) {
    reportEl.innerHTML = window.miniMarkdown.render(report.report_markdown || "");
    const cites = (report.citations || []).length;
    if (cites > 0) { citeBadge.hidden = false; citeBadge.textContent = `${cites} citation${cites === 1 ? "" : "s"}`; }

    if (hasAnime) {
      if (reportSweep) {
        reportSweep.style.transform = "translateX(-110%)";
        anime({ targets: reportSweep, translateX: ["-110%", "110%"], easing: "easeInOutQuad", duration: 900 });
      }
      anime({ targets: "#report > *", translateY: [16, 0], opacity: [0, 1], delay: anime.stagger(45, { start: 200 }), easing: "easeOutQuad", duration: 520 });
    } else {
      reportEl.querySelectorAll("*").forEach((el) => (el.style.opacity = 1));
    }
  }

  // ── Helpers ─────────────────────────────────────────────────────────
  function resetUI() {
    stopStatusCycle();
    trail.innerHTML = ""; sqContainer.innerHTML = "";
    reportEl.innerHTML = '<p class="placeholder" style="opacity:1">Working…</p>';
    citeBadge.hidden = true;
    resetSpine();
    Object.values(agentCards).forEach((c) => {
      c.classList.remove("active", "complete", "failed");
      const bar = c.querySelector(".agent-bar span");
      if (bar && hasAnime) { anime.remove(bar); bar.style.opacity = 0; }
      setCardState(c, "idle");
    });
  }
  function setBusy(busy) {
    askBtn.disabled = busy; input.disabled = busy;
    askBtn.querySelector(".btn-label").textContent = busy ? "Researching…" : "Research";
  }
  function setStatus(cls, text) { statusBadge.className = `status-badge ${cls}`; statusBadge.textContent = text; }
  function formatTime(iso) { if (!iso) return ""; try { return new Date(iso).toLocaleTimeString(); } catch { return ""; } }

  // ── Boot ────────────────────────────────────────────────────────────
  startAmbient();
  introAnimation();
})();
