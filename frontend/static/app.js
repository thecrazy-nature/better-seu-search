const form = document.querySelector("#searchForm");
const queryInput = document.querySelector("#queryInput");
const identityInput = document.querySelector("#identityInput");
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
const collectionSelect = document.querySelector("#collectionSelect");
const collectionMeta = document.querySelector("#collectionMeta");
const resultOverlay = document.querySelector("#resultOverlay");
const closeResults = document.querySelector("#closeResults");
const resultCount = document.querySelector("#resultCount");
const confidenceBadge = document.querySelector("#confidenceBadge");
const sortButtons = document.querySelectorAll("[data-sort-mode]");
const assistantOverlay = document.querySelector("#assistantOverlay");
const closeAssistant = document.querySelector("#closeAssistant");
const assistantTitle = document.querySelector("#assistantTitle");
const assistantQuestionInput = document.querySelector("#assistantQuestionInput");
const assistantForm = document.querySelector("#assistantForm");
const reimbursementFields = document.querySelector("#reimbursementFields");
const overseasFields = document.querySelector("#overseasFields");
const projectNameInput = document.querySelector("#projectNameInput");
const projectCodeInput = document.querySelector("#projectCodeInput");
const expenseTypeInput = document.querySelector("#expenseTypeInput");
const invoiceDateInput = document.querySelector("#invoiceDateInput");
const paymentTargetInput = document.querySelector("#paymentTargetInput");
const applicantTypeInput = document.querySelector("#applicantTypeInput");
const destinationInput = document.querySelector("#destinationInput");
const visitTypeInput = document.querySelector("#visitTypeInput");
const fundingSourceInput = document.querySelector("#fundingSourceInput");
const startDateInput = document.querySelector("#startDateInput");
const assistantAnswerPanel = document.querySelector("#assistantAnswerPanel");
const assistantAnswerText = document.querySelector("#assistantAnswerText");
const assistantSourcesPanel = document.querySelector("#assistantSourcesPanel");
const assistantSources = document.querySelector("#assistantSources");
const assistantSourceCount = document.querySelector("#assistantSourceCount");

let searchSessionId = null;
let canManageIndex = false;
let currentSortMode = "relevance";
let currentHits = [];
let assistantMode = "reimbursement";
let selectedCollectionId = null;
let collections = [];

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

function openAssistant(mode) {
  assistantMode = mode === "overseas" ? "overseas" : "reimbursement";
  assistantTitle.textContent = assistantMode === "overseas" ? "出国申请助手" : "项目报销助手";
  assistantQuestionInput.placeholder =
    assistantMode === "overseas"
      ? "例如：研究生去日本参加国际会议，出国申请怎么走流程？"
      : "例如：我的项目产生了一笔差旅费用，应该怎么报销？";
  reimbursementFields.hidden = assistantMode !== "reimbursement";
  overseasFields.hidden = assistantMode !== "overseas";
  assistantAnswerPanel.hidden = true;
  assistantSourcesPanel.hidden = true;
  assistantAnswerText.textContent = "";
  assistantSources.innerHTML = "";
  assistantOverlay.style.display = "";
  assistantOverlay.hidden = false;
  document.body.style.overflow = "hidden";
  assistantQuestionInput.focus();
}

function hideAssistant() {
  assistantOverlay.hidden = true;
  assistantOverlay.style.display = "none";
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
  updateCollectionMeta();
}

