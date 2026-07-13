<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "./api";
import GraphCanvas from "./components/GraphCanvas.vue";
import type {
  ChatTurn, DocumentItem, EvidenceMeta, GraphData, GraphStatus, KnowledgeBase,
  PublicSettings, RetrievalMode,
} from "./types";

type TabName = "documents" | "chat" | "graph" | "settings";
interface ToastItem { id: number; message: string; type: "success" | "error"; }
interface UploadResult { documents: Array<{ status: string; error?: string }>; }

const knowledgeBases = ref<KnowledgeBase[]>([]);
const current = ref<KnowledgeBase | null>(null);
const documents = ref<DocumentItem[]>([]);
const settings = ref<PublicSettings | null>(null);
const activeTab = ref<TabName>("documents");
const apiOnline = ref(false);
const uploading = ref(false);
const graph = ref<GraphData | null>(null);
const graphSearch = ref("");
const retrievalMode = ref<RetrievalMode>("hybrid");
const chatTurns = ref<ChatTurn[]>([]);
const chatInput = ref("");
const sending = ref(false);
const evidence = ref<EvidenceMeta | null>(null);
const creatingName = ref("");
const creatingDescription = ref("");
const createModal = ref<HTMLDialogElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const messagesElement = ref<HTMLElement | null>(null);
const dragging = ref(false);
const toasts = ref<ToastItem[]>([]);
let toastId = 0;
let graphPoll: number | undefined;
const tabs: Array<{ key: TabName; label: string; index: string }> = [
  { key: "documents", label: "文档", index: "01" },
  { key: "chat", label: "问答", index: "02" },
  { key: "graph", label: "知识图谱", index: "03" },
  { key: "settings", label: "配置", index: "04" },
];

const graphInfo = computed(() => {
  const status = current.value?.graph_status || "empty";
  return ({
    empty: ["图谱未构建", ""], stale: ["图谱待更新", ""], building: ["图谱构建中", "building"],
    ready: ["图谱已就绪", "ready"], error: ["图谱构建失败", "error"],
  } satisfies Record<GraphStatus, [string, string]>)[status];
});

const settingsCards = computed(() => {
  const value = settings.value;
  if (!value) return [];
  return [
    { title: "Embedding", active: value.embedding_configured, values: [["Endpoint", value.embedding_base_url], ["Model", value.embedding_model], ["API Key", value.embedding_configured ? "已配置（隐藏）" : "未配置"]] },
    { title: "Chat / Graph", active: value.chat_configured, values: [["Endpoint", value.chat_base_url], ["Model", value.chat_model], ["用途", "问答 / 实体关系 / 社区摘要"]] },
    { title: "OCR", active: value.ocr_configured, values: [["Endpoint", value.ocr_base_url], ["Model", value.ocr_model], ["触发阈值", `< ${value.ocr_min_text_chars} 字符/页`], ["最大页数", value.ocr_max_pages]] },
    { title: "Reranker", active: value.rerank_configured, values: [["Endpoint", value.rerank_base_url], ["Model", value.rerank_model], ["候选数", value.rerank_candidates], ["故障策略", "自动回退向量排序"]] },
    { title: "Chunking", active: true, values: [["Chunk size", `${value.chunk_size} 字符`], ["Overlap", `${value.chunk_overlap} 字符`], ["Top K", value.default_top_k]] },
    { title: "GraphRAG", active: true, values: [["Max chunks", value.graph_max_chunks || "不限"], ["Index", "实体 + 关系 + 社区摘要"], ["Storage", "SQLite / local"]] },
  ];
});

