const app = {
  data: null, recommendation: null, advisor: null, analysisStale: false,
  loadEpoch: 0, busy: new Set(), activeTab: "team",
  activeTeamSlot: Math.min(3, Math.max(1, Number(localStorage.getItem("elfantasy-active-team")) || 1)),
  playerPreset: "performance", sort: null, overridePlayer: null,
  teamStrategy: null, history: {
    section:"overview", catalog:null, loaded:false,
    playerSort:{key:"fantasy_points_avg", direction:"desc"},
  },
  teamDraftSelections: new Set(),
  teamBuilderDrafts: new Map(),
};
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? "—").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const num = (value, digits=1) => value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : Number(value).toFixed(digits);
const pct = value => value === null || value === undefined ? "—" : `${(100 * Number(value)).toFixed(0)}%`;
const stamp = value => value ? new Date(value).toLocaleString([], {dateStyle:"medium", timeStyle:"short"}) : "Never";
const signed = value => value === null || value === undefined ? "—" : `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(1)}`;
const optionalNumber = value => value === null || value === undefined || value === "" ? null : Number.isFinite(Number(value)) ? Number(value) : null;
const expectedRole = value => {
  if (value?.expected_role) return value.expected_role;
  const minutes = optionalNumber(value?.expected_minutes);
  if (minutes === null) return "UNKNOWN";
  if (minutes >= 28) return "STAR / PRIMARY";
  if (minutes >= 22) return "STARTER";
  if (minutes >= 14) return "ROTATION";
  return "BENCH / LIMITED";
};
const compactExpectedRole = value => expectedRole(value).replace(" / PRIMARY", "").replace(" / LIMITED", "");
const fpPerMinute = value => {
  const stored = optionalNumber(value?.fp_per_minute);
  if (stored !== null) return stored;
  const fp = optionalNumber(value?.expected_fp), minutes = optionalNumber(value?.expected_minutes);
  return fp !== null && minutes !== null && minutes > 0 ? fp / minutes : null;
};

async function api(path, options={}) {
  let response;
  try {
    response = await fetch(path, {headers:{"Content-Type":"application/json","X-Team-Slot":String(app.activeTeamSlot)}, ...options});
  } catch (_) {
    throw new Error("The local Control Center is unreachable. Check that the server is still running, then retry.");
  }
  let value;
  try { value = await response.json(); }
  catch (_) { throw new Error(`The server returned a malformed response (${response.status}).`); }
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}

document.addEventListener("DOMContentLoaded", async () => {
  wireNavigation(); wireActions(); await load();
});

async function load({acceptLatest=true}={}) {
  const epoch = ++app.loadEpoch;
  const slot = app.activeTeamSlot;
  try {
    const data = await api("/api/bootstrap");
    if (epoch !== app.loadEpoch || slot !== app.activeTeamSlot) return;
    app.data = data;
    if (acceptLatest && !app.analysisStale) {
      if (data.latest_recommendation && recommendationMatchesCurrent(data.latest_recommendation, data)) {
        app.recommendation = data.latest_recommendation;
      } else if (data.latest_recommendation) {
        app.recommendation = null; app.analysisStale = true;
      }
      app.advisor = shadowMatchesCurrent(data.latest_shadow_snapshot, data)
        ? (data.latest_advisor || app.advisor) : null;
    }
    applyDemoInitialState();
    renderAll();
  } catch (error) {
    if (epoch !== app.loadEpoch || slot !== app.activeTeamSlot) return;
    showMessage("#global-block", error.message, "error");
    $("#global-block").classList.remove("hidden");
  }
}

function wireNavigation() {
  $$(".nav-item").forEach(button => button.addEventListener("click", () => activateTab(button.dataset.tab)));
}

function activateTab(tabName) {
  app.activeTab = tabName;
  $$(".nav-item").forEach(item => {
    const active = item.dataset.tab === tabName;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  });
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.id === `tab-${tabName}`));
  window.scrollTo({top: 0, behavior: "instant"});
  if (tabName === "history" && !app.history.loaded) loadHistory("overview");
  if (tabName === "strategy" && !app.teamStrategy) analyzeCurrentTeam();
  if (tabName === "builder") renderTeamBuilder();
}

function wireActions() {
  $$(".team-slot-tab").forEach(button => button.addEventListener("click", () => switchActiveTeam(button.dataset.teamSlot)));
  $$(".team-slot-tab").forEach(button => button.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...button.parentElement.querySelectorAll(".team-slot-tab")];
    const index = tabs.indexOf(button);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    if (!app.busy.size && !tabs[next].disabled) { tabs[next].focus(); tabs[next].click(); }
  }));
  $("#refresh-button").addEventListener("click", refreshLive);
  $("#optimize-button").addEventListener("click", optimize);
  $("#reevaluate-button").addEventListener("click", reevaluateStrategy);
  $("#team-search").addEventListener("input", renderTeam);
  $("#team-add-selected").addEventListener("click", addSelectedTeamEntities);
  $("#team-lock-selected").addEventListener("click", lockSelectedTeamEntities);
  $("#team-remove-selected").addEventListener("click", removeSelectedTeamEntities);
  $("#team-clear").addEventListener("click", clearEntireTeam);
  $("#team-complete-roster").addEventListener("click", () => chooseOptimizationMode("COMPLETE_ROSTER"));
  $("#team-improve-current").addEventListener("click", () => chooseOptimizationMode("CURRENT_TEAM"));
  $("#team-analyze").addEventListener("click", () => { activateTab("strategy"); analyzeCurrentTeam(); });
  $("#analyze-team-button").addEventListener("click", analyzeCurrentTeam);
  $$(".history-tab").forEach(button => button.addEventListener("click", () => loadHistory(button.dataset.historySection)));
  $("#optimization-mode").addEventListener("change", () => {
    invalidateAnalysis("Optimization mode changed. Re-optimize before acting.");
    renderOptimizationControls();
    if (app.data) { renderDashboard(); renderTeam(); renderRecommendationsState(); }
  });
  $("#budget-minus").addEventListener("click", () => adjustBudget(-0.1));
  $("#budget-plus").addEventListener("click", () => adjustBudget(0.1));
  ["player-query","player-team","player-position","player-category","player-turn","player-status"].forEach(id => {
    $("#"+id).addEventListener(id === "player-query" ? "input" : "change", renderPlayers);
  });
  $("#player-view").addEventListener("change", () => { app.sort = null; renderPlayers(); });
  $("#player-reset").addEventListener("click", resetPlayerFilters);
  ["builder-position","builder-category","builder-turn"].forEach(id => $("#"+id).addEventListener("change", renderTeamBuilder));
  $("#builder-query").addEventListener("input", renderTeamBuilder);
  $("#builder-budget").addEventListener("input", updateBuilderBudget);
  $("#builder-use-current").addEventListener("click", () => resetTeamBuilderDraft(false));
  $("#builder-clear").addEventListener("click", clearTeamBuilderDraft);
  $("#builder-save").addEventListener("click", saveTeamBuilderDraft);
  $("#builder-save-ai").addEventListener("click", () => saveTeamBuilderDraft({openAICompletion:true}));
  $$(".preset").forEach(button => button.addEventListener("click", () => {
    app.playerPreset = button.dataset.preset;
    $$(".preset").forEach(item => item.classList.toggle("active", item === button));
    renderPlayers();
  }));
  ["availability-group","availability-team","availability-position","availability-turn"].forEach(id => {
    $("#"+id).addEventListener("change", renderAvailability);
  });
  $("#availability-reset").addEventListener("click", resetAvailabilityFilters);
  $("#override-close").addEventListener("click", () => $("#override-dialog").close());
  $("#override-form").addEventListener("submit", submitOverride);
  $("#override-clear").addEventListener("click", clearOverrideFromDialog);
  $("#demo-scenario").addEventListener("change", changeDemoScenario);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && $("#player-detail").open) $("#player-detail").close();
  });
}

function renderAll() {
  renderTeamSlotTabs(); renderDemoMode(); renderOptimizationControls(); renderDashboard(); renderTeam(); populateFilters();
  renderTeamBuilder(); renderPlayers(); renderAvailability(); renderGrowth(); renderConstraints();
  renderRecommendationsState(); renderAdvisor(app.advisor); renderMonitoring(app.data.monitoring);
  if (app.teamStrategy) renderCurrentTeamStrategy(app.teamStrategy);
  activateTab(app.activeTab);
}

