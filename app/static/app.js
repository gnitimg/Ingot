const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  knowledgeBases: [],
  current: null,
  documents: [],
  settings: null,
  graph: null,
  graphView: null,
  graphPoll: null,
  chatHistory: [],
  sending: false,
};

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.headers || {}) } };
  if (init.body && !(init.body instanceof FormData)) init.headers["Content-Type"] = "application/json";
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* no-op */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  $("#toast-container").append(node);
  setTimeout(() => node.remove(), 4200);
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function graphStatus(status) {
  return {
    empty: ["图谱未构建", ""], stale: ["图谱待更新", ""], building: ["图谱构建中", "building"],
    ready: ["图谱已就绪", "ready"], error: ["图谱构建失败", "error"],
  }[status] || [status, ""];
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    $("#api-status-dot").classList.add("online");
    $("#api-status-text").textContent = health.embedding_configured ? "服务在线" : "等待配置 API Key";
    $("#model-chip").textContent = health.embedding_model;
  } catch (error) {
    $("#api-status-text").textContent = "服务不可用";
  }
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  $("#max-upload").textContent = state.settings.max_upload_mb;
  renderSettings();
}

function renderSettings() {
  if (!state.settings) return;
  const s = state.settings;
  const cards = [
    ["Embedding", s.embedding_configured, [["Endpoint", s.embedding_base_url], ["Model", s.embedding_model], ["API Key", s.embedding_configured ? "已配置（隐藏）" : "未配置"]]],
    ["Chat / Graph", s.chat_configured, [["Endpoint", s.chat_base_url], ["Model", s.chat_model], ["API Key", s.chat_configured ? "已配置（隐藏）" : "未配置"]]],
    ["OCR", s.ocr_configured, [["Endpoint", s.ocr_base_url], ["Model", s.ocr_model], ["Max pages", s.ocr_max_pages]]],
    ["Reranker", s.rerank_configured, [["Endpoint", s.rerank_base_url], ["Model", s.rerank_model], ["Candidates", s.rerank_candidates]]],
    ["Chunking", true, [["Chunk size", `${s.chunk_size} 字符`], ["Overlap", `${s.chunk_overlap} 字符`], ["Top K", s.default_top_k]]],
    ["GraphRAG", true, [["Max chunks", s.graph_max_chunks === 0 ? "不限" : s.graph_max_chunks], ["Index", "实体 + 关系 + 社区摘要"], ["Storage", "SQLite / local"]]],
  ];
  $("#settings-grid").innerHTML = cards.map(([title, active, values]) => `
    <article class="setting-card"><div class="setting-card-head"><strong>${title}</strong><i class="config-status ${active ? "" : "off"}"></i></div>
      <dl>${values.map(([key, value]) => `<dt>${key}</dt><dd>${String(value)}</dd>`).join("")}</dl></article>`).join("");
}

async function loadKnowledgeBases(preferredId = state.current?.id) {
  state.knowledgeBases = await api("/api/knowledge-bases");
  renderKnowledgeBases();
  if (preferredId && state.knowledgeBases.some(kb => kb.id === preferredId)) await selectKnowledgeBase(preferredId, false);
  else if (!state.current && state.knowledgeBases.length) await selectKnowledgeBase(state.knowledgeBases[0].id, false);
  else if (!state.knowledgeBases.length) showEmpty();
}

function renderKnowledgeBases() {
  const list = $("#kb-list");
  if (!state.knowledgeBases.length) {
    list.innerHTML = '<div class="sidebar-empty">还没有知识库。创建一个，然后导入你的第一批资料。</div>';
    return;
  }
  list.innerHTML = state.knowledgeBases.map(kb => `
    <button class="kb-item ${state.current?.id === kb.id ? "active" : ""}" data-id="${kb.id}">
      <i class="kb-item-dot"></i><strong></strong><small>${kb.document_count}</small>
    </button>`).join("");
  state.knowledgeBases.forEach((kb, index) => $$(".kb-item strong", list)[index].textContent = kb.name);
  $$(".kb-item", list).forEach(button => button.addEventListener("click", () => selectKnowledgeBase(button.dataset.id)));
}

function showEmpty() {
  state.current = null;
  $("#empty-state").classList.remove("hidden");
  $("#workspace").classList.add("hidden");
}

