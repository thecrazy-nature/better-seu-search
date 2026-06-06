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
const assistantForm = document.querySelector("#assistantForm");
const assistantModeInput = document.querySelector("#assistantModeInput");
const assistantModeCards = Array.from(document.querySelectorAll("[data-assistant-mode]"));
const assistantQuestionInput = document.querySelector("#assistantQuestionInput");
const reimbursementFields = document.querySelector("#reimbursementFields");
const overseasFields = document.querySelector("#overseasFields");
const assistantAnswerPanel = document.querySelector("#assistantAnswerPanel");
const assistantAnswerTitle = document.querySelector("#assistantAnswerTitle");
const assistantAnswerText = document.querySelector("#assistantAnswerText");
const assistantSourcesPanel = document.querySelector("#assistantSourcesPanel");
const assistantSources = document.querySelector("#assistantSources");
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
const titleBlock = document.querySelector(".title-row > div");
const accessHint = document.querySelector("#accessHint") || document.createElement("p");
const collectionBar = document.querySelector("#collectionRow") || document.createElement("div");
const collectionSelect = document.querySelector("#collectionSelect") || document.createElement("select");
const collectionMeta = document.querySelector("#collectionMeta") || document.createElement("span");
const adminLink = document.querySelector("#manageCollectionsLink") || document.createElement("a");
const crawlProgress = document.createElement("section");
const crawlProgressTitle = document.createElement("div");
const crawlProgressTrack = document.createElement("div");
const crawlProgressFill = document.createElement("div");
const crawlProgressMeta = document.createElement("div");

let searchSessionId = null;
let canManageIndex = false;
let selectedCollectionId = null;
let collections = [];

if (!accessHint.id) {
  accessHint.id = "accessHint";
}
accessHint.className = "access-hint";
accessHint.hidden = true;
if (!accessHint.parentElement) {
  titleBlock?.appendChild(accessHint);
}

if (!collectionBar.id) {
  collectionBar.id = "collectionRow";
}
collectionBar.className = "collection-row";

if (!collectionSelect.id) {
  collectionSelect.id = "collectionSelect";
}
if (!collectionMeta.id) {
  collectionMeta.id = "collectionMeta";
}
collectionMeta.className = "collection-meta";

if (!adminLink.id) {
  adminLink.id = "manageCollectionsLink";
}
adminLink.className = "secondary-link";
adminLink.href = "/admin/collections";
adminLink.textContent = "\u7ba1\u7406\u6570\u636e\u5e93\u96c6";
adminLink.hidden = true;

if (!collectionBar.parentElement) {
  const collectionLabel = document.createElement("label");
  collectionLabel.className = "collection-label";
  collectionLabel.innerHTML = `<span>\u5f53\u524d\u6570\u636e\u5e93\u96c6</span>`;
  collectionLabel.appendChild(collectionSelect);
  collectionBar.append(collectionLabel, collectionMeta);
  form.insertBefore(collectionBar, form.firstElementChild);
}

const titleActions = document.querySelector(".title-actions");
if (titleActions && !adminLink.parentElement) {
  titleActions.insertBefore(adminLink, crawlButton);
} else if (!adminLink.parentElement) {
  collectionBar.appendChild(adminLink);
}

crawlProgress.className = "crawl-progress";
crawlProgress.hidden = true;
crawlProgressTitle.className = "crawl-progress-title";
crawlProgressTrack.className = "progress-track";
crawlProgressFill.className = "progress-fill";
crawlProgressMeta.className = "crawl-progress-meta";
crawlProgressTrack.appendChild(crawlProgressFill);
crawlProgress.append(crawlProgressTitle, crawlProgressTrack, crawlProgressMeta);
statusBox.insertAdjacentElement("afterend", crawlProgress);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(message, visible = true) {
  statusBox.hidden = !visible || !message;
  statusBox.textContent = message || "";
}

function taskProgressPercent(task) {
  const raw = Number(task?.progress_percent);
  if (Number.isFinite(raw)) {
    return Math.max(0, Math.min(1, raw));
  }
  if (task?.status === "completed") {
    return 1;
  }
  return 0;
}