function renderTeamSlotTabs() {
  $$(".team-slot-tab").forEach(button => {
    const active = Number(button.dataset.teamSlot) === app.activeTeamSlot;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
}

async function switchActiveTeam(value) {
  const slot = Number(value);
  if (![1,2,3].includes(slot) || slot === app.activeTeamSlot) return;
  if (!app.data) return toast("Wait for the initial team data to load.", "warning");
  if (app.busy.size) return toast("Wait for the current action to finish before switching teams.", "warning");
  ++app.loadEpoch;
  const previousSlot = app.activeTeamSlot;
  const previousAnalysis = {recommendation:app.recommendation, advisor:app.advisor, teamStrategy:app.teamStrategy, analysisStale:app.analysisStale};
  app.activeTeamSlot = slot;
  localStorage.setItem("elfantasy-active-team", String(slot));
  app.recommendation = null; app.advisor = null; app.teamStrategy = null;
  app.analysisStale = false; app.teamDraftSelections.clear();
  $("#team-search").value = "";
  $("#optimize-bank-credits").value = "";
  $("#stale-recommendation").classList.add("hidden");
  renderTeamSlotTabs();
  showTeamSwitchLoading(slot);
  setBusy("team-switch", true);
  setTeamSlotBusy(true);
  try {
    const context = await api("/api/team/context");
    applyTeamContext(context);
    app.data.latest_recommendation = null;
    app.data.latest_shadow_snapshot = null;
    app.data.latest_advisor = null;
    renderAll();
    toast(`Team ${slot} selected.`, "success");
  } catch (error) {
    app.activeTeamSlot = previousSlot;
    Object.assign(app, previousAnalysis);
    localStorage.setItem("elfantasy-active-team", String(previousSlot));
    renderAll();
    toast(error.message, "error");
  } finally {
    setBusy("team-switch", false);
    setTeamSlotBusy(false);
  }
}

function showTeamSwitchLoading(slot) {
  $("#team-summary").innerHTML = `<div class="summary-item neutral"><span>Current team</span><strong>Loading Team ${esc(slot)}…</strong></div>`;
  $("#team-groups").innerHTML = `<div class="empty-state">Loading Team ${esc(slot)} roster…</div>`;
  $("#builder-summary").innerHTML = `<div class="summary-item neutral"><span>Team builder</span><strong>Loading Team ${esc(slot)}…</strong></div>`;
  $("#builder-selected").innerHTML = `<div class="inline-empty">Loading Team ${esc(slot)} draft…</div>`;
  $("#builder-market").innerHTML = "";
}

function setTeamSlotBusy(value) {
  $$(".team-slot-tab").forEach(button => {
    button.disabled = value;
    button.setAttribute("aria-busy", String(value));
  });
}

function applyTeamContext(context) {
  if (!app.data || !context?.state) return;
  const constraints = context.state.player_constraints || {};
  const players = (app.data.players || []).map(row => ({
    ...row, constraint:constraints[String(row.entity_id)] || "NORMAL",
  }));
  const currentTeam = context.current_team || currentTeamFromState(context.state, players);
  const predictions = new Map(players.map(row => [String(row.entity_id), row]));
  const entities = (currentTeam.entities || []).map(row => ({
    ...row,
    ...Object.fromEntries(Object.entries(predictions.get(String(row.entity_id)) || {})
      .filter(([,item]) => item !== null && item !== undefined)),
  }));
  app.data = {
    ...app.data,
    state:context.state,
    current_team:{...currentTeam, entities},
    players,
    availability:players,
  };
}

function currentTeamFromState(state, players) {
  const predictions = new Map((players || []).map(row => [String(row.entity_id), row]));
  const selectedIds = new Set((state.roster_entity_ids || []).map(String));
  const entities = (app.data.market || [])
    .filter(row => selectedIds.has(String(row.entity_id)))
    .map(row => ({
      ...row,
      ...Object.fromEntries(Object.entries(predictions.get(String(row.entity_id)) || {})
        .filter(([,item]) => item !== null && item !== undefined)),
    }));
  const requirements = app.data.current_team?.requirements || {GUARD:4,FORWARD:4,CENTER:2,COACH:1};
  const counts = {GUARD:0,FORWARD:0,CENTER:0,COACH:0};
  entities.forEach(row => { const role = marketRole(row); counts[role] = (counts[role] || 0) + 1; });
  const validationErrors = Object.entries(requirements)
    .filter(([role,target]) => counts[role] !== Number(target))
    .map(([role,target]) => `${pretty(role)}: ${counts[role] || 0}/${target} selected`);
  const missingPositions = Object.fromEntries(Object.entries(requirements)
    .filter(([role,target]) => counts[role] < Number(target))
    .map(([role,target]) => [role, Number(target) - counts[role]]));
  const partialErrors = [];
  const missingEntities = [...selectedIds].filter(id => !entities.some(row => String(row.entity_id) === id));
  if (missingEntities.length) partialErrors.push(`Selected entities are missing from the current market: ${missingEntities.join(", ")}`);
  const overflow = Object.entries(requirements)
    .filter(([role,target]) => counts[role] > Number(target))
    .map(([role,target]) => `${pretty(role)} ${counts[role]}/${target}`);
  if (overflow.length) partialErrors.push(`Selected roster exceeds positional limits: ${overflow.join(", ")}`);
  const rosterValue = entities.reduce((total,row) => total + (Number(row.credits) || 0), 0);
  return {
    entities, roster_size:entities.length, roster_market_value:rosterValue,
    bank_credits:Number(state.bank_credits) || 0,
    available_budget:rosterValue + (Number(state.bank_credits) || 0),
    transfers_available:state.transfers_available,
    complete:validationErrors.length === 0,
    valid_partial:partialErrors.length === 0,
    position_counts:counts, requirements,
    validation_errors:validationErrors,
    partial_validation_errors:partialErrors,
    missing_positions:missingPositions,
    slots_to_fill:Object.values(missingPositions).reduce((total,value) => total + value, 0),
  };
}

function renderDemoMode() {
  const mode = app.data.mode || {};
  const banner = $("#demo-banner");
  banner.classList.toggle("hidden", !mode.demo);
  document.body.classList.toggle("demo-mode", Boolean(mode.demo));
  if (!mode.demo) return;
  const select = $("#demo-scenario");
  select.innerHTML = (mode.scenario_catalog || []).map(item =>
    `<option value="${esc(item.id)}" ${item.id === mode.scenario ? "selected" : ""}>${esc(item.key)} · ${esc(item.title)}</option>`
  ).join("");
  select.title = (mode.scenario_catalog || []).find(item => item.id === mode.scenario)?.description || "";
}

function applyDemoInitialState() {
  const query = app.data?.demo_ui?.initial_player_query;
  if (query && !$("#player-query").dataset.demoApplied) {
    $("#player-query").value = query;
    $("#player-query").dataset.demoApplied = "true";
  } else if (!query) {
    $("#player-query").removeAttribute("data-demo-applied");
  }
}

async function changeDemoScenario() {
  if (isBusy("demo")) return;
  const scenario = $("#demo-scenario").value;
  setBusy("demo", true);
  try {
    app.data = await api("/api/demo/scenario", {method:"POST", body:JSON.stringify({scenario})});
    app.recommendation = app.data.latest_recommendation;
    app.advisor = app.data.latest_advisor;
    app.analysisStale = false; app.sort = null;
    $("#player-query").value = ""; $("#player-query").removeAttribute("data-demo-applied");
    clearScopedLineup(); applyDemoInitialState(); renderAll();
    toast(`Demo state ${scenario} loaded. Production data remain disconnected.`, "success");
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy("demo", false); }
}

function renderDashboard() {
  const d = app.data.dashboard || {}, s = app.data.state || {}, team = app.data.current_team || {};
  $("#snapshot-summary").innerHTML = `${esc(d.current_season || s.season_code)} · Matchday ${esc(d.current_matchday)} · ${esc(d.current_turn || "PRE-LOCK")}<br>Market ${esc(stamp(d.market_snapshot_timestamp))}`;
  $("#transfer-chip").textContent = `${s.transfers_available ?? 0} transfers remaining`;
  $("#bank-chip").textContent = `${num(s.bank_credits)} credits banked`;
  renderReadiness(d, s, team);
  const mode = optimizationMode();
  const rosterReady = mode === "COMPLETE_ROSTER"
    ? (team.valid_partial ?? team.complete)
    : mode === "CURRENT_TEAM" ? Boolean(team.complete) : true;
  const rosterReasons = rosterReady ? [] : mode === "CURRENT_TEAM"
    ? ["Improve My Team requires all 11 current roster slots. Use Complete My Roster for empty positions."]
    : (team.partial_validation_errors || ["Select one Coach before optimizing."]);
  const blockedReasons = [...(d.blocked_reasons || []), ...rosterReasons];
  const uiBlocked = Boolean(d.optimization_blocked || !rosterReady);
  const block = $("#global-block");
  block.classList.toggle("hidden", !uiBlocked);
  block.innerHTML = uiBlocked ? `<strong>${esc(blockedReasons[0] || "Optimization is not ready yet.")}</strong>${blockedReasons.length > 1 ? `<details><summary>More details</summary><ul>${blockedReasons.slice(1).map(reason => `<li>${esc(reason)}</li>`).join("")}</ul></details>` : ""}` : "";
  $("#optimize-button").disabled = Boolean(uiBlocked || isBusy("optimize"));
  const cards = [
    ["Season / Matchday", `${d.current_season || s.season_code} · ${d.current_matchday ?? "—"}`, `${d.slate_rows ?? 0} slate players`, d.current_matchday ? "good" : "warning"],
    ["Last successful refresh", stamp(d.last_successful_refresh_timestamp), d.latest_live_run?.status || "No run", d.last_successful_refresh_timestamp ? "good" : "warning"],
    ["Market snapshot", stamp(d.market_snapshot_timestamp), `${d.fantasy_players_mapped ?? 0}/${d.fantasy_players ?? 0} mapped`, d.market_snapshot_timestamp ? "good" : "blocked"],
    ["Availability", stamp(d.availability_timestamp), `${d.active_manual_overrides ?? 0} active overrides`, d.availability_timestamp ? "good" : "warning"],
    ["Predictions", d.predictions_current ? "CURRENT" : "STALE / MISSING", d.prediction?.predictive_model_version || "No compatible run", d.predictions_current ? "good" : "blocked"],
    ["Rules gate", d.rules_gate?.passed ? "PASS" : "BLOCKED", d.rules_gate?.version || d.rules_gate?.reason, d.rules_gate?.passed ? "good" : "blocked"],
    ["Scoring gate", d.scoring_gate?.passed ? "PASS" : "BLOCKED", d.scoring_gate?.state || d.scoring_gate?.reason, d.scoring_gate?.passed ? "good" : "blocked"],
    ["Current market", `${d.fantasy_players ?? 0} players`, `${d.slate_rows_with_credits ?? 0} prediction rows priced`, d.fantasy_players ? "good" : "warning"],
  ];
  $("#status-cards").innerHTML = cards.map(([label,value,detail,state]) => `<div class="metric ${state}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="detail">${esc(detail)}</div></div>`).join("");
  const freshness = Object.entries(d.freshness || {});
  $("#source-freshness").innerHTML = freshness.length ? freshness.map(([name,value]) => `<div class="source-row"><strong>${esc(pretty(name))}</strong><span class="status ${value.is_stale ? "red" : "green"}">${value.is_stale ? "✕ STALE" : "✓ FRESH"}</span><span>${esc(stamp(value.last_success_at))}<small>${esc(value.message || "Last attempt recorded")}</small></span></div>`).join("") : emptyInline("No live source attempts exist yet.");
  const preseason = $("#preseason-state");
  const noMarket = !(d.fantasy_players > 0);
  preseason.classList.toggle("hidden", !noMarket);
  if (noMarket) preseason.innerHTML = `<div class="empty-icon">◌</div><div><h3>Current ${esc(d.current_season || s.season_code)} Fantasy market is not yet available.</h3><p>Dashboard freshness and manual profile settings remain usable. Player exploration, predictions, and optimization unlock after a verified market snapshot arrives.</p>${app.data.mode?.demo ? "" : "<p>Launch demo mode to inspect every UI workflow safely before the season starts.</p>"}</div>`;
}

function optimizationMode() { return $("#optimization-mode")?.value || "COMPLETE_ROSTER"; }

function renderOptimizationControls() {
  const mode = optimizationMode(), build = mode === "BUILD_NEW";
  const completion = mode === "COMPLETE_ROSTER", improvement = mode === "CURRENT_TEAM";
  $("#build-budget-controls").classList.toggle("hidden", !build);
  $("#current-team-controls").classList.toggle("hidden", build);
  $("#maximum-changes-field").classList.toggle("hidden", !improvement);
  $("#current-credit-label").textContent = completion ? "Remaining credits" : "Available / bank credits";
  $("#current-credit-help").textContent = completion
    ? "Credits available to purchase players for the currently empty slots."
    : "Unused credits currently available in your Fantasy account.";
  if (!app.data) return;
  if (!$("#optimize-bank-credits").value) {
    $("#optimize-bank-credits").value = app.data.state?.bank_credits ?? 0;
  }
  $("#maximum-changes").value = String(
    Math.min(11, Math.max(0, app.data.state?.transfers_available ?? 3))
  );
  $("#optimization-mode-help").textContent = completion
    ? "Every selection stays fixed for this completion run. Manual lock buttons remain editable for Improve My Team."
    : improvement
      ? "Manually locked players and Coach stay. AI may replace any unlocked selection up to the selected maximum changes."
      : "Builds a fresh legal 4 Guard / 4 Forward / 2 Center / 1 Coach team under the entered total budget.";
}

function chooseOptimizationMode(mode) {
  const select = $("#optimization-mode"), changed = select.value !== mode;
  select.value = mode;
  if (changed) invalidateAnalysis("Optimization goal changed.");
  renderOptimizationControls();
  if (app.data) { renderDashboard(); renderTeam(); renderRecommendationsState(); }
  activateTab("recommendations");
}

function adjustBudget(delta) {
  const input = $("#total-budget");
  const current = Number(input.value);
  input.value = Math.max(0, Math.round(((Number.isFinite(current) ? current : 0) + delta) * 10) / 10).toFixed(1);
  input.dispatchEvent(new Event("input", {bubbles:true}));
}

function renderReadiness(d, s, team) {
  const marketStale = Boolean(d.freshness?.fantasy_market?.is_stale);
  const items = [
    ["Season", d.current_season || s.season_code, d.current_matchday ? "good" : "warning"],
    ["Matchday", d.current_matchday ?? "—", d.current_matchday ? "good" : "warning"],
    ["Turn", d.current_turn || "PRE-LOCK", "neutral"],
    ["Refresh", stamp(d.last_successful_refresh_timestamp), d.last_successful_refresh_timestamp ? "good" : "warning"],
    ["Market", marketStale ? "STALE" : d.market_snapshot_timestamp ? "FRESH" : "MISSING", marketStale ? "warning" : d.market_snapshot_timestamp ? "good" : "blocked"],
    ["Prediction", d.predictions_current ? "CURRENT" : "NOT CURRENT", d.predictions_current ? "good" : "blocked"],
    ["Rules", d.rules_gate?.passed ? "GOOD" : "BLOCKED", d.rules_gate?.passed ? "good" : "blocked"],
    ["Scoring", d.scoring_gate?.passed ? "GOOD" : "BLOCKED", d.scoring_gate?.passed ? "good" : "blocked"],
    ["Roster credits", num(team.roster_market_value), team.complete ? "good" : "warning"],
    ["Bank", num(s.bank_credits), "neutral"], ["Transfers", s.transfers_available ?? 0, "neutral"],
    ["Overrides", d.active_manual_overrides ?? 0, d.active_manual_overrides ? "warning" : "neutral"],
  ];
  $("#global-readiness").innerHTML = items.map(([label,value,state]) => `<div class="readiness-item ${state}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
}

function renderTeam() {
  if (!app.data) return;
  const team = app.data.current_team || {entities:[]};
  const mode = optimizationMode(), completionMode = mode === "COMPLETE_ROSTER";
  const requirements = team.requirements || {GUARD:4,FORWARD:4,CENTER:2,COACH:1};
  const counts = team.position_counts || {};
  const missingPositions = team.missing_positions || Object.fromEntries(
    Object.entries(requirements)
      .filter(([position,target]) => position !== "COACH" && (counts[position] || 0) < target)
      .map(([position,target]) => [position, target - (counts[position] || 0)])
  );
  const slotsToFill = team.slots_to_fill ?? Object.values(missingPositions).reduce((total,value) => total + value, 0);
  const fillText = Object.entries(missingPositions)
    .map(([position,count]) => `${count} ${pretty(position.toLowerCase())}${count === 1 ? "" : "s"}`)
    .join(" · ");
  const manuallyLocked = new Set(Object.entries(app.data.state.player_constraints || {})
    .filter(([,value]) => value === "FORCE_INCLUDE").map(([entity]) => String(entity)));
  const lockedCount = (team.entities || [])
    .filter(row => manuallyLocked.has(String(row.entity_id))).length;
  const projection = rosterFantasyProjection(team.entities || []);
  $("#team-summary").innerHTML = [
    ["Selected", `${team.roster_size || 0}/11`, team.valid_partial ? "good" : "warning"],
    ["Manual locks", `${lockedCount}/${team.roster_size || 0}`, lockedCount ? "good" : "neutral"],
    [completionMode ? "AI will fill" : "Empty slots", slotsToFill ? `${slotsToFill} slot${slotsToFill === 1 ? "" : "s"}` : "None", slotsToFill ? "neutral" : "good"],
    ["Current value", `${num(team.roster_market_value)} cr`, "neutral"],
    [projection.label, `${projection.partial && projection.value !== null ? "≈" : ""}${num(projection.value)}`, "neutral", projection.note],
  ].map(([label,value,stateName,note]) => `<div class="summary-item ${stateName}"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note ? `<small>${esc(note)}</small>` : ""}</div>`).join("");
  const groups = {GUARD:[], FORWARD:[], CENTER:[], COACH:[]};
  (team.entities || []).forEach(row => {
    const key = marketRole(row);
    (groups[key] ||= []).push(row);
  });
  const selected = new Set((app.data.state.roster_entity_ids || []).map(String));
  const query = $("#team-search").value.trim().toLowerCase();
  const suggestions = {GUARD:[], FORWARD:[], CENTER:[], COACH:[]};
  selected.forEach(entity => app.teamDraftSelections.delete(entity));
  marketWithPredictions()
    .filter(row => !selected.has(String(row.entity_id)))
    .filter(row => !query || [row.name,teamName(row),row.position,row.entity_type].some(value => String(value || "").toLowerCase().includes(query)))
    .sort(compareTeamSuggestions)
    .forEach(row => (suggestions[marketRole(row)] ||= []).push(row));
  $("#team-groups").innerHTML = Object.entries(groups).map(([position,rows]) => {
    const target = requirements[position] ?? "—", count = counts[position] ?? rows.length;
    const stateName = count === target ? "complete" : count > target ? "invalid" : "incomplete";
    const available = (suggestions[position] || []).slice(0, 8);
    const selectedRows = rows.length
      ? rows.map(row => renderSelectedTeamRow(row, position, mode)).join("")
      : `<div class="slot-empty">No ${position.toLowerCase()} selected</div>`;
    const suggestionRows = available.length
      ? available.map(row => `<div class="suggestion-row"><label class="entity-choice"><input class="suggestion-select" data-id="${esc(row.entity_id)}" type="checkbox" ${app.teamDraftSelections.has(String(row.entity_id)) ? "checked" : ""}><span><strong>${esc(row.name)}</strong><small>${esc(teamName(row))} · ${esc(pretty(position))} · ${num(row.credits)} cr · ${playerStatLine(row)}</small></span></label><button class="tiny quick-add" data-id="${esc(row.entity_id)}" type="button" aria-label="Add ${esc(row.name)}">Add</button></div>`).join("")
      : `<div class="slot-empty">No available matches</div>`;
    return `<section class="position-column ${stateName}" data-role="${esc(position)}"><div class="slot-heading"><h3>${esc(pretty(position))}</h3><div class="role-actions"><span>${count}/${target}</span><button class="tiny clear-role" data-role="${esc(position)}" type="button">Clear role</button></div></div><div class="selected-roster">${selectedRows}</div><div class="suggestion-heading"><strong>Suggested ${position === "COACH" ? "coaches" : position.toLowerCase() + "s"}</strong><span>${query ? "filtered" : "ranked by current predictions"}</span></div><div class="suggestion-list">${suggestionRows}</div></section>`;
  }).join("");
  $$(".remove-team").forEach(button => button.addEventListener("click", () => removeTeamEntity(button.dataset.id)));
  $$(".quick-add").forEach(button => button.addEventListener("click", () => addTeamEntity(button.dataset.id)));
  $$(".suggestion-select").forEach(input => input.addEventListener("change", () => {
    if (input.checked) app.teamDraftSelections.add(String(input.dataset.id));
    else app.teamDraftSelections.delete(String(input.dataset.id));
    updateTeamDraftSelectionCount();
  }));
  $$(".clear-role").forEach(button => button.addEventListener("click", () => clearTeamRole(button.dataset.role)));
  $$(".toggle-team-lock").forEach(button => button.addEventListener("click", () => toggleTeamLock(button.dataset.id, button.dataset.locked !== "true")));
  updateTeamDraftSelectionCount();
  const errors = team.validation_errors || [];
  const forced = app.data.demo_ui?.forced_validation_error;
  if (forced) showMessage("#team-validation", `Current Team needs attention: ${forced}`, "error");
  else if (completionMode && (team.valid_partial ?? false)) showMessage("#team-validation", `Selections are fixed only for Complete My Roster. Manual lock buttons remain editable. AI will fill: ${fillText || "no player slots"}.`, team.complete ? "success" : "info");
  else if (mode === "CURRENT_TEAM" && team.complete) showMessage("#team-validation", `Current roster ready · ${manuallyLocked.size} manual lock${manuallyLocked.size === 1 ? "" : "s"}. AI may replace any unlocked player or Coach.`, "success");
  else if (mode === "CURRENT_TEAM") showMessage("#team-validation", "Improve My Team requires all 11 current roster slots. Use Complete My Roster for empty positions.", "warning");
  else if (!(team.valid_partial ?? false)) showMessage("#team-validation", `Current Team needs attention: ${(team.partial_validation_errors || errors).join(" · ") || "select and lock one Coach."}`, "error");
  else showMessage("#team-validation", `${team.roster_size || 0}/11 roster slots selected.`, "info");
}

function renderSelectedTeamRow(row, position, mode) {
  const coach = position === "COACH";
  const manualLock = (app.data.state.player_constraints || {})[String(row.entity_id)] === "FORCE_INCLUDE";
  const badge = manualLock
    ? `<b class="locked-chip">${coach ? "COACH LOCKED" : "LOCKED"}</b>`
    : mode === "COMPLETE_ROSTER"
      ? `<b class="mode-fixed-chip">FIXED FOR COMPLETION</b>`
      : `<b class="changeable-chip">CHANGEABLE</b>`;
  const toggle = `<button class="tiny toggle-team-lock" data-id="${esc(row.entity_id)}" data-locked="${manualLock}" type="button" title="Manual lock used by Improve My Team">${manualLock ? "Unlock" : "Lock"}</button>`;
  const alternatives = !manualLock && !coach ? renderQuickAlternatives(row) : "";
  return `<div class="roster-entry"><div class="roster-row"><label class="entity-choice"><input class="roster-select" data-id="${esc(row.entity_id)}" type="checkbox"><span><strong>${esc(row.name)} ${badge}</strong><small>${esc(teamName(row))} · ${esc(pretty(position))} · ${num(row.credits)} cr · ${playerStatLine(row)} · ${status(row)}</small></span></label><div class="row-actions">${toggle}<button class="tiny remove-team" data-id="${esc(row.entity_id)}" type="button" aria-label="Remove ${esc(row.name)}">Remove</button></div></div>${alternatives}</div>`;
}

function rosterFantasyProjection(entities) {
  const players = entities.filter(row => marketRole(row) !== "COACH");
  const coaches = entities.filter(row => marketRole(row) === "COACH");
  const missing = players.filter(row => optionalNumber(row.expected_fp) === null);
  const scores = players.map(row => optionalNumber(row.expected_fp)).filter(value => value !== null);
  const coachScores = coaches.map(row => optionalNumber(row.expected_score ?? row.expected_fp));
  const includeCoach = coaches.length === 1 && coachScores[0] !== null;
  const partial = missing.length > 0;
  const label = partial
    ? `Partial FP (${scores.length}/${players.length} players${includeCoach ? " + coach" : "; no coach"})`
    : includeCoach ? "Predicted FP" : "Player FP (coach excluded)";
  if (!scores.length || players.length > 10) {
    return {label, value:null, partial};
  }
  scores.sort((a,b) => b-a);
  // Five starters plus the sixth man score fully. The four lowest projections
  // in a complete roster score half; the highest projection gets the captain bonus.
  const playerFP = scores.reduce((total,value,index) => total + value * (index < 6 ? 1 : 0.5), 0) + scores[0];
  return {label:players.length < 10 ? `Draft ${label}` : label,
    value:playerFP + (includeCoach ? coachScores[0] : 0), partial,
    note:partial ? `Missing forecast: ${missing.map(row => row.name || "Unnamed player").join(", ")}` : ""};
}

function playerStatLine(row) {
  if (marketRole(row) === "COACH") return row.expected_score == null ? "prediction unavailable" : `xFP ${num(row.expected_score)}`;
  const prediction = row.expected_fp == null
    ? "prediction unavailable"
    : `xFP ${num(row.expected_fp)} · xMin ${num(row.expected_minutes)}`;
  return `${prediction} · L5 FP ${num(row.last5_fp_average)} · L5 MIN ${num(row.last5_minutes_median)}`;
}

function quickAlternativeNotes(current, candidate) {
  const pros = [], cons = [];
  const delta = (a, b) => optionalNumber(a) === null || optionalNumber(b) === null ? NaN : Number(a) - Number(b);
  const fpDelta = delta(candidate.expected_fp, current.expected_fp);
  const minDelta = delta(candidate.expected_minutes, current.expected_minutes);
  const rateDelta = delta(fpPerMinute(candidate), fpPerMinute(current));
  const creditDelta = delta(candidate.credits, current.credits);
  if (fpDelta >= 0.5) pros.push(`${signed(fpDelta)} xFP`);
  if (minDelta >= 1) pros.push(`${signed(minDelta)} expected min`);
  if (rateDelta >= 0.03) pros.push("better FP/min");
  if (creditDelta <= -0.5) pros.push(`${num(Math.abs(creditDelta))} cr cheaper`);
  if (fpDelta <= -0.5) cons.push(`${signed(fpDelta)} xFP`);
  if (minDelta <= -1) cons.push(`${signed(minDelta)} expected min`);
  if (creditDelta >= 0.5) cons.push(`${signed(creditDelta)} cr`);
  if (Math.abs(creditDelta) > 3) cons.push("larger budget jump");
  return {pros:pros.slice(0,2).join(" · ") || "similar projection", cons:cons.slice(0,2).join(" · ") || "no major statistical drawback"};
}

function renderQuickAlternatives(current) {
  const selected = new Set((app.data.state.roster_entity_ids || []).map(String));
  const alternatives = marketWithPredictions()
    .filter(row => marketRole(row) === marketRole(current) && !selected.has(String(row.entity_id)))
    .filter(row => String(row.availability_group || "").toUpperCase() !== "RED" && row.expected_fp != null)
    .map(row => ({...row, credit_distance:Math.abs(Number(row.credits) - Number(current.credits))}))
    .sort((a,b) => (a.credit_distance > 3) - (b.credit_distance > 3) || a.credit_distance - b.credit_distance || Number(b.expected_fp) - Number(a.expected_fp))
    .slice(0,4);
  if (!alternatives.length) return "";
  return `<details class="quick-alternatives"><summary>Consider alternatives</summary><div>${alternatives.map(row => { const notes = quickAlternativeNotes(current,row); return `<article><strong>${esc(row.name)}</strong><span>${num(row.credits)} cr · ${playerStatLine(row)}</span><small><b>Pro:</b> ${esc(notes.pros)} <b>Con:</b> ${esc(notes.cons)}</small></article>`; }).join("")}</div></details>`;
}

function marketRole(row) { return String(row.entity_type || "").toUpperCase() === "COACH" ? "COACH" : broadPosition(row.position); }
function priceCategory(row) {
  if (row?.credits === null || row?.credits === undefined || row?.credits === "") return "";
  const credits = Number(row.credits);
  if (!Number.isFinite(credits)) return "";
  if (credits >= 11) return "PREMIUM";
  if (credits >= 7) return "MID";
  if (credits >= 4) return "CHEAP";
  return "BELOW_CHEAP";
}
function priceCategoryLabel(row) {
  return {PREMIUM:"Premium", MID:"Mid", CHEAP:"Cheap", BELOW_CHEAP:"Under 4 cr"}[priceCategory(row)] || "No price";
}
function marketWithPredictions() {
  const predictions = new Map((app.data.players || []).map(row => [String(row.entity_id), row]));
  return (app.data.market || []).map(row => {
    const prediction = predictions.get(String(row.entity_id)) || {};
    return {
      ...row,
      expected_fp:prediction.expected_fp ?? row.expected_fp,
      expected_score:prediction.expected_score ?? row.expected_score,
      expected_minutes:prediction.expected_minutes ?? row.expected_minutes,
      fp_per_minute:prediction.fp_per_minute ?? row.fp_per_minute,
      p10_fp:prediction.p10_fp ?? row.p10_fp,
      p90_fp:prediction.p90_fp ?? row.p90_fp,
      last5_fp_average:prediction.last5_fp_average ?? row.last5_fp_average,
      last5_minutes_median:prediction.last5_minutes_median ?? row.last5_minutes_median,
      last5_games:prediction.last5_games ?? row.last5_games ?? [],
      turn:prediction.turn ?? row.turn ?? row.source_turn,
      availability:prediction.availability ?? row.availability ?? "UNKNOWN",
      availability_group:prediction.availability_group ?? row.availability_group,
      source_availability_status:prediction.source_availability_status ?? row.source_availability_status,
    };
  });
}
function compareTeamSuggestions(a,b) {
  const statusRank = row => String(row.availability_group || "YELLOW").toUpperCase() === "RED" ? 1 : 0;
  const availabilityDelta = statusRank(a) - statusRank(b);
  if (availabilityDelta) return availabilityDelta;
  return compareValues(a.expected_fp ?? a.expected_score, b.expected_fp ?? b.expected_score, -1, a.name, b.name);
}
function acceptedAdditions(ids) {
  const requirements = app.data.current_team?.requirements || {GUARD:4,FORWARD:4,CENTER:2,COACH:1};
  const market = new Map(marketWithPredictions().map(row => [String(row.entity_id), row]));
  const roster = (app.data.state.roster_entity_ids || []).map(String), selected = new Set(roster);
  const counts = {GUARD:0,FORWARD:0,CENTER:0,COACH:0};
  roster.forEach(id => { const row = market.get(id); if (row) counts[marketRole(row)] = (counts[marketRole(row)] || 0) + 1; });
  const accepted = [];
  ids.map(String).forEach(id => {
    const row = market.get(id), role = row && marketRole(row);
    if (row && !selected.has(id) && counts[role] < (requirements[role] || 0)) {
      selected.add(id); counts[role] += 1; accepted.push(id);
    }
  });
  return accepted;
}
async function addTeamEntity(entity) {
  const accepted = acceptedAdditions([entity]);
  if (!accepted.length) return showMessage("#team-validation", "That role is already full or the entity is already selected.", "warning");
  await saveState({roster_entity_ids:[...(app.data.state.roster_entity_ids || []), ...accepted]});
}
async function addSelectedTeamEntities() {
  const requested = [...app.teamDraftSelections];
  if (!requested.length) return showMessage("#team-validation", "Select one or more suggestions to add.", "warning");
  const accepted = acceptedAdditions(requested);
  if (!accepted.length) return showMessage("#team-validation", "The selected roles are already full.", "warning");
  await saveState({roster_entity_ids:[...(app.data.state.roster_entity_ids || []), ...accepted]});
}
function updateTeamDraftSelectionCount() {
  const button = $("#team-add-selected");
  if (button) button.textContent = app.teamDraftSelections.size
    ? `Add selected (${app.teamDraftSelections.size})` : "Add selected";
}
async function lockSelectedTeamEntities() {
  const selected = $$(".roster-select:checked").map(input => String(input.dataset.id));
  if (!selected.length) return showMessage("#team-validation", "Select one or more current-team players or the Coach to lock.", "warning");
  const constraints = {...(app.data.state.player_constraints || {})};
  selected.forEach(entity => { constraints[entity] = "FORCE_INCLUDE"; });
  await saveState({player_constraints:constraints});
}
async function removeSelectedTeamEntities() {
  const removed = new Set($$(".roster-select:checked").map(input => input.dataset.id));
  if (!removed.size) return showMessage("#team-validation", "Select one or more current-team entries to remove.", "warning");
  await saveState({
    roster_entity_ids:(app.data.state.roster_entity_ids || []).filter(id => !removed.has(String(id))),
    player_constraints:constraintsWithoutLocks(removed),
  });
}
async function removeTeamEntity(entity) {
  await saveState({
    roster_entity_ids:app.data.state.roster_entity_ids.filter(id => String(id) !== String(entity)),
    player_constraints:constraintsWithoutLocks([entity]),
  });
}
async function toggleTeamLock(entity, shouldLock) {
  const current = (app.data.state.player_constraints || {})[String(entity)] || "NORMAL";
  await setConstraint(entity, shouldLock ? "FORCE_INCLUDE" : "NORMAL", {value:current});
}
async function clearTeamRole(role) {
  const market = new Map((app.data.market || []).map(row => [String(row.entity_id), row]));
  const removed = (app.data.state.roster_entity_ids || [])
    .filter(id => marketRole(market.get(String(id)) || {}) === role);
  const removedIds = new Set(removed.map(String));
  await saveState({
    roster_entity_ids:(app.data.state.roster_entity_ids || []).filter(id => !removedIds.has(String(id))),
    player_constraints:constraintsWithoutLocks(removed),
  });
}
function constraintsWithoutLocks(entityIds) {
  const removed = new Set([...entityIds].map(String));
  return Object.fromEntries(Object.entries(app.data.state.player_constraints || {})
    .filter(([entity,value]) => !removed.has(String(entity)) || String(value).toUpperCase() !== "FORCE_INCLUDE"));
}
async function clearEntireTeam() {
  app.teamDraftSelections.clear();
  const constraints = Object.fromEntries(
    Object.entries(app.data.state.player_constraints || {})
      .filter(([,value]) => String(value).toUpperCase() !== "FORCE_INCLUDE")
  );
  await saveState({roster_entity_ids:[], player_constraints:constraints});
}
async function saveState(change, {messageSelector="#team-validation", successMessage="Current team saved locally."}={}) {
  if (isBusy("team")) return false;
  setTeamEditorBusy(true);
  try {
    const context = await api("/api/team", {method:"POST",body:JSON.stringify(change)});
    applyTeamContext(context);
    invalidateAnalysis("Current-team inputs changed.", {clearLineup:true});
    renderAll();
    toast(successMessage, "success");
    return true;
  } catch (error) { showMessage(messageSelector, error.message, "error"); }
  finally { setTeamEditorBusy(false); }
  return false;
}

function setTeamEditorBusy(value) {
  setBusy("team", value);
  $$("#tab-team button, #tab-team input, #tab-builder button, #tab-builder input, #tab-builder select").forEach(control => { control.disabled = value; });
}

function currentTeamFingerprint() {
  const roster = (app.data?.state?.roster_entity_ids || []).map(String).sort().join("|");
  return `${roster}::${Number(app.data?.state?.bank_credits) || 0}`;
}

function defaultBuilderBudget() {
  const rosterValue = Number(app.data?.current_team?.roster_market_value);
  const bank = Number(app.data?.state?.bank_credits);
  const total = (Number.isFinite(rosterValue) ? rosterValue : 0) + (Number.isFinite(bank) ? bank : 0);
  return total > 0 ? Math.round(total * 10) / 10 : 100;
}

function freshTeamBuilderDraft(markDirty=false) {
  return {
    roster:[...(app.data?.state?.roster_entity_ids || [])].map(String),
    budget:defaultBuilderBudget(), dirty:Boolean(markDirty), sourceFingerprint:currentTeamFingerprint(),
  };
}

function resetTeamBuilderDraft(markDirty=false) {
  if (!app.data) return;
  app.teamBuilderDrafts.set(app.activeTeamSlot, freshTeamBuilderDraft(markDirty));
  renderTeamBuilder();
}

function ensureTeamBuilderDraft() {
  if (!app.data) return null;
  let draft = app.teamBuilderDrafts.get(app.activeTeamSlot);
  if (!draft || (!draft.dirty && draft.sourceFingerprint !== currentTeamFingerprint())) {
    draft = freshTeamBuilderDraft(false);
    app.teamBuilderDrafts.set(app.activeTeamSlot, draft);
  }
  return draft;
}

function teamBuilderState() {
  const draft = ensureTeamBuilderDraft();
  const market = new Map(marketWithPredictions().map(row => [String(row.entity_id), row]));
  const selected = (draft?.roster || []).map(id => market.get(String(id))).filter(Boolean);
  const counts = {GUARD:0,FORWARD:0,CENTER:0,COACH:0};
  selected.forEach(row => { const role = marketRole(row); counts[role] = (counts[role] || 0) + 1; });
  const requirements = app.data?.current_team?.requirements || {GUARD:4,FORWARD:4,CENTER:2,COACH:1};
  const used = selected.reduce((total,row) => total + (Number.isFinite(Number(row.credits)) ? Number(row.credits) : 0), 0);
  const allPricesKnown = selected.every(row => row.credits !== null && row.credits !== undefined && row.credits !== "" && Number.isFinite(Number(row.credits)));
  const budget = Number(draft?.budget);
  const complete = Object.entries(requirements).every(([role,target]) => counts[role] === Number(target)) && selected.length === 11;
  const validBudget = Number.isFinite(budget) && budget >= 0;
  const overBudget = validBudget && used > budget + 0.001;
  const saveable = allPricesKnown && validBudget && !overBudget;
  return {draft, market, selected, counts, requirements, used, budget, complete, allPricesKnown, validBudget, overBudget, saveable};
}

function renderTeamBuilder() {
  if (!app.data || !$("#builder-market")) return;
  const state = teamBuilderState();
  if (!state.draft) return;
  if (document.activeElement !== $("#builder-budget")) $("#builder-budget").value = Number(state.draft.budget).toFixed(1);
  const remaining = state.validBudget ? state.budget - state.used : null;
  const projection = rosterFantasyProjection(state.selected);
  const missing = Object.entries(state.requirements)
    .filter(([role,target]) => state.counts[role] !== Number(target))
    .map(([role,target]) => `${pretty(role)} ${state.counts[role]}/${target}`);
  $("#builder-summary").innerHTML = [
    ["Selected", `${state.selected.length}/11`, state.complete ? "good" : "warning"],
    ["Guards", `${state.counts.GUARD}/${state.requirements.GUARD}`, state.counts.GUARD === state.requirements.GUARD ? "good" : "neutral"],
    ["Forwards", `${state.counts.FORWARD}/${state.requirements.FORWARD}`, state.counts.FORWARD === state.requirements.FORWARD ? "good" : "neutral"],
    ["Centers", `${state.counts.CENTER}/${state.requirements.CENTER}`, state.counts.CENTER === state.requirements.CENTER ? "good" : "neutral"],
    ["Coach", `${state.counts.COACH}/${state.requirements.COACH}`, state.counts.COACH === state.requirements.COACH ? "good" : "neutral"],
    ["Used", `${num(state.used)} cr`, state.overBudget ? "blocked" : "neutral"],
    ["Remaining", remaining == null ? "—" : `${num(remaining)} cr`, state.overBudget ? "blocked" : "good"],
    [projection.label, `${projection.partial && projection.value !== null ? "≈" : ""}${num(projection.value)}`, "neutral", projection.note],
  ].map(([label,value,stateName,note]) => `<div class="summary-item ${stateName}"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note ? `<small>${esc(note)}</small>` : ""}</div>`).join("");
  $("#builder-selected-count").textContent = `${state.selected.length}/11 selected for Team ${app.activeTeamSlot}`;
  const roleOrder = {GUARD:0,FORWARD:1,CENTER:2,COACH:3};
  const selectedRows = [...state.selected].sort((a,b) => roleOrder[marketRole(a)] - roleOrder[marketRole(b)] || String(a.name).localeCompare(String(b.name)));
  $("#builder-selected").innerHTML = selectedRows.length ? selectedRows.map(row => builderPickRow(row, true)).join("") : `<div class="inline-empty">Your draft is empty. Add any guard, forward, center, or coach first.</div>`;
  const selectedIds = new Set(state.draft.roster.map(String));
  const query = $("#builder-query").value.trim().toLowerCase();
  const position = $("#builder-position").value;
  const category = $("#builder-category").value;
  const turn = $("#builder-turn").value;
  const available = marketWithPredictions()
    .filter(row => !selectedIds.has(String(row.entity_id)))
    .filter(row => !position || marketRole(row) === position)
    .filter(row => !category || priceCategory(row) === category)
    .filter(row => !turn || String(row.turn) === turn)
    .filter(row => !query || [row.name,teamName(row),row.position,row.entity_type].some(value => String(value || "").toLowerCase().includes(query)))
    .sort(compareTeamSuggestions);
  $("#builder-result-count").textContent = `${available.length} available ${available.length === 1 ? "pick" : "picks"}`;
  $("#builder-market").innerHTML = available.length ? available.map(row => builderPickRow(row, false, state)).join("") : `<div class="inline-empty">No available picks match these filters.</div>`;
  $$("#builder-selected .builder-remove").forEach(button => button.addEventListener("click", () => removeTeamBuilderPick(button.dataset.id)));
  $$("#builder-market .builder-add").forEach(button => button.addEventListener("click", () => addTeamBuilderPick(button.dataset.id)));
  $$("#tab-builder .detail-player").forEach(button => button.addEventListener("click", () => showPlayerDetail(button.dataset.player)));
  $("#builder-save").textContent = state.complete ? "Save as current team" : "Save partial team";
  $("#builder-save").disabled = !state.saveable || isBusy("team");
  $("#builder-save-ai").disabled = !state.saveable || state.complete || isBusy("team");
  if (!state.validBudget) showMessage("#builder-message", "Enter a valid total credit amount.", "error");
  else if (!state.allPricesKnown) showMessage("#builder-message", "Every selected entry needs a current credit price before this draft can be saved.", "error");
  else if (state.overBudget) showMessage("#builder-message", `This draft is ${num(state.used - state.budget)} credits over budget. Remove or replace a pick.`, "error");
  else if (!state.complete) showMessage("#builder-message", `Partial team can be saved now. Still needed: ${missing.join(" · ") || "complete the roster"}. Save it directly or continue to AI completion.`, "info");
  else showMessage("#builder-message", `Legal roster ready with ${num(remaining)} credits remaining. Save it to Team ${app.activeTeamSlot}.`, "success");
}

function builderPickRow(row, selected, state=null) {
  const role = marketRole(row), prediction = row.expected_fp ?? row.expected_score;
  const roleFull = state ? state.counts[role] >= Number(state.requirements[role] || 0) : false;
  const priceKnown = row.credits !== null && row.credits !== undefined && row.credits !== "" && Number.isFinite(Number(row.credits));
  const price = Number(row.credits);
  const tooExpensive = state && priceKnown && state.validBudget && state.used + price > state.budget + 0.001;
  const disabled = !selected && (roleFull || tooExpensive || !priceKnown);
  const reason = roleFull ? `${pretty(role)} slots are full` : !priceKnown ? "Current credit price unavailable" : tooExpensive ? "Not enough remaining credits" : `Add ${row.name}`;
  const name = row.player_id
    ? `<button class="link-button detail-player" data-player="${esc(row.player_id)}" type="button">${esc(row.name)}</button>`
    : `<strong>${esc(row.name)}</strong>`;
  const turn = row.turn ?? row.source_turn;
  return `<article class="builder-pick ${selected ? "selected" : ""}"><div class="builder-pick-identity">${name}<span><b class="builder-turn-badge">${turn == null ? "TURN —" : `T${esc(turn)}`}</b>${esc(teamName(row))} · ${esc(pretty(role))} · <b>${num(row.credits)} cr</b> · ${esc(priceCategoryLabel(row))}</span></div><div class="builder-evidence"><span><b>xMin</b>${num(row.expected_minutes)}</span><span><b>xFP</b>${num(prediction)}</span><span><b>Upside P90</b>${num(row.p90_fp)}</span><span><b>L5 FP</b>${num(row.last5_fp_average)}</span><span><b>L5 MIN</b>${num(row.last5_minutes_median)}</span></div>${selected ? `<button class="tiny builder-remove" data-id="${esc(row.entity_id)}" type="button">Remove</button>` : `<button class="tiny builder-add" data-id="${esc(row.entity_id)}" type="button" ${disabled ? "disabled" : ""} title="${esc(reason)}">Add</button>`}</article>`;
}

function updateBuilderBudget() {
  const draft = ensureTeamBuilderDraft();
  if (!draft) return;
  draft.budget = $("#builder-budget").value === "" ? NaN : Number($("#builder-budget").value);
  draft.dirty = true;
  renderTeamBuilder();
}

function addTeamBuilderPick(entityId) {
  const state = teamBuilderState(), id = String(entityId), row = state.market.get(id);
  if (!row || state.draft.roster.includes(id)) return;
  const role = marketRole(row);
  if (state.counts[role] >= Number(state.requirements[role] || 0)) return showMessage("#builder-message", `${pretty(role)} is already full. Remove a ${pretty(role).toLowerCase()} before choosing another.`, "warning");
  if (row.credits === null || row.credits === undefined || row.credits === "" || !Number.isFinite(Number(row.credits))) return showMessage("#builder-message", `${row.name} does not have a current credit price.`, "warning");
  const price = Number(row.credits);
  if (state.validBudget && state.used + price > state.budget + 0.001) return showMessage("#builder-message", `${row.name} would put the draft over budget.`, "warning");
  state.draft.roster.push(id); state.draft.dirty = true; renderTeamBuilder();
}

function removeTeamBuilderPick(entityId) {
  const draft = ensureTeamBuilderDraft();
  if (!draft) return;
  draft.roster = draft.roster.filter(id => String(id) !== String(entityId));
  draft.dirty = true; renderTeamBuilder();
}

function clearTeamBuilderDraft() {
  const draft = ensureTeamBuilderDraft();
  if (!draft) return;
  draft.roster = []; draft.dirty = true; renderTeamBuilder();
}

async function saveTeamBuilderDraft({openAICompletion=false}={}) {
  const state = teamBuilderState();
  if (!state.saveable) return renderTeamBuilder();
  const remaining = Math.max(0, Math.round((state.budget - state.used) * 10) / 10);
  const saved = await saveState(
    {roster_entity_ids:[...state.draft.roster], bank_credits:remaining},
    {messageSelector:"#builder-message", successMessage:`Team ${app.activeTeamSlot} saved with ${num(remaining)} credits remaining.`},
  );
  if (!saved) return;
  state.draft.dirty = false;
  state.draft.sourceFingerprint = currentTeamFingerprint();
  renderTeamBuilder();
  if (openAICompletion && !state.complete) {
    chooseOptimizationMode("COMPLETE_ROSTER");
    $("#optimize-bank-credits").value = remaining.toFixed(1);
    showMessage("#optimization-message", `Team ${app.activeTeamSlot} was saved as a partial roster. Review the remaining credits, then select Optimize to let the AI fill every open slot.`, "info");
  }
}

function populateFilters() {
  const preserve = id => $("#"+id)?.value || "";
  const teamValues = {"player-team":preserve("player-team"), "availability-team":preserve("availability-team")};
  const turnValues = {"player-turn":preserve("player-turn"), "availability-turn":preserve("availability-turn"), "builder-turn":preserve("builder-turn")};
  const teams = [...new Set((app.data.players || []).map(teamName).filter(Boolean))].sort();
  const turns = [...new Set((app.data.players || []).map(row => row.turn).filter(value => value !== null && value !== undefined))].sort((a,b)=>a-b);
  Object.keys(teamValues).forEach(id => { $("#"+id).innerHTML = `<option value="">All teams</option>` + teams.map(value => `<option>${esc(value)}</option>`).join(""); $("#"+id).value = teamValues[id]; });
  Object.keys(turnValues).forEach(id => { $("#"+id).innerHTML = `<option value="">All Turns</option>` + turns.map(value => `<option value="${esc(value)}">T${esc(value)}</option>`).join(""); $("#"+id).value = turnValues[id]; });
}

function filteredRows(kind) {
  let rows = [...(app.data.players || [])];
  const availability = kind === "availability";
  const prefix = availability ? "availability" : "player";
  const team = $(`#${prefix}-team`)?.value, position = $(`#${prefix}-position`)?.value, turn = $(`#${prefix}-turn`)?.value;
  if (team) rows = rows.filter(row => teamName(row) === team);
  if (position) rows = rows.filter(row => broadPosition(row.position) === position);
  if (turn) rows = rows.filter(row => String(row.turn) === turn);
  if (availability) {
    const group = $("#availability-group").value;
    if (group) rows = rows.filter(row => row.availability_group === group);
  } else {
    const query = $("#player-query").value.casefold?.() || $("#player-query").value.toLowerCase();
    const group = $("#player-status").value;
    const category = $("#player-category").value;
    if (query) rows = rows.filter(row => String(row.name).toLowerCase().includes(query));
    if (group) rows = rows.filter(row => row.availability_group === group);
    if (category) rows = rows.filter(row => priceCategory(row) === category);
  }
  return rows;
}

const PLAYER_COLUMNS = {
  performance: [
    {key:"credits",label:"Cr",numeric:true},{key:"expected_minutes",label:"xMin",numeric:true,title:"Expected minutes conditional on playing"},
    {key:"expected_fp",label:"xFP",numeric:true,title:"Expected Fantasy points"},{key:"fp_per_minute",label:"FP/min",numeric:true,digits:2,title:"Expected Fantasy points per expected minute"},{key:"p50_fp",label:"P50",numeric:true},
    {key:"recent_form",label:"Recent",numeric:true},{key:"constraint",label:"Pool"},
  ],
  risk: [
    {key:"expected_fp",label:"xFP",numeric:true},{key:"p10_fp",label:"P10",numeric:true,title:"10th percentile"},
    {key:"p50_fp",label:"P50",numeric:true},{key:"p90_fp",label:"P90",numeric:true,title:"90th percentile"},
    {key:"p95_fp",label:"P95",numeric:true},{key:"prob_fp_le_15",label:"Down ≤15",numeric:true,percent:true},
    {key:"prob_fp_ge_30",label:"Up ≥30",numeric:true,percent:true},{key:"constraint",label:"Pool"},
  ],
  value: [
    {key:"credits",label:"Current Cr",numeric:true},{key:"expected_fp",label:"xFP",numeric:true},
    {key:"fp_per_credit",label:"FP/Cr",numeric:true,digits:2},{key:"expected_next_price",label:"Next Cr",numeric:true},
    {key:"expected_credit_change",label:"Δ Cr",numeric:true,signed:true},{key:"probability_increase",label:"P↑",numeric:true,percent:true},
    {key:"constraint",label:"Pool"},
  ],
  minutes: [
    {key:"expected_minutes",label:"xMin",numeric:true},{key:"expected_fp",label:"xFP",numeric:true},{key:"fp_per_minute",label:"FP/min",numeric:true,digits:2},
    {key:"recent_form",label:"Recent FP",numeric:true},{key:"credits",label:"Cr",numeric:true},
    {key:"constraint",label:"Pool"},
  ],
  strategy: [
    {key:"expected_fp",label:"xFP",numeric:true},{key:"p90_fp",label:"P90",numeric:true},
    {key:"prob_fp_le_15",label:"Down ≤15",numeric:true,percent:true},{key:"prob_fp_ge_30",label:"Up ≥30",numeric:true,percent:true},
    {key:"constraint",label:"Pool"},
  ],
};

function renderPlayers() {
  if (!app.data) return;
  let rows = filteredRows("players");
  const view = $("#player-view").value.replaceAll(" ","_");
  if (view === "VALUE_CREDIT_GROWTH") rows = rows.filter(row => row.value_growth_qualified);
  const specs = {BEST_EXPECTED_FP:["expected_fp",-1],BEST_VALUE:["fp_per_credit",-1],MOST_EXPECTED_MINUTES:["expected_minutes",-1],HIGHEST_UPSIDE:["p90_fp",-1],SAFEST:["prob_fp_le_15",1],VALUE_CREDIT_GROWTH:["value_growth_score",-1]};
  const [defaultColumn,defaultDirection] = specs[view] || specs.BEST_EXPECTED_FP;
  const sort = app.sort || {key:defaultColumn,direction:defaultDirection};
  rows.sort((a,b) => compareValues(a[sort.key], b[sort.key], sort.direction, a.name, b.name));
  $("#player-result-count").textContent = `${rows.length} of ${(app.data.players || []).length} players`;
  const columns = PLAYER_COLUMNS[app.playerPreset];
  const common = [
    {key:"name",label:"Player"},{key:"team_id",label:"Team"},{key:"opponent_team_id",label:"Opp"},
    {key:"position",label:"Pos"},{key:"turn",label:"Turn",numeric:true},{key:"availability",label:"Status"},
    {key:"last5_fp_average",label:"L5 FP",numeric:true,title:"Average available Fantasy points in the latest five appearances: EuroLeague, friendlies, tournaments, SuperCups, and included domestic games. Missing FP is omitted, never zero."},
    {key:"last5_minutes_median",label:"L5 MIN",numeric:true,title:"Median available minutes in the same latest five appearances across all included competitions. Missing minutes are omitted, never zero."},
    {key:"last_game_fp",label:"Last FP",numeric:true,title:"Fantasy points from the most recent stored game"},
    {key:"last_game_minutes",label:"Last MIN",numeric:true,title:"Minutes from the most recent stored game"},
    {key:"season_fp_average",label:"Season FP",numeric:true,title:"Average Fantasy points in the latest EuroLeague season with appearances"},
    {key:"season_minutes_average",label:"Season MIN",numeric:true,title:"Average minutes in the latest EuroLeague season with appearances"},
  ];
  const allColumns = [...common, ...columns];
  const table = $("#players-table");
  table.innerHTML = `<caption>Upcoming Matchday players · ${esc(pretty(app.playerPreset))} columns</caption><thead><tr>${allColumns.map(column => `<th class="${column.numeric ? "numeric" : ""}"><button class="sort-button" data-sort="${esc(column.key)}" title="${esc(column.title || `Sort by ${column.label}`)}">${esc(column.label)}${sort.key === column.key ? `<span aria-hidden="true">${sort.direction > 0 ? " ↑" : " ↓"}</span>` : ""}</button></th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map(row => `<tr class="constraint-${String(row.constraint || "normal").toLowerCase()}">${allColumns.map(column => `<td class="${column.numeric ? "numeric" : column.key === "name" ? "name" : ""}">${playerCell(row,column)}</td>`).join("")}</tr>`).join("") : emptyTableRow(allColumns.length, emptyPlayerMessage())}</tbody>`;
  $$("#players-table .sort-button").forEach(button => button.addEventListener("click", () => {
    const key = button.dataset.sort;
    app.sort = {key, direction:app.sort?.key === key ? -app.sort.direction : -1};
    renderPlayers();
  }));
  $$("#players-table .constraint-select").forEach(select => select.addEventListener("change", () => setConstraint(select.dataset.id, select.value, select)));
  $$("#players-table .detail-player").forEach(button => button.addEventListener("click", () => showPlayerDetail(button.dataset.player)));
}

function playerCell(row, column) {
  const value = column.key === "team_id" ? teamName(row) : column.key === "opponent_team_id" ? teamLabel(row.opponent_team_id, row.opponent_team_name) : row[column.key];
  if (column.key === "name") return `<button class="link-button detail-player" data-player="${esc(row.player_id)}" type="button">${esc(row.name)}</button>${row.manual_override ? '<span class="mini-flag">OVERRIDE</span>' : ""}`;
  if (column.key === "availability") return status(row);
  if (column.key === "position") return esc(broadPosition(value));
  if (column.key === "turn") return value == null ? "—" : `T${esc(value)}`;
  if (column.key === "constraint") return `<select class="constraint-select ${value !== "NORMAL" ? "active" : ""}" data-id="${esc(row.entity_id)}" aria-label="Optimization pool state for ${esc(row.name)}"><option value="NORMAL" ${value === "NORMAL" ? "selected" : ""}>NORMAL</option><option value="EXCLUDE" ${value === "EXCLUDE" ? "selected" : ""}>EXCLUDE</option><option value="FORCE_INCLUDE" ${value === "FORCE_INCLUDE" ? "selected" : ""}>FORCE INCLUDE</option></select>`;
  if (column.percent) return pct(value);
  if (column.signed) return signed(value);
  if (column.numeric) return num(value, column.digits ?? 1);
  return esc(value);
}

function resetPlayerFilters() {
  ["player-query","player-team","player-position","player-category","player-turn","player-status"].forEach(id => $("#"+id).value = "");
  app.sort = null; renderPlayers();
}
function resetAvailabilityFilters() {
  ["availability-group","availability-team","availability-position","availability-turn"].forEach(id => $("#"+id).value = "");
  renderAvailability();
}

function renderAvailability() {
  const rows = filteredRows("availability"), table = $("#availability-table");
  const count = 8;
  table.innerHTML = `<caption>Availability source, override, and resolved status · ${rows.length} players</caption><thead><tr><th>Player</th><th>Team / role</th><th>Turn</th><th>Source status</th><th>User override</th><th>Resolved status</th><th>Source / timestamp</th><th>Action</th></tr></thead><tbody>${rows.length ? rows.map(row => `<tr><td class="name">${esc(row.name)}</td><td>${esc(teamName(row))} · ${esc(broadPosition(row.position))}</td><td class="numeric">T${esc(row.turn)}</td><td>${sourceStatus(row)}</td><td>${row.manual_override ? `<span class="status yellow">✎ ${esc(row.manual_override_status)}</span><small>${esc(row.manual_override_note || "No note")}</small>` : `<span class="muted">No override</span>`}</td><td>${status(row)}</td><td>${esc(row.availability_source)}<small>${esc(stamp(row.availability_timestamp))}</small></td><td><button class="tiny edit-override" data-player="${esc(row.player_id)}" type="button">${row.manual_override ? "Edit override" : "Add override"}</button></td></tr>`).join("") : emptyTableRow(count, "No players match the current availability filters.")}</tbody>`;
  $$(".edit-override").forEach(button => button.addEventListener("click", () => openOverride(button.dataset.player)));
}

function sourceStatus(row) {
  const presentation = availabilityClass(row.source_availability_status);
  return `<span class="status ${presentation.cls}">${presentation.icon} ${esc(row.source_availability_status || "UNKNOWN")}</span>`;
}

function openOverride(playerId) {
  const row = app.data.players.find(item => item.player_id === playerId);
  if (!row) return;
  app.overridePlayer = row;
  $("#override-context").innerHTML = `<div><span>Source</span>${sourceStatus(row)}</div><div><span>User override</span><strong>${esc(row.manual_override_status || "NONE")}</strong></div><div><span>Resolved</span>${status(row)}</div>`;
  $("#override-decision").value = row.manual_override_status || "UNKNOWN";
  $("#override-note").value = row.manual_override_note || "";
  $("#override-clear").classList.toggle("hidden", !row.manual_override);
  $("#override-dialog").showModal();
  $("#override-decision").focus();
}

async function submitOverride(event) {
  event.preventDefault();
  const row = app.overridePlayer;
  if (!row || isBusy("override")) return;
  setBusy("override", true);
  try {
    await api("/api/override", {method:"POST", body:JSON.stringify({player_id:row.player_id, decision:$("#override-decision").value, note:$("#override-note").value.trim() || null})});
    $("#override-dialog").close();
    invalidateAnalysis("Availability changed after the displayed prediction.", {clearLineup:true});
    await load({acceptLatest:false});
    toast("Manual availability decision saved. Refresh predictions before optimizing.", "success");
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy("override", false); }
}

async function clearOverrideFromDialog() {
  const row = app.overridePlayer;
  if (!row || isBusy("override")) return;
  setBusy("override", true);
  try {
    await api("/api/override", {method:"POST", body:JSON.stringify({player_id:row.player_id, clear:true})});
    $("#override-dialog").close();
    invalidateAnalysis("Availability override was cleared.", {clearLineup:true});
    await load({acceptLatest:false}); toast("Manual override cleared.", "success");
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy("override", false); }
}

async function setConstraint(entity, constraint, select) {
  const row = app.data.players.find(player => player.entity_id === entity);
  if (constraint === "FORCE_INCLUDE" && row?.availability_group === "RED") {
    const allowed = window.confirm(`${row.name} is resolved ${row.availability}. FORCE INCLUDE may make every legal roster impossible. Continue?`);
    if (!allowed) { select.value = row.constraint || "NORMAL"; return; }
  }
  if (isBusy("constraint")) { select.value = row?.constraint || "NORMAL"; return; }
  setBusy("constraint", true);
  try {
    const result = await api("/api/constraint", {method:"POST", body:JSON.stringify({entity_id:entity,constraint})});
    applyTeamContext(result);
    invalidateAnalysis("Optimization pool constraints changed.", {clearLineup:true});
    renderAll();
    if (result.warnings?.length) toast(result.warnings.join(" "), "warning"); else toast("Optimization pool updated.", "success");
  } catch (error) { toast(error.message, "error"); renderPlayers(); }
  finally { setBusy("constraint", false); }
}

function renderConstraints() {
  const values = Object.entries(app.data.state.player_constraints || {});
  $("#active-constraints").innerHTML = values.length ? `<strong>Manual pool constraints</strong>${values.map(([id,value]) => `<span class="constraint ${value.toLowerCase()}">${esc(pretty(value))} · ${esc(nameFor(id))}</span>`).join("")}` : `<span class="constraint">No force-includes or exclusions</span>`;
}

async function refreshLive() {
  if (isBusy("refresh")) return;
  const button = $("#refresh-button"); setBusy("refresh", true, button, "Updating…");
  button.setAttribute("aria-busy", "true");
  showMessage("#refresh-message", "Refresh started. The last valid snapshot remains visible while sources update.", "info");
  const progress = $("#refresh-progress");
  const pending = ["Connect to supported sources", "Refresh schedule, results, stats and market", "Resolve availability and rebuild slate", "Check gates and predictions", "Attach completed outcomes"];
  let progressIndex = 0;
  const drawPending = () => { progress.classList.remove("hidden"); progress.innerHTML = pending.map((name,index) => `<div class="progress-step ${index < progressIndex ? "done" : index === progressIndex ? "active" : "pending"}"><span>${index < progressIndex ? "✓" : index === progressIndex ? "●" : "○"}</span>${esc(name)}<small>${index === progressIndex ? "In progress" : index < progressIndex ? "Completed" : "Waiting"}</small></div>`).join(""); };
  drawPending();
  const timer = window.setInterval(() => { progressIndex = Math.min(progressIndex + 1, pending.length - 1); drawPending(); }, 1100);
  try {
    const result = await api("/api/refresh", {method:"POST",body:"{}"});
    window.clearInterval(timer); renderRefreshResult(result);
    invalidateAnalysis("Live snapshots changed. Re-optimize before acting.");
    await load({acceptLatest:false});
    if (result.status === "FAILED") showMessage("#refresh-message", result.message, "error");
    else if (result.status === "PARTIAL") showMessage("#refresh-message", result.message, "warning");
    else if (result.status === "ALREADY_RUNNING") showMessage("#refresh-message", result.message, "warning");
    else showMessage("#refresh-message", result.message || "Refresh complete.", "success");
  } catch (error) {
    window.clearInterval(timer);
    showMessage("#refresh-message", `${error.message} Last valid values remain displayed.`, "error");
    progress.innerHTML = `<div class="progress-step failed"><span>✕</span>Refresh request failed<small>Retry when the local service is available.</small></div>`;
    await load({acceptLatest:false});
  } finally {
    setBusy("refresh", false, button, "Update latest data"); button.removeAttribute("aria-busy");
  }
}

function renderRefreshResult(result) {
  const progress = $("#refresh-progress"); progress.classList.remove("hidden");
  progress.innerHTML = (result.steps || []).map(step => `<div class="progress-step ${String(step.status).toLowerCase()}"><span>${step.status === "SUCCEEDED" ? "✓" : step.status === "SKIPPED" ? "○" : "!"}</span>${esc(pretty(step.name))}<small>${esc(step.detail || step.status)}</small></div>`).join("") || `<div class="progress-step warning"><span>!</span>No per-stage detail returned<small>${esc(result.status)}</small></div>`;
  const changes = result.changes || {}, strip = $("#refresh-changes"); strip.classList.remove("hidden");
  strip.innerHTML = Object.entries(changes).map(([name,count]) => `<div><span>${esc(pretty(name))}</span><strong>${count == null ? "—" : esc(count)}</strong></div>`).join("");
}

async function optimize() {
  if (isBusy("optimize")) return;
  const mode = optimizationMode();
  const body = {mode};
  if (mode === "BUILD_NEW") {
    const budget = Number($("#total-budget").value);
    if (!Number.isFinite(budget) || budget < 0) return showMessage("#optimization-message", "Total budget must be zero or greater.", "error");
    body.total_budget = budget;
  } else {
    const bank = Number($("#optimize-bank-credits").value);
    if (!Number.isFinite(bank) || bank < 0) return showMessage("#optimization-message", "Remaining credits must be zero or greater.", "error");
    body.bank_credits = bank;
    if (mode === "COMPLETE_ROSTER") {
      body.max_changes = Math.max(1, app.data.current_team?.slots_to_fill || 0);
    } else {
      const changes = Number($("#maximum-changes").value);
      if (!Number.isInteger(changes) || changes < 0 || changes > 11) return showMessage("#optimization-message", "Maximum changes must be a whole number from 0 to 11.", "error");
      body.max_changes = changes;
    }
  }
  const button = $("#optimize-button"); setBusy("optimize", true, button, "Simulating legal teams…");
  showMessage("#optimization-message", "Checking current predictions, then running constrained roster generation and frozen Phase 7 simulation…", "info");
  try {
    const result = await api("/api/optimize", {method:"POST",body:JSON.stringify(body)});
    app.recommendation = result; app.analysisStale = false;
    if (result.status === "SUCCEEDED") {
      clearScopedLineup(); renderRecommendations(result);
      const availabilityNote = result.availability_assumption_policy === "PLAY_ALL_UNRESOLVED"
        ? " Players with no resolved availability evidence were treated as playing for this shared prediction snapshot; explicit OUT decisions stayed excluded."
        : "";
      showMessage("#optimization-message", `${result.prediction_generated_on_demand ? "Current predictions generated automatically. " : ""}Saved reproducible run ${result.control_center_run_id || "with current fingerprints"}.${availabilityNote}`, "success");
    } else {
      renderRecommendations(result);
      showMessage("#optimization-message", (result.blocked_reasons || ["No legal roster is available."]).join(" · "), "error");
    }
    $("#stale-recommendation").classList.add("hidden");
  } catch (error) { showMessage("#optimization-message", humanOptimizerError(error.message), "error"); }
  finally {
    setBusy("optimize", false, button, app.analysisStale ? "Re-optimize" : "Optimize");
    const finalMode = optimizationMode();
    const currentBlocked = finalMode === "COMPLETE_ROSTER"
      ? !(app.data.current_team?.valid_partial ?? app.data.current_team?.complete)
      : finalMode === "CURRENT_TEAM" && !app.data.current_team?.complete;
    button.disabled = Boolean(app.data.dashboard.optimization_blocked || currentBlocked);
  }
}

function renderRecommendationsState() {
  $("#stale-recommendation").classList.toggle("hidden", !app.analysisStale);
  if (app.analysisStale) {
    $("#recommendation-cards").className = "recommendation-grid empty-state";
    $("#recommendation-cards").innerHTML = `<div><strong>Recommendation is out of date.</strong><p>Roster, availability, market, or pool inputs changed. Run optimization again.</p></div>`;
    $("#alternatives-panel").classList.add("hidden");
    return;
  }
  if (app.recommendation) return renderRecommendations(app.recommendation);
  const d = app.data.dashboard;
  const message = !d.fantasy_players ? "The current Fantasy market is not available yet." : !d.predictions_current ? "Predictions are missing or stale; Optimize will generate the current snapshot automatically." : "Run optimization to compare legal strategies.";
  $("#recommendation-cards").className = "recommendation-grid empty-state";
  $("#recommendation-cards").textContent = message;
}

function recommendationPositionClass(position) {
  return broadPosition(position).toLowerCase().replace(/[^a-z]+/g, "-");
}

function recommendationForm(player) {
  const games = (player.last5_games || []).slice(0, 5);
  if (!games.length) return `<span class="recommendation-form-empty">No recent games</span>`;
  return `<div class="recommendation-form" aria-label="Newest five Fantasy scores">${games.map(game => {
    const preparation = game.game_type && game.game_type !== "EUROLEAGUE";
    const title = `${shortDate(game.game_date)} · ${preparationTypeLabel(game.game_type || "EUROLEAGUE")} · ${num(game.minutes)} min`;
    return `<span class="${preparation ? "preparation" : "official"}" title="${esc(title)}">${num(game.fantasy_points)}${preparation ? " P" : ""}</span>`;
  }).join("")}</div>`;
}

function lastFiveGameEvidence(player) {
  const games = (player.last5_games || []).slice(0, 5);
  if (!games.length) return `<p class="last-five-empty">No recent game evidence is available.</p>`;
  return `<div class="last-five-games" aria-label="Last five Fantasy points and minutes">${games.map(game => {
    const preparation = game.game_type && game.game_type !== "EUROLEAGUE";
    return `<span class="${preparation ? "preparation" : "official"}" title="${esc(preparationTypeLabel(game.game_type || "EUROLEAGUE"))}"><b>${shortDate(game.game_date)}</b><strong>${num(game.fantasy_points)} FP</strong><small>${num(game.minutes)} MIN${preparation ? " · P" : ""}</small></span>`;
  }).join("")}</div>`;
}

function recommendationPlayerRow(player, transferClass="") {
  const isCoach = String(player.entity_type || player.position || "").toUpperCase() === "COACH";
  if (isCoach) return `<article class="recommendation-player coach ${transferClass}"><div class="recommendation-player-name"><strong>${esc(player.name)}</strong><small>${esc(teamName(player))} · T${esc(player.turn ?? "—")} · ${num(player.credits)} cr</small></div><div class="recommendation-player-metrics coach-metrics"><span><b>xFP</b>${num(player.expected_score)}</span></div></article>`;
  return `<article class="recommendation-player ${recommendationPositionClass(player.position)} ${transferClass}">
    <div class="recommendation-player-name"><strong>${player.captain ? "© " : ""}${esc(player.name)}</strong><small>${esc(teamName(player))} · T${esc(player.turn ?? "—")} · ${esc(broadPosition(player.position))} · ${num(player.credits)} cr${player.role ? ` · ${esc(pretty(player.role))}` : ""}</small>${recommendationForm(player)}</div>
    <div class="recommendation-player-metrics"><span><b>xFP</b>${num(player.expected_fp)}</span><span><b>xMIN</b>${num(player.expected_minutes)}</span><span><b>L5 FP AVG</b>${num(player.last5_fp_average)}</span><span><b>L5 MIN MED</b>${num(player.last5_minutes_median)}</span></div>
  </article>`;
}

function recommendationRoster(rec) {
  const players = rec.players || [];
  const turnCounts = Object.entries(players.reduce((counts, player) => { const turn = `T${player.turn ?? "—"}`; counts[turn] = (counts[turn] || 0) + 1; return counts; }, {})).sort(([a],[b]) => a.localeCompare(b)).map(([turn,count]) => `${turn}: ${count}`).join(" · ");
  const groups = ["GUARD", "FORWARD", "CENTER"].map(position => {
    const rows = players.filter(player => broadPosition(player.position) === position).sort((a,b) => Number(a.turn ?? 99) - Number(b.turn ?? 99) || String(a.name).localeCompare(String(b.name)));
    return `<section class="recommendation-position ${position.toLowerCase()}"><div class="recommendation-position-heading"><strong>${position}</strong><span>${rows.length}</span></div>${rows.map(player => recommendationPlayerRow(player)).join("") || `<p>No ${esc(position.toLowerCase())}s</p>`}</section>`;
  }).join("");
  return `<div class="recommendation-roster"><div class="recommendation-roster-heading"><strong>Projected roster</strong><span>${esc(turnCounts)} · P marks a preparation-game FP</span></div><div class="recommendation-position-grid">${groups}</div>${recommendationPlayerRow({...rec.coach, entity_type:"COACH"})}</div>`;
}

function recommendationRotation(rec) {
  const options = rec.rotation_options || [];
  if (!options.length) return "";
  return `<div class="strategy-rule"><strong>Later-Turn rotation kept available</strong><span>${options.map(option => `${esc(option.name)} (T${esc(option.turn)}) can replace ${option.can_replace.map(player => esc(player.name)).join(" or ")} after T1`).join(" · ")}</span></div>`;
}

function recommendationTransfers(rec) {
  const column = (label, items, cssClass) => `<section class="transfer-column ${cssClass}"><h4>${label}</h4>${items.length ? items.map(player => recommendationPlayerRow(player, cssClass)).join("") : `<p>None</p>`}</section>`;
  return `<div class="transfer-players">${column("OUT", rec.players_out || [], "outgoing")}${column("IN", rec.players_in || [], "incoming")}</div>`;
}

function renderRecommendations(result) {
  if (!result || result.status !== "SUCCEEDED") {
    $("#recommendation-cards").className = "recommendation-grid empty-state";
    $("#recommendation-cards").textContent = "Optimization is blocked. Resolve the readiness and roster errors shown above.";
    return;
  }
  const recommendations = result.recommendations || [], best = recommendations[0] || {};
  const selectedMode = result.optimization_mode || optimizationMode();
  const transferMode = selectedMode === "CURRENT_TEAM";
  const showFullRoster = true;
  const cards = $("#recommendation-cards"); cards.className = "recommendation-grid";
  cards.innerHTML = recommendations.map((rec,index) => {
    const captain = rec.players?.find(player => player.captain) || rec.players?.find(player => player.player_id === rec.captain);
    const formation = formationFor(rec.starting_five, rec.players);
    const meanDelta = index ? Number(rec.expected_final_score) - Number(best.expected_final_score) : 0;
    const p10Delta = index ? Number(rec.p10_team_score) - Number(best.p10_team_score) : 0;
    const p90Delta = index ? Number(rec.p90_team_score) - Number(best.p90_team_score) : 0;
    const accounting = rec.credit_accounting || {};
    const action = transferMode
      ? `<button class="primary apply-recommendation" data-index="${index}" type="button">Apply Recommendation</button><button class="ghost use-current-team" data-index="${index}" type="button">Use Result as Current Team</button>`
      : `<button class="primary use-current-team" data-index="${index}" type="button">Use as Current Team</button>`;
    return `<article class="recommendation-card ${index === 0 ? "best" : rec.label.includes("SAFER") ? "safer" : "upside"}">
      <div class="recommendation-heading"><div><p class="eyebrow">${esc(pretty(rec.label))}</p><h3>${esc(rec.major_characteristic)}</h3></div>${index === 0 ? '<span class="choice-badge">TOP VALUE</span>' : `<span class="delta-chip">Mean ${signed(meanDelta)}</span>`}</div>
      <div class="scoreline"><div><span title="Expected final Fantasy score">MEAN</span><strong>${num(rec.expected_final_score)}</strong></div><div><span title="10th percentile final team score">P10</span><strong>${num(rec.p10_team_score)}</strong><small>${index ? signed(p10Delta) : "baseline"}</small></div><div><span title="Median final team score">P50</span><strong>${num(rec.p50_team_score)}</strong></div><div><span title="90th percentile final team score">P90</span><strong>${num(rec.p90_team_score)}</strong><small>${index ? signed(p90Delta) : "baseline"}</small></div></div>
      <div class="recommendation-facts"><span><b>Captain</b>${esc(captain?.name || nameForPlayer(rec.captain))}</span><span><b>Formation</b>${esc(formation)}</span><span><b>Credits</b>${num(rec.credits_used)}</span><span><b>Changes</b>${esc(rec.transfers_required)}</span></div>
      ${recommendationRotation(rec)}
      ${showFullRoster ? recommendationRoster(rec) : ""}
      ${recommendationTransfers(rec)}
      <div class="transfer-summary"><div><strong>CREDIT IMPACT</strong>Before ${num(accounting.credits_before)} · +${num(accounting.credits_received)} received · −${num(accounting.credits_spent)} spent · ${num(accounting.credits_remaining)} remaining</div><div><strong>FP IMPROVEMENT</strong>${num(rec.predicted_team_fp_before)} before → ${num(rec.predicted_team_fp_after)} after · ${signed(rec.predicted_improvement)}</div></div>
      <div class="recommendation-actions">${action}</div>
    </article>`;
  }).join("");
  $$(".use-current-team").forEach(button => button.addEventListener("click", () => useRecommendationAsCurrentTeam(Number(button.dataset.index), "loaded")));
  $$(".apply-recommendation").forEach(button => button.addEventListener("click", () => useRecommendationAsCurrentTeam(Number(button.dataset.index), "applied")));
  const panel = $("#alternatives-panel"), alternatives = result.player_alternatives || [];
  panel.classList.toggle("hidden", !alternatives.length);
  panel.innerHTML = alternatives.length ? `<div class="panel-heading"><div><h3>Re-optimized player alternatives</h3><span>Each alternative rebuilds the full legal roster.</span></div></div><div class="alternative-grid">${alternatives.map(item => `<div class="alternative-row"><div><span class="choice-badge ${item.alternative_type === "SAFER" ? "safe" : "risk"}">${esc(pretty(item.alternative_type))}</span><strong>${esc(item.player.name)}</strong><small>instead of ${esc(item.replaces.name)} · T${esc(item.turn)}</small></div><div><span>Credits ${signed(item.credits_difference)}</span><span>Mean ${signed(item.expected_final_team_fp_difference)}</span><span>P10 ${signed(item.p10_difference)}</span><span>P90 ${signed(item.p90_difference)}</span></div></div>`).join("")}</div>` : "";
  renderStrategy(result.strategy);
}

async function useRecommendationAsCurrentTeam(index, verb) {
  const rec = app.recommendation?.recommendations?.[index];
  if (!rec || isBusy("team")) return;
  const shadowId = app.recommendation?.shadow?.shadow_snapshot_id || app.data.latest_shadow_snapshot?.shadow_snapshot_id;
  const selectedLineup = {
    player_ids:(rec.players || []).map(player => String(player.player_id)),
    coach_id:String(rec.coach?.coach_id || ""),
    starters:(rec.starting_five || []).map(String),
    sixth_man:String(rec.sixth_man || ""),
    captain:String(rec.captain || ""),
  };
  const roster = [...(rec.players || []).map(player => String(player.entity_id)), String(rec.coach?.entity_id || "")].filter(Boolean);
  if (roster.length !== 11 || new Set(roster).size !== 11) return toast("This recommendation does not contain a complete unique roster.", "error");
  const remaining = Number(rec.credit_accounting?.credits_remaining);
  const change = {roster_entity_ids:roster};
  if (Number.isFinite(remaining) && remaining >= 0) change.bank_credits = remaining;
  setBusy("team", true);
  try {
    await api("/api/team", {method:"POST",body:JSON.stringify(change)});
    invalidateAnalysis("Current Team now uses the selected recommendation.", {clearLineup:true});
    await load({acceptLatest:false});
    if (shadowId && app.data.latest_shadow_snapshot?.shadow_snapshot_id === shadowId) {
      localStorage.setItem(scopedLineupKey(), JSON.stringify({shadow_snapshot_id:shadowId,lineup:selectedLineup}));
    }
    activateTab("team");
    toast(`Recommendation ${verb} as Current Team.`, "success");
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy("team", false); }
}

async function analyzeCurrentTeam() {
  if (isBusy("team-strategy")) return;
  const button = $("#analyze-team-button"), root = $("#current-team-strategy");
  setBusy("team-strategy", true, button, "Analyzing roster…");
  root.className = "empty-state";
  root.innerHTML = `<div class="dialog-loading">Analyzing Current Team prices, predictions, locks, and empty slots…</div>`;
  try {
    app.teamStrategy = await api("/api/strategy/current");
    renderCurrentTeamStrategy(app.teamStrategy);
  } catch (error) {
    root.innerHTML = `<strong>Strategy unavailable</strong><p>${esc(error.message)}</p>`;
  } finally {
    setBusy("team-strategy", false, button, "Analyze Current Team");
  }
}

function renderCurrentTeamStrategy(strategy) {
  const root = $("#current-team-strategy"), team = strategy.current_team || {};
  const actions = strategy.actions || [], missing = team.missing_positions || {};
  const selected = team.roster_size || 0;
  const actionCards = actions.map(item => {
    const alternative = item.alternative;
    const downgrade = item.downgrade;
    const detail = item.locked
      ? "Locked in Current Team"
      : alternative && item.recommendation !== "KEEP"
        ? `${esc(pretty(item.recommendation))}: ${esc(item.name)} → ${esc(alternative.name)} · ${signed((alternative.expected_fp ?? 0) - (item.expected_fp ?? 0))} FP · ${signed((alternative.credits ?? 0) - (item.credits ?? 0))} cr`
        : "Keep based on the current affordable alternatives";
    const release = downgrade && !item.locked
      ? `<small>Downgrade option: ${esc(downgrade.name)} frees ${num((item.credits ?? 0) - (downgrade.credits ?? 0))} cr with xFP ${num(downgrade.expected_fp)}</small>` : "";
    const name = item.player_id
      ? `<button class="link-button detail-player" data-player="${esc(item.player_id)}" type="button">${esc(item.name)}</button>`
      : `<strong>${esc(item.name)}</strong>`;
    return `<article class="strategy-action ${item.locked ? "locked" : ""}"><div>${name}<span>${esc(item.team_name)} · ${esc(item.position)} · ${num(item.credits)} cr · xMin ${num(item.expected_minutes)} · xFP ${num(item.expected_fp)} · L5 FP ${num(item.last5_fp_average)} · L5 MIN ${num(item.last5_minutes_median)}</span></div><b>${esc(pretty(item.recommendation))}</b><p>${detail}${alternative ? ` · In: xMin ${num(alternative.expected_minutes)} · xFP ${num(alternative.expected_fp)} · L5 FP ${num(alternative.last5_fp_average)} · L5 MIN ${num(alternative.last5_minutes_median)}` : ""}</p>${release}</article>`;
  }).join("");
  const fills = Object.entries(missing).map(([position,count]) => {
    const candidates = strategy.fill_candidates?.[position] || [];
    return `<div class="strategy-rule"><strong>Fill ${count} ${esc(pretty(position))} slot${count === 1 ? "" : "s"}</strong><span>${candidates.slice(0,3).map(row => `${esc(row.name)} (${num(row.credits)} cr, xFP ${num(row.expected_fp)}, L5 ${num(row.last5_fp_average)} FP / ${num(row.last5_minutes_median)} MIN)`).join(" · ") || "No predicted candidate available"}</span></div>`;
  }).join("");
  root.className = "team-strategy-view";
  root.innerHTML = `<div class="strategy-summary panel"><div><p class="eyebrow">SOURCE OF TRUTH</p><h3>Current Team · ${selected}/11 selected</h3><p>${esc(strategy.season)} · Matchday ${esc(strategy.matchday)} · ${num(strategy.bank_credits)} bank credits</p></div><div class="strategy-position-summary"><span><b>Strong position</b>${esc(pretty(strategy.strong_position || "Not enough evidence"))}</span><span><b>Weak position</b>${esc(pretty(strategy.weak_position || "Not enough evidence"))}</span><span><b>Locked</b>${actions.filter(row => row.locked).length}</span><span><b>Empty</b>${team.slots_to_fill || 0}</span></div></div>${fills ? `<section class="panel"><div class="panel-heading"><div><h3>Empty positions</h3><span>Complete My Roster will optimize these slots while preserving every selection.</span></div></div>${fills}</section>` : ""}<section class="strategy-actions">${actionCards || `<div class="empty-state">Add players in Current Team to receive roster-relative guidance.</div>`}</section>`;
  $$("#current-team-strategy .detail-player").forEach(button => button.addEventListener("click", () => showPlayerDetail(button.dataset.player)));
}

function renderStrategy(strategy) {
  const root = $("#strategy-content");
  if (!strategy) { root.className = "empty-state"; root.textContent = "Pre-Matchday strategy appears after a successful optimization."; return; }
  root.className = "strategy-grid";
  root.innerHTML = `<section class="panel"><p class="eyebrow">CAPTAIN PATH</p><h3>${esc(nameForPlayer(strategy.initial_captain))}</h3><p>Later alternative: <strong>${esc(nameForPlayer(strategy.best_later_captain_alternative))}</strong></p><p class="reason">${esc(strategy.instruction)}</p></section><section class="panel"><p class="eyebrow">USEFUL EARLY UPSIDE</p>${(strategy.useful_early_upside || []).map(player => `<div class="strategy-rule"><strong>${esc(player.name)}</strong><span>P90 ${num(player.p90)} with later legal recourse</span></div>`).join("") || emptyInline("None identified for this roster.")}</section><section class="panel"><p class="eyebrow">COMPUTED DECISION RULES</p>${(strategy.decision_rules || []).map(rule => `<div class="strategy-rule"><strong>If ${esc(rule.player)} scores below ${num(rule.threshold)}</strong><span>${esc(rule.action_below_threshold)}</span><small>${esc(rule.derivation)}</small></div>`).join("") || emptyInline("No legal replacement boundary exists.")}</section><section class="panel"><p class="eyebrow">DOWNSIDE PROTECTION</p><p><b>Protected:</b> ${(strategy.downside_protected_players || []).map(nameForPlayer).map(esc).join(", ") || "None"}</p><p><b>No later safety net:</b> ${(strategy.players_without_later_safety_net || []).map(player => esc(player.name)).join(", ") || "None"}</p></section>`;
}

async function loadHistory(section=app.history.section, explicitFilters=null) {
  if (isBusy("history")) return;
  const switched = section !== app.history.section;
  const filters = explicitFilters || (switched ? {} : collectHistoryFilters());
  if (["games","players","teams"].includes(section) && !("season" in filters)) {
    filters.season = app.data?.dashboard?.current_season || app.data?.state?.season_code || "";
  }
  app.history.section = section;
  $$(".history-tab").forEach(button => button.classList.toggle("active", button.dataset.historySection === section));
  const root = $("#history-content");
  root.className = "empty-state";
  root.innerHTML = `<div class="dialog-loading">Loading ${esc(pretty(section))}…</div>`;
  setBusy("history", true);
  try {
    const params = new URLSearchParams({section, ...filters});
    const payload = await api(`/api/history?${params}`);
    app.history = {...app.history, ...payload, filters, loaded:true};
    renderHistoryFilters(section, payload.catalog || {}, filters);
    renderHistory(payload);
    $("#history-message").classList.add("hidden");
  } catch (error) {
    showMessage("#history-message", error.message, "error");
    root.innerHTML = `<strong>History could not be loaded.</strong><p>Try the filters again.</p>`;
  } finally { setBusy("history", false); }
}

function collectHistoryFilters() {
  const result = {};
  $$("#history-filters [data-history-filter]").forEach(input => {
    if (input.value !== "" || input.dataset.historyFilter === "season") {
      result[input.dataset.historyFilter] = input.value;
    }
  });
  return result;
}

function historyOptions(rows, valueKey, labelKey, selected, firstLabel) {
  return `<option value="">${esc(firstLabel)}</option>` + (rows || []).map(row =>
    `<option value="${esc(row[valueKey])}" ${String(row[valueKey]) === String(selected || "") ? "selected" : ""}>${esc(row[labelKey])}</option>`
  ).join("");
}

function renderHistoryFilters(section, catalog, values) {
  const root = $("#history-filters");
  if (section === "overview") { root.classList.add("hidden"); root.innerHTML = ""; return; }
  const seasons = historyOptions(catalog.seasons, "season_code", "season_code", values.season, "All seasons");
  const teams = historyOptions(catalog.teams, "team_id", "team_name", values.team_id, "All teams");
  const preseasonTeams = historyOptions(catalog.preseason_teams || [], "team_id", "team_name", values.team_id, "All EuroLeague teams");
  const players = historyOptions(catalog.players, "player_id", "player_name", values.player_id, "All players");
  const common = `<label>Season<select data-history-filter="season">${seasons}</select></label><label>Team<select data-history-filter="team_id">${teams}</select></label>`;
  if (section === "preseason") {
    const types = (catalog.preparation_game_types || []).map(value => `<option value="${esc(value)}" ${value === values.competition ? "selected" : ""}>${esc(preparationTypeLabel(value))}</option>`).join("");
    root.innerHTML = `<label>Season<select disabled><option>2026 Preseason</option></select></label><label>EuroLeague team<select data-history-filter="team_id">${preseasonTeams}</select></label><label>Player<select data-history-filter="player_id">${players}</select></label><label>From<input data-history-filter="date_from" type="date" value="${esc(values.date_from || "")}"></label><label>To<input data-history-filter="date_to" type="date" value="${esc(values.date_to || "")}"></label><label>Game type<select data-history-filter="competition"><option value="">All</option>${types}</select></label><label>Game search<input data-history-filter="query" type="search" value="${esc(values.query || "")}" placeholder="Team or game ID"></label><button class="primary history-apply" type="button">Apply filters</button>`;
  } else if (section === "games") {
    root.innerHTML = `${common}<label>Player<select data-history-filter="player_id">${players}</select></label><label>Round<input data-history-filter="round" type="number" min="1" value="${esc(values.round || "")}"></label><label>From<input data-history-filter="date_from" type="date" value="${esc(values.date_from || "")}"></label><label>To<input data-history-filter="date_to" type="date" value="${esc(values.date_to || "")}"></label><label>Competition<select data-history-filter="competition"><option value="">All</option>${(catalog.competitions || []).map(value => `<option ${value === values.competition ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></label><label>Game search<input data-history-filter="query" type="search" value="${esc(values.query || "")}" placeholder="Team or game code"></label><button class="primary history-apply" type="button">Apply filters</button>`;
  } else if (section === "players") {
    const singleRound = values.round !== undefined && values.round !== null && values.round !== "";
    const roundValues = [...new Set((catalog.rounds || [])
      .filter(row => !values.season || String(row.season_code) === String(values.season))
      .map(row => Number(row.round_number)).filter(Number.isFinite))].sort((a,b) => a-b);
    const rounds = `<option value="">All rounds</option>` + roundValues.map(round => `<option value="${round}" ${String(round) === String(values.round || "") ? "selected" : ""}>Round ${round}</option>`).join("");
    const toRound = {fantasy_points_avg:"fantasy_points",minutes_avg:"minutes",points_avg:"points",rebounds_avg:"rebounds",assists_avg:"assists",steals_avg:"steals",blocks_avg:"blocks",blocks_received_avg:"blocks_received",turnovers_avg:"turnovers",fouls_committed_avg:"fouls_committed",fouls_drawn_avg:"fouls_drawn",free_throws_made_avg:"free_throws_made",credits_latest:"credits_after",pir_avg:"pir"};
    const toAverage = Object.fromEntries(Object.entries(toRound).map(([average,round]) => [round,average]));
    const currentSort = app.history.playerSort || {key:"fantasy_points_avg",direction:"desc"};
    currentSort.key = singleRound ? (toRound[currentSort.key] || currentSort.key) : (toAverage[currentSort.key] || currentSort.key);
    app.history.playerSort = currentSort;
    const sortOptions = singleRound
      ? `<option value="fantasy_points">Fantasy points</option><option value="credits_start">Starting credits</option><option value="credits_after">Credits after round</option><option value="credits_change">Credit change</option><option value="minutes">Minutes</option><option value="points">Points</option><option value="rebounds">Rebounds</option><option value="assists">Assists</option><option value="steals">Steals</option><option value="blocks">Blocks</option><option value="blocks_received">Blocks received</option><option value="turnovers">Turnovers</option><option value="fouls_committed">Personal fouls</option><option value="fouls_drawn">Fouls won</option><option value="free_throws_made">Free throws made</option><option value="pir">PIR</option>`
      : `<option value="fantasy_points_avg">Average FP</option><option value="credits_start">Starting credits</option><option value="credits_latest">Latest credits</option><option value="credits_change">Total credit change</option><option value="minutes_avg">Average minutes</option><option value="points_avg">Average points</option><option value="rebounds_avg">Average rebounds</option><option value="assists_avg">Average assists</option><option value="steals_avg">Average steals</option><option value="blocks_avg">Average blocks</option><option value="blocks_received_avg">Average blocks received</option><option value="turnovers_avg">Average turnovers</option><option value="fouls_committed_avg">Average personal fouls</option><option value="fouls_drawn_avg">Average fouls won</option><option value="free_throws_made_avg">Average free throws made</option><option value="pir_avg">Average PIR</option>`;
    root.innerHTML = `${common}<label>Round<select data-history-filter="round">${rounds}</select></label><label>${singleRound ? "Minimum minutes" : "Minimum average minutes"}<input data-history-filter="min_minutes" type="number" min="0" max="100" step="1" value="${esc(values.min_minutes || "")}" placeholder="Any"></label><label class="grow">Player search<input data-history-filter="query" type="search" value="${esc(values.query || "")}" placeholder="Player name"></label><label>Arrange by<select id="history-player-sort">${sortOptions}<option value="games_played">Games played</option><option value="player">Player name</option></select></label><label>Order<select id="history-player-order"><option value="desc">High to low</option><option value="asc">Low to high</option></select></label><button class="primary history-apply" type="button">Apply filters</button>`;
    $("#history-player-sort").value = currentSort.key;
    $("#history-player-order").value = currentSort.direction;
  } else {
    root.innerHTML = `${common}<button class="primary history-apply" type="button">Apply filters</button>`;
  }
  root.classList.remove("hidden");
  $("#history-filters .history-apply")?.addEventListener("click", () => loadHistory(section, collectHistoryFilters()));
  $$("#history-filters input").forEach(input => input.addEventListener("keydown", event => {
    if (event.key === "Enter") loadHistory(section, collectHistoryFilters());
  }));
  ["history-player-sort","history-player-order"].forEach(id => $("#"+id)?.addEventListener("change", () => {
    app.history.playerSort = {
      key:$("#history-player-sort").value,
      direction:$("#history-player-order").value,
    };
    renderHistory({section:"players",data:app.history.data || []});
  }));
}

function sortedHistoryPlayers(rows) {
  const sort = app.history.playerSort || {key:"fantasy_points_avg",direction:"desc"};
  const direction = sort.direction === "asc" ? 1 : -1;
  return [...(rows || [])].sort((left,right) => {
    const a = left[sort.key], b = right[sort.key];
    if (a == null && b == null) return String(left.player).localeCompare(String(right.player));
    if (a == null) return 1;
    if (b == null) return -1;
    const comparison = sort.key === "player"
      ? String(a).localeCompare(String(b))
      : Number(a) - Number(b);
    return comparison * direction || String(left.player).localeCompare(String(right.player));
  });
}

function historyCreditChange(row) {
  if (row.credits_change == null) return "—";
  const pending = row.credits_change_includes_last_round === false ? "*" : "";
  const title = pending ? "Latest round change awaits the next pre-round market snapshot." : "Verified through the selected final round.";
  return `<span title="${esc(title)}">${signed(row.credits_change)}${pending}</span>`;
}

function renderHistory(payload) {
  const root = $("#history-content"), section = payload.section, data = payload.data;
  if (section === "overview") {
    const totals = data.totals || {};
    root.className = "history-view";
    root.innerHTML = `<div class="history-metrics"><div><span>Games</span><strong>${esc(totals.games || 0)}</strong></div><div><span>Completed</span><strong>${esc(totals.completed_games || 0)}</strong></div><div><span>Players</span><strong>${esc(totals.players || 0)}</strong></div><div><span>Player box-score rows</span><strong>${esc(totals.player_rows || 0)}</strong></div></div><div class="table-wrap"><table><thead><tr><th>Season</th><th>Label</th><th class="numeric">Games</th><th class="numeric">Completed</th><th class="numeric">Players</th><th class="numeric">Box rows</th></tr></thead><tbody>${(data.seasons || []).map(row => `<tr><td class="name">${esc(row.season)}</td><td>${esc(row.season_label)}</td><td class="numeric">${esc(row.games)}</td><td class="numeric">${esc(row.completed_games)}</td><td class="numeric">${esc(row.players)}</td><td class="numeric">${esc(row.player_rows)}</td></tr>`).join("")}</tbody></table></div>`;
  } else if (section === "games") {
    root.className = "history-view";
    root.innerHTML = `<div class="history-result-heading"><strong>${data.length} games</strong><span>Canonical EuroLeague history</span></div><div class="table-wrap"><table><thead><tr><th>Date</th><th>Season</th><th>Round</th><th>Game</th><th>Score</th><th>Status</th><th>Box</th></tr></thead><tbody>${data.length ? data.map(row => `<tr><td>${esc(shortDate(row.game_date))}</td><td>${esc(row.season)}</td><td>${row.round == null ? "—" : esc(row.round)}</td><td class="name"><button class="link-button history-game" data-id="${esc(row.game_id)}" type="button">${esc(row.home_team)} vs ${esc(row.away_team)}</button></td><td>${row.home_score == null ? "—" : `${esc(row.home_score)}–${esc(row.away_score)}`}</td><td>${esc(row.played ? "Final" : row.status || "Scheduled")}</td><td>${esc(row.player_rows)} players</td></tr>`).join("") : emptyTableRow(7,"No games match these filters.")}</tbody></table></div>`;
    $$(".history-game").forEach(button => button.addEventListener("click", () => openHistoryGame(button.dataset.id)));
  } else if (section === "players") {
    const rows = sortedHistoryPlayers(data);
    const filters = app.history.filters || {};
    const singleRound = Boolean(filters.round);
    const scope = singleRound ? `Round ${filters.round}` : "All rounds";
    const header = singleRound
      ? `<tr><th>Player</th><th>Team</th><th class="numeric">Start Cr</th><th class="numeric">After Cr</th><th class="numeric">Δ Cr</th><th class="numeric">GP</th><th class="numeric">FP</th><th class="numeric">MIN</th><th class="numeric">PTS</th><th class="numeric">2P</th><th class="numeric">3P</th><th class="numeric">FT</th><th class="numeric">REB</th><th class="numeric">AST</th><th class="numeric">STL</th><th class="numeric">BLK</th><th class="numeric">BA</th><th class="numeric">TO</th><th class="numeric">PF</th><th class="numeric">FD</th><th class="numeric">PIR</th></tr>`
      : `<tr><th>Player</th><th>Team</th><th class="numeric">Start Cr</th><th class="numeric">Latest Cr</th><th class="numeric">Total Δ Cr</th><th class="numeric">GP</th><th class="numeric">Avg FP</th><th class="numeric">Avg MIN</th><th class="numeric">Avg PTS</th><th class="numeric">Avg 2P</th><th class="numeric">Avg 3P</th><th class="numeric">Avg FT</th><th class="numeric">Avg REB</th><th class="numeric">Avg AST</th><th class="numeric">Avg STL</th><th class="numeric">Avg BLK</th><th class="numeric">Avg BA</th><th class="numeric">Avg TO</th><th class="numeric">Avg PF</th><th class="numeric">Avg FD</th><th class="numeric">Avg PIR</th></tr>`;
    const body = rows.map(row => singleRound
      ? `<tr><td class="name"><button class="link-button history-player" data-id="${esc(row.player_id)}" type="button">${esc(row.player)}</button></td><td>${esc(row.teams)}</td><td class="numeric">${num(row.credits_start)}</td><td class="numeric">${num(row.credits_after)}</td><td class="numeric">${historyCreditChange(row)}</td><td class="numeric">${esc(row.games_played)}</td><td class="numeric"><strong>${num(row.fantasy_points)}</strong></td><td class="numeric"><strong>${num(row.minutes)}</strong></td><td class="numeric">${num(row.points)}</td><td class="numeric">${madeAttempted(row.two_points_made,row.two_points_attempted)}</td><td class="numeric">${madeAttempted(row.three_points_made,row.three_points_attempted)}</td><td class="numeric">${madeAttempted(row.free_throws_made,row.free_throws_attempted)}</td><td class="numeric">${num(row.rebounds)}</td><td class="numeric">${num(row.assists)}</td><td class="numeric">${num(row.steals)}</td><td class="numeric">${num(row.blocks)}</td><td class="numeric">${num(row.blocks_received)}</td><td class="numeric">${num(row.turnovers)}</td><td class="numeric">${num(row.fouls_committed)}</td><td class="numeric">${num(row.fouls_drawn)}</td><td class="numeric">${num(row.pir)}</td></tr>`
      : `<tr><td class="name"><button class="link-button history-player" data-id="${esc(row.player_id)}" type="button">${esc(row.player)}</button></td><td>${esc(row.teams)}</td><td class="numeric">${num(row.credits_start)}</td><td class="numeric">${num(row.credits_latest)}</td><td class="numeric">${historyCreditChange(row)}</td><td class="numeric">${esc(row.games_played)}</td><td class="numeric"><strong>${num(row.fantasy_points_avg)}</strong></td><td class="numeric"><strong>${num(row.minutes_avg)}</strong></td><td class="numeric">${num(row.points_avg)}</td><td class="numeric">${madeAttempted(row.two_points_made_avg,row.two_points_attempted_avg)}</td><td class="numeric">${madeAttempted(row.three_points_made_avg,row.three_points_attempted_avg)}</td><td class="numeric">${madeAttempted(row.free_throws_made_avg,row.free_throws_attempted_avg)}</td><td class="numeric">${num(row.rebounds_avg)}</td><td class="numeric">${num(row.assists_avg)}</td><td class="numeric">${num(row.steals_avg)}</td><td class="numeric">${num(row.blocks_avg)}</td><td class="numeric">${num(row.blocks_received_avg)}</td><td class="numeric">${num(row.turnovers_avg)}</td><td class="numeric">${num(row.fouls_committed_avg)}</td><td class="numeric">${num(row.fouls_drawn_avg)}</td><td class="numeric">${num(row.pir_avg)}</td></tr>`).join("");
    root.className = "history-view";
    root.innerHTML = `<div class="history-result-heading"><strong>${rows.length} players · ${esc(scope)}</strong><span>${singleRound ? "Actual statistics and point-in-time credits from the selected round." : "Per-game averages with cumulative credit movement across the selected rounds."} * latest round pending next market.</span></div><div class="table-wrap"><table class="history-player-table"><thead>${header}</thead><tbody>${rows.length ? body : emptyTableRow(21,"No players match these filters.")}</tbody></table></div>`;
    $$(".history-player").forEach(button => button.addEventListener("click", () => openHistoryPlayer(button.dataset.id)));
  } else if (section === "teams") {
    root.className = "history-view";
    root.innerHTML = `<div class="history-card-grid">${data.length ? data.map(row => `<button class="history-entity-card history-team" data-id="${esc(row.team_id)}" type="button"><strong>${esc(row.team)}</strong><span>${esc(row.team_code)}</span><div><b>${esc(row.games)} games</b><b>${num(row.points_avg)} PTS</b><b>${num(row.rebounds_avg)} REB</b><b>${num(row.assists_avg)} AST</b></div></button>`).join("") : `<div class="empty-state">No teams match these filters.</div>`}</div>`;
    $$(".history-team").forEach(button => button.addEventListener("click", () => openHistoryTeam(button.dataset.id)));
  } else if (section === "preseason") {
    root.className = "history-view";
    root.innerHTML = `<div class="history-result-heading"><strong>${data.length} games</strong><span>2026 preparation data · stored separately from EuroLeague history</span></div><div class="table-wrap"><table><thead><tr><th>Date</th><th>Season</th><th>Game</th><th>Score</th><th>Game type</th><th>Box</th></tr></thead><tbody>${data.length ? data.map(row => `<tr><td>${esc(shortDate(row.game_date))}</td><td>2026 Preseason</td><td class="name"><button class="link-button history-game" data-id="${esc(row.game_id)}" type="button">${esc(row.home_team)} vs ${esc(row.away_team)}</button></td><td>${row.home_score == null && row.away_score == null ? "—" : `${esc(row.home_score ?? "—")}–${esc(row.away_score ?? "—")}`}</td><td><span class="status-pill">${esc(preparationTypeLabel(row.game_type))}</span></td><td>${esc(row.player_rows)} players</td></tr>`).join("") : emptyTableRow(6,"No preseason games match these filters.")}</tbody></table></div>`;
    $$(".history-game").forEach(button => button.addEventListener("click", () => openHistoryGame(button.dataset.id)));
  }
}

function historyDetailLoading(text) {
  const dialog = $("#history-detail");
  $("#history-detail-content").innerHTML = `<div class="dialog-loading">${esc(text)}</div>`;
  if (!dialog.open) dialog.showModal();
}

function historyDialogHeader(title, subtitle) {
  return `<div class="dialog-heading"><div><p class="eyebrow">HISTORY</p><h2 id="history-detail-title">${esc(title)}</h2><p>${esc(subtitle)}</p></div><button class="icon-button history-detail-close" type="button" aria-label="Close history detail">×</button></div>`;
}

function wireHistoryDialog() { $(".history-detail-close")?.addEventListener("click", () => $("#history-detail").close()); }

async function openHistoryGame(gameId) {
  historyDetailLoading("Loading full box score…");
  try {
    const value = await api(`/api/history/game/${encodeURIComponent(gameId)}`), game = value.game;
    const rows = value.player_stats || [];
    const content = $("#history-detail-content");
    const context = game.game_type ? `${preparationTypeLabel(game.game_type)} · ${shortDate(game.game_date)}` : `${game.season} · Round ${game.round ?? "—"} · ${shortDate(game.game_date)} · ${game.venue_name || "Venue unavailable"}`;
    const fpNote = game.game_type ? `<p class="section-subtitle">Fantasy FP uses the verified scoring formula when every required box-score field is present. ≈ is either published PIR/VAL or a conservative lower estimate when only positive stats such as fouls drawn are missing. Unknown penalties are never treated as zero. * means the team-win bonus could not be established.</p>` : "";
    const fantasyColumns = game.game_type ? `<th>Fantasy pos.</th><th class="numeric">Credits</th>` : "";
    content.innerHTML = `${historyDialogHeader(`${game.home_team} ${game.home_score ?? "—"} – ${game.away_score ?? "—"} ${game.away_team}`, context)}${fpNote}${(value.team_stats || []).length ? `<div class="history-metrics">${value.team_stats.map(row => `<div><span>${esc(row.team)}</span><strong>${esc(row.points ?? "—")}</strong><small>${esc(row.assists ?? "—")} AST · ${esc(row.total_rebounds ?? "—")} REB · ${esc(row.pir ?? "—")} PIR</small></div>`).join("")}</div>` : ""}<div class="table-wrap"><table><thead><tr><th>Player</th><th>Team</th>${fantasyColumns}<th>Starter</th><th class="numeric">MIN</th><th class="numeric">PTS</th><th class="numeric">2P</th><th class="numeric">3P</th><th class="numeric">FT</th><th class="numeric">REB</th><th class="numeric">AST</th><th class="numeric">STL</th><th class="numeric">BLK</th><th class="numeric">TO</th><th class="numeric">PF</th><th class="numeric">FD</th><th class="numeric">BA</th><th class="numeric">PIR/VAL</th><th class="numeric">Fantasy FP</th></tr></thead><tbody>${rows.length ? rows.map(row => `<tr><td class="name">${row.player_id ? `<button class="link-button history-player" data-id="${esc(row.player_id)}" type="button">${esc(row.player)}</button>` : esc(row.player)}</td><td>${esc(row.team)}</td>${game.game_type ? `<td>${row.fantasy_entity_id ? esc(row.fantasy_position || "—") : "—"}</td><td class="numeric">${row.fantasy_entity_id ? num(row.fantasy_credits) : "—"}</td>` : ""}<td>${row.starter == null ? "—" : row.starter ? "Yes" : "No"}</td><td class="numeric">${num(row.minutes)}</td><td class="numeric">${esc(row.points ?? "—")}</td><td class="numeric">${madeAttempted(row.two_points_made,row.two_points_attempted)}</td><td class="numeric">${madeAttempted(row.three_points_made,row.three_points_attempted)}</td><td class="numeric">${madeAttempted(row.free_throws_made,row.free_throws_attempted)}</td><td class="numeric">${esc(row.total_rebounds ?? "—")}</td><td class="numeric">${esc(row.assists ?? "—")}</td><td class="numeric">${esc(row.steals ?? "—")}</td><td class="numeric">${esc(row.blocks ?? "—")}</td><td class="numeric">${esc(row.turnovers ?? "—")}</td><td class="numeric">${esc(row.fouls_committed ?? "—")}</td><td class="numeric">${esc(row.fouls_drawn ?? "—")}</td><td class="numeric">${esc(row.blocks_received ?? "—")}</td><td class="numeric">${esc(row.pir ?? "—")}</td><td class="numeric">${fantasyScoreCell(row)}</td></tr>`).join("") : emptyTableRow(game.game_type ? 20 : 18,"No player box score is stored for this game.")}</tbody></table></div>`;
    wireHistoryDialog();
    $$("#history-detail .history-player").forEach(button => button.addEventListener("click", () => openHistoryPlayer(button.dataset.id)));
  } catch (error) { $("#history-detail-content").innerHTML = `${historyDialogHeader("Game unavailable","")}<p>${esc(error.message)}</p>`; wireHistoryDialog(); }
}

async function openHistoryPlayer(playerId) {
  historyDetailLoading("Loading player history…");
  try {
    const filters = collectHistoryFilters(), params = new URLSearchParams(filters);
    const value = await api(`/api/history/player/${encodeURIComponent(playerId)}?${params}`), summary = value.summary || {};
    $("#history-detail-content").innerHTML = `${historyDialogHeader(value.player.player, `${filters.season || "All seasons"} · Canonical EuroLeague games`)}<div class="history-metrics"><div><span>Games</span><strong>${esc(summary.games_played || 0)}</strong></div><div><span>Minutes</span><strong>${num(summary.minutes_avg)}</strong></div><div><span>Fantasy FP</span><strong>${num(summary.fantasy_points_avg)}</strong></div><div><span>PTS / REB / AST</span><strong>${num(summary.points_avg)} / ${num(summary.rebounds_avg)} / ${num(summary.assists_avg)}</strong></div></div>${historyGameLog(value.games || [], true)}`;
    wireHistoryDialog(); wireHistoryGameLinks();
  } catch (error) { $("#history-detail-content").innerHTML = `${historyDialogHeader("Player unavailable","")}<p>${esc(error.message)}</p>`; wireHistoryDialog(); }
}

async function openHistoryTeam(teamId) {
  historyDetailLoading("Loading team history…");
  try {
    const filters = collectHistoryFilters(), params = new URLSearchParams(filters);
    const value = await api(`/api/history/team/${encodeURIComponent(teamId)}?${params}`);
    $("#history-detail-content").innerHTML = `${historyDialogHeader(value.team.team, `${filters.season || "All seasons"} · ${value.team.team_code || ""}`)}<h3>Roster history</h3><div class="table-wrap"><table><thead><tr><th>Player</th><th class="numeric">GP</th><th class="numeric">MIN</th><th class="numeric">PTS</th><th class="numeric">Fantasy FP</th></tr></thead><tbody>${(value.roster || []).map(row => `<tr><td class="name"><button class="link-button history-player" data-id="${esc(row.player_id)}" type="button">${esc(row.player)}</button></td><td class="numeric">${esc(row.games_played)}</td><td class="numeric">${num(row.minutes_avg)}</td><td class="numeric">${num(row.points_avg)}</td><td class="numeric">${num(row.fantasy_points_avg)}</td></tr>`).join("")}</tbody></table></div><h3>Schedule / results</h3>${historyGameLog(value.games || [])}`;
    wireHistoryDialog(); wireHistoryGameLinks();
    $$("#history-detail .history-player").forEach(button => button.addEventListener("click", () => openHistoryPlayer(button.dataset.id)));
  } catch (error) { $("#history-detail-content").innerHTML = `${historyDialogHeader("Team unavailable","")}<p>${esc(error.message)}</p>`; wireHistoryDialog(); }
}

function historyGameLog(rows, showCredits=false) {
  const creditsHeader = showCredits ? `<th class="numeric">CR</th>` : "";
  return `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Season</th><th>Round</th><th>Game</th><th>Score</th><th class="numeric">MIN</th>${creditsHeader}<th class="numeric">PTS</th><th class="numeric">FP</th></tr></thead><tbody>${rows.length ? rows.map(row => `<tr><td>${esc(shortDate(row.game_date))}</td><td>${esc(row.season)}</td><td>${esc(row.round ?? "—")}</td><td class="name"><button class="link-button history-game" data-id="${esc(row.game_id)}" type="button">${esc(row.home_team)} vs ${esc(row.away_team)}</button></td><td>${row.home_score == null ? "—" : `${esc(row.home_score)}–${esc(row.away_score)}`}</td><td class="numeric">${num(row.minutes)}</td>${showCredits ? `<td class="numeric">${num(row.fantasy_credits_pre_matchday)}</td>` : ""}<td class="numeric">${esc(row.points ?? "—")}</td><td class="numeric">${num(row.fantasy_points)}</td></tr>`).join("") : emptyTableRow(showCredits ? 9 : 8,"No games available for this selection.")}</tbody></table></div>`;
}

function wireHistoryGameLinks() { $$("#history-detail .history-game").forEach(button => button.addEventListener("click", () => openHistoryGame(button.dataset.id))); }
function shortDate(value) { return value ? new Date(value).toLocaleDateString([], {year:"numeric",month:"short",day:"numeric"}) : "—"; }
function madeAttempted(made,attempted) { return made == null && attempted == null ? "—" : `${esc(made ?? "—")}/${esc(attempted ?? "—")}`; }
function preparationTypeLabel(value) {
  return ({FRIENDLY:"Friendly",PRESEASON_TOURNAMENT:"Preseason Tournament",SUPERCUP:"SuperCup",DOMESTIC_OFFICIAL:"Domestic Official"})[value] || pretty(value || "");
}
function fantasyScoreCell(row) {
  if (row.fantasy_points == null) return "N/A";
  const kind = row.fantasy_points_kind || "EXACT";
  const prefix = ["PIR_EQUIVALENT","VAL_ESTIMATE","PIR_BASE_ONLY","VAL_BASE_ONLY","CONSERVATIVE_LOWER_BOUND","CONSERVATIVE_LOWER_BOUND_BASE_ONLY"].includes(kind) ? "≈" : "";
  const suffix = kind.endsWith("BASE_ONLY") ? "*" : "";
  const missing = (row.fantasy_points_missing || []).join(", ");
  const title = kind === "EXACT" ? "Exact verified Fantasy formula" : `${kind.replaceAll("_"," ")}${missing ? `; missing: ${missing}` : ""}`;
  return `<span title="${esc(title)}">${prefix}${num(row.fantasy_points)}${suffix}</span>`;
}

async function reevaluateStrategy() {
  if (isBusy("advisor")) return;
  const button = $("#reevaluate-button"); setBusy("advisor", true, button, "Refreshing completed Turns…");
  showMessage("#advisor-message", "Attaching completed-game outcomes, then evaluating every legal formation and captain action…", "info");
  try {
    const currentLineup = scopedLineupForCurrentShadow();
    const result = await api("/api/reevaluate", {method:"POST",body:JSON.stringify({refresh_first:true,current_lineup:currentLineup})});
    app.advisor = result; renderAdvisor(result);
    if (["BLOCKED","NO_COMPLETED_TURN"].includes(result.status)) showMessage("#advisor-message", result.reason || "No full Turn has completed yet.", "warning");
    else showMessage("#advisor-message", `Advisor ${result.status}. Realized results through T${result.completed_turn} are locked into this run.`, "success");
    await load({acceptLatest:false});
  } catch (error) { showMessage("#advisor-message", humanOptimizerError(error.message), "error"); }
  finally { setBusy("advisor", false, button, "Update Turn Results / Re-evaluate Strategy"); }
}

function renderAdvisor(advisor) {
  const root = $("#advisor-content");
  if (!advisor) { root.className = "empty-state"; root.innerHTML = `<div><strong>No completed Turn yet.</strong><p>After results arrive, update data and re-evaluate. Future outcomes remain probabilistic.</p></div>`; return; }
  if (advisor.status === "CONDITIONAL_READY") { renderConditionalTurnAdvisor(root, advisor); return; }
  if (!advisor.evidence) { root.className = "empty-state"; root.textContent = advisor.reason || "No advisor evidence is available yet."; return; }
  const evidence = advisor.evidence, comparison = evidence.full_team_keep_vs_switch || {}, captain = evidence.captain || {};
  const switches = evidence.player_switches || [], actionState = switches.length ? "SWITCH" : "NO ACTION";
  root.className = "advisor-layout";
  root.innerHTML = `<section class="panel advisor-summary"><div class="recommendation-heading"><div><p class="eyebrow">FULL TEAM DECISION</p><h3>${actionState === "SWITCH" ? "Apply recommended legal recourse" : "Keep the current legal setup"}</h3></div><span class="decision-badge ${actionState === "SWITCH" ? "switch" : "no-action"}">${actionState}</span></div><div class="scoreline"><div><span>KEEP MEAN</span><strong>${num(comparison.expected_final_team_score_keep)}</strong></div><div><span>SWITCH MEAN</span><strong>${num(comparison.expected_final_team_score_switch)}</strong></div><div><span>GAIN</span><strong>${signed(comparison.expected_final_team_gain)}</strong></div><div><span>P(SWITCH &gt; KEEP)</span><strong>${pct(comparison.probability_switch_final_score_exceeds_keep)}</strong></div></div><div class="action-buttons"><button id="confirm-advisor-state" class="secondary" type="button">I applied this legal setup</button><button id="clear-advisor-state" class="ghost" type="button">Use original pre-lock setup</button></div><small class="reason">Confirmation is stored only for immutable shadow ${esc(advisor.shadow_snapshot_id)}.</small></section><section class="switch-list"><div class="panel-heading"><div><h3>Formation-aware player actions</h3><span>Cross-position changes are legal when the full five-man formation remains valid.</span></div></div>${switches.length ? switches.map(renderSwitch).join("") : `<div class="panel no-action-card"><span class="decision-badge no-action">NO ACTION</span><div><strong>No legal switch improves expected final-team value.</strong><p>Played scores remain active; upcoming alternatives are either unavailable or strategically inferior.</p></div></div>`}</section><section class="panel captain-card"><div class="recommendation-heading"><div><p class="eyebrow">CAPTAIN KEEP / SWITCH</p><h3>${esc(pretty(captain.recommendation || "NO ACTION"))}</h3></div><span class="decision-badge ${captain.recommendation === "SWITCH_CAPTAIN" ? "switch" : "keep"}">${captain.recommendation === "SWITCH_CAPTAIN" ? "SWITCH" : "KEEP"}</span></div><div class="played-option"><div><span>CURRENT CAPTAIN</span><strong>${esc(captain.current_captain)}</strong><b>${num(captain.current_captain_realized_fp)} realized FP</b></div><div class="arrow">→</div><div><span>LATER CANDIDATE</span><strong>${esc(captain.candidate_captain)}</strong><b>${num(captain.candidate_statistics?.expected_fp)} expected FP</b></div></div><div class="scoreline"><div><span>P10</span><strong>${num(captain.candidate_statistics?.p10)}</strong></div><div><span>P50</span><strong>${num(captain.candidate_statistics?.p50)}</strong></div><div><span>P90</span><strong>${num(captain.candidate_statistics?.p90)}</strong></div><div><span>P(SWITCH BETTER)</span><strong>${pct(captain.probability_switch_produces_better_final_result)}</strong></div></div><p><b>Final team:</b> KEEP ${num(captain.expected_final_score_keep)} → SWITCH ${num(captain.expected_final_score_switch)} · <strong>${signed(captain.expected_gain)} FP</strong></p>${(captain.statistical_reasons || []).map(reason => `<div class="evidence-line">${esc(reason)}</div>`).join("")}</section><section class="panel portfolio-card"><p class="eyebrow">TURN PORTFOLIO / OPTIONALITY</p>${renderPortfolio(evidence.turn_portfolio || {})}</section>`;
  $("#confirm-advisor-state")?.addEventListener("click", () => {
    localStorage.setItem(scopedLineupKey(), JSON.stringify({shadow_snapshot_id:advisor.shadow_snapshot_id,lineup:advisor.recommended_lineup}));
    showMessage("#advisor-message", "Confirmed for this immutable shadow snapshot only.", "success");
  });
  $("#clear-advisor-state")?.addEventListener("click", () => { clearScopedLineup(); showMessage("#advisor-message", "Cleared. The immutable pre-lock setup will be used.", "info"); });
}

function renderConditionalTurnAdvisor(root, advisor) {
  const played = advisor.played_players || [], upcoming = advisor.upcoming_players || [];
  const swaps = advisor.conditional_swaps || [], captain = advisor.captain_candidate;
  const playerCard = (player, completed) => `<article class="switch-card"><div class="recommendation-heading"><div><span>${completed ? `TURN ${esc(player.turn)} · PLAYED` : `TURN ${esc(player.turn)} · UPCOMING`}</span><strong>${esc(player.name)}</strong><small>${esc(player.position)} · ${esc(player.team_name || "")}</small></div><span class="decision-badge ${completed ? "keep" : "no-action"}">${completed ? `${num(player.actual_fp)} FP` : `${num(player.expected_fp)} xFP`}</span></div><div class="scoreline">${completed ? `<div><span>REALIZED FP</span><strong>${num(player.actual_fp)}</strong></div><div><span>MINUTES</span><strong>${num(player.actual_minutes)}</strong></div>` : `<div><span>EXPECTED FP</span><strong>${num(player.expected_fp)}</strong></div><div><span>P10</span><strong>${num(player.p10)}</strong></div><div><span>P50</span><strong>${num(player.p50)}</strong></div><div><span>P90</span><strong>${num(player.p90)}</strong></div>`}</div></article>`;
  root.className = "advisor-layout";
  root.innerHTML = `<section class="panel advisor-summary"><div class="recommendation-heading"><div><p class="eyebrow">CURRENT ROSTER TURN ANALYSIS</p><h3>Turn ${esc(advisor.completed_turn)} results attached</h3></div><span class="decision-badge keep">CONDITIONAL</span></div><p>${esc(advisor.reason)}</p><small>Played FP comes from verified current-game outcomes. Check the role condition before applying any swap.</small></section><section class="switch-list"><div class="panel-heading"><div><h3>Completed players</h3><span>Actual Fantasy points for players whose games have finished.</span></div></div>${played.map(player => playerCard(player, true)).join("") || `<div class="empty-state">No completed player scores are available.</div>`}</section><section class="switch-list"><div class="panel-heading"><div><h3>Upcoming players</h3><span>Frozen expected FP and uncertainty for the remaining Turn.</span></div></div>${upcoming.map(player => playerCard(player, false)).join("") || `<div class="empty-state">No upcoming player remains.</div>`}</section><section class="switch-list"><div class="panel-heading"><div><h3>Conditional same-position actions</h3><span>These preserve position counts but depend on your actual field and bench roles.</span></div></div>${swaps.map(item => `<article class="switch-card"><div class="played-option"><div><span>PLAYED</span><strong>${esc(item.played_player_name)}</strong><b>${num(item.realized_fp)} realized FP</b></div><div class="arrow">→</div><div><span>UPCOMING OPTION</span><strong>${esc(item.new_player_name)}</strong><b>${num(item.expected_fp)} expected FP</b></div><span class="decision-badge switch">IF LEGAL</span></div><div class="switch-evidence"><span><b>Projected difference</b>${signed(item.projected_gain)} FP</span><span><b>Position</b>${esc(item.new_player_position)}</span><span><b>P10 / P50 / P90</b>${num(item.p10)} / ${num(item.p50)} / ${num(item.p90)}</span></div><p class="reason">${esc(item.condition)}</p></article>`).join("") || `<div class="panel no-action-card"><span class="decision-badge no-action">NO ACTION</span><div><strong>No positive same-position conditional swap was found.</strong></div></div>`}</section><section class="panel captain-card"><p class="eyebrow">LATER CAPTAIN CANDIDATE</p>${captain ? `<h3>${esc(captain.name)}</h3><div class="scoreline"><div><span>EXPECTED FP</span><strong>${num(captain.expected_fp)}</strong></div><div><span>P10</span><strong>${num(captain.p10)}</strong></div><div><span>P50</span><strong>${num(captain.p50)}</strong></div><div><span>P90</span><strong>${num(captain.p90)}</strong></div></div><p>Use this candidate only if your current captain has already played and this player is eligible on your field.</p>` : `<p>No later captain candidate remains.</p>`}</section>`;
}

function renderSwitch(item) {
  const incoming = item.new_player || {};
  const outgoing = item.old_player || {};
  item = {
    ...item,
    played_player_name:item.keep_player || item.played_player_name,
    new_player_name:item.switch_player || item.new_player_name,
    realized_fp:item.keep_realized_fp ?? item.realized_fp,
    expected_fp:incoming.expected_fp ?? item.expected_fp,
    p10:incoming.p10 ?? item.p10,
    p50:incoming.p50 ?? item.p50,
    p90:incoming.p90 ?? item.p90,
    expected_final_team_gain:item.expected_fantasy_gain ?? item.expected_final_team_gain,
    played_player_position:outgoing.position ?? item.played_player_position,
    new_player_position:incoming.position ?? item.new_player_position,
    optionality_effect:item.later_optionality ?? item.optionality_effect,
  };
  const formation = item.formation_before && item.formation_after ? `${item.formation_before} → ${item.formation_after}` : item.cross_position ? "Legal formation rearranged" : "Formation unchanged";
  const p10Effect = item.switch_p10 != null && item.keep_p10 != null ? signed(item.switch_p10 - item.keep_p10) : "—";
  const p90Effect = item.switch_p90 != null && item.keep_p90 != null ? signed(item.switch_p90 - item.keep_p90) : "—";
  return `<article class="switch-card"><div class="played-option"><div><span>PLAYED</span><strong>${esc(item.played_player_name || nameForPlayer(item.played_player_id))}</strong><b>${num(item.realized_fp)} realized FP</b><small>xMin ${num(outgoing.expected_minutes)} · xFP ${num(outgoing.expected_fp)}</small></div><div class="arrow">→</div><div><span>OPTION</span><strong>${esc(item.new_player_name || nameForPlayer(item.new_player_id))}</strong><b>xFP ${num(item.expected_fp)} · xMin ${num(incoming.expected_minutes ?? item.expected_minutes)}</b><small>P10/P50/P90 ${num(item.p10)} / ${num(item.p50)} / ${num(item.p90)}</small></div><span class="decision-badge switch">SWITCH</span></div><div class="switch-evidence"><span><b>P(new &gt; ${num(item.realized_fp)})</b>${pct(item.probability_new_player_exceeds_realized_player)}</span><span><b>Team gain</b>${signed(item.expected_final_team_gain)} FP</span><span><b>KEEP / SWITCH mean</b>${num(item.expected_final_team_score_keep)} / ${num(item.expected_final_team_score_switch)}</span><span><b>P10 / P90 effect</b>${p10Effect} / ${p90Effect}</span></div><div class="formation-change"><strong>${esc(formation)}</strong><span>${item.cross_position ? `${esc(item.played_player_position)} → ${esc(item.new_player_position)} is legal because the entire formation is re-optimized.` : "The switch preserves a legal starting formation."}</span></div><div class="switch-notes"><span>${esc(item.bench_multiplier_effect || "Bench scoring handled by Phase 7")}</span><span>${esc(item.optionality_effect || "Later optionality included in final-team value")}</span></div></article>`;
}

function renderPortfolio(portfolio) {
  const turns = Object.entries(portfolio.players_by_turn || {}).map(([turn,count]) => `<span class="turn-chip">T${esc(turn)} · ${esc(count)}</span>`).join("") || "—";
  const replacements = (portfolio.later_replacements || []).map(player => `${esc(player.name)} (T${esc(player.turn)}, ${esc(player.position)})`).join(", ") || "None";
  const captains = (portfolio.later_captain_options || []).map(player => `${esc(player.name)} (P90 ${num(player.p90)})`).join(", ") || "None";
  return `<div class="turn-chips">${turns}</div><dl class="strategy-list"><div><dt>Later replacements</dt><dd>${replacements}</dd></div><div><dt>Later captain options</dt><dd>${captains}</dd></div><div><dt>No safety net</dt><dd>${(portfolio.positions_without_later_safety_net || []).map(esc).join(", ") || "None"}</dd></div><div><dt>Expected adaptation value</dt><dd>${num(portfolio.expected_adaptation_value)} FP</dd></div></dl><small>Turn balance is observed, never forced.</small>`;
}

function scopedLineupForCurrentShadow() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(scopedLineupKey()) || "null"); }
  catch (_) { clearScopedLineup(); }
  const currentShadow = app.data.latest_shadow_snapshot?.shadow_snapshot_id;
  if (!saved || saved.shadow_snapshot_id !== currentShadow) { if (saved) clearScopedLineup(); return null; }
  return saved.lineup || null;
}
function scopedLineupKey() { return `elfantasy-current-lineup-team-${app.activeTeamSlot}`; }
function clearScopedLineup() { localStorage.removeItem(scopedLineupKey()); }

