const adminStatus = document.querySelector("#adminStatus");
const collectionList = document.querySelector("#collectionList");
const sourceList = document.querySelector("#sourceList");
const collectionForm = document.querySelector("#collectionForm");
const sourceForm = document.querySelector("#sourceForm");
const newCollectionButton = document.querySelector("#newCollectionButton");
const deleteCollectionButton = document.querySelector("#deleteCollectionButton");
const newSourceButton = document.querySelector("#newSourceButton");
const deleteSourceButton = document.querySelector("#deleteSourceButton");
const crawlCollectionButton = document.querySelector("#crawlCollectionButton");

const collectionIdInput = document.querySelector("#collectionIdInput");
const collectionNameInput = document.querySelector("#collectionNameInput");
const collectionDescriptionInput = document.querySelector("#collectionDescriptionInput");
const collectionEnabledInput = document.querySelector("#collectionEnabledInput");

const sourceIdInput = document.querySelector("#sourceIdInput");
const sourceNameInput = document.querySelector("#sourceNameInput");
const sourceBaseInput = document.querySelector("#sourceBaseInput");
const sourceSeedsInput = document.querySelector("#sourceSeedsInput");
const sourceIncludeInput = document.querySelector("#sourceIncludeInput");
const sourceExcludeInput = document.querySelector("#sourceExcludeInput");
const sourceDepthInput = document.querySelector("#sourceDepthInput");
const sourcePagesInput = document.querySelector("#sourcePagesInput");
const sourceDaysInput = document.querySelector("#sourceDaysInput");
const sourceEnabledInput = document.querySelector("#sourceEnabledInput");

const state = {
  collections: [],
  selectedCollectionId: null,
};

const copy = {
  adminTitle: "\u6570\u636e\u5e93\u96c6\u7ba1\u7406",
  adminIntro:
    "\u5728\u8fd9\u91cc\u53ef\u4ee5\u65b0\u5efa\u6570\u636e\u5e93\u96c6\uff0c\u5e76\u4e3a\u6bcf\u4e2a\u96c6\u5408\u914d\u7f6e\u5355\u72ec\u7684\u5b98\u7f51\u7ad9\u70b9\u548c\u722c\u53d6\u8303\u56f4\u3002",
  collectionListTitle: "\u6570\u636e\u5e93\u96c6\u5217\u8868",
  collectionEditorTitle: "\u6570\u636e\u5e93\u96c6\u4fe1\u606f",
  collectionNameLabel: "\u540d\u79f0",
  collectionDescLabel: "\u8bf4\u660e",
  collectionEnabledLabel: "\u542f\u7528\u8be5\u6570\u636e\u5e93\u96c6",
  sourceSectionTitle: "\u7ad9\u70b9\u914d\u7f6e",
  sourceNameLabel: "\u7ad9\u70b9\u540d\u79f0",
  sourceBaseLabel: "\u57fa\u7840 URL",
  sourceSeedsLabel: "\u79cd\u5b50 URL\uff08\u6bcf\u884c\u4e00\u4e2a\uff09",
  sourceIncludeLabel: "\u5305\u542b\u8def\u5f84\u524d\u7f00\uff08\u6bcf\u884c\u4e00\u4e2a\uff09",
  sourceExcludeLabel: "\u6392\u9664\u8def\u5f84\u524d\u7f00\uff08\u6bcf\u884c\u4e00\u4e2a\uff09",
  sourceDepthLabel: "\u6700\u5927\u6df1\u5ea6",
  sourcePagesLabel: "\u6700\u5927\u9875\u6570",
  sourceDaysLabel: "\u8ffd\u6eaf\u5929\u6570",
  sourceEnabledLabel: "\u542f\u7528\u8be5\u7ad9\u70b9",
};

for (const [id, value] of Object.entries(copy)) {
  const node = document.getElementById(id);
  if (node) {
    node.textContent = value;
  }
}

newCollectionButton.textContent = "\u65b0\u5efa\u96c6\u5408";
deleteCollectionButton.textContent = "\u5220\u9664\u96c6\u5408";
newSourceButton.textContent = "\u65b0\u5efa\u7ad9\u70b9";
deleteSourceButton.textContent = "\u5220\u9664\u7ad9\u70b9";
crawlCollectionButton.textContent = "\u53ea\u66f4\u65b0\u5f53\u524d\u6570\u636e\u5e93\u96c6";
document.querySelector(".secondary-link").textContent = "\u8fd4\u56de\u641c\u7d22\u9875";

