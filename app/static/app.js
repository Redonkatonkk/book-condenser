const ACTIVE_JOB_KEY = "bookCondenser.activeJobId";

const state = {
  user: null,
  jobId: null,
  poller: null,
  lastJob: null,
  hasBackendApiKey: true,
  selectedChapters: new Set(),
  libraryJobs: [],
};

const $ = (id) => document.getElementById(id);

const statusLabels = {
  queued: "排队中",
  analyzing: "分析章节",
  ready: "等待选择",
  condensing: "浓缩中",
  building: "生成 EPUB",
  completed: "已完成",
  failed: "失败",
};

const chapterStatusLabels = {
  pending: "等待",
  running: "进行中",
  done: "完成",
  failed: "失败",
};

async function init() {
  bindActions();
  await refreshAuth();
  await loadModels();

  const savedJobId = localStorage.getItem(ACTIVE_JOB_KEY);
  if (savedJobId) {
    state.jobId = savedJobId;
    showWorkspace();
    startPolling();
    return;
  }
  if (state.user) {
    await showLibrary();
  } else {
    showWorkspace();
  }
}

function bindActions() {
  $("loginOpenBtn").addEventListener("click", showAuthPanel);
  $("workspaceBtn").addEventListener("click", showWorkspace);
  $("libraryBtn").addEventListener("click", () => showLibrary());
  $("logoutBtn").addEventListener("click", logout);
  $("loginForm").addEventListener("submit", (event) => submitAuth(event, "login"));
  $("registerForm").addEventListener("submit", (event) => submitAuth(event, "register"));
  $("accountKeyForm").addEventListener("submit", saveAccountKey);
  $("clearAccountKeyBtn").addEventListener("click", clearAccountKey);
  $("refreshLibraryBtn").addEventListener("click", refreshLibrary);

  $("condenseOneBtn").addEventListener("click", () => startCondense("one"));
  $("condenseTenBtn").addEventListener("click", () => startCondense("ten"));
  $("condenseAllBtn").addEventListener("click", () => startCondense("all"));
  $("condenseSelectedBtn").addEventListener("click", () =>
    startCondense("selected", getSelectedChapterIds()),
  );
  $("stopCondenseBtn").addEventListener("click", stopCondense);
  $("retryFailedBtn").addEventListener("click", () => startCondense("failed"));
  $("exportSelectedBtn").addEventListener("click", () => exportChapters(getSelectedChapterIds()));
  $("exportDoneBtn").addEventListener("click", () => exportChapters([]));
  $("selectAllChapters").addEventListener("change", (event) => {
    const chapters = state.lastJob?.chapters || [];
    if (event.target.checked) {
      chapters.forEach((chapter) => state.selectedChapters.add(chapter.id));
    } else {
      chapters.forEach((chapter) => state.selectedChapters.delete(chapter.id));
    }
    renderJob(state.lastJob);
  });

  $("fileInput").addEventListener("change", (event) => {
    const file = event.target.files[0];
    $("fileName").textContent = file ? file.name : "选择书籍文件";
  });

  $("uploadForm").addEventListener("submit", createJobFromUpload);
}

async function refreshAuth() {
  const response = await fetch("/api/auth/me");
  const data = await response.json();
  state.user = data.authenticated ? data.user : null;
  renderAuth();
}