async function selectKnowledgeBase(id, resetTab = true) {
  clearTimeout(state.graphPoll);
  const [knowledgeBase, documents] = await Promise.all([
    api(`/api/knowledge-bases/${id}`), api(`/api/knowledge-bases/${id}/documents`),
  ]);
  state.current = knowledgeBase;
  state.documents = documents;
  state.chatHistory = [];
  state.graph = null;
  $("#messages").innerHTML = welcomeMarkup();
  bindSuggestions();
  $("#evidence-list").innerHTML = '<div class="evidence-empty">提问后，这里会展示模型使用的原始证据。</div>';
  $("#evidence-count").textContent = "0 ITEMS";
  $("#empty-state").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  renderKnowledgeBases();
  renderWorkspace();
  renderDocuments();
  if (resetTab) activateTab("documents");
  if (knowledgeBase.graph_status === "building") pollGraphStatus();
}

function renderWorkspace() {
  const kb = state.current;
  if (!kb) return;
  $("#kb-title").textContent = kb.name;
  $("#kb-description").textContent = kb.description || "未添加描述";
  $("#kb-updated").textContent = `UPDATED ${formatDate(kb.updated_at).toUpperCase()}`;
  $("#stat-docs").textContent = kb.document_count;
  $("#stat-chunks").textContent = kb.chunk_count;
  $("#stat-entities").textContent = kb.entity_count;
  const [label, css] = graphStatus(kb.graph_status);
  const badge = $("#graph-status-badge");
  badge.textContent = label;
  badge.className = `graph-badge ${css}`;
  const build = $("#build-graph-button");
  build.disabled = kb.graph_status === "building" || !kb.chunk_count;
  build.innerHTML = kb.graph_status === "building" ? '<span class="button-icon">◌</span> 正在构建…' : '<span class="button-icon">⌘</span> 构建 GraphRAG';
}

function renderDocuments() {
  $("#document-count-label").textContent = `${state.documents.length} 个文件`;
  const list = $("#document-list");
  if (!state.documents.length) {
    list.innerHTML = '<div class="list-empty">暂无文档。把文件拖到上方区域开始构建知识库。</div>';
    return;
  }
  list.innerHTML = state.documents.map(doc => `
    <div class="document-row">
      <div class="file-icon">${doc.file_type.toUpperCase().slice(0, 4)}</div>
      <div class="doc-name"><strong></strong><small>${formatDate(doc.created_at)}${doc.error ? ` · ${doc.error}` : ""}</small></div>
      <div class="doc-meta">${formatBytes(doc.size_bytes)}</div><div class="doc-meta">${doc.chunk_count} 块 · ${doc.extraction_method === "hybrid" ? `混合 OCR ${doc.ocr_page_count}页` : doc.extraction_method === "ocr" ? `OCR ${doc.ocr_page_count}页` : "原生"}</div>
      <div class="doc-status ${doc.status === "error" ? "error" : ""}">${doc.status === "ready" ? "已索引" : doc.status === "error" ? "失败" : "处理中"}</div>
      <button class="delete-doc" data-id="${doc.id}" title="删除文档">×</button>
    </div>`).join("");
  state.documents.forEach((doc, index) => $$(".doc-name strong", list)[index].textContent = doc.filename);
  $$(".delete-doc", list).forEach(button => button.addEventListener("click", () => deleteDocument(button.dataset.id)));
}

function openKnowledgeBaseModal() {
  $("#kb-form").reset();
  $("#kb-modal").showModal();
  setTimeout(() => $("#kb-name-input").focus(), 50);
}

async function createKnowledgeBase(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const submit = $("#create-kb-submit");
  submit.disabled = true;
  try {
    const data = new FormData(form);
    const kb = await api("/api/knowledge-bases", { method: "POST", body: JSON.stringify({ name: data.get("name"), description: data.get("description") }) });
    $("#kb-modal").close();
    state.current = null;
    await loadKnowledgeBases(kb.id);
    toast("知识库已创建");
  } catch (error) { toast(error.message, "error"); }
  finally { submit.disabled = false; }
}

