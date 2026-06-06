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
const assistantQuestionInput = document.querySelector("#assistantQuestionInput");
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
const assistantAnswerTitle = document.querySelector("#assistantAnswerTitle");
const assistantAnswerText = document.querySelector("#assistantAnswerText");
const assistantSourcesPanel = document.querySelector("#assistantSourcesPanel");
const assistantSources = document.querySelector("#assistantSources");
let searchSessionId = null;

function setStatus(message, visible = true) {
  statusBox.hidden = !visible;
  statusBox.textContent = message;
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

function renderAssistantAnswer(value) {
  const escaped = escapeHtml(value);
  assistantAnswerText.innerHTML = escaped.replace(/\*\*(.+?)\*\*/gs, "<strong>$1</strong>");
}

function renderAssistantSources(sources) {
  if (!sources || !sources.length) {
    assistantSources.innerHTML = '<p class="empty">没有可展示的独立事务数据源。</p>';
    return;
  }
  assistantSources.innerHTML = sources
    .map((source) => {
      const tags = [source.source_unit, source.publish_date, source.slot]
        .filter(Boolean)
        .map((item) => `<span class="tag">${escapeHtml(item)}</span>`)
        .join("");
      return `
        <article class="result-item">
          <a class="result-title" href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(
            source.title,
          )}</a>
          <div class="meta">${tags}</div>
          <p class="snippet">${escapeHtml(source.quote || "")}</p>
        </article>
      `;
    })
    .join("");
}

function syncAssistantMode() {
  const isOverseas = assistantModeInput.value === "overseas";
  reimbursementFields.hidden = isOverseas;
  overseasFields.hidden = !isOverseas;
  assistantQuestionInput.placeholder = isOverseas
    ? "例如：我是研究生，要去日本参加国际会议，如何申请？"
    : "例如：我的项目xxx产生了一笔差旅费用，如何报销？";
  assistantAnswerTitle.textContent = isOverseas ? "出国申请建议" : "报销办理建议";
}

function renderResults(hits) {
  if (!hits.length) {
    results.innerHTML = '<p class="empty">没有检索到可引用的官网来源。</p>';
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
              item.name || "附件",
            )}</a>`,
        )
        .join("");
      const judgeMeta = hit.evidence_judge_label
        ? [
            `Judge：${hit.evidence_judge_label}`,
            hit.evidence_judge_confidence !== null && hit.evidence_judge_confidence !== undefined
              ? `置信度 ${Number(hit.evidence_judge_confidence).toFixed(2)}`
              : "",
            (hit.evidence_judge_answerable_slots || []).length
              ? `可回答 ${hit.evidence_judge_answerable_slots.join("、")}`
              : "",
          ]
            .filter(Boolean)
            .join(" · ")
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
    judgeList.innerHTML = '<p class="empty">没有被 AI Evidence Judge 剔除的候选。</p>';
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
        .join(" · ");
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
  setStatus("正在让 AI 解析检索意图，并检索官网索引...");
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        profile: profilePayload(),
        limit: 10,
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
    renderWarnings(data.answer.warnings);
    renderEvidence(data.answer.evidence);
    renderJudge(data.evidence_judge);
    renderResults(data.hits);
    planPanel.hidden = false;
    answerPanel.hidden = false;
    resultsPanel.hidden = false;
    setStatus(`完成：找到 ${data.hits.length} 条候选来源。`, true);
  } catch (error) {
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

assistantForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = assistantQuestionInput.value.trim();
  if (!question) return;
  const mode = assistantModeInput.value;
  const submitButton = assistantForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  setStatus("正在使用独立校内事务数据集生成办理流程...");
  try {
    const endpoint =
      mode === "overseas" ? "/api/assistant/overseas-application" : "/api/assistant/reimbursement";
    const payload =
      mode === "overseas"
        ? {
            question,
            applicant_type: applicantTypeInput.value || null,
            destination: destinationInput.value.trim() || null,
            visit_type: visitTypeInput.value.trim() || null,
            funding_source: fundingSourceInput.value.trim() || null,
            start_date: startDateInput.value.trim() || null,
            profile: profilePayload(),
          }
        : {
            question,
            project_name: projectNameInput.value.trim() || null,
            project_code: projectCodeInput.value.trim() || null,
            expense_type: expenseTypeInput.value.trim() || null,
            invoice_date: invoiceDateInput.value.trim() || null,
            payment_target: paymentTargetInput.value || null,
            profile: profilePayload(),
          };
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    renderAssistantAnswer(data.answer);
    renderAssistantSources(data.sources);
    assistantAnswerPanel.hidden = false;
    assistantSourcesPanel.hidden = false;
    setStatus(`已生成${mode === "overseas" ? "出国申请" : "报销"}流程；数据集：${data.dataset}，来源 ${data.sources.length} 条。`);
  } catch (error) {
    setStatus(`私人助理生成失败：${error.message}`);
  } finally {
    submitButton.disabled = false;
  }
});

assistantModeInput.addEventListener("change", syncAssistantMode);
syncAssistantMode();

document.querySelectorAll("[data-query]").forEach((button) => {
  button.addEventListener("click", () => {
    queryInput.value = button.dataset.query;
    runSearch(button.dataset.query);
  });
});

crawlButton.addEventListener("click", async () => {
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

fetch("/api/health")
  .then((response) => response.json())
  .then((data) => {
    setStatus(`当前索引中有 ${data.documents} 条官网资料。首次使用请点击“更新索引”。`, true);
  })
  .catch(() => {});