async function loadModels() {
  const response = await fetch("/api/models");
  const data = await response.json();
  $("modelSelect").innerHTML = data.models
    .map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`)
    .join("");
  $("modelSelect").value = data.default;
  const regionOptions = data.regions
    .map((region) => `<option value="${escapeHtml(region.id)}">${escapeHtml(region.label)}</option>`)
    .join("");
  $("regionSelect").innerHTML = regionOptions;
  $("accountRegionSelect").innerHTML = regionOptions;
  const selectedRegion = data.stored_region || data.default_region || "cn";
  $("regionSelect").value = selectedRegion;
  $("accountRegionSelect").value = selectedRegion;
  state.hasBackendApiKey = Boolean(data.has_api_key);
  renderApiKeyRequirement();
  renderAccount();
}

function renderAuth() {
  const loggedIn = Boolean(state.user);
  $("loginOpenBtn").classList.toggle("hidden", loggedIn);
  $("userBadge").classList.toggle("hidden", !loggedIn);
  $("libraryBtn").classList.toggle("hidden", !loggedIn);
  $("workspaceBtn").classList.toggle("hidden", false);
  $("userEmail").textContent = state.user?.email || "";
  renderAccount();
}

function renderAccount() {
  $("accountEmail").textContent = state.user?.email || "-";
  $("accountKeyStatus").textContent = state.user?.has_api_key ? "已保存个人 Key" : "未保存个人 Key";
}

function showAuthPanel() {
  window.scrollTo(0, 0);
  $("authPanel").classList.remove("hidden");
  $("libraryView").classList.add("hidden");
  $("uploadView").classList.add("hidden");
  $("previewView").classList.add("hidden");
  $("downloadBtn").classList.add("hidden");
  $("subtitle").textContent = "登录后可管理个人书库";
}

function showWorkspace() {
  window.scrollTo(0, 0);
  $("authPanel").classList.add("hidden");
  $("libraryView").classList.add("hidden");
  $("uploadView").classList.remove("hidden");
  $("previewView").classList.add("hidden");
  if (state.lastJob) {
    renderJob(state.lastJob);
  } else {
    $("downloadBtn").classList.add("hidden");
    $("subtitle").textContent = "EPUB / PDF / TXT → 浓缩 EPUB";
  }
}

async function showLibrary() {
  if (!state.user) {
    showAuthPanel();
    return;
  }
  window.scrollTo(0, 0);
  $("authPanel").classList.add("hidden");
  $("uploadView").classList.add("hidden");
  $("previewView").classList.add("hidden");
  $("downloadBtn").classList.add("hidden");
  $("libraryView").classList.remove("hidden");
  $("subtitle").textContent = "个人书库";
  await refreshLibrary();
}

async function submitAuth(event, mode) {
  event.preventDefault();
  const prefix = mode === "login" ? "login" : "register";
  const email = $(`${prefix}Email`).value.trim();
  const password = $(`${prefix}Password`).value;
  try {
    const response = await fetch(`/api/auth/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "认证失败");
    state.user = data.user;
    renderAuth();
    await loadModels();
    await showLibrary();
  } catch (error) {
    showError(error.message);
  }
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  state.user = null;
  state.libraryJobs = [];
  renderAuth();
  await loadModels();
  showWorkspace();
}

async function saveAccountKey(event) {
  event.preventDefault();
  const apiKey = $("accountApiKeyInput").value.trim();
  if (!apiKey) {
    showError("请输入要保存的 API Key。");
    $("accountApiKeyInput").focus();
    return;
  }
  try {
    const response = await fetch("/api/account/api-key", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: apiKey,
        region: $("accountRegionSelect").value,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "保存失败");
    $("accountApiKeyInput").value = "";
    state.user.has_api_key = data.has_api_key;
    state.user.region = data.region;
    hideError();
    await loadModels();
  } catch (error) {
    showError(error.message);
  }
}

async function clearAccountKey() {
  try {
    const response = await fetch("/api/account/api-key", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: "", region: $("accountRegionSelect").value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "清除失败");
    state.user.has_api_key = data.has_api_key;
    hideError();
    await loadModels();
  } catch (error) {
    showError(error.message);
  }
}

async function refreshLibrary() {
  if (!state.user) return;
  try {
    const response = await fetch("/api/me/jobs");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "读取书库失败");
    state.libraryJobs = data.jobs || [];
    renderLibrary();
  } catch (error) {
    showError(error.message);
  }
}

function renderLibrary() {
  const jobs = state.libraryJobs;
  $("libraryEmpty").classList.toggle("hidden", jobs.length > 0);
  $("libraryTable").classList.toggle("hidden", jobs.length === 0);
  $("libraryRows").innerHTML = jobs.map(renderLibraryRow).join("");
  bindLibraryRows();
}

function renderLibraryRow(job) {
  const title = job.title || job.filename || "未命名书籍";
  const downloadButton = job.download_ready
    ? `<button class="row-action download-library" data-job-id="${escapeHtml(job.id)}" type="button">下载</button>`
    : "";
  return `
    <tr>
      <td>
        <div class="library-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
        <div class="library-filename">${escapeHtml(job.filename || "")}</div>
      </td>
      <td><span class="pill status-${escapeHtml(job.status)}">${statusLabels[job.status] || job.status}</span></td>
      <td>${formatNumber(job.progress)}%</td>
      <td>${formatNumber(job.completed_count)}/${formatNumber(job.chapter_count)}</td>
      <td>${formatDate(job.updated_at)}</td>
      <td>
        <button class="row-action continue-library" data-job-id="${escapeHtml(job.id)}" type="button">继续</button>
        ${downloadButton}
        <button class="row-action delete-library danger-text" data-job-id="${escapeHtml(job.id)}" type="button">删除</button>
      </td>
    </tr>
  `;
}

