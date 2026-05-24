const ACTIVE_JOB_KEY = "bookCondenser.activeJobId";

const state = {
  jobId: null,
  poller: null,
  lastJob: null,
  previewLoaded: false,
  hasBackendApiKey: true,
  selectedChapters: new Set(),
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
  const response = await fetch("/api/models");
  const data = await response.json();
  $("modelSelect").innerHTML = data.models
    .map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`)
    .join("");
  $("modelSelect").value = data.default;
  $("regionSelect").innerHTML = data.regions
    .map((region) => `<option value="${escapeHtml(region.id)}">${escapeHtml(region.label)}</option>`)
    .join("");
  $("regionSelect").value = data.stored_region || data.default_region || "cn";
  state.hasBackendApiKey = Boolean(data.has_api_key);
  renderApiKeyRequirement();
  bindActions();

  const savedJobId = localStorage.getItem(ACTIVE_JOB_KEY);
  if (savedJobId) {
    state.jobId = savedJobId;
    startPolling();
  }
}

function bindActions() {
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
}

$("fileInput").addEventListener("change", (event) => {
  const file = event.target.files[0];
  $("fileName").textContent = file ? file.name : "选择书籍文件";
});

$("uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = $("fileInput").files[0];
  if (!file) return;
  const apiKey = $("apiKeyInput").value.trim();
  if (!state.hasBackendApiKey && !apiKey) {
    showError("后台未配置 MiniMax API Key，请先填写 API Key。");
    $("apiKeyInput").focus();
    return;
  }

  $("startBtn").disabled = true;
  $("errorText").classList.add("hidden");
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
  if (!state.hasBackendApiKey) form.append("api_key", apiKey);

  try {
    const response = await fetch("/api/jobs", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "创建任务失败");
    state.jobId = data.job_id;
    localStorage.setItem(ACTIVE_JOB_KEY, state.jobId);
    state.previewLoaded = false;
    startPolling();
  } catch (error) {
    showError(error.message);
    $("startBtn").disabled = false;
  }
});

$("chapterSelect").addEventListener("change", () => {
  loadChapter($("chapterSelect").value);
});

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
    if (data.status === "completed" && !state.previewLoaded) {
      enterPreview(data);
    }
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
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/exports`, {
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
  $("progressPanel").classList.remove("hidden");
  $("statusText").textContent = statusLabels[job.status] || job.status;
  $("progressText").textContent = `${job.progress || 0}%`;
  $("etaText").textContent = formatEta(job.eta_seconds, job.status);
  $("elapsedText").textContent = formatElapsed(job.elapsed_seconds, job.status);
  setProgress(job.progress || 0);

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
      <td>${retry}</td>
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

function enterPreview(job) {
  state.previewLoaded = true;
  $("previewView").classList.remove("hidden");
  $("downloadBtn").classList.remove("hidden");
  $("downloadBtn").href = `/api/jobs/${job.id}/download`;
  $("subtitle").textContent = job.title ? `${job.title} - 浓缩完成` : "浓缩完成";
  $("chapterSelect").innerHTML = job.chapters
    .filter((chapter) => chapter.status === "done")
    .map((chapter) => `<option value="${escapeHtml(chapter.id)}">${escapeHtml(chapter.title)}</option>`)
    .join("");
  const first = $("chapterSelect").value;
  if (first) loadChapter(first);
}

async function loadChapter(chapterId) {
  if (!state.jobId || !chapterId) return;
  $("previewTitle").textContent = "加载中";
  $("previewContent").innerHTML = "";
  $("previewMeta").textContent = "";
  const response = await fetch(`/api/jobs/${state.jobId}/chapters/${chapterId}`);
  const data = await response.json();
  if (!response.ok) {
    $("previewTitle").textContent = "章节加载失败";
    $("previewContent").textContent = data.detail || "无法读取章节";
    return;
  }
  $("previewTitle").textContent = data.title;
  $("previewContent").innerHTML = textToParagraphs(data.content);
  $("previewMeta").innerHTML = `
    <div>原字数：${formatNumber(data.original_count)}</div>
    <div>浓缩后：${formatNumber(data.condensed_count)}</div>
  `;
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
  $("errorText").textContent = message;
  $("errorText").classList.remove("hidden");
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
