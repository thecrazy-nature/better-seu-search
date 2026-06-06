const form = document.querySelector("#searchForm");
const queryInput = document.querySelector("#queryInput");
const collegeInput = document.querySelector("#collegeInput");
const gradeInput = document.querySelector("#gradeInput");
const studentTypeInput = document.querySelector("#studentTypeInput");
const statusBox = document.querySelector("#status");
const planPanel = document.querySelector("#planPanel");
const planJson = document.querySelector("#planJson");
const answerPanel = document.querySelector("#answerPanel");
const answerText = document.querySelector("#answerText");
const warningList = document.querySelector("#warningList");
const evidencePanel = document.querySelector("#evidencePanel");
const evidenceList = document.querySelector("#evidenceList");
const judgePanel = document.querySelector("#judgePanel");
const judgeSummary = document.querySelector("#judgeSummary");
const judgeList = document.querySelector("#judgeList");
const resultsPanel = document.querySelector("#resultsPanel");
const results = document.querySelector("#results");
const crawlButton = document.querySelector("#crawlButton");
const accessHint = document.querySelector("#accessHint");
const resultOverlay = document.querySelector("#resultOverlay");
const closeResults = document.querySelector("#closeResults");
const resultCount = document.querySelector("#resultCount");
const confidenceBadge = document.querySelector("#confidenceBadge");
const sortButtons = document.querySelectorAll("[data-sort-mode]");

let searchSessionId = null;
let canManageIndex = false;
let currentSortMode = "relevance";
let currentHits = [];

function setStatus(message, visible = true) {
  statusBox.hidden = !visible;
  statusBox.textContent = message;
}

function openResults() {
  resultOverlay.style.display = "";
  resultOverlay.hidden = false;
  document.body.style.overflow = "hidden";
}

function hideResults() {
  resultOverlay.hidden = true;
  resultOverlay.style.display = "none";
  document.body.style.overflow = "";
}

function resetResultPanels() {
  planPanel.hidden = true;
  answerPanel.hidden = true;
  evidencePanel.hidden = true;
  judgePanel.hidden = true;
  resultsPanel.hidden = true;
  planJson.textContent = "";
  answerText.innerHTML = "";
  evidenceList.innerHTML = "";
  judgeSummary.innerHTML = "";
  judgeList.innerHTML = "";
  results.innerHTML = "";
  resultCount.textContent = "";
  confidenceBadge.hidden = true;
  warningList.hidden = true;
  warningList.innerHTML = "";
  currentHits = [];
}

function applyAccess(access) {
  canManageIndex = Boolean(access?.can_manage_index);
  crawlButton.hidden = !canManageIndex;
  accessHint.hidden = canManageIndex;
  accessHint.textContent = canManageIndex
    ? ""
    : "局域网访客模式：仅支持搜索与查看结果，不可更新索引。";
}