function renderCrawlProgress(task) {
  const percent = taskProgressPercent(task);
  const phase = task?.phase || task?.status || "";
  const current = Number(task?.progress_current || 0);
  const total = Number(task?.progress_total || 0);
  crawlProgress.hidden = false;
  crawlProgress.dataset.state = task?.status || "running";
  crawlProgressTitle.textContent = task?.message || "Crawl task is running.";
  crawlProgressFill.style.width = `${Math.round(percent * 100)}%`;
  crawlProgressMeta.textContent =
    total > 0
      ? `${phase} / ${current} of ${total} pages / ${Math.round(percent * 100)}%`
      : `${phase} / ${Math.round(percent * 100)}%`;
}

function profilePayload() {
  return {
    college: collegeInput.value.trim() || null,
    grade: gradeInput.value.trim() || null,
    student_type: studentTypeInput.value || null,
  };
}

function currentCollection() {
  return collections.find((item) => item.id === selectedCollectionId) || null;
}

function updateCollectionMeta() {
  const collection = currentCollection();
  if (!collection) {
    collectionMeta.textContent = "\u8fd8\u6ca1\u6709\u53ef\u641c\u7d22\u7684\u6570\u636e\u5e93\u96c6";
    crawlButton.disabled = true;
    return;
  }
  collectionMeta.textContent = `${Number(collection.document_count || 0)} \u7bc7\u6587\u6863 / ${Number(
    collection.source_count || 0,
  )} \u4e2a\u7ad9\u70b9`;
  crawlButton.disabled = !canManageIndex;
}