function notify(message: string, type: "success" | "error" = "success") {
  const item = { id: ++toastId, message, type };
  toasts.value.push(item);
  window.setTimeout(() => { toasts.value = toasts.value.filter(toast => toast.id !== item.id); }, 4500);
}

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDate(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function extractionLabel(document: DocumentItem) {
  if (document.extraction_method === "hybrid") return `混合 · OCR ${document.ocr_page_count} 页`;
  if (document.extraction_method === "ocr") return `OCR · ${document.ocr_page_count} 页`;
  if (document.extraction_method === "native") return "原生文本";
  return "等待解析";
}

async function loadHealth() {
  try {
    await api<{ status: string }>("/api/health");
    apiOnline.value = true;
  } catch { apiOnline.value = false; }
}

async function loadSettings() { settings.value = await api<PublicSettings>("/api/settings"); }

async function loadKnowledgeBases(preferredId?: string) {
  knowledgeBases.value = await api<KnowledgeBase[]>("/api/knowledge-bases");
  const target = preferredId || current.value?.id || knowledgeBases.value[0]?.id;
  if (target && knowledgeBases.value.some(kb => kb.id === target)) await selectKnowledgeBase(target, false);
  else { current.value = null; documents.value = []; }
}

async function selectKnowledgeBase(id: string, resetTab = true) {
  window.clearTimeout(graphPoll);
  const [kb, docs] = await Promise.all([
    api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
    api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
  ]);
  current.value = kb;
  documents.value = docs;
  graph.value = null;
  chatTurns.value = [];
  evidence.value = null;
  if (resetTab) activeTab.value = "documents";
  if (kb.graph_status === "building") pollGraphStatus();
}

async function refreshCurrent() {
  if (!current.value) return;
  const id = current.value.id;
  const [kb, docs, list] = await Promise.all([
    api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
    api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
    api<KnowledgeBase[]>("/api/knowledge-bases"),
  ]);
  current.value = kb; documents.value = docs; knowledgeBases.value = list;
}

function openCreateModal() {
  creatingName.value = ""; creatingDescription.value = ""; createModal.value?.showModal();
}

async function createKnowledgeBase() {
  if (!creatingName.value.trim()) return;
  try {
    const kb = await api<KnowledgeBase>("/api/knowledge-bases", {
      method: "POST",
      body: JSON.stringify({ name: creatingName.value.trim(), description: creatingDescription.value.trim() }),
    });
    createModal.value?.close();
    await loadKnowledgeBases(kb.id);
    notify("知识库已创建");
  } catch (error) { notify((error as Error).message, "error"); }
}

async function deleteKnowledgeBase() {
  if (!current.value || !window.confirm(`确定删除知识库「${current.value.name}」及其全部文档和图谱吗？此操作不可撤销。`)) return;
  try {
    await api<void>(`/api/knowledge-bases/${current.value.id}`, { method: "DELETE" });
    current.value = null;
    await loadKnowledgeBases();
    notify("知识库已删除");
  } catch (error) { notify((error as Error).message, "error"); }
}

async function deleteDocument(id: string) {
  if (!current.value || !window.confirm("确定删除这份文档及其全部文本块吗？")) return;
  try {
    await api<void>(`/api/knowledge-bases/${current.value.id}/documents/${id}`, { method: "DELETE" });
    await refreshCurrent();
    notify("文档已删除；图谱需要重新构建");
  } catch (error) { notify((error as Error).message, "error"); }
}

async function uploadFiles(files: FileList | File[]) {
  if (!current.value || !files.length || uploading.value) return;
  const form = new FormData();
  Array.from(files).forEach(file => form.append("files", file));
  uploading.value = true;
  try {
    const result = await api<UploadResult>(`/api/knowledge-bases/${current.value.id}/documents`, { method: "POST", body: form });
    const failed = result.documents.filter(item => item.status === "error");
    if (failed.length) notify(`${failed.length} 个文件处理失败：${failed[0].error || "未知错误"}`, "error");
    else notify(`${result.documents.length} 个文件已完成解析和索引`);
    await refreshCurrent();
  } catch (error) { notify((error as Error).message, "error"); }
  finally { uploading.value = false; if (fileInput.value) fileInput.value.value = ""; }
}

function handleDrop(event: DragEvent) {
  dragging.value = false;
  if (event.dataTransfer?.files.length) void uploadFiles(event.dataTransfer.files);
}

function handleFileInput(event: Event) {
  const target = event.target as HTMLInputElement;
  if (target.files?.length) void uploadFiles(target.files);
}

async function buildGraph() {
  if (!current.value) return;
  try {
    await api(`/api/knowledge-bases/${current.value.id}/graph/rebuild`, { method: "POST" });
    current.value.graph_status = "building";
    notify("GraphRAG 构建已启动");
    pollGraphStatus();
  } catch (error) { notify((error as Error).message, "error"); }
}

async function pollGraphStatus() {
  window.clearTimeout(graphPoll);
  if (!current.value) return;
  const id = current.value.id;
  try {
    const kb = await api<KnowledgeBase>(`/api/knowledge-bases/${id}`);
    if (current.value?.id !== id) return;
    current.value = kb;
    if (kb.graph_status === "building") graphPoll = window.setTimeout(pollGraphStatus, 2500);
    else {
      await refreshCurrent();
      if (kb.graph_status === "ready") { notify("知识图谱与社区索引已构建完成"); if (activeTab.value === "graph") await loadGraph(); }
      if (kb.graph_status === "error") notify(`图谱构建失败：${kb.graph_error || "未知错误"}`, "error");
    }
  } catch { graphPoll = window.setTimeout(pollGraphStatus, 4000); }
}

async function loadGraph() {
  if (!current.value) return;
  try { graph.value = await api<GraphData>(`/api/knowledge-bases/${current.value.id}/graph?limit=300`); }
  catch (error) { notify((error as Error).message, "error"); }
}

async function scrollMessages() {
  await nextTick();
  if (messagesElement.value) messagesElement.value.scrollTop = messagesElement.value.scrollHeight;
}

async function sendChat() {
  const query = chatInput.value.trim();
  if (!query || !current.value || sending.value) return;
  const history = chatTurns.value.slice(-20).map(turn => ({ ...turn }));
  chatTurns.value.push({ role: "user", content: query });
  const answer: ChatTurn = { role: "assistant", content: "" };
  chatTurns.value.push(answer);
  chatInput.value = "";
  sending.value = true;
  await scrollMessages();
  try {
    const response = await fetch(`/api/knowledge-bases/${current.value.id}/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, mode: retrievalMode.value, top_k: settings.value?.default_top_k || 6, history }),
    });
    if (!response.ok) {
      const payload = await response.json() as { detail?: string };
      throw new Error(payload.detail || "问答请求失败");
    }
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, "\n");
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        let eventName = "message"; let dataText = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          if (line.startsWith("data:")) dataText += line.slice(5).trim();
        }
        if (!dataText) continue;
        const data = JSON.parse(dataText) as EvidenceMeta & { content?: string; detail?: string };
        if (eventName === "meta") { evidence.value = data; if (data.warnings?.length) notify(data.warnings[0], "error"); }
        if (eventName === "delta") { answer.content += data.content || ""; await scrollMessages(); }
        if (eventName === "error") throw new Error(data.detail || "模型请求失败");
      }
      if (done) break;
    }
  } catch (error) {
    answer.content = `请求失败：${(error as Error).message}`;
    notify((error as Error).message, "error");
  } finally { sending.value = false; }
}

watch(activeTab, tab => { if (tab === "graph") void loadGraph(); });
onMounted(async () => {
  await Promise.allSettled([loadHealth(), loadSettings()]);
  try { await loadKnowledgeBases(); } catch (error) { notify((error as Error).message, "error"); }
});
onBeforeUnmount(() => window.clearTimeout(graphPoll));
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark"><span /><span /><span /></div><div><strong>INGOT</strong><small>RAG STUDIO</small></div></div>
      <button class="button button-primary button-full" @click="openCreateModal"><span>＋</span> 新建知识库</button>
      <div class="sidebar-label">知识库</div>
      <nav class="kb-list">
        <button v-for="kb in knowledgeBases" :key="kb.id" class="kb-item" :class="{ active: current?.id === kb.id }" @click="selectKnowledgeBase(kb.id)">
          <i class="kb-item-dot" /><strong>{{ kb.name }}</strong><small>{{ kb.document_count }}</small>
        </button>
        <div v-if="!knowledgeBases.length" class="sidebar-empty">还没有知识库。创建一个，然后导入你的第一批资料。</div>
      </nav>
      <div class="sidebar-foot"><div class="status-line"><span class="status-dot" :class="{ online: apiOnline }" /><span>{{ apiOnline ? (settings?.embedding_configured ? "服务在线" : "等待配置 API Key") : "服务不可用" }}</span></div><div class="model-chip">{{ settings?.embedding_model || "BAAI/bge-m3" }}</div></div>
    </aside>

    <main class="main">
      <section v-if="!current" class="empty-state">
        <div class="empty-graphic"><div class="orbit orbit-one" /><div class="orbit orbit-two" /><div class="core">K</div></div>
        <p class="eyebrow">RAG + OCR + GRAPHRAG KNOWLEDGE ENGINE</p>
        <h1>把散落的文档，锻造成<br>可追溯的知识网络。</h1>
        <p>浏览器完成文档解析、扫描件 OCR、向量索引、知识图谱与问答，无需命令行管理。</p>
        <button class="button button-primary" @click="openCreateModal">创建第一个知识库 <span>→</span></button>
      </section>

      <section v-else class="workspace">
        <header class="workspace-header">
          <div><div class="eyebrow">KNOWLEDGE BASE / UPDATED {{ formatDate(current.updated_at).toUpperCase() }}</div><div class="title-row"><h1>{{ current.name }}</h1><span class="graph-badge" :class="graphInfo[1]">{{ graphInfo[0] }}</span></div><p>{{ current.description || "未添加描述" }}</p></div>
          <div class="header-actions"><div class="stat"><strong>{{ current.document_count }}</strong><span>文档</span></div><div class="stat"><strong>{{ current.chunk_count }}</strong><span>文本块</span></div><div class="stat"><strong>{{ current.entity_count }}</strong><span>实体</span></div><button class="icon-button danger" title="删除知识库" @click="deleteKnowledgeBase">⌫</button></div>
        </header>
        <nav class="tabs">
          <button v-for="tab in tabs" :key="tab.key" class="tab" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key"><span>{{ tab.index }}</span>{{ tab.label }}</button>
        </nav>

        <section v-show="activeTab === 'documents'" id="panel-documents" class="tab-panel active">
          <div class="section-heading"><div><p class="eyebrow">INGESTION PIPELINE</p><h2>构建知识底座</h2><p>原生文本优先；图片和低文本密度 PDF 页面自动进入 OCR，再执行切分、向量化与索引。</p></div><button class="button button-secondary" :disabled="current.graph_status === 'building' || !current.chunk_count" @click="buildGraph"><span class="button-icon">⌘</span>{{ current.graph_status === "building" ? "正在构建…" : "构建 GraphRAG" }}</button></div>
          <div class="pipeline-strip"><span><i>01</i> 文档解析</span><b>→</b><span><i>02</i> OCR fallback</span><b>→</b><span><i>03</i> Chunk + Embed</span><b>→</b><span><i>04</i> Rerank / Graph</span></div>
          <div class="upload-zone" :class="{ dragging }" @click="fileInput?.click()" @dragenter.prevent="dragging = true" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="handleDrop">
            <input ref="fileInput" type="file" multiple hidden accept=".txt,.md,.markdown,.pdf,.docx,.pptx,.xlsx,.csv,.json,.html,.htm,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff" @change="handleFileInput">
            <div class="upload-icon">↥</div><div><strong>拖放文档或扫描图片到这里</strong><p>自动判断是否需要 OCR · 单文件最大 {{ settings?.max_upload_mb || 50 }} MB</p></div><div class="format-list"><span>PDF</span><span>DOCX</span><span>PPTX</span><span>XLSX</span><span>IMAGE/OCR</span></div>
          </div>
          <div v-if="uploading" class="upload-progress"><div><span>正在解析、OCR 并建立索引…</span><strong>处理中</strong></div><div class="progress-track"><span /></div></div>
          <div class="document-section"><div class="list-heading"><h3>已导入文档</h3><span>{{ documents.length }} 个文件</span></div>
            <div v-if="!documents.length" class="list-empty">暂无文档。把文件拖到上方区域开始构建知识库。</div>
            <div v-for="document in documents" :key="document.id" class="document-row document-row-rich">
              <div class="file-icon">{{ document.file_type.toUpperCase().slice(0, 4) }}</div><div class="doc-name"><strong>{{ document.filename }}</strong><small>{{ formatDate(document.created_at) }}<template v-if="document.warning"> · {{ document.warning }}</template></small></div>
              <div class="doc-meta">{{ formatBytes(document.size_bytes) }}<br>{{ document.page_count || 1 }} 页</div><div class="doc-meta">{{ document.chunk_count }} 文本块</div><div class="extraction-badge" :class="document.extraction_method">{{ extractionLabel(document) }}</div>
              <div class="doc-status" :class="{ error: document.status === 'error' }">{{ document.status === "ready" ? "已索引" : document.status === "error" ? "失败" : "处理中" }}</div><button class="delete-doc" title="删除文档" @click="deleteDocument(document.id)">×</button>
            </div>
          </div>
        </section>

        <section v-show="activeTab === 'chat'" id="panel-chat" class="tab-panel chat-panel active">
          <div class="chat-main"><div class="chat-toolbar"><div><p class="eyebrow">GROUNDED CONVERSATION</p><h2>知识问答</h2></div><label class="select-wrap">检索模式<select v-model="retrievalMode"><option value="hybrid">混合检索</option><option value="vector">向量检索</option><option value="graph_local">图谱局部检索</option><option value="graph_global">图谱全局检索</option></select></label></div>
            <div ref="messagesElement" class="messages">
              <div v-if="!chatTurns.length" class="chat-welcome"><span class="welcome-index">A/01</span><h3>向你的知识库提问</h3><p>候选文本会先经过 Reranker 精排；回答附带原文、图关系或图社区证据。</p><div class="prompt-suggestions"><button v-for="prompt in ['总结知识库中的核心主题', '有哪些关键实体及其关系？', '比较文档中的主要观点']" :key="prompt" @click="chatInput = prompt">{{ prompt }}</button></div></div>
              <div v-for="(turn, index) in chatTurns" :key="index" class="message" :class="turn.role"><div class="message-label">{{ turn.role === "user" ? "YOU / QUERY" : "FORGE / ANSWER" }}</div><div class="message-body" :class="{ streaming: sending && index === chatTurns.length - 1 }">{{ turn.content }}</div></div>
            </div>
            <form class="chat-composer" @submit.prevent="sendChat"><textarea v-model="chatInput" rows="1" maxlength="4000" placeholder="输入问题，Enter 发送，Shift + Enter 换行…" @keydown.enter.exact.prevent="sendChat" /><button class="send-button" type="submit" :disabled="sending">↑</button></form>
          </div>
          <aside class="evidence-panel"><div class="evidence-head"><span>检索证据</span><small>{{ (evidence?.sources.length || 0) + (evidence?.facts.length || 0) + (evidence?.communities.length || 0) }} ITEMS</small></div><div class="evidence-list">
            <div v-if="!evidence" class="evidence-empty">提问后，这里会展示模型使用的原始证据。</div>
            <article v-for="(source, index) in evidence?.sources" :key="source.id" class="evidence-card"><div class="evidence-type"><span>SOURCE {{ index + 1 }}</span><span>{{ source.score_type === "rerank" ? "RERANK" : `${Math.round(source.score * 100)}%` }}</span></div><strong>{{ source.filename }}{{ source.page_number ? ` · 第 ${source.page_number} 页` : "" }}</strong><p>{{ source.content }}</p></article>
            <article v-for="(fact, index) in evidence?.facts" :key="fact.id" class="evidence-card"><div class="evidence-type"><span>RELATION {{ index + 1 }}</span><span>W {{ fact.weight }}</span></div><strong>{{ fact.source }} → {{ fact.target }}</strong><p>{{ fact.predicate }}：{{ fact.description }}</p></article>
            <article v-for="(community, index) in evidence?.communities" :key="community.id" class="evidence-card"><div class="evidence-type"><span>COMMUNITY {{ index + 1 }}</span><span>GLOBAL</span></div><strong>{{ community.title }}</strong><p>{{ community.summary }}</p></article>
          </div></aside>
        </section>

        <section v-show="activeTab === 'graph'" id="panel-graph" class="tab-panel active"><div class="graph-toolbar"><div><p class="eyebrow">ENTITY RELATIONSHIP MAP</p><h2>知识图谱</h2><p>{{ graph ? `${graph.nodes.length} 个实体 · ${graph.edges.length} 条关系 · ${graph.communities.length} 个图社区` : "尚未构建图谱" }}</p></div><div class="graph-actions"><label class="search-box">⌕<input v-model="graphSearch" placeholder="查找实体"></label><button class="button button-secondary" @click="loadGraph">刷新</button></div></div><div class="graph-layout"><GraphCanvas :nodes="graph?.nodes || []" :edges="graph?.edges || []" :search="graphSearch" /><aside class="community-panel"><div class="evidence-head"><span>图社区</span><small>GLOBAL INDEX</small></div><div class="community-list"><div v-if="!graph?.communities.length" class="evidence-empty">构建完成后，这里会显示用于全局检索的主题社区。</div><article v-for="(community, index) in graph?.communities" :key="community.id" class="community-card"><span>COMMUNITY {{ String(index + 1).padStart(2, "0") }} · {{ community.member_count }} ENTITIES</span><strong>{{ community.title }}</strong><p>{{ community.summary }}</p></article></div></aside></div></section>

        <section v-show="activeTab === 'settings'" id="panel-settings" class="tab-panel active"><div class="section-heading"><div><p class="eyebrow">RUNTIME CONFIGURATION</p><h2>运行配置</h2><p>配置从本地 <code>.env</code> 读取。为保护密钥，此页面不会显示 API Key。</p></div></div><div class="settings-grid"><article v-for="card in settingsCards" :key="card.title" class="setting-card"><div class="setting-card-head"><strong>{{ card.title }}</strong><i class="config-status" :class="{ off: !card.active }" /></div><dl><template v-for="value in card.values" :key="value[0]"><dt>{{ value[0] }}</dt><dd>{{ value[1] }}</dd></template></dl></article></div></section>
      </section>
    </main>
  </div>

  <dialog ref="createModal" class="modal"><form @submit.prevent="createKnowledgeBase"><div class="modal-top"><span class="eyebrow">NEW KNOWLEDGE BASE</span><button type="button" @click="createModal?.close()">×</button></div><h2>创建知识库</h2><label>名称<input v-model="creatingName" maxlength="80" required placeholder="例如：产品技术文档"></label><label>描述（可选）<textarea v-model="creatingDescription" maxlength="500" rows="3" placeholder="这个知识库将用于什么？" /></label><div class="modal-actions"><button type="button" class="button button-ghost" @click="createModal?.close()">取消</button><button class="button button-primary" type="submit">创建知识库</button></div></form></dialog>
  <div class="toast-container"><div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.type">{{ toast.message }}</div></div>
</template>