function bindLibraryRows() {
  document.querySelectorAll(".continue-library").forEach((button) => {
    button.addEventListener("click", () => loadJob(button.dataset.jobId));
  });
  document.querySelectorAll(".download-library").forEach((button) => {
    button.addEventListener("click", () => downloadJob(button.dataset.jobId));
  });
  document.querySelectorAll(".delete-library").forEach((button) => {
    button.addEventListener("click", () => deleteJob(button.dataset.jobId));
  });
}

async function loadJob(jobId) {
  state.jobId = jobId;
  state.selectedChapters.clear();
  localStorage.setItem(ACTIVE_JOB_KEY, jobId);
  showWorkspace();
  startPolling();
}

async function deleteJob(jobId) {
  if (!confirm("删除这本书及其本地结果？")) return;
  try {
    const response = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "删除失败");
    if (state.jobId === jobId) {
      state.jobId = null;
      state.lastJob = null;
      localStorage.removeItem(ACTIVE_JOB_KEY);
    }
    await refreshLibrary();
  } catch (error) {
    showError(error.message);
  }
}

async function createJobFromUpload(event) {
  event.preventDefault();
  const file = $("fileInput").files[0];
  if (!file) return;
  const apiKey = $("apiKeyInput").value.trim();
  if (!state.hasBackendApiKey && !apiKey) {
    showError("请先填写 MiniMax API Key。");
    $("apiKeyInput").focus();
    return;
  }

  $("startBtn").disabled = true;
  hideError();
  $("analysisPanel").classList.add("hidden");
  $("actionPanel").classList.add("hidden");
  $("progressPanel").classList.remove("hidden");
  $("chapterPanel").classList.add("hidden");
  $("previewView").classList.add("hidden");
  $("downloadBtn").classList.add("hidden");
  $("statusText").textContent = "上传中";
  $("progressText").textContent = "0%";
  $("elapsedText").textContent = "-";
  setProgress(0);
  state.selectedChapters.clear();

  const form = new FormData();
  form.append("file", file);
  form.append("model", $("modelSelect").value);
  form.append("region", $("regionSelect").value);
  if (apiKey) form.append("api_key", apiKey);

  try {
    const response = await fetch("/api/jobs", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "创建任务失败");
    state.jobId = data.job_id;
    localStorage.setItem(ACTIVE_JOB_KEY, state.jobId);
    startPolling();
  } catch (error) {
    showError(error.message);
    $("startBtn").disabled = false;
  }
}

function startPolling() {
  if (state.poller) clearInterval(state.poller);
  pollJob();
  state.poller = setInterval(pollJob, 1200);
}