async function showPlayerDetail(playerId) {
  const dialog = $("#player-detail"), content = $("#player-detail-content");
  content.innerHTML = `<div class="dialog-loading">Loading player evidence…</div>`; dialog.showModal();
  try {
    const detail = await api(`/api/player/${encodeURIComponent(playerId)}`), player = detail.player, perf = detail.performance || {}, value = detail.value || {}, strategy = detail.strategy || {}, playerStatus = detail.status || player;
    content.innerHTML = `<div class="dialog-heading"><div><p class="eyebrow">PLAYER DETAIL</p><h2 id="detail-title">${esc(player.name)}</h2><p>${esc(teamName(player))} · ${esc(broadPosition(player.position))} · T${esc(player.turn)}</p></div><button class="icon-button" id="detail-close" type="button" aria-label="Close player detail">×</button></div><div class="detail-grid"><section><p class="eyebrow">PERFORMANCE</p><div class="expected-role-callout"><span>Expected Role</span><strong>${esc(expectedRole(perf))}</strong><small>${num(perf.expected_minutes)} expected min · ${num(fpPerMinute(perf),2)} FP/min · ${num(perf.recent_minutes)} recent min${perf.role_trend ? ` · ${esc(pretty(perf.role_trend))}` : ""}</small></div><div class="hero-metrics"><div><span>Expected FP</span><strong>${num(perf.expected_fp)}</strong></div><div><span>Expected minutes</span><strong>${num(perf.expected_minutes)}</strong></div><div><span>Previous-season MPG</span><strong>${num(perf.previous_season_minutes)}</strong></div></div><div class="quantile-line"><span>P10 <b>${num(perf.p10_fp)}</b></span><span>P50 <b>${num(perf.p50_fp)}</b></span><span>P90 <b>${num(perf.p90_fp)}</b></span><span>P95 <b>${num(perf.p95_fp)}</b></span></div><p>Upside 30+ <strong>${pct(perf.prob_fp_ge_30)}</strong> · Downside ≤15 <strong>${pct(perf.prob_fp_le_15)}</strong></p></section><section><p class="eyebrow">VALUE</p><div class="hero-metrics"><div><span>Credits</span><strong>${num(value.credits)}</strong></div><div><span>FP / credit</span><strong>${num(value.fp_per_credit,2)}</strong></div><div><span>Next credit</span><strong>${num(value.expected_next_price)}</strong></div></div><p>Expected Δ <strong>${signed(value.expected_credit_change)}</strong> · P↑ ${pct(value.probability_increase)} · P↓ ${pct(value.probability_decrease)}</p><small>Price confidence: ${esc(value.price_model_confidence)}; price never changes the Phase 7 FP objective.</small></section><section><p class="eyebrow">STATUS</p><div class="status-comparison"><div><span>Source</span><strong>${esc(playerStatus.source_availability_status || "UNKNOWN")}</strong></div><div><span>User override</span><strong>${esc(playerStatus.manual_override_status || "NONE")}</strong></div><div><span>Resolved</span>${status(playerStatus)}</div></div><p>${esc(playerStatus.availability_source || "No source")} · ${esc(stamp(playerStatus.availability_timestamp))}</p>${playerStatus.manual_override ? `<p class="override-note">${esc(playerStatus.manual_override_note || "Manual override has no note")}</p>` : ""}</section><section><p class="eyebrow">STRATEGY</p><div class="strategy-list"><div><dt>Fantasy lineup role</dt><dd>${esc(strategy.role || "Not selected")}</dd></div><div><dt>Captain value</dt><dd>${esc(strategy.captain_suitability || "Not available")}</dd></div><div><dt>Optionality</dt><dd>${esc(strategy.replacement_optionality || "Not available")}</dd></div></div><p>${esc(strategy.why_selected || strategy.why_not_selected)}</p><p><b>Best alternatives:</b> ${(strategy.alternatives || []).map(item => esc(item.player?.name || item.replaces?.name)).join(", ") || "None available"}</p></section></div>`;
    const performanceSection = content.querySelector(".detail-grid section");
    performanceSection?.querySelector(".hero-metrics")?.insertAdjacentHTML(
      "afterend",
      `<div class="last-five-summary"><span><b>L5 FP average</b>${num(perf.last5_fp_average)}</span><span><b>L5 minutes median</b>${num(perf.last5_minutes_median)}</span></div>${lastFiveGameEvidence(perf)}`
    );
    $("#detail-close").addEventListener("click", () => dialog.close());
  } catch (error) { content.innerHTML = `<div class="dialog-error"><h2>Player detail unavailable</h2><p>${esc(error.message)}</p><button class="secondary" id="detail-close" type="button">Close</button></div>`; $("#detail-close").addEventListener("click", () => dialog.close()); }
}