collectionNameInput.placeholder = "\u4f8b\u5982\uff1a\u6559\u52a1\u901a\u77e5\u4e13\u96c6";
collectionDescriptionInput.placeholder = "\u5199\u7ed9\u4f60\u81ea\u5df1\u770b\u7684\u8bf4\u660e";
sourceNameInput.placeholder = "\u4f8b\u5982\uff1a\u6559\u52a1\u5904";
sourceBaseInput.placeholder = "https://jwc.seu.edu.cn/";
sourceSeedsInput.placeholder = "https://jwc.seu.edu.cn/\nhttps://jwc.seu.edu.cn/jwxx/list.htm";
sourceIncludeInput.placeholder = "/jwxx/\n/bszn/";
sourceExcludeInput.placeholder = "/video/\n/demo/";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(message, tone = "info") {
  adminStatus.hidden = !message;
  adminStatus.textContent = message || "";
  adminStatus.dataset.tone = tone;
}

function parseLines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function selectedCollection() {
  return state.collections.find((item) => item.id === state.selectedCollectionId) || null;
}

function selectedSource() {
  const collection = selectedCollection();
  if (!collection) {
    return null;
  }
  return (
    (collection.sources || []).find((item) => item.id === Number(sourceIdInput.value || 0)) || null
  );
}

function fillCollectionForm(collection) {
  collectionIdInput.value = collection?.id || "";
  collectionNameInput.value = collection?.name || "";
  collectionDescriptionInput.value = collection?.description || "";
  collectionEnabledInput.checked = collection ? Boolean(collection.is_enabled) : true;
}

function fillSourceForm(source) {
  sourceIdInput.value = source?.id || "";
  sourceNameInput.value = source?.source_name || "";
  sourceBaseInput.value = source?.base_url || "";
  sourceSeedsInput.value = (source?.seed_urls || []).join("\n");
  sourceIncludeInput.value = (source?.include_path_prefixes || []).join("\n");
  sourceExcludeInput.value = (source?.exclude_path_prefixes || []).join("\n");
  sourceDepthInput.value = source?.max_depth ?? "";
  sourcePagesInput.value = source?.max_pages ?? "";
  sourceDaysInput.value = source?.days_back ?? "";
  sourceEnabledInput.checked = source ? Boolean(source.is_enabled) : true;
}

function renderCollectionList() {
  if (!state.collections.length) {
    collectionList.innerHTML =
      '<p class="empty">\u8fd8\u6ca1\u6709\u53ef\u7528\u7684\u6570\u636e\u5e93\u96c6\u3002</p>';
    return;
  }
  collectionList.innerHTML = state.collections
    .map((collection) => {
      const active = collection.id === state.selectedCollectionId ? " is-active" : "";
      const meta = [
        `ID ${collection.id}`,
        `${Number(collection.document_count || 0)} \u7bc7\u6587\u6863`,
        `${Number(collection.source_count || 0)} \u4e2a\u7ad9\u70b9`,
        collection.is_enabled ? "\u5df2\u542f\u7528" : "\u5df2\u505c\u7528",
      ].join(" / ");
      return `
        <button type="button" class="collection-card${active}" data-collection-id="${collection.id}">
          <strong>${escapeHtml(collection.name)}</strong>
          <span>${escapeHtml(meta)}</span>
          ${
            collection.description
              ? `<small>${escapeHtml(collection.description)}</small>`
              : ""
          }
        </button>
      `;
    })
    .join("");

  collectionList.querySelectorAll("[data-collection-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedCollectionId = Number(button.dataset.collectionId);
      fillCollectionForm(selectedCollection());
      fillSourceForm(null);
      renderCollectionList();
      renderSourceList();
      updateActionState();
    });
  });
}