function refreshAccess() {
  fetch("/api/health")
    .then((response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
    .then((data) => {
      applyAccess(data.access || {});
      setStatus(`当前索引中有 ${data.documents} 条官网资料。`, true);
    })
    .catch(() => {});
}

function profilePayload() {
  return {
    college: collegeInput.value.trim() || null,
    grade: gradeInput.value.trim() || null,
    student_type: studentTypeInput.value || null,
  };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderAnswer(value) {
  const escaped = escapeHtml(value);
  answerText.innerHTML = escaped.replace(/\*\*(.+?)\*\*/gs, "<strong>$1</strong>");
}

function renderConfidence(confidence) {
  if (!confidence) {
    confidenceBadge.hidden = true;
    return;
  }
  const labels = {
    high: "高置信度",
    medium: "中等置信度",
    low: "低置信度",
    none: "无明确依据",
  };
  confidenceBadge.hidden = false;
  confidenceBadge.textContent = labels[confidence] || confidence;
}

function parsePublishTime(hit) {
  const rawDate = String(hit.publish_date || "").trim();
  if (!rawDate) return 0;
  const normalized = rawDate.includes("/") ? rawDate.replaceAll("/", "-") : rawDate;
  const timestamp = Date.parse(normalized);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function sortedHits(hits) {
  if (currentSortMode === "time") {
    return [...hits].sort((left, right) => {
      const timeDelta = parsePublishTime(right) - parsePublishTime(left);
      if (timeDelta !== 0) return timeDelta;
      return Number(right.score || 0) - Number(left.score || 0);
    });
  }
  return [...hits].sort((left, right) => {
    const scoreDelta = Number(right.score || 0) - Number(left.score || 0);
    if (scoreDelta !== 0) return scoreDelta;
    return parsePublishTime(right) - parsePublishTime(left);
  });
}

function updateSortButtons() {
  sortButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.sortMode === currentSortMode);
  });
}

function setSortMode(mode) {
  currentSortMode = mode === "time" ? "time" : "relevance";
  updateSortButtons();
  renderResults();
}

function renderResults(hits = currentHits) {
  currentHits = hits;
  const visibleHits = sortedHits(currentHits);
  resultCount.textContent = visibleHits.length ? `${visibleHits.length} 条来源` : "未找到来源";
  if (!visibleHits.length) {
    results.innerHTML = '<p class="empty">没有检索到可引用的官网来源。</p>';
    return;
  }
  results.innerHTML = visibleHits
    .map((hit) => {
      const tags = [
        hit.source,
        hit.category,
        hit.publish_date,
        ...(hit.topics || []),
        ...(hit.keywords || []).slice(0, 4),
        ...(hit.applicable_colleges || []).slice(0, 2),
        ...(hit.applicable_grades || []).slice(0, 2),
      ]
        .filter(Boolean)
        .map((item) => `<span class="tag">${escapeHtml(item)}</span>`)
        .join("");
      const attachments = (hit.attachments || [])
        .slice(0, 4)
        .map(
          (item) =>
            `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(
              item.name || "附件",
            )}</a>`,
        )
        .join("");
      const judgeMeta = hit.evidence_judge_label
        ? [
            `证据判断：${hit.evidence_judge_label}`,
            hit.evidence_judge_confidence !== null && hit.evidence_judge_confidence !== undefined
              ? `置信度 ${Number(hit.evidence_judge_confidence).toFixed(2)}`
              : "",
            (hit.evidence_judge_answerable_slots || []).length
              ? `可回答：${hit.evidence_judge_answerable_slots.join("、")}`
              : "",
          ]
            .filter(Boolean)
            .join(" / ")
        : "";
      return `
        <article class="result-item">
          <a class="result-title" href="${escapeHtml(hit.url)}" target="_blank" rel="noreferrer">${escapeHtml(
            hit.title,
          )}</a>
          <div class="meta">${tags}<span>相关度 ${Number(hit.score || 0).toFixed(2)}</span></div>
          ${
            hit.relevance_note
              ? `<p class="relevance-note">相关说明：${escapeHtml(hit.relevance_note)}</p>`
              : ""
          }
          ${judgeMeta ? `<p class="judge-meta">${escapeHtml(judgeMeta)}</p>` : ""}
          <p class="snippet">${escapeHtml(hit.snippet || "")}</p>
          ${attachments ? `<div class="attachments">${attachments}</div>` : ""}
        </article>
      `;
    })
    .join("");
}

function renderJudge(report) {
  if (!report) {
    judgePanel.hidden = true;
    judgeSummary.innerHTML = "";
    judgeList.innerHTML = "";
    return;
  }
  judgePanel.hidden = false;
  judgeSummary.innerHTML = `
    <div class="judge-summary-grid">
      <span>状态：${escapeHtml(report.status || "-")}</span>
      <span>候选：${Number(report.candidate_count || 0)}</span>
      <span>保留：${Number(report.accepted_count || 0)}</span>
      <span>剔除：${Number(report.rejected_count || 0)}</span>
    </div>
    ${report.notes ? `<p class="relevance-note">${escapeHtml(report.notes)}</p>` : ""}
  `;
  const rejected = (report.rejected || []).slice(0, 6);
  if (!rejected.length) {
    judgeList.innerHTML = '<p class="empty">没有被 Evidence Judge 剔除的候选。</p>';
    return;
  }
  judgeList.innerHTML = rejected
    .map((item) => {
      const meta = [
        item.label,
        item.confidence !== null && item.confidence !== undefined ? `置信度 ${Number(item.confidence).toFixed(2)}` : "",
        item.publish_date,
        item.chunk_kind,
        item.attachment_name ? `附件：${item.attachment_name}` : "",
      ]
        .filter(Boolean)
        .join(" / ");
      return `
        <article class="judge-item">
          <a href="${escapeHtml(item.url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(item.title || "未命名来源")}</a>
          <p>${escapeHtml(meta)}</p>
          <p>${escapeHtml(item.reason || "未说明原因")}</p>
        </article>
      `;
    })
    .join("");
}

function renderWarnings(warnings) {
  if (!warnings || !warnings.length) {
    warningList.hidden = true;
    warningList.innerHTML = "";
    return;
  }
  warningList.hidden = false;
  warningList.innerHTML = warnings
    .map((warning) => `<div class="warning-item">${escapeHtml(warning)}</div>`)
    .join("");
}

function renderEvidence(evidence) {
  if (!evidence || !evidence.length) {
    evidencePanel.hidden = true;
    evidenceList.innerHTML = "";
    return;
  }
  evidencePanel.hidden = false;
  evidenceList.innerHTML = evidence
    .map((item) => {
      const location = [
        item.attachment_name ? `附件：${item.attachment_name}` : "",
        item.page ? `第 ${item.page} 页` : "",
        item.heading,
      ].filter(Boolean);
      const quality = [
        item.evidence_type ? `证据类型：${item.evidence_type}` : "",
        item.fact_confidence !== null && item.fact_confidence !== undefined
          ? `事实置信度 ${Number(item.fact_confidence).toFixed(2)}`
          : "",
      ].filter(Boolean);
      const meta = [item.source, item.publish_date, item.title, ...location, ...quality].filter(Boolean).join(" / ");
      const attachments = (item.attachments || [])
        .slice(0, 3)
        .map(
          (attachment) =>
            `<a href="${escapeHtml(attachment.url)}" target="_blank" rel="noreferrer">${escapeHtml(
              attachment.name || "附件",
            )}</a>`,
        )
        .join("");
      return `
        <article class="evidence-item">
          <div class="evidence-head">
            <span>${escapeHtml(item.ref)} ${escapeHtml(item.reason)}</span>
            <a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开来源</a>
          </div>
          ${meta ? `<p class="evidence-meta">${escapeHtml(meta)}</p>` : ""}
          <blockquote>${escapeHtml(item.quote)}</blockquote>
          ${attachments ? `<div class="attachments">${attachments}</div>` : ""}
        </article>
      `;
    })
    .join("");
}

async function runSearch(query) {
  const submitButton = form.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  resetResultPanels();
  openResults();
  answerPanel.hidden = false;
  answerText.textContent = "正在理解问题、检索官网资料并生成总结...";
  setStatus("正在检索官网资料...");
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        profile: profilePayload(),
        limit: 20,
        session_id: searchSessionId,
      }),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    searchSessionId = data.session_id || searchSessionId;
    planJson.textContent = JSON.stringify(data.query_plan, null, 2);
    renderAnswer(data.answer.answer);
    renderConfidence(data.answer.confidence);
    renderWarnings(data.answer.warnings);
    renderEvidence(data.answer.evidence);
    renderJudge(data.evidence_judge);
    currentSortMode = "relevance";
    updateSortButtons();
    renderResults(data.hits);
    planPanel.hidden = false;
    answerPanel.hidden = false;
    resultsPanel.hidden = false;
    setStatus(`完成：找到 ${data.hits.length} 条候选来源。`, true);
  } catch (error) {
    answerText.textContent = `检索失败：${error.message}`;
    setStatus(`检索失败：${error.message}`);
  } finally {
    submitButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (query) runSearch(query);
});