function renderGrowth() {
  const rows = [...(app.data.players || [])].filter(row => row.value_growth_qualified).sort((a,b) => compareValues(a.value_growth_score,b.value_growth_score,-1,a.name,b.name));
  const columns = 12;
  $("#growth-table").innerHTML = `<caption>Competitive Fantasy value with credible credit growth · ${rows.length} players</caption><thead><tr><th>Player</th><th>Team</th><th>Pos</th><th class="numeric">Current Cr</th><th class="numeric">xFP</th><th class="numeric">FP/Cr</th><th class="numeric">Next Cr</th><th class="numeric">Δ Cr</th><th class="numeric">P↑</th><th class="numeric">xMin</th><th class="numeric">P10/P50/P90</th><th>Status</th></tr></thead><tbody>${rows.length ? rows.map(row => `<tr><td class="name"><button class="link-button detail-player" data-player="${esc(row.player_id)}" type="button">${esc(row.name)}</button></td><td>${esc(teamName(row))}</td><td>${esc(broadPosition(row.position))}</td><td class="numeric">${num(row.credits)}</td><td class="numeric">${num(row.expected_fp)}</td><td class="numeric">${num(row.fp_per_credit,2)}</td><td class="numeric">${num(row.expected_next_price)}</td><td class="numeric">${signed(row.expected_credit_change)}</td><td class="numeric">${pct(row.probability_increase)}</td><td class="numeric">${num(row.expected_minutes)}</td><td class="numeric">${num(row.p10_fp)} / ${num(row.p50_fp)} / ${num(row.p90_fp)}</td><td>${status(row)}</td></tr>`).join("") : emptyTableRow(columns, app.data.players?.length ? "No player combines competitive current FP value with credible price growth in this snapshot." : "Price intelligence becomes available after a verified market and prediction snapshot.")}</tbody>`;
  $$("#growth-table .detail-player").forEach(button => button.addEventListener("click", () => showPlayerDetail(button.dataset.player)));
}