function renderSourceList() {
  const collection = selectedCollection();
  if (!collection) {
    sourceList.innerHTML =
      '<p class="empty">\u5148\u9009\u4e2d\u6216\u65b0\u5efa\u4e00\u4e2a\u6570\u636e\u5e93\u96c6\u3002</p>';
    return;
  }
  const sources = collection.sources || [];
  if (!sources.length) {
    sourceList.innerHTML =
      '<p class="empty">\u8fd8\u6ca1\u6709\u7ad9\u70b9\u914d\u7f6e\uff0c\u53ef\u4ee5\u5148\u6dfb\u52a0\u4e00\u4e2a\u3002</p>';
    return;
  }
  sourceList.innerHTML = sources
    .map((source) => {
      const meta = [
        source.is_enabled ? "\u5df2\u542f\u7528" : "\u5df2\u505c\u7528",
        source.max_depth !== null && source.max_depth !== undefined
          ? `depth ${source.max_depth}`
          : "",
        source.max_pages !== null && source.max_pages !== undefined
          ? `pages ${source.max_pages}`
          : "",
        source.days_back !== null && source.days_back !== undefined
          ? `${source.days_back} days`
          : "",
      ]
        .filter(Boolean)
        .join(" / ");
      return `
        <article class="source-card">
          <div>
            <strong>${escapeHtml(source.source_name)}</strong>
            <p>${escapeHtml(source.base_url)}</p>
            ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
          </div>
          <button type="button" class="secondary-button" data-source-id="${source.id}">
            \u7f16\u8f91
          </button>
        </article>
      `;
    })
    .join("");

  sourceList.querySelectorAll("[data-source-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const collection = selectedCollection();
      const source = (collection?.sources || []).find(
        (item) => item.id === Number(button.dataset.sourceId),
      );
      fillSourceForm(source || null);
      updateActionState();
    });
  });
}

function updateActionState() {
  const hasCollection = Boolean(selectedCollection());
  deleteCollectionButton.disabled = !collectionIdInput.value;
  newSourceButton.disabled = !hasCollection;
  crawlCollectionButton.disabled = !hasCollection;
  deleteSourceButton.disabled = !sourceIdInput.value;
  sourceForm.querySelectorAll("input, textarea, button").forEach((node) => {
    if (node === newSourceButton || node === crawlCollectionButton) {
      return;
    }
    if (node.id === "deleteSourceButton") {
      return;
    }
    node.disabled = !hasCollection;
  });
}

async function loadCollections(preferredCollectionId = null) {
  setStatus("\u6b63\u5728\u52a0\u8f7d\u6570\u636e\u5e93\u96c6...");
  const response = await fetch("/api/admin/collections");
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data = await response.json();
  state.collections = data.collections || [];
  state.selectedCollectionId =
    preferredCollectionId ||
    state.selectedCollectionId ||
    state.collections[0]?.id ||
    null;
  if (!state.collections.some((item) => item.id === state.selectedCollectionId)) {
    state.selectedCollectionId = state.collections[0]?.id || null;
  }
  fillCollectionForm(selectedCollection());
  fillSourceForm(null);
  renderCollectionList();
  renderSourceList();
  updateActionState();
  setStatus("");
}

collectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    name: collectionNameInput.value.trim(),
    description: collectionDescriptionInput.value.trim(),
    is_enabled: collectionEnabledInput.checked,
  };
  const collectionId = collectionIdInput.value ? Number(collectionIdInput.value) : null;
  const url = collectionId ? `/api/admin/collections/${collectionId}` : "/api/admin/collections";
  const method = collectionId ? "PUT" : "POST";
  setStatus("\u6b63\u5728\u4fdd\u5b58\u6570\u636e\u5e93\u96c6...");
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  const data = await response.json();
  await loadCollections(data.id);
  setStatus("\u6570\u636e\u5e93\u96c6\u5df2\u4fdd\u5b58\u3002", "success");
});

deleteCollectionButton.addEventListener("click", async () => {
  const collectionId = Number(collectionIdInput.value || 0);
  if (!collectionId) {
    return;
  }
  if (!window.confirm("\u786e\u5b9a\u5220\u9664\u8fd9\u4e2a\u6570\u636e\u5e93\u96c6\u5417\uff1f")) {
    return;
  }
  setStatus("\u6b63\u5728\u5220\u9664\u6570\u636e\u5e93\u96c6...");
  const response = await fetch(`/api/admin/collections/${collectionId}`, { method: "DELETE" });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  collectionIdInput.value = "";
  await loadCollections();
  setStatus("\u6570\u636e\u5e93\u96c6\u5df2\u5220\u9664\u3002", "success");
});

newCollectionButton.addEventListener("click", () => {
  collectionIdInput.value = "";
  fillCollectionForm(null);
  fillSourceForm(null);
  updateActionState();
});

sourceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const collection = selectedCollection();
  if (!collection) {
    throw new Error("\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u6570\u636e\u5e93\u96c6\u3002");
  }
  const payload = {
    source_name: sourceNameInput.value.trim(),
    base_url: sourceBaseInput.value.trim(),
    seed_urls: parseLines(sourceSeedsInput.value),
    include_path_prefixes: parseLines(sourceIncludeInput.value),
    exclude_path_prefixes: parseLines(sourceExcludeInput.value),
    max_depth: sourceDepthInput.value ? Number(sourceDepthInput.value) : null,
    max_pages: sourcePagesInput.value ? Number(sourcePagesInput.value) : null,
    days_back: sourceDaysInput.value ? Number(sourceDaysInput.value) : null,
    is_enabled: sourceEnabledInput.checked,
  };
  const sourceId = sourceIdInput.value ? Number(sourceIdInput.value) : null;
  const url = sourceId
    ? `/api/admin/collection-sources/${sourceId}`
    : `/api/admin/collections/${collection.id}/sources`;
  const method = sourceId ? "PUT" : "POST";
  setStatus("\u6b63\u5728\u4fdd\u5b58\u7ad9\u70b9\u914d\u7f6e...");
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  await loadCollections(collection.id);
  fillSourceForm(null);
  updateActionState();
  setStatus("\u7ad9\u70b9\u914d\u7f6e\u5df2\u4fdd\u5b58\u3002", "success");
});

deleteSourceButton.addEventListener("click", async () => {
  const source = selectedSource();
  if (!source) {
    return;
  }
  if (!window.confirm("\u786e\u5b9a\u5220\u9664\u8fd9\u4e2a\u7ad9\u70b9\u914d\u7f6e\u5417\uff1f")) {
    return;
  }
  setStatus("\u6b63\u5728\u5220\u9664\u7ad9\u70b9\u914d\u7f6e...");
  const response = await fetch(`/api/admin/collection-sources/${source.id}`, { method: "DELETE" });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  const collection = selectedCollection();
  await loadCollections(collection?.id || null);
  fillSourceForm(null);
  updateActionState();
  setStatus("\u7ad9\u70b9\u914d\u7f6e\u5df2\u5220\u9664\u3002", "success");
});

newSourceButton.addEventListener("click", () => {
  fillSourceForm(null);
  updateActionState();
});

async function pollCrawlTask(taskId) {
  while (true) {
    const response = await fetch(`/api/crawl/tasks/${encodeURIComponent(taskId)}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const task = await response.json();
    if (task.status === "queued") {
      setStatus("\u722c\u53d6\u4efb\u52a1\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u6392\u961f...");
    } else if (task.status === "running") {
      setStatus("\u6b63\u5728\u722c\u53d6\u5e76\u5237\u65b0\u8be5\u6570\u636e\u5e93\u96c6...");
    } else if (task.status === "completed") {
      setStatus(
        `\u66f4\u65b0\u5b8c\u6210\uff1a\u672c\u6b21\u5199\u5165 ${Number(task.upserted || 0)} \u6761\uff0c\u96c6\u5408\u5185\u5171 ${Number(task.total_documents || 0)} \u6761\u6587\u6863\u3002`,
        "success",
      );
      await loadCollections(selectedCollection()?.id || null);
      return;
    } else if (task.status === "failed") {
      throw new Error(task.error || "\u672a\u77e5\u9519\u8bef");
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

crawlCollectionButton.addEventListener("click", async () => {
  const collection = selectedCollection();
  if (!collection) {
    throw new Error("\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u6570\u636e\u5e93\u96c6\u3002");
  }
  setStatus("\u6b63\u5728\u63d0\u4ea4\u722c\u53d6\u4efb\u52a1...");
  const response = await fetch(`/api/collections/${collection.id}/crawl`, { method: "POST" });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  const data = await response.json();
  await pollCrawlTask(data.task_id);
});

window.addEventListener("error", (event) => {
  if (event.error instanceof Error) {
    setStatus(`\u64cd\u4f5c\u5931\u8d25\uff1a${event.error.message}`, "error");
  }
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  const message = reason instanceof Error ? reason.message : String(reason || "");
  setStatus(`\u64cd\u4f5c\u5931\u8d25\uff1a${message}`, "error");
});

loadCollections().catch((error) => {
  setStatus(`\u52a0\u8f7d\u5931\u8d25\uff1a${error.message}`, "error");
});