function refreshAccess() {
  return fetch("/api/health")
    .then((response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
    .then((data) => {
      applyAccess(data.access || {});
      return data;
    });
}

function normalizeStudentType(value) {
  if (!value) {
    return null;
  }
  if (/留学生/.test(value)) {
    return "留学生";
  }
  if (/交换生/.test(value)) {
    return "交换生";
  }
  if (/(研究生|硕士|博士|研[一二三]|博[一二三四五])/.test(value)) {
    return "研究生";
  }
  if (/(本科生|大[一二三四五六])/.test(value)) {
    return "本科生";
  }
  return null;
}

function parseIdentityProfile(rawValue) {
  const text = String(rawValue || "").trim();
  if (!text) {
    return {
      identity_text: null,
      college: null,
      grade: null,
      student_type: null,
    };
  }

  const compactText = text.replace(/\s+/g, "");
  const collegeMatch = compactText.match(/([\u4e00-\u9fff]{2,24}(?:学院|书院|系))/);
  const gradeMatch = compactText.match(/(20\d{2}级|大[一二三四五六](?:上|下)?|研[一二三](?:上|下)?|博[一二三四五](?:上|下)?)/);
  const studentTypeMatch = compactText.match(/(本科生|研究生|硕士|博士|留学生|交换生)/);
  const normalizedStudentType = normalizeStudentType(studentTypeMatch ? studentTypeMatch[1] : compactText);

  return {
    identity_text: text,
    college: collegeMatch ? collegeMatch[1] : null,
    grade: gradeMatch ? gradeMatch[1] : null,
    student_type: normalizedStudentType,
  };
}

function profilePayload() {
  const identityProfile = parseIdentityProfile(identityInput?.value);
  return {
    identity_text: identityProfile.identity_text,
    college: identityProfile.college,
    grade: identityProfile.grade,
    student_type: identityProfile.student_type,
  };
}

function currentCollection() {
  return collections.find((item) => item.id === selectedCollectionId) || null;
}

function updateCollectionMeta() {
  if (!collectionMeta) {
    return;
  }
  const collection = currentCollection();
  if (!collection) {
    collectionMeta.textContent = "暂无可用数据库集";
    crawlButton.disabled = true;
    return;
  }
  collectionMeta.textContent = `${Number(collection.document_count || 0)} 篇文档 / ${Number(
    collection.source_count || 0,
  )} 个站点`;
  crawlButton.disabled = !canManageIndex;
}

function renderCollections() {
  if (!collectionSelect) {
    return;
  }
  collectionSelect.innerHTML = "";
  if (!collections.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无可用数据库集";
    collectionSelect.appendChild(option);
    collectionSelect.disabled = true;
    selectedCollectionId = null;
    updateCollectionMeta();
    return;
  }

  if (!collections.some((item) => item.id === selectedCollectionId)) {
    selectedCollectionId = collections[0].id;
  }

  collectionSelect.disabled = false;
  for (const collection of collections) {
    const option = document.createElement("option");
    option.value = String(collection.id);
    option.textContent = collection.name;
    option.selected = collection.id === selectedCollectionId;
    collectionSelect.appendChild(option);
  }
  updateCollectionMeta();
}

function refreshCollections() {
  return fetch("/api/collections")
    .then((response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
    .then((data) => {
      collections = data.collections || [];
      renderCollections();
      return data;
    });
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

function renderAssistantAnswer(data) {
  assistantAnswerPanel.hidden = false;
  assistantAnswerText.textContent = data.answer || "未生成办理建议。";
  const sources = data.sources || [];
  assistantSourceCount.textContent = sources.length ? `${sources.length} 条来源` : "未返回来源";
  if (!sources.length) {
    assistantSourcesPanel.hidden = true;
    assistantSources.innerHTML = "";
    return;
  }
  assistantSourcesPanel.hidden = false;
  assistantSources.innerHTML = sources
    .map(
      (source) => `
        <article class="result-item">
          <a class="result-title" href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(
            source.title || "未命名来源",
          )}</a>
          <div class="meta">
            <span>${escapeHtml(source.ref || "")}</span>
            <span>${escapeHtml(source.source_unit || "")}</span>
            <span>${escapeHtml(source.publish_date || "")}</span>
            <span>${escapeHtml(source.slot || "")}</span>
          </div>
          <p class="snippet">${escapeHtml(source.quote || "")}</p>
        </article>
      `,
    )
    .join("");
}

async function runAssistant() {
  const submitButton = assistantForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  assistantAnswerPanel.hidden = false;
  assistantSourcesPanel.hidden = true;
  assistantAnswerText.textContent = "正在根据校内事务数据源生成办理建议...";
  try {
    const basePayload = {
      question: assistantQuestionInput.value.trim(),
      profile: profilePayload(),
    };
    const url =
      assistantMode === "overseas" ? "/api/assistant/overseas" : "/api/assistant/reimbursement";
    const payload =
      assistantMode === "overseas"
        ? {
            ...basePayload,
            applicant_type: applicantTypeInput.value || null,
            destination: destinationInput.value.trim() || null,
            visit_type: visitTypeInput.value.trim() || null,
            funding_source: fundingSourceInput.value.trim() || null,
            start_date: startDateInput.value.trim() || null,
          }
        : {
            ...basePayload,
            project_name: projectNameInput.value.trim() || null,
            project_code: projectCodeInput.value.trim() || null,
            expense_type: expenseTypeInput.value.trim() || null,
            invoice_date: invoiceDateInput.value.trim() || null,
            payment_target: paymentTargetInput.value || null,
          };
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    renderAssistantAnswer(await response.json());
  } catch (error) {
    assistantAnswerText.textContent = `生成失败：${error.message}`;
  } finally {
    submitButton.disabled = false;
  }
}

async function runSearch(query) {
  if (!selectedCollectionId) {
    setStatus("请先选择一个数据库集。");
    return;
  }
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
        collection_id: selectedCollectionId,
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
    const collectionName = data.collection_name || currentCollection()?.name || "";
    setStatus(
      collectionName
        ? `完成：已在 ${collectionName} 中找到 ${data.hits.length} 条候选来源。`
        : `完成：找到 ${data.hits.length} 条候选来源。`,
      true,
    );
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

if (collectionSelect) {
  collectionSelect.addEventListener("change", () => {
    selectedCollectionId = Number(collectionSelect.value || 0) || null;
    searchSessionId = null;
    updateCollectionMeta();
  });
}

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

document.querySelectorAll("[data-assistant-open]").forEach((button) => {
  button.addEventListener("click", () => {
    openAssistant(button.dataset.assistantOpen);
  });
});

assistantForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (assistantQuestionInput.value.trim()) {
    runAssistant();
  }
});

closeAssistant.addEventListener("click", hideAssistant);

assistantOverlay.addEventListener("click", (event) => {
  if (event.target === assistantOverlay) {
    hideAssistant();
  }
});

closeResults.addEventListener("click", hideResults);

resultOverlay.addEventListener("click", (event) => {
  if (event.target === resultOverlay) {
    hideResults();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !assistantOverlay.hidden) {
    hideAssistant();
  } else if (event.key === "Escape" && !resultOverlay.hidden) {
    hideResults();
  }
});

crawlButton.addEventListener("click", async () => {
  if (!canManageIndex) {
    setStatus("局域网访客模式下仅支持搜索，不能更新索引。");
    return;
  }
  if (!selectedCollectionId) {
    setStatus("请先选择一个数据库集。");
    return;
  }
  crawlButton.disabled = true;
  setStatus("索引更新任务已提交，正在等待后台开始...");
  try {
    const response = await fetch(`/api/collections/${selectedCollectionId}/crawl`, { method: "POST" });
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

Promise.all([refreshAccess(), refreshCollections()])
  .then(([health]) => {
    if (!collections.length) {
      setStatus("还没有启用的数据集，请先在本机管理页面创建一个。");
      return;
    }
    if (Number(health.documents || 0) === 0) {
      setStatus("当前索引还是空的。可以先选择一个数据库集，再执行索引更新。");
      return;
    }
    setStatus(`当前索引中有 ${health.documents} 条官网资料，可在上方切换数据库集。`, true);
  })
  .catch(() => {});