function renderMonitoring(report) {
  if (!report) return;
  const summary = report.summary || {}, rounds = report.rounds || [], latest = rounds.at(-1) || {};
  const insufficient = report.sample_status === "INSUFFICIENT_LIVE_SAMPLE";
  const sample = $("#monitoring-sample");
  sample.className = insufficient ? "warning-banner" : "success-banner";
  sample.innerHTML = insufficient ? `<strong>Insufficient live sample.</strong> ${rounds.length}/${report.minimum_trend_sample || 6} Matchdays are available; trends and alerts are withheld.` : `<strong>Trend monitoring active.</strong> ${rounds.length} Matchdays are available. Diagnostics remain read-only.`;
  renderMetricList("#monitoring-model", [["Evaluated Matchdays",summary.evaluated_matchdays],["Player MAE",num(summary.mean_player_mae)],["Player RMSE",num(summary.mean_player_rmse)],["Minutes MAE",num(summary.mean_expected_minutes_mae)],["Oracle regret",num(summary.mean_regret_vs_hindsight_oracle)]]);
  renderMetricList("#monitoring-strategy", [["Transfers used / saved",latest.transfers_used == null ? "—" : `${latest.transfers_used} / ${latest.transfers_saved}`],["Turn allocation",Object.entries(latest.turn_allocation || {}).map(([turn,count]) => `T${turn}:${count}`).join(" · ") || "—"],["Captain width",num(latest.captain_risk_width)],["Roster P10–P90 width",num(latest.average_roster_p10_p90_width)],["Reversed after Turn",latest.recommendation_reversed_after_turn ? "YES" : "NO"]]);
  renderMetricList("#monitoring-data", [["Shadow Matchdays",summary.shadow_matchdays ?? 0],["Overrides",latest.availability_override_count ?? 0],["FORCE / EXCLUDE",`${latest.force_include_count ?? 0} / ${latest.exclude_count ?? 0}`],["Mode",report.mode],["Optimizer modified",report.optimization_modified ? "YES" : "NO"]]);
  renderMetricList("#monitoring-price", [["Shadow outcomes",summary.price_shadow_rows ?? 0],["MAE",num(summary.price_mae,2)],["RMSE",num(summary.price_rmse,2)],["Direction accuracy",pct(summary.price_direction_accuracy)],["Objective", "SECONDARY ONLY"]]);
  $("#monitoring-alerts").innerHTML = `<div class="panel-heading"><div><h3>Diagnostics</h3><span>Observations, not recommendations</span></div></div>${insufficient ? emptyInline("Insufficient live sample; no trend is interpreted as statistically meaningful.") : (report.alerts || []).map(alert => `<div class="strategy-rule"><strong>${esc(pretty(alert.metric))}</strong><span>${esc(alert.message)}</span></div>`).join("") || emptyInline("No behavior alert is active.")}`;
  const columns = 10;
  $("#monitoring-table").innerHTML = `<caption>Read-only Matchday behavior history · ${rounds.length} rounds</caption><thead><tr><th>Season</th><th>MD</th><th>Transfers used/saved</th><th class="numeric">Avg credits</th><th class="numeric">Bank</th><th class="numeric">Top-3 concentration</th><th>Turns</th><th class="numeric">Captain width</th><th class="numeric">Overrides</th><th>Reversed?</th></tr></thead><tbody>${rounds.length ? rounds.map(row => `<tr><td>${esc(row.season)}</td><td class="numeric">${esc(row.matchday)}</td><td>${esc(row.transfers_used)}/${esc(row.transfers_saved)}</td><td class="numeric">${num(row.average_roster_credits)}</td><td class="numeric">${num(row.bank_credits)}</td><td class="numeric">${pct(row.expensive_player_concentration)}</td><td>${Object.entries(row.turn_allocation || {}).map(([turn,count]) => `T${turn}:${count}`).join(" ")}</td><td class="numeric">${num(row.captain_risk_width)}</td><td class="numeric">${esc(row.availability_override_count)}</td><td>${row.recommendation_reversed_after_turn ? "YES" : "NO"}</td></tr>`).join("") : emptyTableRow(columns, "No immutable shadow Matchdays have been evaluated yet.")}</tbody>`;
}

