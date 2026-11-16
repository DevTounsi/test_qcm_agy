(() => {
  const tauri = window.__TAURI__;
  const invoke = tauri ? tauri.core.invoke : async () => null;
  const listen = tauri ? tauri.event.listen : async () => () => {};

  const statusDot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");
  const answerMain = document.getElementById("answer-main");
  const answerMeta = document.getElementById("answer-meta");
  const historyEl = document.getElementById("history");
  const selectModel = document.getElementById("select-model");
  const selectEffort = document.getElementById("select-effort");

  const btnClose = document.getElementById("btn-close");
  const btnMin = document.getElementById("btn-min");
  const btnTop = document.getElementById("btn-top");
  const btnScreen = document.getElementById("btn-screen");
  const btnRead = document.getElementById("btn-read");
  const btnPause = document.getElementById("btn-pause");
  const btnClear = document.getElementById("btn-clear");
  const btnAccount = document.getElementById("btn-account");

  const PAUSE_SVG = '<path d="M4.5 3h2a.5.5 0 0 1 .5.5v9a.5.5 0 0 1-.5.5h-2a.5.5 0 0 1-.5-.5v-9a.5.5 0 0 1 .5-.5zm5 0h2a.5.5 0 0 1 .5.5v9a.5.5 0 0 1-.5.5h-2a.5.5 0 0 1-.5-.5v-9a.5.5 0 0 1 .5-.5z"/>';
  const PLAY_SVG = '<path d="M4.5 3.1a.5.5 0 0 1 .76-.43l7.5 4.9a.5.5 0 0 1 0 .84l-7.5 4.9A.5.5 0 0 1 4.5 12.9V3.1z"/>';

  let catalogue = {};
  let currentFamily = "";
  let currentEffort = "Medium";
  let isTopmost = true;
  let isPaused = false;
  let lastDuration = null;

  function renderStatus({ busy, queued, paused, last_duration }) {
    if (typeof paused === "boolean") isPaused = paused;
    if (typeof last_duration === "number") lastDuration = last_duration;

    statusDot.className = "dot";
    if (busy || queued > 0) {
      statusDot.classList.add("busy");
      const extra = busy ? queued : Math.max(0, queued - 1);
      statusText.textContent = extra > 0 ? `Réflexion… (+${extra})` : "Réflexion…";
    } else if (isPaused) {
      statusDot.classList.add("paused");
      statusText.textContent = "En pause";
    } else {
      statusDot.classList.add("ready");
      statusText.textContent = lastDuration !== null ? `Prêt · ${lastDuration.toFixed(2)}s` : "Prêt";
    }
  }

  function populateSelectors(newCat, family, effort) {
    catalogue = newCat || {};
    const families = Object.keys(catalogue);
    if (families.length === 0) return;

    currentFamily = families.includes(family) ? family : families[0];
    selectModel.innerHTML = "";
    for (const fam of families) {
      const opt = document.createElement("option");
      opt.value = fam;
      opt.textContent = fam;
      selectModel.appendChild(opt);
    }
    selectModel.value = currentFamily;

    populateEfforts(currentFamily, effort);
  }

  function populateEfforts(family, preferredEffort) {
    const effortsMap = catalogue[family] || {};
    const efforts = Object.keys(effortsMap);
    if (efforts.length === 0) return;

    currentEffort = efforts.includes(preferredEffort)
      ? preferredEffort
      : (efforts.includes("Medium") ? "Medium" : efforts[0]);

    selectEffort.innerHTML = "";
    for (const eff of efforts) {
      const opt = document.createElement("option");
      opt.value = eff;
      opt.textContent = eff;
      selectEffort.appendChild(opt);
    }
    selectEffort.value = currentEffort;
  }

  function showResult({ idx, answer, duration, is_error }) {
    lastDuration = duration;
    const durText = `${duration.toFixed(2)}s`;
    const isMulti = !is_error && answer.includes("\n");

    answerMain.className = "answer-main";
    if (is_error) {
      answerMain.classList.add("error");
      answerMain.textContent = answer;
      answerMeta.textContent = durText;
    } else if (isMulti) {
      const lines = answer.split("\n").map((l) => l.trim()).filter(Boolean);
      answerMain.classList.add("multi");
      answerMain.textContent = `${lines.length} réponses prêtes`;
      answerMeta.textContent = durText;

      const header = document.createElement("div");
      header.className = "hist-batch-header";
      header.textContent = `${lines.length} réponses · ${durText}`;
      historyEl.appendChild(header);

      for (const line of lines) {
        const sep = line.indexOf(":");
        const row = document.createElement("div");
        row.className = "hist-row";
        const left = document.createElement("div");
        left.className = "hist-left";
        if (sep > 0) {
          const numSpan = document.createElement("span");
          numSpan.className = "hist-num";
          numSpan.textContent = `${line.slice(0, sep).trim()}:`;
          const ansSpan = document.createElement("span");
          ansSpan.className = "hist-ans";
          ansSpan.textContent = line.slice(sep + 1).trim();
          left.append(numSpan, ansSpan);
        } else {
          const ansSpan = document.createElement("span");
          ansSpan.className = "hist-ans";
          ansSpan.textContent = line;
          left.append(ansSpan);
        }
        row.appendChild(left);
        historyEl.appendChild(row);
      }
      header.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    } else {
      if (answer.length <= 3) answerMain.classList.add("letter");
      answerMain.textContent = answer;
      answerMeta.textContent = durText;
    }

    const row = document.createElement("div");
    row.className = "hist-row";
    const left = document.createElement("div");
    left.className = "hist-left";

    const numSpan = document.createElement("span");
    numSpan.className = "hist-num";
    numSpan.textContent = `#${idx}`;

    const ansSpan = document.createElement("span");
    ansSpan.className = "hist-ans";
    ansSpan.textContent = answer;
    if (is_error) ansSpan.style.color = "var(--apple-red)";

    const timeSpan = document.createElement("span");
    timeSpan.className = "hist-time";
    timeSpan.textContent = durText;

    left.append(numSpan, ansSpan);
    row.append(left, timeSpan);
    historyEl.appendChild(row);
    historyEl.scrollTop = historyEl.scrollHeight;
  }

  function showNotice(ok, message) {
    answerMain.className = ok ? "answer-main placeholder" : "answer-main error";
    answerMain.textContent = message;
    answerMeta.textContent = "";
  }

  function updatePauseButton() {
    const icon = document.getElementById("icon-pause");
    icon.innerHTML = isPaused ? PLAY_SVG : PAUSE_SVG;
    btnPause.classList.toggle("paused-state", isPaused);
    btnPause.title = isPaused ? "Reprendre la surveillance" : "Mettre en pause";
  }

  // --- Events UI ---
  btnClose.addEventListener("click", () => invoke("close_app"));
  btnMin.addEventListener("click", () => invoke("minimize_app"));

  btnTop.addEventListener("click", async () => {
    isTopmost = !isTopmost;
    btnTop.classList.toggle("active", isTopmost);
    await invoke("set_topmost", { topmost: isTopmost });
  });

  btnScreen.addEventListener("click", () => invoke("capture_screen"));
  btnRead.addEventListener("click", () => invoke("read_clipboard_now"));

  btnPause.addEventListener("click", async () => {
    isPaused = !isPaused;
    updatePauseButton();
    await invoke("set_paused", { paused: isPaused });
  });

  btnClear.addEventListener("click", () => {
    lastDuration = null;
    answerMain.className = "answer-main placeholder";
    answerMain.textContent = "En attente d'un Ctrl+C ou Win+Shift+S…";
    answerMeta.textContent = "";
    historyEl.innerHTML = "";
    invoke("reset_counter");
    renderStatus({ busy: false, queued: 0, paused: isPaused, last_duration: null });
  });

  btnAccount.addEventListener("click", async () => {
    const res = await invoke("open_account");
    if (res && typeof res.ok === "boolean") {
      showNotice(res.ok, res.message);
    }
  });

  selectModel.addEventListener("change", async () => {
    currentFamily = selectModel.value;
    populateEfforts(currentFamily, currentEffort);
    await invoke("set_model_effort", { family: currentFamily, effort: currentEffort });
  });

  selectEffort.addEventListener("change", async () => {
    currentEffort = selectEffort.value;
    await invoke("set_model_effort", { family: currentFamily, effort: currentEffort });
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "F9") {
      e.preventDefault();
      invoke("capture_screen");
    } else if (e.key === "F5" || (e.key === "Enter" && document.activeElement?.tagName !== "SELECT")) {
      e.preventDefault();
      invoke("read_clipboard_now");
    }
  });

  // --- Boot & Tauri event listeners ---
  async function init() {
    await listen("qcm:status", (e) => renderStatus(e.payload));
    await listen("qcm:result", (e) => showResult(e.payload));
    await listen("qcm:catalogue", (e) => {
      const p = e.payload;
      populateSelectors(p.catalogue, p.family, p.effort);
    });
    await listen("qcm:notice", (e) => showNotice(e.payload.ok, e.payload.message));

    const state = await invoke("get_initial_state");
    if (state) {
      isTopmost = state.topmost;
      isPaused = state.paused;
      btnTop.classList.toggle("active", isTopmost);
      updatePauseButton();
      populateSelectors(state.catalogue, state.family, state.effort);
      if (!state.agy_installed) {
        showNotice(false, "Installe agy puis clique sur Compte");
      }
      renderStatus({ busy: false, queued: 0, paused: isPaused, last_duration: null });
    }
  }

  init();
})();