function stopPollingIfIdle(status) {
  if (!["ready", "completed", "failed"].includes(status)) return;
  if (state.poller) clearInterval(state.poller);
  state.poller = null;
  $("startBtn").disabled = false;
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const response = await fetch(`/api/jobs/${state.jobId}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "读取任务失败");
    state.lastJob = data;
    renderJob(data);
    stopPollingIfIdle(data.status);
  } catch (error) {
    showError(error.message);
    if (state.poller) clearInterval(state.poller);
    state.poller = null;
    $("startBtn").disabled = false;
  }
}

async function startCondense(mode, chapterIds = []) {
  if (!state.jobId) return;
  try {
    setActionButtonsDisabled(true);
    const response = await fetch(`/api/jobs/${state.jobId}/condense`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, chapter_ids: chapterIds }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "启动浓缩失败");
    startPolling();
  } catch (error) {
    showError(error.message);
    setActionButtonsDisabled(false);
  }
}

async function retryChapter(chapterId) {
  await startCondense("selected", [chapterId]);
}

async function stopCondense() {
  if (!state.jobId) return;
  try {
    $("stopCondenseBtn").disabled = true;
    const response = await fetch(`/api/jobs/${state.jobId}/stop`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "停止任务失败");
    await pollJob();
  } catch (error) {
    showError(error.message);
  }
}

async function exportChapters(chapterIds) {
  if (!state.jobId) return;
  await downloadJob(state.jobId, chapterIds);
}

async function downloadJob(jobId, chapterIds = []) {
  try {
    const response = await fetch(`/api/jobs/${jobId}/exports`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_ids: chapterIds }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "导出失败");
    const anchor = document.createElement("a");
    anchor.href = data.download_url;
    anchor.download = data.filename || "condensed.epub";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } catch (error) {
    showError(error.message);
  }
}

function renderJob(job) {
  if (!job) return;
  if (!job.error) hideError();
  $("progressPanel").classList.remove("hidden");
  $("statusText").textContent = statusLabels[job.status] || job.status;
  $("progressText").textContent = `${job.progress || 0}%`;
  $("etaText").textContent = formatEta(job.eta_seconds, job.status);
  $("elapsedText").textContent = formatElapsed(job.elapsed_seconds, job.status);
  setProgress(job.progress || 0);

  if (job.status === "completed") {
    $("downloadBtn").classList.remove("hidden");
    $("downloadBtn").href = `/api/jobs/${job.id}/download`;
    $("subtitle").textContent = job.title ? `${job.title} - 已完成` : "浓缩完成";
  } else {
    $("downloadBtn").classList.add("hidden");
    $("subtitle").textContent = job.title || "EPUB / PDF / TXT → 浓缩 EPUB";
  }

  if (job.title || job.integrity) {
    $("analysisPanel").classList.remove("hidden");
    $("bookTitle").textContent = job.title || job.filename || "-";
    $("chapterCount").textContent = job.integrity ? job.integrity.chapter_count : "-";
    $("totalCount").textContent = job.integrity ? formatNumber(job.integrity.total_count) : "-";
    $("integrityState").textContent = job.integrity
      ? job.integrity.is_complete
        ? "通过"
        : "有警告"
      : "-";
  }

  if (job.error) showError(job.error);
  if (job.status === "failed" && looksLikeAuthFailure(job.error)) {
    state.hasBackendApiKey = false;
    renderApiKeyRequirement();
    $("apiKeyInput").focus();
  }

  if (job.chapters && job.chapters.length) {
    $("chapterPanel").classList.remove("hidden");
    $("actionPanel").classList.remove("hidden");
    $("chapterRows").innerHTML = job.chapters.map(renderChapterRow).join("");
    bindRowControls();
    renderSelectionState(job);
  }
  setActionButtonsDisabled(job.status === "analyzing" || job.status === "condensing");
}

function renderChapterRow(chapter) {
  const cls = `status-${chapter.status}`;
  const error = chapter.error ? `<div class="chapter-error">${escapeHtml(chapter.error)}</div>` : "";
  const checked = state.selectedChapters.has(chapter.id) ? "checked" : "";
  const retry =
    chapter.status === "failed"
      ? `<button class="row-action retry-chapter" data-chapter-id="${escapeHtml(chapter.id)}" type="button">重试</button>`
      : "";
  const preview =
    chapter.status === "done"
      ? `<button class="row-action preview-chapter" data-chapter-id="${escapeHtml(chapter.id)}" type="button">预览</button>`
      : "";
  return `
    <tr>
      <td class="select-col">
        <input class="chapter-check" data-chapter-id="${escapeHtml(chapter.id)}" type="checkbox" ${checked} />
      </td>
      <td>
        <div class="chapter-title" title="${escapeHtml(chapter.title)}">${escapeHtml(chapter.title)}</div>
        ${error}
      </td>
      <td>${formatNumber(chapter.original_count)}</td>
      <td><span class="pill ${cls}">${chapterStatusLabels[chapter.status] || chapter.status}</span></td>
      <td>${chapter.condensed_count ? formatNumber(chapter.condensed_count) : "-"}</td>
      <td>${preview}${retry}</td>
    </tr>
  `;
}

function bindRowControls() {
  document.querySelectorAll(".chapter-check").forEach((checkbox) => {
    checkbox.addEventListener("change", (event) => {
      const chapterId = event.target.dataset.chapterId;
      if (event.target.checked) {
        state.selectedChapters.add(chapterId);
      } else {
        state.selectedChapters.delete(chapterId);
      }
      renderSelectionState(state.lastJob);
    });
  });
  document.querySelectorAll(".retry-chapter").forEach((button) => {
    button.addEventListener("click", () => retryChapter(button.dataset.chapterId));
  });
  document.querySelectorAll(".preview-chapter").forEach((button) => {
    button.addEventListener("click", () => previewChapter(button.dataset.chapterId));
  });
}

function renderSelectionState(job) {
  const chapters = job?.chapters || [];
  const selected = getSelectedChapterIds();
  const doneSelected = chapters.filter(
    (chapter) => selected.includes(chapter.id) && chapter.status === "done",
  ).length;
  const done = job.completed_count || 0;
  const failed = job.failed_count || 0;
  const total = chapters.length;
  $("selectAllChapters").checked = total > 0 && selected.length === total;
  $("actionSummary").textContent =
    `已选 ${selected.length} 章，选中可导出 ${doneSelected} 章；` +
    `已完成 ${done}/${total} 章，失败 ${failed} 章。`;
  $("retryFailedBtn").disabled = failed === 0 || job.status === "condensing";
  $("stopCondenseBtn").disabled = job.status !== "condensing";
  $("exportDoneBtn").disabled = done === 0;
  $("exportSelectedBtn").disabled = doneSelected === 0;
  $("condenseSelectedBtn").disabled = selected.length === 0 || job.status === "condensing";
}

async function previewChapter(chapterId) {
  if (!state.jobId || !chapterId) return;
  window.scrollTo(0, 0);
  $("uploadView").classList.add("hidden");
  $("libraryView").classList.add("hidden");
  $("authPanel").classList.add("hidden");
  $("previewView").classList.remove("hidden");
  $("subtitle").textContent = "章节预览";
  $("previewOriginalTitle").textContent = "加载中";
  $("previewTitle").textContent = "加载中";
  $("originalContent").innerHTML = "";
  $("previewContent").innerHTML = "";
  $("originalCountText").textContent = "-";
  $("condensedCountText").textContent = "-";

  const response = await fetch(`/api/jobs/${state.jobId}/chapters/${chapterId}`);
  const data = await response.json();
  if (!response.ok) {
    $("previewOriginalTitle").textContent = "章节加载失败";
    $("previewTitle").textContent = "章节加载失败";
    $("previewContent").textContent = data.detail || "无法读取章节";
    return;
  }
  $("previewOriginalTitle").textContent = data.title;
  $("previewTitle").textContent = data.title;
  $("originalContent").innerHTML = textToParagraphs(data.original_content);
  $("previewContent").innerHTML = textToParagraphs(data.condensed_content || data.content);
  $("originalCountText").textContent = `原字数 ${formatNumber(data.original_count)}`;
  $("condensedCountText").textContent = `浓缩后 ${formatNumber(data.condensed_count)}`;
}

function getSelectedChapterIds() {
  return Array.from(state.selectedChapters);
}

function setActionButtonsDisabled(disabled) {
  [
    "condenseOneBtn",
    "condenseTenBtn",
    "condenseAllBtn",
    "condenseSelectedBtn",
    "exportSelectedBtn",
    "exportDoneBtn",
    "retryFailedBtn",
  ].forEach((id) => {
    $(id).disabled = disabled;
  });
  if (!disabled && state.lastJob) renderSelectionState(state.lastJob);
}

function setProgress(value) {
  const clamped = Math.max(0, Math.min(100, Number(value) || 0));
  $("progressFill").style.width = `${clamped}%`;
}

function renderApiKeyRequirement() {
  const form = $("uploadForm");
  const field = $("apiKeyField");
  const input = $("apiKeyInput");
  if (state.hasBackendApiKey) {
    form.classList.remove("needs-key");
    field.classList.add("hidden");
    input.required = false;
    input.value = "";
    return;
  }
  form.classList.add("needs-key");
  field.classList.remove("hidden");
  input.required = true;
}

function looksLikeAuthFailure(message) {
  return /API Key|鉴权|Unauthorized|401|403/i.test(String(message || ""));
}

function showError(message) {
  if (!message) {
    hideError();
    return;
  }
  $("globalError").textContent = message;
  $("globalError").classList.remove("hidden");
  $("errorText").textContent = message;
  $("errorText").classList.remove("hidden");
}

function hideError() {
  $("globalError").textContent = "";
  $("globalError").classList.add("hidden");
  $("errorText").textContent = "";
  $("errorText").classList.add("hidden");
}

function formatEta(seconds, status) {
  if (status === "ready") return "等待选择浓缩范围";
  if (status === "completed") return "全部完成";
  if (seconds === null || seconds === undefined) return "预计时间计算中";
  if (seconds <= 0) return "即将完成";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes <= 0) return `预计剩余约 ${rest} 秒`;
  return `预计剩余约 ${minutes} 分 ${rest} 秒`;
}

function formatElapsed(seconds, status) {
  if (seconds === null || seconds === undefined) return "-";
  const text = `已用时 ${formatDuration(seconds)}`;
  return status === "completed" ? `完成用时 ${formatDuration(seconds)}` : text;
}

function formatDuration(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const rest = safe % 60;
  if (hours) return `${hours} 时 ${minutes} 分 ${rest} 秒`;
  if (minutes) return `${minutes} 分 ${rest} 秒`;
  return `${rest} 秒`;
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(value || 0);
}

function formatDate(seconds) {
  if (!seconds) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(seconds * 1000));
}

function textToParagraphs(text) {
  return String(text || "")
    .split(/\n{2,}/)
    .filter(Boolean)
    .map((block) => `<p>${escapeHtml(block).replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

init().catch((error) => showError(error.message));