function renderMetricList(selector, rows) { $(selector).innerHTML = rows.map(([label,value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join(""); }

function invalidateAnalysis(reason, {clearLineup=false}={}) {
  app.analysisStale = true; app.recommendation = null; app.teamStrategy = null;
  if (clearLineup) { app.advisor = null; clearScopedLineup(); }
  $("#stale-recommendation").textContent = `${reason} Run optimization again before acting.`;
  $("#stale-recommendation").classList.remove("hidden");
}

function setBusy(name, value, button=null, text=null) {
  if (value) app.busy.add(name); else app.busy.delete(name);
  if (button) { button.disabled = value; if (text) button.textContent = text; button.setAttribute("aria-busy", String(value)); }
}
function isBusy(name) { return app.busy.has(name); }

function status(row) {
  const group = (row.availability_group || availabilityClass(row.availability).group || "YELLOW").toLowerCase();
  const icon = group === "green" ? "✓" : group === "red" ? "✕" : "△";
  return `<span class="status ${group}">${icon} ${esc(row.availability || "UNKNOWN")}</span>`;
}
function availabilityClass(value) {
  const normalized = String(value || "UNKNOWN").toUpperCase();
  if (["AVAILABLE","PROBABLE","PLAY"].includes(normalized)) return {group:"GREEN",cls:"green",icon:"✓"};
  if (["OUT","INJURED","SUSPENDED","NOT_REGISTERED"].includes(normalized)) return {group:"RED",cls:"red",icon:"✕"};
  return {group:"YELLOW",cls:"yellow",icon:"△"};
}
function broadPosition(value) { const text=String(value||"").toUpperCase(); if(text.includes("GUARD")||["G","PG","SG"].includes(text))return "GUARD"; if(text.includes("CENTER")||text==="C")return "CENTER"; if(text.includes("FORWARD")||["F","SF","PF"].includes(text))return "FORWARD"; return text||"—"; }
function teamLabel(id, name) {
  if (name) return name;
  const team = (app.data?.market || []).find(row => row.team_id === id && (row.team_name || row.team_code));
  return team?.team_name || team?.team_code || "—";
}
function teamName(row) { return teamLabel(row?.team_id, row?.team_name || row?.team_code); }
function nameFor(entity) { return (app.data.market || []).find(row => row.entity_id === entity)?.name || entity; }
function nameForPlayer(player) { return (app.data.players || []).find(row => row.player_id === player)?.name || player || "None"; }
function pretty(value) { return String(value ?? "—").replaceAll("_"," ").replace(/\b\w/g, char => char.toUpperCase()); }
function compareValues(a,b,direction,nameA="",nameB="") { const missingA=a===null||a===undefined||a==="", missingB=b===null||b===undefined||b===""; if(missingA&&missingB)return String(nameA).localeCompare(String(nameB)); if(missingA)return 1;if(missingB)return -1; const na=Number(a),nb=Number(b); if(Number.isFinite(na)&&Number.isFinite(nb)&&na!==nb)return direction*(na-nb); const compared=String(a).localeCompare(String(b)); return compared ? direction*compared : String(nameA).localeCompare(String(nameB)); }
function formationFor(starters, players) { const ids=new Set(starters || []),counts={GUARD:0,FORWARD:0,CENTER:0}; (players || []).filter(player => ids.has(player.player_id)).forEach(player => counts[broadPosition(player.position)]++); return `${counts.GUARD}-${counts.FORWARD}-${counts.CENTER}`; }
function emptyTableRow(columns,message) { return `<tr><td colspan="${columns}"><div class="table-empty"><strong>No results</strong><span>${esc(message)}</span></div></td></tr>`; }
function emptyInline(message) { return `<div class="inline-empty">${esc(message)}</div>`; }
function emptyPlayerMessage() { return app.data.players?.length ? "No players match the current search and filters. Clear filters to restore the slate." : "No player slate is available. The market or prediction snapshot may not exist yet."; }
function humanOptimizerError(message) { const lower=String(message).toLowerCase(); if(lower.includes("no legal"))return `No legal roster exists under the current credits, transfers, positions, and FORCE/EXCLUDE constraints. ${message}`; if(lower.includes("database"))return "The local database is unavailable. No recommendation was changed; inspect the technical log and retry."; return message; }
function recommendationMatchesCurrent(recommendation, data) {
  if (data.mode?.demo) return true;
  if (recommendation.restored_without_snapshot) return false;
  if (recommendation.profile_id !== data.state.profile_id) return false;
  if (recommendation.season && recommendation.season !== data.state.season_code) return false;
  if (recommendation.matchday && Number(recommendation.matchday) !== Number(data.state.fantasy_matchday)) return false;
  if (!Array.isArray(recommendation.current_roster)) return false;
  const sameIds = (a, b) => JSON.stringify([...a].map(String).sort()) === JSON.stringify([...b].map(String).sort());
  if (!sameIds(recommendation.current_roster, data.state.roster_entity_ids || [])) return false;
  if (optionalNumber(recommendation.bank_credits) !== optionalNumber(data.state.bank_credits)) return false;
  if (optionalNumber(recommendation.transfers_available) !== optionalNumber(data.state.transfers_available)) return false;
  const constraints = value => Object.entries(value || {}).filter(([,v]) => v !== "NORMAL").sort(([a],[b]) => a.localeCompare(b));
  if (JSON.stringify(constraints(recommendation.constraints)) !== JSON.stringify(constraints(data.state.player_constraints))) return false;
  const prediction = data.dashboard?.prediction || {};
  if (!recommendation.snapshot?.prediction_fingerprint || recommendation.snapshot.prediction_fingerprint !== prediction.prediction_fingerprint) return false;
  if (!recommendation.snapshot?.market_fingerprint || recommendation.snapshot.market_fingerprint !== prediction.market_snapshot_fingerprint) return false;
  if (recommendation.snapshot?.scenario_id !== undefined && recommendation.snapshot.scenario_id !== data.state.scenario_id) return false;
  return true;
}
function shadowMatchesCurrent(shadow, data) {
  if (!shadow) return false;
  if (data.mode?.demo) return true;
  const season = shadow.season || shadow.season_code;
  const matchday = shadow.matchday ?? shadow.fantasy_matchday;
  if (season !== data.state.season_code) return false;
  if (Number(matchday) !== Number(data.state.fantasy_matchday)) return false;
  const shadowRoster = shadow.knowledge?.current_roster;
  const currentRoster = data.state.roster_entity_ids || [];
  if (!Array.isArray(shadowRoster)) return false;
  const normalized = values => [...values].map(String).sort();
  if (JSON.stringify(normalized(shadowRoster)) !== JSON.stringify(normalized(currentRoster))) return false;
  return true;
}

function showMessage(selector, message, kind="info") {
  const element = $(selector); if (!element) return;
  element.classList.remove("hidden","message-error","message-warning","message-success","message-info");
  element.classList.add(`message-${kind}`); element.textContent = message;
}
let toastTimer;
function toast(message, kind="info") { const element=$("#toast"); window.clearTimeout(toastTimer); element.className=`toast message-${kind}`; element.textContent=message; $("#sr-live").textContent=message; toastTimer=window.setTimeout(()=>element.classList.add("hidden"),5000); }
