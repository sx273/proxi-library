const searchForm = document.querySelector("#searchForm");
const searchInput = document.querySelector("#searchInput");
const searchButton = document.querySelector(".search-submit");
const resultsSection = document.querySelector("#resultsSection");
const resultList = document.querySelector("#resultList");
const settingsDialog = document.querySelector("#settingsDialog");
const confirmDialog = document.querySelector("#confirmDialog");
const toast = document.querySelector("#toast");
let confirmCandidate = null;
let confirmTrackNumbers = [];
let toastTimer = null;
let accessPollTimer = null;
const activeJobs = new Set();

/** Send a JSON request and turn API errors into readable messages. */
async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Richiesta non riuscita (${response.status}).`);
  return payload;
}

/** Show a short, accessible status message near the bottom of the page. */
function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3600);
}

/** Escape provider text before placing it into the dashboard's HTML. */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

/** Render an artwork image with a musical fallback if no cover is available. */
function artworkMarkup(url, alt = "") {
  const image = url
    ? `<img class="artwork-image" src="/api/artwork?url=${encodeURIComponent(url)}" alt="${escapeHtml(alt)}" loading="eager" decoding="async"><span class="artwork-fallback" aria-hidden="true" hidden>♫</span>`
    : '<span class="artwork-fallback" aria-hidden="true">♫</span>';
  return `<span class="artwork-frame">${image}</span>`;
}

/** Replace an image that fails to load with the matching fallback. */
function handleArtworkFailure(event) {
  if (!event.target.matches(".artwork-image")) return;
  event.target.hidden = true;
  const fallback = event.target.nextElementSibling;
  if (fallback) fallback.hidden = false;
}

/** Translate the repository's provider identifiers into dashboard labels. */
function serviceName(service) {
  return ({ amazon: "Amazon Music", qobuz: "Qobuz", grilledcheese: "GrilledCheese" })[service] || service;
}

/** Load the configured provider and destination into the page and dialog. */
async function loadSettings() {
  const settings = await request("/api/settings");
  document.querySelector("#activeService").textContent = serviceName(settings.service);
  document.querySelector("#serviceSelect").value = settings.service;
  document.querySelector("#musicFolder").value = settings.music_folder;
  document.querySelector("#musicRootLabel").textContent = settings.music_root;
  document.querySelector("#folderCaption").textContent = settings.music_directory;
}

/** Reflect the persisted lucidadl browser-session state in the header. */
async function loadAccessStatus() {
  const indicator = document.querySelector("#accessIndicator");
  const label = document.querySelector("#accessLabel");
  try {
    const status = await request("/api/access");
    indicator.classList.toggle("ready", status.ready);
    indicator.classList.remove("error");
    label.textContent = status.ready ? "Sessione pronta" : "Sessione da preparare";
  } catch {
    indicator.classList.add("error");
    label.textContent = "Sessione non verificata";
  }
}

/** Create one result row with an explicit button to inspect its metadata. */
function resultMarkup(result, index) {
  const album = result.kind === "album";
  return `<article class="result-row" data-candidate="${escapeHtml(result.id)}" style="animation-delay:${Math.min(index * 35, 280)}ms">
    ${artworkMarkup(result.artwork_url, result.title)}
    <div><p class="result-title"><span class="result-kind">${album ? "Album" : "Brano"}</span>${escapeHtml(result.title)}</p>
      <p class="result-meta">${escapeHtml(result.artist)}${result.album && !album ? ` · ${escapeHtml(result.album)}` : ""}</p></div>
    <button class="text-button preview-button" type="button">Apri</button>
  </article>`;
}

/** Search the selected provider and render its track and album candidates. */
async function searchMusic(query) {
  searchButton.disabled = true;
  searchButton.querySelector("span").textContent = "Cerco...";
  resultsSection.hidden = false;
  resultList.innerHTML = '<p class="empty-state">Ricerca in corso...</p>';
  try {
    const payload = await request("/api/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    document.querySelector("#resultsTitle").textContent = `Risultati per “${query}”`;
    document.querySelector("#resultCount").textContent = `${payload.results.length} TROVATI`;
    resultList.innerHTML = payload.results.length
      ? payload.results.map(resultMarkup).join("")
      : '<p class="empty-state">Nessun risultato. Prova con un’altra ricerca.</p>';
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    const needsSession = error.message.toLocaleLowerCase().includes("sessione");
    const message = needsSession
      ? "Prepara la sessione lucida.to nelle impostazioni prima di cercare."
      : error.message;
    resultList.innerHTML = `<div class="empty-state">${escapeHtml(message)}${needsSession ? ' <button class="text-button" id="openSearchSettings" type="button">Apri impostazioni</button>' : ""}</div>`;
    resultList.querySelector("#openSearchSettings")?.addEventListener("click", openSettings);
    showToast(message);
  } finally {
    searchButton.disabled = false;
    searchButton.querySelector("span").textContent = "Cerca";
  }
}

/** Render the track list returned by the selected result's preview. */
function trackMarkup(track, selectable) {
  const number = String(track.number).padStart(2, "0");
  const title = `${escapeHtml(track.title)}${track.available ? "" : ' <span class="track-unavailable">non disponibile</span>'}`;
  if (selectable) {
    return `<li><label class="track-pick"><input class="track-choice" type="checkbox" value="${track.number}" ${track.available ? "checked" : "disabled"}><span class="track-number">${number}</span><span class="track-copy"><span class="track-title">${title}</span><span class="track-artist">${escapeHtml(track.artist)}</span></span></label></li>`;
  }
  return `<li class="track-static"><span class="track-number">${number}</span><span class="track-copy"><span class="track-title">${title}</span><span class="track-artist">${escapeHtml(track.artist)}</span></span></li>`;
}

/** Render a numbered track list, optionally making each title selectable. */
function trackListMarkup(tracks, selectable = false) {
  return `<ol class="track-list">${tracks.map((track) => trackMarkup(track, selectable)).join("")}</ol>`;
}

/** Keep the bulk selector, selected count, and download button in sync. */
function updateTrackSelection(row) {
  const choices = [...row.querySelectorAll(".track-choice:not(:disabled)")];
  const selected = choices.filter((choice) => choice.checked);
  const selectAll = row.querySelector(".select-all");
  const confirmButton = row.querySelector(".confirm-start");
  const count = row.querySelector(".selection-count");
  if (selectAll) selectAll.checked = choices.length > 0 && selected.length === choices.length;
  if (count) count.textContent = `${selected.length} selezionate`;
  if (confirmButton) {
    confirmButton.disabled = selected.length === 0;
    confirmButton.textContent = selected.length === choices.length
      ? "Scarica tutte"
      : `Scarica selezionate (${selected.length})`;
  }
}

/** Display one enlarged cover with album details above a track list. */
function collectionArtworkMarkup(preview, compact = false) {
  const sizeClass = compact ? "confirm-artwork" : "preview-artwork";
  return `<div class="collection-artwork ${sizeClass}">${artworkMarkup(preview.artwork_url, preview.title)}<div><p class="collection-title">${escapeHtml(preview.title)}</p><p class="collection-artist">${escapeHtml(preview.artist)}</p></div></div>`;
}

/** Resolve a candidate and expand its album tracks before enabling confirmation. */
async function previewCandidate(row) {
  const candidateId = row.dataset.candidate;
  const button = row.querySelector(".preview-button");
  button.disabled = true;
  button.textContent = "Apro...";
  try {
    const preview = await request(`/api/candidates/${candidateId}/preview`, { method: "POST" });
    row.querySelector(".preview-panel")?.remove();
    const panel = document.createElement("div");
    panel.className = "preview-panel";
    const trackLabel = preview.tracks.length === 1 ? "traccia" : "tracce";
    panel.innerHTML = `${collectionArtworkMarkup(preview)}<div class="preview-summary"><span>${preview.tracks.length} ${trackLabel} · ${preview.available_count} disponibili</span><span>${escapeHtml(serviceName((await request("/api/settings")).service))}</span></div>
      ${trackListMarkup(preview.tracks, true)}
      <div class="preview-actions"><label class="select-all-label"><input class="select-all" type="checkbox" checked> Tutte</label><span class="selection-count"></span><button class="button-primary confirm-start" type="button">Scarica tutte</button><button class="button-secondary reject-start" type="button">Rifiuta</button></div>`;
    row.append(panel);
    button.textContent = "Nascondi";
    panel.dataset.preview = JSON.stringify(preview);
    updateTrackSelection(row);
  } catch (error) {
    showToast(error.message);
    button.textContent = "Riprova";
  } finally {
    button.disabled = false;
  }
}

/** Prepare the confirmation dialog using the exact candidate and track list. */
function openConfirmation(row) {
  const panel = row.querySelector(".preview-panel");
  if (!panel) return;
  confirmCandidate = row.dataset.candidate;
  const preview = JSON.parse(panel.dataset.preview);
  confirmTrackNumbers = [...panel.querySelectorAll(".track-choice:checked")].map((choice) => Number(choice.value));
  if (!confirmTrackNumbers.length) {
    showToast("Seleziona almeno una traccia.");
    return;
  }
  const selectedTracks = preview.tracks.filter((track) => confirmTrackNumbers.includes(track.number));
  const allSelected = confirmTrackNumbers.length === preview.available_count;
  const countLabel = confirmTrackNumbers.length === 1 ? "traccia" : "tracce";
  document.querySelector("#confirmTitle").textContent = preview.kind === "album"
    ? (allSelected ? `Scaricare tutto “${preview.title}”?` : `Scaricare ${confirmTrackNumbers.length} ${countLabel}?`)
    : `Scaricare “${preview.title}”?`;
  document.querySelector("#confirmSubtitle").textContent = `${preview.artist}${preview.album ? ` · ${preview.album}` : ""} · ${confirmTrackNumbers.length} ${countLabel} selezionate`;
  document.querySelector("#confirmTracks").innerHTML = `${collectionArtworkMarkup(preview, true)}${trackListMarkup(selectedTracks)}`;
  confirmDialog.showModal();
}

/** Submit the user's explicit confirmation and start polling the new download. */
async function startDownload() {
  if (!confirmCandidate) return;
  const button = document.querySelector("#acceptDownload");
  button.disabled = true;
  button.textContent = "Avvio...";
  try {
    const result = await request(`/api/candidates/${confirmCandidate}/download`, {
      method: "POST",
      body: JSON.stringify({ track_numbers: confirmTrackNumbers }),
    });
    confirmDialog.close();
    activeJobs.add(result.id);
    document.querySelector("#activitySection").hidden = false;
    showToast("Download avviato sul server.");
    pollJob(result.id);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Conferma download";
  }
}

/** Poll one job until lucidadl reports completion or an error. */
async function pollJob(jobId) {
  try {
    const job = await request(`/api/jobs/${jobId}`);
    renderJob(job);
    if (["complete", "error"].includes(job.status)) {
      activeJobs.delete(jobId);
      if (job.status === "complete") {
        await loadRecent();
        showToast("Download completato.");
      } else {
        showToast(job.error || "Download non riuscito.");
      }
      removeFinishedJob(jobId);
      return;
    }
  } catch (error) {
    showToast(error.message);
    activeJobs.delete(jobId);
    return;
  }
  setTimeout(() => pollJob(jobId), 1200);
}

/** Update the download queue with status, progress bars, and recent log lines. */
function renderJob(job) {
  const section = document.querySelector("#activitySection");
  const list = document.querySelector("#jobList");
  section.hidden = false;
  let row = list.querySelector(`[data-job="${job.id}"]`);
  if (!row) {
    list.querySelector(".empty-state")?.remove();
    row = document.createElement("article");
    row.className = "job-row";
    row.dataset.job = job.id;
    list.prepend(row);
  }
  const status = ({ queued: "In coda", downloading: "Download in corso", complete: "Completato", error: "Non riuscito" })[job.status] || job.status;
  const tracks = Object.values(job.tracks || {}).map((track) => {
    const percent = track.progress === null ? null : Math.max(0, Math.min(100, track.progress));
    const progress = percent === null
      ? '<div class="download-meter indeterminate"><span></span></div><span class="download-percent">Preparazione...</span>'
      : `<div class="download-meter"><span style="width:${percent}%"></span></div><span class="download-percent">${percent}%</span>`;
    return `<div class="job-track">${escapeHtml(track.label)} · ${escapeHtml(track.status)}<div class="job-progress">${progress}</div></div>`;
  }).join("");
  const logs = (job.logs || []).slice(-5).map(escapeHtml).join("\n");
  row.innerHTML = `<div class="job-header"><strong>${escapeHtml(job.title)} · ${escapeHtml(job.artist)}</strong><span class="job-status">${status}</span></div>${tracks}${job.error ? `<p class="job-error">${escapeHtml(job.error)}</p>` : ""}${logs ? `<pre class="job-log">${logs}</pre>` : ""}`;
}

/** Remove a finished job and restore the empty message when the queue is clear. */
function removeFinishedJob(jobId) {
  const list = document.querySelector("#jobList");
  [...list.querySelectorAll(".job-row")].find((row) => row.dataset.job === jobId)?.remove();
  if (!list.querySelector(".job-row")) {
    list.innerHTML = '<p class="empty-state">Tutto vuoto qui!</p>';
  }
}

/** Load and display the twelve newest completed downloads. */
async function loadRecent() {
  const payload = await request("/api/recent");
  const list = document.querySelector("#recentList");
  if (!payload.downloads.length) {
    list.innerHTML = '<p class="empty-state">La raccolta è pronta per il primo ascolto.</p>';
    return;
  }
  list.innerHTML = payload.downloads.map((item) => `<article class="recent-row">
    ${artworkMarkup(item.artwork_url, item.title)}<div><p class="recent-title">${escapeHtml(item.title)}</p>
    <p class="recent-meta">${escapeHtml(item.artist)}${item.album ? ` · ${escapeHtml(item.album)}` : ""}${item.files.length ? ` · ${item.files.length} file` : ""}</p></div>
    <time class="recent-date" datetime="${new Date(item.created_at * 1000).toISOString()}">${new Date(item.created_at * 1000).toLocaleDateString("it-IT", { day: "numeric", month: "short" })}</time>
  </article>`).join("");
}

/** Open settings and refresh their values from the API. */
async function openSettings() {
  try {
    await loadSettings();
    settingsDialog.showModal();
    document.querySelector("#settingsMessage").textContent = "";
    await syncPrepareStatus();
  } catch (error) {
    showToast(error.message);
  }
}

/** Save settings after the backend verifies the mounted destination. */
async function saveSettings(event) {
  event.preventDefault();
  const message = document.querySelector("#settingsMessage");
  message.textContent = "Salvataggio...";
  try {
    await request("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        service: document.querySelector("#serviceSelect").value,
        music_folder: document.querySelector("#musicFolder").value,
      }),
    });
    await loadSettings();
    settingsDialog.close();
    showToast("Impostazioni salvate.");
  } catch (error) {
    message.textContent = error.message;
  }
}

/** Update the settings modal UI from the live session-preparation state. */
function renderPrepareState(state) {
  const button = document.querySelector("#prepareAccess");
  const message = document.querySelector("#settingsMessage");
  const remoteBox = document.querySelector("#remoteBrowserBox");
  button.disabled = Boolean(state.running);
  button.textContent = state.running ? "Attendi..." : "Prepara";
  remoteBox.hidden = !state.vnc_ready;
  if (state.message && (state.running || state.status === "error" || state.status === "ready")) {
    message.textContent = state.message;
  }
}

/** Poll /api/access while Chromium + noVNC preparation is running. */
async function syncPrepareStatus() {
  clearTimeout(accessPollTimer);
  try {
    const state = await request("/api/access");
    renderPrepareState(state);
    if (state.running) {
      accessPollTimer = setTimeout(syncPrepareStatus, 1200);
    } else if (state.status === "ready") {
      await loadAccessStatus();
    }
  } catch (error) {
    document.querySelector("#settingsMessage").textContent = error.message;
    document.querySelector("#prepareAccess").disabled = false;
    document.querySelector("#prepareAccess").textContent = "Prepara";
  }
}

/** Run lucidadl's browser-based Cloudflare preparation with on-demand noVNC access. */
async function prepareAccess() {
  const button = document.querySelector("#prepareAccess");
  const message = document.querySelector("#settingsMessage");
  button.disabled = true;
  button.textContent = "Attendi...";
  message.textContent = "Avvio del browser remoto in corso...";
  try {
    const state = await request("/api/access/prepare", { method: "POST" });
    renderPrepareState(state);
    if (state.running) {
      accessPollTimer = setTimeout(syncPrepareStatus, 800);
    } else {
      await loadAccessStatus();
    }
  } catch (error) {
    message.textContent = error.message;
    button.disabled = false;
    button.textContent = "Prepara";
  }
}

/** Bind dashboard actions once the document has loaded. */
function bindEvents() {
  document.addEventListener("error", handleArtworkFailure, true);
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    searchMusic(searchInput.value.trim());
  });
  document.querySelector("#settingsOpen").addEventListener("click", openSettings);
  document.querySelector("#settingsClose").addEventListener("click", () => settingsDialog.close());
  document.querySelector("#settingsCancel").addEventListener("click", () => settingsDialog.close());
  document.querySelector("#settingsForm").addEventListener("submit", saveSettings);
  document.querySelector("#prepareAccess").addEventListener("click", prepareAccess);
  document.querySelector("#rejectDownload").addEventListener("click", () => confirmDialog.close());
  document.querySelector("#acceptDownload").addEventListener("click", startDownload);
  resultList.addEventListener("click", (event) => {
    const row = event.target.closest(".result-row");
    if (!row) return;
    if (event.target.closest(".preview-button")) {
      if (row.querySelector(".preview-panel")) row.querySelector(".preview-panel").remove();
      else previewCandidate(row);
    }
    if (event.target.closest(".confirm-start")) openConfirmation(row);
    if (event.target.closest(".reject-start")) row.querySelector(".preview-panel")?.remove();
  });
  resultList.addEventListener("change", (event) => {
    const row = event.target.closest(".result-row");
    if (!row) return;
    if (event.target.matches(".select-all")) {
      row.querySelectorAll(".track-choice:not(:disabled)").forEach((choice) => {
        choice.checked = event.target.checked;
      });
    }
    if (event.target.matches(".select-all, .track-choice")) updateTrackSelection(row);
  });
}

/** Initialize dashboard data and show a useful message if the API is offline. */
async function initialize() {
  bindEvents();
  try {
    await Promise.all([loadSettings(), loadRecent(), loadAccessStatus()]);
  } catch (error) {
    showToast(error.message);
  }
}

initialize();