function renderCollections() {
  collectionSelect.innerHTML = "";
  if (!collections.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "\u6682\u65e0\u53ef\u7528\u6570\u636e\u5e93\u96c6";
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

async function refreshCollections() {
  const response = await fetch("/api/collections");
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data = await response.json();
  collections = data.collections || [];
  renderCollections();
}

function applyAccess(access) {
  canManageIndex = Boolean(access?.can_manage_index);
  crawlButton.hidden = !canManageIndex;
  adminLink.hidden = !canManageIndex;
  accessHint.hidden = canManageIndex;
  accessHint.textContent = canManageIndex
    ? ""
    : "\u5c40\u57df\u7f51\u8bbf\u5ba2\u6a21\u5f0f\uff1a\u4ec5\u652f\u6301\u641c\u7d22\u4e0e\u67e5\u770b\u7ed3\u679c\uff0c\u4e0d\u53ef\u66f4\u65b0\u7d22\u5f15\u6216\u7f16\u8f91\u6570\u636e\u5e93\u96c6\u3002";
  updateCollectionMeta();
}

async function refreshAccess() {
  const response = await fetch("/api/health");
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data = await response.json();
  applyAccess(data.access || {});
  return data;
}

function renderAnswer(value) {
  const escaped = escapeHtml(value);
  answerText.innerHTML = escaped.replace(/\*\*(.+?)\*\*/gs, "<strong>$1</strong>");
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
        item.attachment_name ? `\u9644\u4ef6\uff1a${item.attachment_name}` : "",
        item.page ? `\u7b2c ${item.page} \u9875` : "",
        item.heading,
      ]
        .filter(Boolean)
        .join(" / ");
      const quality = [
        item.evidence_type ? `\u8bc1\u636e\u7c7b\u578b\uff1a${item.evidence_type}` : "",
        item.fact_confidence !== null && item.fact_confidence !== undefined
          ? `\u4e8b\u5b9e\u7f6e\u4fe1\u5ea6 ${Number(item.fact_confidence).toFixed(2)}`
          : "",
      ]
        .filter(Boolean)
        .join(" / ");
      const meta = [item.source, item.publish_date, item.title, location, quality]
        .filter(Boolean)
        .join(" / ");
      const attachments = (item.attachments || [])
        .slice(0, 3)
        .map(
          (attachment) =>
            `<a href="${escapeHtml(attachment.url)}" target="_blank" rel="noreferrer">${escapeHtml(
              attachment.name || "\u9644\u4ef6",
            )}</a>`,
        )
        .join("");
      return `
        <article class="evidence-item">
          <div class="evidence-head">
            <span>${escapeHtml(item.ref)} ${escapeHtml(item.reason)}</span>
            <a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">\u6253\u5f00\u6765\u6e90</a>
          </div>
          ${meta ? `<p class="evidence-meta">${escapeHtml(meta)}</p>` : ""}
          <blockquote>${escapeHtml(item.quote)}</blockquote>
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
      <span>\u72b6\u6001\uff1a${escapeHtml(report.status || "-")}</span>
      <span>\u5019\u9009\uff1a${Number(report.candidate_count || 0)}</span>
      <span>\u4fdd\u7559\uff1a${Number(report.accepted_count || 0)}</span>
      <span>\u526a\u9664\uff1a${Number(report.rejected_count || 0)}</span>
    </div>
    ${report.notes ? `<p class="relevance-note">${escapeHtml(report.notes)}</p>` : ""}
  `;
  const rejected = (report.rejected || []).slice(0, 6);
  if (!rejected.length) {
    judgeList.innerHTML =
      '<p class="empty">\u6ca1\u6709\u88ab AI Evidence Judge \u5254\u9664\u7684\u5019\u9009\u6765\u6e90\u3002</p>';
    return;
  }
  judgeList.innerHTML = rejected
    .map((item) => {
      const meta = [
        item.label,
        item.confidence !== null && item.confidence !== undefined
          ? `\u7f6e\u4fe1\u5ea6 ${Number(item.confidence).toFixed(2)}`
          : "",
        item.publish_date,
        item.chunk_kind,
        item.attachment_name ? `\u9644\u4ef6\uff1a${item.attachment_name}` : "",
      ]
        .filter(Boolean)
        .join(" / ");
      return `
        <article class="judge-item">
          <a href="${escapeHtml(item.url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(
            item.title || "\u672a\u547d\u540d\u6765\u6e90",
          )}</a>
          <p>${escapeHtml(meta)}</p>
          <p>${escapeHtml(item.reason || "\u672a\u8bf4\u660e\u539f\u56e0")}</p>
        </article>
      `;
    })
    .join("");
}

function renderResults(hits) {
  if (!hits.length) {
    results.innerHTML =
      '<p class="empty">\u6ca1\u6709\u68c0\u7d22\u5230\u53ef\u5f15\u7528\u7684\u5b98\u7f51\u6765\u6e90\u3002</p>';
    return;
  }
  results.innerHTML = hits
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
              item.name || "\u9644\u4ef6",
            )}</a>`,
        )
        .join("");
      const judgeMeta = hit.evidence_judge_label
        ? [
            `Judge\uff1a${hit.evidence_judge_label}`,
            hit.evidence_judge_confidence !== null && hit.evidence_judge_confidence !== undefined
              ? `\u7f6e\u4fe1\u5ea6 ${Number(hit.evidence_judge_confidence).toFixed(2)}`
              : "",
            (hit.evidence_judge_answerable_slots || []).length
              ? `\u53ef\u56de\u7b54\uff1a${hit.evidence_judge_answerable_slots.join("\u3001")}`
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
          <div class="meta">${tags}<span>\u76f8\u5173\u5ea6 ${Number(hit.score || 0).toFixed(2)}</span></div>
          ${
            hit.relevance_note
              ? `<p class="relevance-note">\u76f8\u5173\u8bf4\u660e\uff1a${escapeHtml(hit.relevance_note)}</p>`
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

function assistantModeMeta(mode) {
  if (mode === "overseas") {
    return {
      title: "\u51fa\u56fd\u7533\u8bf7\u5efa\u8bae",
      placeholder:
        "\u4f8b\u5982\uff1a\u6211\u662f\u7814\u7a76\u751f\uff0c\u60f3\u53bb\u65e5\u672c\u53c2\u4f1a\uff0c\u73b0\u5728\u9700\u8981\u51c6\u5907\u4ec0\u4e48\u6750\u6599\uff1f",
      action: "\u751f\u6210\u51fa\u56fd\u6d41\u7a0b",
      endpoint: "/api/assistant/overseas",
      status: "\u6b63\u5728\u6574\u7406\u51fa\u56fd\u7533\u8bf7\u6d41\u7a0b\u4e0e\u6750\u6599...",
      success: "\u51fa\u56fd\u7533\u8bf7\u5efa\u8bae\u5df2\u751f\u6210\u3002",
    };
  }
  return {
    title: "\u9879\u76ee\u62a5\u9500\u5efa\u8bae",
    placeholder:
      "\u4f8b\u5982\uff1a\u6211\u7684\u9879\u76ee xxx \u4ea7\u751f\u4e86\u4e00\u7b14\u5dee\u65c5\u8d39\uff0c\u73b0\u5728\u600e\u4e48\u62a5\u9500\uff1f",
    action: "\u751f\u6210\u62a5\u9500\u6d41\u7a0b",
    endpoint: "/api/assistant/reimbursement",
    status: "\u6b63\u5728\u6574\u7406\u62a5\u9500\u6d41\u7a0b\u4e0e\u6750\u6599...",
    success: "\u62a5\u9500\u5efa\u8bae\u5df2\u751f\u6210\u3002",
  };
}

function setAssistantMode(mode) {
  const nextMode = mode === "overseas" ? "overseas" : "reimbursement";
  if (assistantModeInput) {
    assistantModeInput.value = nextMode;
  }
  if (reimbursementFields) {
    reimbursementFields.hidden = nextMode !== "reimbursement";
  }
  if (overseasFields) {
    overseasFields.hidden = nextMode !== "overseas";
  }
  for (const card of assistantModeCards) {
    const active = card.dataset.assistantMode === nextMode;
    card.classList.toggle("is-active", active);
    card.setAttribute("aria-pressed", active ? "true" : "false");
  }
  const meta = assistantModeMeta(nextMode);
  if (assistantAnswerTitle) {
    assistantAnswerTitle.textContent = meta.title;
  }
  if (assistantQuestionInput) {
    assistantQuestionInput.placeholder = meta.placeholder;
  }
  const submitButton = assistantForm?.querySelector('button[type="submit"]');
  if (submitButton) {
    submitButton.textContent = meta.action;
  }
}

function renderAssistantSources(sourceItems) {
  if (!assistantSources) {
    return;
  }
  if (!sourceItems || !sourceItems.length) {
    assistantSourcesPanel.hidden = true;
    assistantSources.innerHTML = "";
    return;
  }
  assistantSourcesPanel.hidden = false;
  assistantSources.innerHTML = `
    <div class="assistant-source-list">
      ${sourceItems
        .map((item) => {
          const meta = [item.source_unit, item.publish_date, item.slot].filter(Boolean).join(" / ");
          return `
            <article class="assistant-source-item">
              <a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>
              ${meta ? `<p class="assistant-source-meta">${escapeHtml(meta)}</p>` : ""}
              <p class="assistant-source-quote">${escapeHtml(item.quote || "")}</p>
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderAssistantAnswer(mode, data) {
  if (!assistantAnswerPanel || !assistantAnswerText) {
    return;
  }
  const blocks = [
    ["\u8fd8\u7f3a\u54ea\u4e9b\u4fe1\u606f", data.missing_fields || []],
    ["\u6750\u6599\u6e05\u5355", data.materials || []],
    ["\u529e\u7406\u6b65\u9aa4", data.steps || []],
    ["\u9700\u8981\u627e\u8c01", data.actors || []],
    ["\u529e\u7406\u5730\u70b9", data.locations || []],
    ["\u76f8\u5173\u7cfb\u7edf", data.systems || []],
  ].filter(([, items]) => items.length);

  const warnings = data.warnings || [];
  assistantAnswerPanel.hidden = false;
  assistantAnswerTitle.textContent = assistantModeMeta(mode).title;
  assistantAnswerText.innerHTML = `
    <div class="answer-text">${escapeHtml(data.answer || "").replace(/\*\*(.+?)\*\*/gs, "<strong>$1</strong>")}</div>
    ${
      warnings.length
        ? `<div class="warning-list">${warnings
            .map((warning) => `<div class="warning-item">${escapeHtml(warning)}</div>`)
            .join("")}</div>`
        : ""
    }
    ${
      blocks.length
        ? `<div class="assistant-summary">
            ${blocks
              .map(
                ([title, items]) => `
                  <section class="assistant-summary-block">
                    <h3>${escapeHtml(title)}</h3>
                    <ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
                  </section>
                `,
              )
              .join("")}
          </div>`
        : ""
    }
  `;
}

async function runAssistant() {
  if (!assistantForm || !assistantQuestionInput || !assistantModeInput) {
    return;
  }
  const question = assistantQuestionInput.value.trim();
  if (!question) {
    return;
  }
  const mode = assistantModeInput.value === "overseas" ? "overseas" : "reimbursement";
  const meta = assistantModeMeta(mode);
  const submitButton = assistantForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  setStatus(meta.status);
  const payload =
    mode === "overseas"
      ? {
          question,
          applicant_type: applicantTypeInput?.value || null,
          destination: destinationInput?.value.trim() || null,
          visit_type: visitTypeInput?.value.trim() || null,
          funding_source: fundingSourceInput?.value.trim() || null,
          start_date: startDateInput?.value.trim() || null,
          profile: profilePayload(),
        }
      : {
          question,
          project_name: projectNameInput?.value.trim() || null,
          project_code: projectCodeInput?.value.trim() || null,
          expense_type: expenseTypeInput?.value.trim() || null,
          invoice_date: invoiceDateInput?.value.trim() || null,
          payment_target: paymentTargetInput?.value || null,
          profile: profilePayload(),
        };
  try {
    const response = await fetch(meta.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    renderAssistantAnswer(mode, data);
    renderAssistantSources(data.sources || []);
    setStatus(meta.success);
  } catch (error) {
    setStatus(`\u52a9\u624b\u751f\u6210\u5931\u8d25\uff1a${error.message}`);
  } finally {
    submitButton.disabled = false;
  }
}

async function runSearch(query) {
  if (!selectedCollectionId) {
    setStatus("\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u6570\u636e\u5e93\u96c6\u3002");
    return;
  }
  const submitButton = form.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  setStatus("\u6b63\u5728\u8ba9 AI \u7406\u89e3\u4f60\u7684\u95ee\u9898\uff0c\u5e76\u5728\u5f53\u524d\u6570\u636e\u5e93\u96c6\u5185\u68c0\u7d22...");
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        profile: profilePayload(),
        limit: 10,
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
    renderWarnings(data.answer.warnings);
    renderEvidence(data.answer.evidence);
    renderJudge(data.evidence_judge);
    renderResults(data.hits);
    planPanel.hidden = false;
    answerPanel.hidden = false;
    resultsPanel.hidden = false;
    setStatus(
      `\u5b8c\u6210\uff1a${data.collection_name || currentCollection()?.name || ""} \u4e2d\u627e\u5230 ${data.hits.length} \u6761\u5019\u9009\u6765\u6e90\u3002`,
      true,
    );
  } catch (error) {
    setStatus(`\u68c0\u7d22\u5931\u8d25\uff1a${error.message}`);
  } finally {
    submitButton.disabled = false;
  }
}

setAssistantMode(assistantModeInput?.value || "reimbursement");

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (query) {
    runSearch(query);
  }
});

if (assistantForm) {
  assistantForm.addEventListener("submit", (event) => {
    event.preventDefault();
    runAssistant();
  });
}

if (assistantModeInput) {
  assistantModeInput.addEventListener("change", () => {
    setAssistantMode(assistantModeInput.value);
  });
}

for (const card of assistantModeCards) {
  card.addEventListener("click", () => {
    setAssistantMode(card.dataset.assistantMode || "reimbursement");
  });
}

document.querySelectorAll("[data-query]").forEach((button) => {
  button.addEventListener("click", () => {
    const query = button.dataset.query || "";
    queryInput.value = query;
    runSearch(query);
  });
});

collectionSelect.addEventListener("change", () => {
  selectedCollectionId = Number(collectionSelect.value || 0) || null;
  searchSessionId = null;
  updateCollectionMeta();
});

crawlButton.addEventListener("click", async () => {
  if (!canManageIndex) {
    setStatus("\u5c40\u57df\u7f51\u8bbf\u5ba2\u6a21\u5f0f\u4e0b\u4ec5\u652f\u6301\u641c\u7d22\uff0c\u4e0d\u80fd\u66f4\u65b0\u7d22\u5f15\u3002");
    return;
  }
  if (!selectedCollectionId) {
    setStatus("\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u6570\u636e\u5e93\u96c6\u3002");
    return;
  }
  crawlButton.disabled = true;
  setStatus("\u5f53\u524d\u6570\u636e\u5e93\u96c6\u7684\u7d22\u5f15\u66f4\u65b0\u4efb\u52a1\u5df2\u63d0\u4ea4...");
  try {
    const response = await fetch(`/api/collections/${selectedCollectionId}/crawl`, { method: "POST" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    await pollCrawlTask(data.task_id);
    await refreshCollections();
  } catch (error) {
    setStatus(`\u7d22\u5f15\u66f4\u65b0\u5931\u8d25\uff1a${error.message}`);
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
        renderCrawlProgress(task);
        setStatus("\u7d22\u5f15\u66f4\u65b0\u6392\u961f\u4e2d...");
      } else if (task.status === "running") {
        renderCrawlProgress(task);
        setStatus(task.message || "\u6b63\u5728\u540e\u53f0\u66f4\u65b0\u5f53\u524d\u6570\u636e\u5e93\u96c6\uff0c\u4f60\u53ef\u4ee5\u7ee7\u7eed\u641c\u7d22...");
      } else if (task.status === "completed") {
        renderCrawlProgress(task);
        setStatus(
          `\u7d22\u5f15\u66f4\u65b0\u5b8c\u6210\uff1a\u672c\u6b21\u5199\u5165 ${Number(task.upserted || 0)} \u6761\uff0c\u8be5\u96c6\u5408\u73b0\u5728\u5171\u6709 ${Number(task.total_documents || 0)} \u6761\u6587\u6863\u3002`,
        );
        crawlButton.disabled = false;
        return;
      } else if (task.status === "failed") {
        renderCrawlProgress(task);
        setStatus(
          `\u7d22\u5f15\u66f4\u65b0\u5931\u8d25\uff1a${task.message || task.error || "\u672a\u77e5\u9519\u8bef"}`,
        );
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
    setAssistantMode(assistantModeInput?.value || "reimbursement");
    if (!collections.length) {
      setStatus("\u8fd8\u6ca1\u6709\u542f\u7528\u7684\u6570\u636e\u5e93\u96c6\uff0c\u8bf7\u5148\u5728\u672c\u673a\u7ba1\u7406\u9875\u9762\u521b\u5efa\u4e00\u4e2a\u3002");
      return;
    }
    if (Number(health.documents || 0) === 0) {
      setStatus(
        "\u5f53\u524d\u7d22\u5f15\u8fd8\u662f\u7a7a\u7684\u3002\u53ef\u4ee5\u5148\u9009\u4e00\u4e2a\u6570\u636e\u5e93\u96c6\uff0c\u7136\u540e\u70b9\u51fb\u201c\u66f4\u65b0\u7d22\u5f15\u201d\u3002",
      );
      return;
    }
    setStatus(
      `\u5f53\u524d\u5168\u5c40\u7d22\u5f15\u5171\u6709 ${Number(health.documents || 0)} \u6761\u5b98\u7f51\u8d44\u6599\uff0c\u53ef\u4ee5\u5728\u4e0a\u65b9\u5207\u6362\u8981\u641c\u7684\u6570\u636e\u5e93\u96c6\u3002`,
      true,
    );
  })
  .catch((error) => {
    setStatus(`\u521d\u59cb\u5316\u5931\u8d25\uff1a${error.message}`);
  });