async function deleteKnowledgeBase() {
  if (!state.current || !confirm(`确定删除知识库「${state.current.name}」及其全部文档和图谱吗？此操作不可撤销。`)) return;
  try {
    await api(`/api/knowledge-bases/${state.current.id}`, { method: "DELETE" });
    state.current = null;
    await loadKnowledgeBases();
    toast("知识库已删除");
  } catch (error) { toast(error.message, "error"); }
}

async function deleteDocument(id) {
  if (!state.current || !confirm("确定删除这份文档及其全部文本块吗？")) return;
  try {
    await api(`/api/knowledge-bases/${state.current.id}/documents/${id}`, { method: "DELETE" });
    await refreshCurrent();
    toast("文档已删除；图谱需要重新构建");
  } catch (error) { toast(error.message, "error"); }
}

async function uploadFiles(files) {
  if (!state.current || !files.length) return;
  const form = new FormData();
  [...files].forEach(file => form.append("files", file));
  const progress = $("#upload-progress");
  progress.classList.remove("hidden");
  $("#upload-progress-label").textContent = `正在解析并向量化 ${files.length} 个文件…`;
  $("#upload-progress-value").textContent = "处理中";
  try {
    const result = await api(`/api/knowledge-bases/${state.current.id}/documents`, { method: "POST", body: form });
    const failed = result.documents.filter(doc => doc.status === "error");
    if (failed.length) toast(`${failed.length} 个文件处理失败：${failed[0].error}`, "error");
    else toast(`${result.documents.length} 个文件已完成索引`);
    await refreshCurrent();
  } catch (error) { toast(error.message, "error"); }
  finally { progress.classList.add("hidden"); $("#file-input").value = ""; }
}

async function refreshCurrent() {
  if (!state.current) return;
  const id = state.current.id;
  const [kb, documents, list] = await Promise.all([
    api(`/api/knowledge-bases/${id}`), api(`/api/knowledge-bases/${id}/documents`), api("/api/knowledge-bases"),
  ]);
  state.current = kb; state.documents = documents; state.knowledgeBases = list;
  renderKnowledgeBases(); renderWorkspace(); renderDocuments();
}

async function buildGraph() {
  if (!state.current) return;
  try {
    await api(`/api/knowledge-bases/${state.current.id}/graph/rebuild`, { method: "POST" });
    state.current.graph_status = "building";
    renderWorkspace();
    toast("GraphRAG 构建已启动，耗时取决于文本块数量");
    pollGraphStatus();
  } catch (error) { toast(error.message, "error"); }
}

async function pollGraphStatus() {
  clearTimeout(state.graphPoll);
  if (!state.current) return;
  const id = state.current.id;
  try {
    const kb = await api(`/api/knowledge-bases/${id}`);
    if (!state.current || state.current.id !== id) return;
    state.current = kb; renderWorkspace();
    if (kb.graph_status === "building") state.graphPoll = setTimeout(pollGraphStatus, 2500);
    else {
      await refreshCurrent();
      if (kb.graph_status === "ready") { toast("知识图谱与社区索引已构建完成"); if ($("#panel-graph").classList.contains("active")) loadGraph(); }
      if (kb.graph_status === "error") toast(`图谱构建失败：${kb.graph_error || "未知错误"}`, "error");
    }
  } catch (_) { state.graphPoll = setTimeout(pollGraphStatus, 4000); }
}

function activateTab(name) {
  $$(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === name));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === `panel-${name}`));
  if (name === "graph") setTimeout(loadGraph, 30);
}

function welcomeMarkup() {
  return `<div class="chat-welcome"><span class="welcome-index">A/01</span><h3>向你的知识库提问</h3>
    <p>回答会附带文本来源或图谱证据。混合检索适合大多数问题，全局图谱适合跨文档主题总结。</p>
    <div class="prompt-suggestions"><button>总结知识库中的核心主题</button><button>有哪些关键实体及其关系？</button><button>比较文档中的主要观点</button></div></div>`;
}

function bindSuggestions() {
  $$(".prompt-suggestions button").forEach(button => button.addEventListener("click", () => {
    $("#chat-input").value = button.textContent; $("#chat-input").focus(); resizeComposer();
  }));
}