document.querySelectorAll("[data-query]").forEach((button) => {
  button.addEventListener("click", () => {
    queryInput.value = button.dataset.query;
    runSearch(button.dataset.query);
  });
});

sortButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setSortMode(button.dataset.sortMode);
  });
});

closeResults.addEventListener("click", hideResults);

resultOverlay.addEventListener("click", (event) => {
  if (event.target === resultOverlay) {
    hideResults();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !resultOverlay.hidden) {
    hideResults();
  }
});

crawlButton.addEventListener("click", async () => {
  if (!canManageIndex) {
    setStatus("局域网访客模式下仅支持搜索，不能更新索引。");
    return;
  }
  crawlButton.disabled = true;
  setStatus("索引更新任务已提交，正在等待后台开始...");
  try {
    const response = await fetch("/api/crawl", { method: "POST" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    await pollCrawlTask(data.task_id);
  } catch (error) {
    setStatus(`索引更新失败：${error.message}`);
    crawlButton.disabled = false;
  }
});

async function pollCrawlTask(taskId) {
  try {
    while (true) {
      const response = await fetch(`/api/crawl/tasks/${encodeURIComponent(taskId)}`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const task = await response.json();
      if (task.status === "queued") {
        setStatus("索引更新排队中...");
      } else if (task.status === "running") {
        setStatus("正在后台更新索引。可以继续检索，完成后会自动提示。");
      } else if (task.status === "completed") {
        setStatus(`索引更新完成：本次写入 ${task.upserted} 条，库内共有 ${task.total_documents} 条。`);
        crawlButton.disabled = false;
        return;
      } else if (task.status === "failed") {
        setStatus(`索引更新失败：${task.error || "未知错误"}`);
        crawlButton.disabled = false;
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  } finally {
    if (crawlButton.disabled && document.hidden) {
      crawlButton.disabled = false;
    }
  }
}

refreshAccess();