function appendMessage(role, content = "") {
  $(".chat-welcome")?.remove();
  const message = document.createElement("div");
  message.className = `message ${role}`;
  const label = document.createElement("div"); label.className = "message-label"; label.textContent = role === "user" ? "YOU / QUERY" : "FORGE / ANSWER";
  const body = document.createElement("div"); body.className = `message-body ${role === "assistant" ? "streaming" : ""}`; body.textContent = content;
  message.append(label, body); $("#messages").append(message); scrollMessages();
  return body;
}

function scrollMessages() { const messages = $("#messages"); messages.scrollTop = messages.scrollHeight; }

async function sendChat(event) {
  event.preventDefault();
  const input = $("#chat-input");
  const query = input.value.trim();
  if (!query || !state.current || state.sending) return;
  const previousHistory = [...state.chatHistory];
  appendMessage("user", query);
  const answerBody = appendMessage("assistant", "");
  state.sending = true; $("#send-button").disabled = true; input.value = ""; resizeComposer();
  let answer = "";
  try {
    const response = await fetch(`/api/knowledge-bases/${state.current.id}/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, mode: $("#retrieval-mode").value, top_k: state.settings?.default_top_k || 6, history: previousHistory }),
    });
    if (!response.ok) { const error = await response.json(); throw new Error(error.detail || "问答请求失败"); }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, "\n");
      const blocks = buffer.split("\n\n"); buffer = blocks.pop() || "";
      for (const block of blocks) {
        let eventName = "message", dataText = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          if (line.startsWith("data:")) dataText += line.slice(5).trim();
        }
        if (!dataText) continue;
        const data = JSON.parse(dataText);
        if (eventName === "meta") renderEvidence(data);
        if (eventName === "delta") { answer += data.content; answerBody.textContent = answer; scrollMessages(); }
        if (eventName === "error") throw new Error(data.detail);
      }
      if (done) break;
    }
    answerBody.classList.remove("streaming");
    state.chatHistory.push({ role: "user", content: query }, { role: "assistant", content: answer });
    if (state.chatHistory.length > 20) state.chatHistory = state.chatHistory.slice(-20);
  } catch (error) {
    answerBody.classList.remove("streaming"); answerBody.textContent = `请求失败：${error.message}`; toast(error.message, "error");
  } finally { state.sending = false; $("#send-button").disabled = false; input.focus(); }
}

function renderEvidence(meta) {
  const items = [
    ...(meta.sources || []).map((source, index) => ({ type: `SOURCE ${index + 1}`, score: source.score ? `${Math.round(source.score * 100)}%` : "GRAPH", title: source.filename, text: source.content })),
    ...(meta.facts || []).map((fact, index) => ({ type: `RELATION ${index + 1}`, score: `W ${fact.weight}`, title: `${fact.source} → ${fact.target}`, text: `${fact.predicate}：${fact.description}` })),
    ...(meta.communities || []).map((community, index) => ({ type: `COMMUNITY ${index + 1}`, score: `${Math.round(community.score * 100)}%`, title: community.title, text: community.summary })),
  ];
  $("#evidence-count").textContent = `${items.length} ITEMS`;
  const list = $("#evidence-list"); list.innerHTML = "";
  if (!items.length) { list.innerHTML = '<div class="evidence-empty">没有检索到可用证据。</div>'; return; }
  items.forEach(item => {
    const card = document.createElement("article"); card.className = "evidence-card";
    const type = document.createElement("div"); type.className = "evidence-type"; type.innerHTML = `<span>${item.type}</span><span>${item.score}</span>`;
    const title = document.createElement("strong"); title.textContent = item.title;
    const text = document.createElement("p"); text.textContent = item.text;
    card.append(type, title, text); list.append(card);
  });
}

function resizeComposer() {
  const input = $("#chat-input"); input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
}

async function loadGraph() {
  if (!state.current) return;
  try {
    state.graph = await api(`/api/knowledge-bases/${state.current.id}/graph?limit=300`);
    const { nodes, edges, communities, status } = state.graph;
    $("#graph-summary").textContent = status === "building" ? "正在抽取实体、关系与图社区…" : `${nodes.length} 个实体 · ${edges.length} 条关系 · ${communities.length} 个图社区`;
    $("#graph-empty").classList.toggle("hidden", nodes.length > 0);
    renderCommunities(communities);
    renderLegend(nodes);
    if (!state.graphView) state.graphView = new GraphView($("#graph-canvas"), $("#node-tooltip"));
    state.graphView.setData(nodes, edges);
  } catch (error) { toast(error.message, "error"); }
}

function renderCommunities(communities) {
  const list = $("#community-list");
  if (!communities.length) { list.innerHTML = '<div class="evidence-empty">构建完成后，这里会显示用于全局检索的主题社区。</div>'; return; }
  list.innerHTML = "";
  communities.forEach((community, index) => {
    const card = document.createElement("article"); card.className = "community-card";
    const label = document.createElement("span"); label.textContent = `COMMUNITY ${String(index + 1).padStart(2, "0")} · ${community.member_count} ENTITIES`;
    const title = document.createElement("strong"); title.textContent = community.title;
    const summary = document.createElement("p"); summary.textContent = community.summary;
    card.append(label, title, summary); list.append(card);
  });
}

const graphColors = ["#477ae8", "#df6c42", "#87a91c", "#8c61c9", "#d59b1d", "#299d8f", "#c65c8b", "#69736d"];
function typeColor(type) { let hash = 0; for (const char of type || "其他") hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0; return graphColors[Math.abs(hash) % graphColors.length]; }
function renderLegend(nodes) {
  const types = [...new Set(nodes.map(node => node.type || "其他"))].slice(0, 8);
  $("#graph-legend").innerHTML = types.map(type => `<span class="legend-item"><i style="background:${typeColor(type)}"></i>${type}</span>`).join("");
}

class GraphView {
  constructor(canvas, tooltip) {
    this.canvas = canvas; this.ctx = canvas.getContext("2d"); this.tooltip = tooltip; this.nodes = []; this.edges = []; this.tickCount = 0; this.query = ""; this.hover = null;
    this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(canvas.parentElement);
    canvas.addEventListener("mousemove", event => this.onMove(event)); canvas.addEventListener("mouseleave", () => { this.hover = null; tooltip.classList.add("hidden"); this.draw(); });
  }
  resize() { const rect = this.canvas.parentElement.getBoundingClientRect(); this.width = Math.max(1, rect.width); this.height = Math.max(1, rect.height); const dpr = window.devicePixelRatio || 1; this.canvas.width = this.width * dpr; this.canvas.height = this.height * dpr; this.canvas.style.width = `${this.width}px`; this.canvas.style.height = `${this.height}px`; this.draw(); }
  setData(nodes, edges) {
    this.resize(); const cx = this.width / 2, cy = this.height / 2;
    this.nodes = nodes.map((node, index) => ({ ...node, x: cx + Math.cos(index * 2.4) * (35 + index * .7), y: cy + Math.sin(index * 2.4) * (35 + index * .7), vx: 0, vy: 0, degree: 0 }));
    this.nodeMap = new Map(this.nodes.map(node => [node.id, node]));
    this.edges = edges.filter(edge => this.nodeMap.has(edge.source) && this.nodeMap.has(edge.target));
    this.edges.forEach(edge => { this.nodeMap.get(edge.source).degree++; this.nodeMap.get(edge.target).degree++; });
    this.tickCount = 0; cancelAnimationFrame(this.frame); this.animate();
  }
  animate() { if (this.tickCount++ < 160) { this.simulate(); this.draw(); this.frame = requestAnimationFrame(() => this.animate()); } else this.draw(); }
  simulate() {
    const nodes = this.nodes, centerX = this.width / 2, centerY = this.height / 2;
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      let dx = nodes[j].x - nodes[i].x, dy = nodes[j].y - nodes[i].y, distance2 = dx * dx + dy * dy + .1, force = Math.min(2.2, 900 / distance2); const distance = Math.sqrt(distance2); dx /= distance; dy /= distance;
      nodes[i].vx -= dx * force; nodes[i].vy -= dy * force; nodes[j].vx += dx * force; nodes[j].vy += dy * force;
    }
    for (const edge of this.edges) { const a = this.nodeMap.get(edge.source), b = this.nodeMap.get(edge.target); const dx = b.x - a.x, dy = b.y - a.y, distance = Math.sqrt(dx * dx + dy * dy) || 1, force = (distance - 75) * .006; a.vx += dx / distance * force; a.vy += dy / distance * force; b.vx -= dx / distance * force; b.vy -= dy / distance * force; }
    for (const node of nodes) { node.vx += (centerX - node.x) * .0009; node.vy += (centerY - node.y) * .0009; node.vx *= .86; node.vy *= .86; node.x = Math.max(12, Math.min(this.width - 12, node.x + node.vx)); node.y = Math.max(12, Math.min(this.height - 12, node.y + node.vy)); }
  }
  draw() {
    if (!this.ctx || !this.width) return; const dpr = window.devicePixelRatio || 1; const ctx = this.ctx; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, this.width, this.height);
    ctx.lineWidth = .7; ctx.strokeStyle = "rgba(72,83,77,.27)";
    for (const edge of this.edges) { const a = this.nodeMap.get(edge.source), b = this.nodeMap.get(edge.target); ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
    for (const node of this.nodes) {
      const match = this.query && node.name.toLowerCase().includes(this.query); const radius = 3.5 + Math.min(6, Math.sqrt(node.degree + Number(node.mentions || 0)));
      ctx.beginPath(); ctx.arc(node.x, node.y, match || node === this.hover ? radius + 3 : radius, 0, Math.PI * 2); ctx.fillStyle = typeColor(node.type); ctx.fill(); if (match || node === this.hover) { ctx.lineWidth = 2; ctx.strokeStyle = "#18231f"; ctx.stroke(); }
      if (node.degree > 2 || match || node === this.hover || this.nodes.length < 25) { ctx.font = `${match ? 700 : 500} 10px system-ui`; ctx.fillStyle = "#26322d"; ctx.fillText(node.name.slice(0, 20), node.x + radius + 4, node.y + 3); }
    }
  }
  search(query) { this.query = query.trim().toLowerCase(); this.draw(); }
  onMove(event) {
    const rect = this.canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
    this.hover = this.nodes.find(node => (node.x - x) ** 2 + (node.y - y) ** 2 < 120) || null;
    if (!this.hover) { this.tooltip.classList.add("hidden"); this.draw(); return; }
    this.tooltip.innerHTML = ""; const title = document.createElement("strong"); title.textContent = `${this.hover.name} · ${this.hover.type}`; const text = document.createElement("span"); text.textContent = this.hover.description || `${this.hover.mentions || 0} 个文本证据`;
    this.tooltip.append(title, text); this.tooltip.style.left = `${Math.min(this.width - 260, x + 15)}px`; this.tooltip.style.top = `${Math.max(8, y - 10)}px`; this.tooltip.classList.remove("hidden"); this.draw();
  }
}

function bindEvents() {
  $("#new-kb-button").addEventListener("click", openKnowledgeBaseModal); $("#empty-create-button").addEventListener("click", openKnowledgeBaseModal);
  $("#kb-form").addEventListener("submit", createKnowledgeBase); $("#delete-kb-button").addEventListener("click", deleteKnowledgeBase);
  $$(".tab").forEach(tab => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
  $("#build-graph-button").addEventListener("click", buildGraph); $("#refresh-graph-button").addEventListener("click", loadGraph);
  $("#graph-search").addEventListener("input", event => state.graphView?.search(event.target.value));
  const zone = $("#upload-zone"), input = $("#file-input"); zone.addEventListener("click", () => input.click()); zone.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) input.click(); }); input.addEventListener("change", () => uploadFiles(input.files));
  ["dragenter", "dragover"].forEach(name => zone.addEventListener(name, event => { event.preventDefault(); zone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(name => zone.addEventListener(name, event => { event.preventDefault(); zone.classList.remove("dragging"); })); zone.addEventListener("drop", event => uploadFiles(event.dataTransfer.files));
  $("#chat-form").addEventListener("submit", sendChat); $("#chat-input").addEventListener("input", resizeComposer); $("#chat-input").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#chat-form").requestSubmit(); } });
  bindSuggestions();
}

async function bootstrap() {
  bindEvents();
  await Promise.allSettled([loadHealth(), loadSettings()]);
  try { await loadKnowledgeBases(); } catch (error) { toast(error.message, "error"); }
}

document.addEventListener("DOMContentLoaded", bootstrap);
