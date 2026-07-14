<script setup lang="ts">
import DOMPurify from "dompurify";
import MarkdownIt from "markdown-it";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "./api";
import GraphCanvas from "./components/GraphCanvas.vue";
import type {
  ChatTurn, DocumentItem, EvidenceMeta, GraphData, GraphStatus, KnowledgeBase,
  PublicSettings, RetrievalMode, SettingsUpdate,
} from "./types";

type TabName = "home" | "documents" | "chat" | "graph" | "settings";
type SettingsSection = keyof SettingsUpdate;
interface ToastItem { id: number; message: string; type: "success" | "error"; }
interface UploadResult { documents: Array<{ status: string; error?: string }>; }

const knowledgeBases = ref<KnowledgeBase[]>([]);
const current = ref<KnowledgeBase | null>(null);
const documents = ref<DocumentItem[]>([]);
const settings = ref<PublicSettings | null>(null);
const settingsForm = ref<SettingsUpdate | null>(null);
const savingSettings = ref(false);
const savingSettingsSection = ref<SettingsSection | "all" | null>(null);
const editingSettingsSection = ref<SettingsSection | null>(null);
const secretVisibility = ref({ embedding: false, chat: false, ocr: false, rerank: false });
const activeTab = ref<TabName>("home");
const showLanding = ref(false);
const apiOnline = ref(false);
const uploading = ref(false);
const graph = ref<GraphData | null>(null);
const graphSearch = ref("");
const graphPanel = ref<HTMLElement | null>(null);
const graphFullscreen = ref(false);
const graphFullscreenFallback = ref(false);
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
const toastContainer = ref<HTMLElement | null>(null);
let toastId = 0;
let autoRefreshTimer: number | undefined;
let autoRefreshInFlight = false;
let autoRefreshFailures = 0;
const AUTO_REFRESH_IDLE_MS = 8000;
const AUTO_REFRESH_BUILDING_MS = 2500;
const AUTO_REFRESH_RETRY_MS = 4000;
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true });
const graphStageLabels: Record<string, string> = {
  queued: "准备构建",
  extracting: "抽取实体关系",
  communities: "生成图社区",
  embedding: "生成社区向量",
  completed: "图谱已构建",
  interrupted: "构建已中断",
  failed: "构建失败",
};
const tabs: Array<{ key: TabName; label: string; index: string }> = [
  { key: "home", label: "首页", index: "01" },
  { key: "documents", label: "文档", index: "02" },
  { key: "chat", label: "问答", index: "03" },
  { key: "graph", label: "知识图谱", index: "04" },
  { key: "settings", label: "配置", index: "05" },
];
const settingsSectionLabels: Record<SettingsSection, string> = {
  embedding: "Embedding",
  chat: "Chat / Graph",
  ocr: "OCR",
  rerank: "Reranker",
  chunking: "Chunking",
  graph: "GraphRAG",
};
const settingsSections = Object.keys(settingsSectionLabels) as SettingsSection[];

const graphInfo = computed(() => {
  const status = current.value?.graph_status || "empty";
  if (status === "building") return [graphProgressLabel(current.value), "building"] as [string, string];
  return ({
    empty: ["图谱未构建", ""], stale: ["图谱待更新", ""], building: ["图谱构建中", "building"],
    ready: ["图谱已就绪", "ready"], error: ["图谱构建失败", "error"],
  } satisfies Record<GraphStatus, [string, string]>)[status];
});

const graphProgressPercent = computed(() => {
  const total = current.value?.graph_progress_total || 0;
  const value = current.value?.graph_progress_current || 0;
  return total ? Math.min(100, Math.round((value / total) * 100)) : 0;
});
const landingDocumentCount = computed(() => knowledgeBases.value.reduce((total, kb) => total + kb.document_count, 0));

function graphProgressLabel(kb: KnowledgeBase | null) {
  if (!kb) return "图谱构建中";
  const label = graphStageLabels[kb.graph_stage || ""] || "图谱构建中";
  const total = kb.graph_progress_total || 0;
  const value = Math.min(kb.graph_progress_current || 0, total);
  return total ? `${label} ${value}/${total}` : label;
}

function renderMarkdown(content: string) {
  return DOMPurify.sanitize(markdown.render(content || ""));
}

function showToastLayer(forceToFront = false) {
  const element = toastContainer.value;
  if (!element || typeof element.showPopover !== "function") return;
  try {
    if (forceToFront && element.matches(":popover-open")) element.hidePopover();
    if (!element.matches(":popover-open")) element.showPopover();
  } catch {
    // Browsers without Popover API support keep the fixed-position fallback visible.
  }
}

function hideToastLayer() {
  const element = toastContainer.value;
  if (!element || typeof element.hidePopover !== "function") return;
  try {
    if (element.matches(":popover-open")) element.hidePopover();
  } catch {
    // No action is needed for the fixed-position fallback.
  }
}

function notify(message: string, type: "success" | "error" = "success") {
  const item = { id: ++toastId, message, type };
  toasts.value.push(item);
  void nextTick(() => showToastLayer(true));
  window.setTimeout(() => {
    toasts.value = toasts.value.filter(toast => toast.id !== item.id);
    if (!toasts.value.length) hideToastLayer();
  }, 4500);
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

function settingsToForm(value: PublicSettings): SettingsUpdate {
  return {
    embedding: {
      base_url: value.embedding_base_url,
      model: value.embedding_model,
      api_key: "",
      batch_size: value.embedding_batch_size,
      timeout: value.embedding_timeout,
    },
    chat: {
      base_url: value.chat_base_url,
      model: value.chat_model,
      api_key: "",
      use_embedding_provider: value.chat_uses_embedding_provider,
      timeout: value.chat_timeout,
      temperature: value.chat_temperature,
      max_tokens: value.chat_max_tokens,
    },
    ocr: {
      enabled: value.ocr_enabled,
      base_url: value.ocr_base_url,
      model: value.ocr_model,
      api_key: "",
      use_embedding_provider: value.ocr_uses_embedding_provider,
      timeout: value.ocr_timeout,
      concurrency: value.ocr_concurrency,
      min_text_chars: value.ocr_min_text_chars,
      max_pages: value.ocr_max_pages,
      render_dpi: value.ocr_render_dpi,
    },
    rerank: {
      enabled: value.rerank_enabled,
      base_url: value.rerank_base_url,
      model: value.rerank_model,
      api_key: "",
      use_embedding_provider: value.rerank_uses_embedding_provider,
      candidates: value.rerank_candidates,
      timeout: value.rerank_timeout,
    },
    chunking: {
      chunk_size: value.chunk_size,
      chunk_overlap: value.chunk_overlap,
      default_top_k: value.default_top_k,
    },
    graph: {
      concurrency: value.graph_concurrency,
      max_chunks: value.graph_max_chunks,
      chunk_timeout: value.graph_chunk_timeout,
      build_timeout: value.graph_build_timeout,
    },
  };
}

async function loadSettings() {
  const value = await api<PublicSettings>("/api/settings");
  settings.value = value;
  settingsForm.value = settingsToForm(value);
  editingSettingsSection.value = null;
}

function resetSettingsForm() {
  if (settings.value) settingsForm.value = settingsToForm(settings.value);
  secretVisibility.value = { embedding: false, chat: false, ocr: false, rerank: false };
  editingSettingsSection.value = null;
}

function secretPlaceholder(configured: boolean, inherited = false) {
  if (inherited) return "复用 Embedding 密钥";
  return configured ? "••••••••（已配置，留空保留）" : "输入 API Key";
}

function hideSectionSecret(section: SettingsSection) {
  if (section === "embedding" || section === "chat" || section === "ocr" || section === "rerank") {
    secretVisibility.value[section] = false;
  }
}

function beginSettingsEdit(section: SettingsSection) {
  editingSettingsSection.value = section;
}

function discardSettingsSection(section: SettingsSection) {
  if (!settings.value || !settingsForm.value) return;
  const savedForm = settingsToForm(settings.value);
  Object.assign(settingsForm.value[section], savedForm[section]);
  hideSectionSecret(section);
  if (editingSettingsSection.value === section) editingSettingsSection.value = null;
}

function handleSettingsPointerDown(event: PointerEvent) {
  const section = editingSettingsSection.value;
  if (!section || activeTab.value !== "settings" || !(event.target instanceof Element)) return;
  if (event.target.closest(`[data-settings-section="${section}"]`)) return;
  if (event.target.closest(".settings-actions")) return;
  discardSettingsSection(section);
}

function cloneSettingsForm(value: SettingsUpdate): SettingsUpdate {
  return JSON.parse(JSON.stringify(value)) as SettingsUpdate;
}

async function saveSettings(section?: SettingsSection) {
  if (!settingsForm.value || savingSettings.value) return;
  const editedForm = cloneSettingsForm(settingsForm.value);
  const payload = section && settings.value ? settingsToForm(settings.value) : cloneSettingsForm(editedForm);
  if (section) Object.assign(payload[section], editedForm[section]);
  if ((!section || section === "chunking") && payload.chunking.chunk_overlap >= payload.chunking.chunk_size) {
    notify("切分重叠字符数必须小于单块字符数", "error");
    return;
  }
  savingSettings.value = true;
  savingSettingsSection.value = section || "all";
  try {
    const value = await api<PublicSettings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    settings.value = value;
    const refreshedForm = settingsToForm(value);
    if (section) {
      for (const otherSection of settingsSections) {
        if (otherSection !== section) Object.assign(refreshedForm[otherSection], editedForm[otherSection]);
      }
      hideSectionSecret(section);
    } else {
      secretVisibility.value = { embedding: false, chat: false, ocr: false, rerank: false };
    }
    settingsForm.value = refreshedForm;
    editingSettingsSection.value = null;
    notify(`${section ? `${settingsSectionLabels[section]} ` : "全部"}配置已保存并应用`);
  } catch (error) {
    notify((error as Error).message, "error");
  } finally {
    savingSettings.value = false;
    savingSettingsSection.value = null;
  }
}

async function loadKnowledgeBases(preferredId?: string) {
  knowledgeBases.value = await api<KnowledgeBase[]>("/api/knowledge-bases");
  const target = preferredId || current.value?.id || knowledgeBases.value[0]?.id;
  if (target && knowledgeBases.value.some(kb => kb.id === target)) await selectKnowledgeBase(target, false);
  else { current.value = null; documents.value = []; }
  scheduleAutoRefresh();
}

async function selectKnowledgeBase(id: string, resetTab = true) {
  window.clearTimeout(autoRefreshTimer);
  const [kb, docs] = await Promise.all([
    api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
    api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
  ]);
  current.value = kb;
  documents.value = docs;
  showLanding.value = false;
  graph.value = null;
  chatTurns.value = [];
  evidence.value = null;
  if (resetTab) activeTab.value = "home";
  scheduleAutoRefresh();
}

function openLanding() {
  if (editingSettingsSection.value) discardSettingsSection(editingSettingsSection.value);
  showLanding.value = true;
}

async function refreshCurrent() {
  if (!current.value) return;
  const id = current.value.id;
  const [kb, docs, list] = await Promise.all([
    api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
    api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
    api<KnowledgeBase[]>("/api/knowledge-bases"),
  ]);
  if (current.value?.id !== id) return;
  current.value = kb; documents.value = docs; knowledgeBases.value = list;
}

async function openCreateModal() {
  creatingName.value = "";
  creatingDescription.value = "";
  await nextTick();
  createModal.value?.showModal();
  if (toasts.value.length) showToastLayer(true);
}

function closeCreateModal() {
  creatingName.value = "";
  creatingDescription.value = "";
  createModal.value?.close();
}

async function createKnowledgeBase() {
  if (!creatingName.value.trim()) return;
  try {
    const kb = await api<KnowledgeBase>("/api/knowledge-bases", {
      method: "POST",
      body: JSON.stringify({ name: creatingName.value.trim(), description: creatingDescription.value.trim() }),
    });
    closeCreateModal();
    await loadKnowledgeBases(kb.id);
    activeTab.value = "home";
    notify("知识库已创建");
  } catch (error) { notify((error as Error).message, "error"); }
}

async function deleteKnowledgeBase() {
  if (!current.value || !window.confirm(`确定删除知识库「${current.value.name}」及其全部文档和图谱吗？此操作不可撤销。`)) return;
  try {
    await api<void>(`/api/knowledge-bases/${current.value.id}`, { method: "DELETE" });
    current.value = null;
    await loadKnowledgeBases();
    activeTab.value = "home";
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
    if (failed.length) notify(`${failed.length} 个文件处理失败：${failed[0]?.error || "未知错误"}`, "error");
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
    current.value.graph_stage = "queued";
    current.value.graph_progress_current = 0;
    current.value.graph_progress_total = Math.min(
      current.value.chunk_count,
      settings.value?.graph_max_chunks || current.value.chunk_count,
    );
    current.value.graph_failed_chunks = 0;
    notify("GraphRAG 构建已启动");
    scheduleAutoRefresh(250);
  } catch (error) { notify((error as Error).message, "error"); }
}

async function cancelGraph() {
  if (!current.value || !window.confirm("确定停止当前 GraphRAG 构建吗？已生成的部分图数据不会标记为可用。")) return;
  try {
    await api(`/api/knowledge-bases/${current.value.id}/graph/cancel`, { method: "POST" });
    notify("正在停止 GraphRAG 构建");
    scheduleAutoRefresh(250);
  } catch (error) { notify((error as Error).message, "error"); }
}

function graphSnapshot(kb: KnowledgeBase) {
  return [
    kb.graph_status,
    kb.entity_count,
    kb.relationship_count,
    kb.community_count || 0,
    kb.updated_at,
  ].join(":");
}

function nextAutoRefreshDelay() {
  return current.value?.graph_status === "building"
    ? AUTO_REFRESH_BUILDING_MS
    : AUTO_REFRESH_IDLE_MS;
}

function scheduleAutoRefresh(delay = nextAutoRefreshDelay()) {
  window.clearTimeout(autoRefreshTimer);
  if (document.visibilityState === "hidden") return;
  autoRefreshTimer = window.setTimeout(() => void syncWorkspace(), delay);
}

async function syncWorkspace() {
  window.clearTimeout(autoRefreshTimer);
  if (document.visibilityState === "hidden") return;
  if (autoRefreshInFlight) {
    scheduleAutoRefresh();
    return;
  }

  autoRefreshInFlight = true;
  try {
    if (!current.value) {
      const list = await api<KnowledgeBase[]>("/api/knowledge-bases");
      knowledgeBases.value = list;
      const firstKnowledgeBase = list[0];
      if (firstKnowledgeBase) await selectKnowledgeBase(firstKnowledgeBase.id, false);
      apiOnline.value = true;
      autoRefreshFailures = 0;
      return;
    }

    const id = current.value.id;
    const previousStatus = current.value.graph_status;
    const previousGraph = graphSnapshot(current.value);
    let kb: KnowledgeBase;

    if (previousStatus === "building") {
      kb = await api<KnowledgeBase>(`/api/knowledge-bases/${id}`);
    } else {
      const [nextKnowledgeBase, docs, list] = await Promise.all([
        api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
        api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
        api<KnowledgeBase[]>("/api/knowledge-bases"),
      ]);
      kb = nextKnowledgeBase;
      if (current.value?.id !== id) return;
      documents.value = docs;
      knowledgeBases.value = list;
    }

    if (current.value?.id !== id) return;
    apiOnline.value = true;
    autoRefreshFailures = 0;
    current.value = kb;

    const statusChanged = previousStatus !== kb.graph_status;
    const graphChanged = previousGraph !== graphSnapshot(kb);
    if (previousStatus === "building" && kb.graph_status !== "building") {
      await refreshCurrent();
      if (kb.graph_status === "ready") {
        notify("知识图谱与社区索引已构建完成");
        if (activeTab.value === "graph") await loadGraph();
      }
      if (kb.graph_status === "error") {
        notify(`图谱构建失败：${kb.graph_error || "未知错误"}`, "error");
      }
    } else if (activeTab.value === "graph" && (statusChanged || graphChanged)) {
      await loadGraph();
    }
  } catch (error) {
    apiOnline.value = false;
    autoRefreshFailures += 1;
    if (autoRefreshFailures === 3) {
      notify(`暂时无法自动同步页面：${(error as Error).message}`, "error");
    }
  } finally {
    autoRefreshInFlight = false;
    scheduleAutoRefresh(apiOnline.value ? nextAutoRefreshDelay() : AUTO_REFRESH_RETRY_MS);
  }
}

function handleVisibilityChange() {
  if (document.visibilityState === "hidden") window.clearTimeout(autoRefreshTimer);
  else void syncWorkspace();
}

function handleWindowFocus() {
  if (document.visibilityState === "visible") void syncWorkspace();
}

async function toggleGraphFullscreen() {
  const panel = graphPanel.value;
  if (!panel) return;
  if (graphFullscreenFallback.value) {
    graphFullscreenFallback.value = false;
    graphFullscreen.value = false;
    return;
  }
  try {
    if (document.fullscreenElement === panel) await document.exitFullscreen();
    else await panel.requestFullscreen();
  } catch {
    graphFullscreenFallback.value = true;
    graphFullscreen.value = true;
    notify("浏览器未允许系统全屏，已切换为页面全屏模式");
  }
}

function handleFullscreenChange() {
  const nativeFullscreen = document.fullscreenElement === graphPanel.value;
  if (nativeFullscreen) graphFullscreenFallback.value = false;
  graphFullscreen.value = nativeFullscreen || graphFullscreenFallback.value;
}

function handleGraphFullscreenKeydown(event: KeyboardEvent) {
  if (event.key !== "Escape" || !graphFullscreenFallback.value) return;
  graphFullscreenFallback.value = false;
  graphFullscreen.value = false;
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
        if (eventName === "meta") {
          evidence.value = data;
          const firstWarning = data.warnings?.[0];
          if (firstWarning) notify(firstWarning, "error");
        }
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

watch(activeTab, tab => {
  if (tab !== "settings" && editingSettingsSection.value) discardSettingsSection(editingSettingsSection.value);
  if (tab === "graph") void loadGraph();
});
onMounted(async () => {
  document.addEventListener("pointerdown", handleSettingsPointerDown, true);
  document.addEventListener("visibilitychange", handleVisibilityChange);
  document.addEventListener("fullscreenchange", handleFullscreenChange);
  document.addEventListener("keydown", handleGraphFullscreenKeydown);
  window.addEventListener("focus", handleWindowFocus);
  await Promise.allSettled([loadHealth(), loadSettings()]);
  try { await loadKnowledgeBases(); } catch (error) { notify((error as Error).message, "error"); }
  scheduleAutoRefresh();
});
onBeforeUnmount(() => {
  window.clearTimeout(autoRefreshTimer);
  document.removeEventListener("pointerdown", handleSettingsPointerDown, true);
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  document.removeEventListener("fullscreenchange", handleFullscreenChange);
  document.removeEventListener("keydown", handleGraphFullscreenKeydown);
  window.removeEventListener("focus", handleWindowFocus);
  graphFullscreenFallback.value = false;
  if (document.fullscreenElement === graphPanel.value) void document.exitFullscreen();
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <button type="button" class="brand brand-button" :class="{ active: showLanding || !current }" aria-label="返回 Ingot 首页" @click="openLanding"><span class="brand-mark" aria-hidden="true"><span /><span /><span /></span><span><strong>INGOT</strong><small>RAG STUDIO</small></span></button>
      <button class="button button-primary button-full" @click="openCreateModal"><span>＋</span> 新建知识库</button>
      <div class="sidebar-label">知识库</div>
      <nav class="kb-list">
        <button v-for="kb in knowledgeBases" :key="kb.id" class="kb-item" :class="{ active: !showLanding && current?.id === kb.id }" @click="selectKnowledgeBase(kb.id)">
          <i class="kb-item-dot" /><strong>{{ kb.name }}</strong><small>{{ kb.document_count }}</small>
        </button>
        <div v-if="!knowledgeBases.length" class="sidebar-empty">还没有知识库。创建一个，然后导入你的第一批资料。</div>
      </nav>
      <div class="sidebar-foot"><div class="status-line"><span class="status-dot" :class="{ online: apiOnline }" /><span>{{ apiOnline ? (settings?.embedding_configured ? "服务在线" : "等待配置 API Key") : "服务不可用" }}</span></div><div class="model-chip">{{ settings?.embedding_model || "BAAI/bge-m3" }}</div></div>
    </aside>

    <main class="main">
      <section v-if="showLanding || !current" class="empty-state">
        <div class="empty-graphic"><div class="orbit orbit-one" /><div class="orbit orbit-two" /><div class="core">I</div></div>
        <p class="eyebrow">RAG + OCR + GRAPHRAG KNOWLEDGE ENGINE</p>
        <h1>把散落的文档，锻造成<br>可追溯的知识网络。</h1>
        <p>浏览器完成文档解析、扫描件 OCR、向量索引、知识图谱与问答，无需命令行管理。</p>
        <div class="empty-status" aria-label="Ingot 当前状态">
          <span><i class="status-dot" :class="{ online: apiOnline }" />{{ apiOnline ? "服务在线" : "服务不可用" }}</span>
          <span><strong>{{ knowledgeBases.length }}</strong> 个知识库</span>
          <span><strong>{{ landingDocumentCount }}</strong> 份文档</span>
        </div>
        <div class="empty-actions">
          <button class="button button-primary" @click="openCreateModal">{{ knowledgeBases.length ? "创建下一个知识库" : "创建第一个知识库" }} <span>→</span></button>
        </div>
      </section>

      <section v-else class="workspace">
        <nav class="tabs">
          <button v-for="tab in tabs" :key="tab.key" class="tab" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key"><span>{{ tab.index }}</span>{{ tab.label }}</button>
        </nav>

        <section v-show="activeTab === 'home'" id="panel-home" class="tab-panel" :class="{ active: activeTab === 'home' }">
          <div class="home-hero">
            <div class="home-identity">
              <p class="eyebrow">KNOWLEDGE BASE / UPDATED {{ formatDate(current.updated_at).toUpperCase() }}</p>
              <div class="home-title-row"><h1>{{ current.name }}</h1><span class="graph-badge" :class="graphInfo[1]">{{ graphInfo[0] }}</span></div>
              <p class="home-description">{{ current.description || "还没有描述。你可以直接导入资料，或在后续版本中补充这个知识库的用途。" }}</p>
              <div class="home-actions">
                <button class="button button-primary" @click="activeTab = 'documents'">管理文档 <span>→</span></button>
                <button class="button button-secondary" :disabled="!current.chunk_count" @click="activeTab = 'chat'">开始问答</button>
                <button class="button button-ghost home-delete" @click="deleteKnowledgeBase">删除知识库</button>
              </div>
            </div>
            <div class="home-seal" aria-hidden="true"><span>I</span><small>INGOT<br>KNOWLEDGE</small></div>
          </div>

          <div class="home-stats" aria-label="知识库统计">
            <article><span>DOCUMENTS</span><strong>{{ current.document_count }}</strong><small>份文档</small></article>
            <article><span>CHUNKS</span><strong>{{ current.chunk_count }}</strong><small>个文本块</small></article>
            <article><span>ENTITIES</span><strong>{{ current.entity_count }}</strong><small>个实体</small></article>
            <article><span>RELATIONS</span><strong>{{ current.relationship_count }}</strong><small>条关系</small></article>
            <article><span>COMMUNITIES</span><strong>{{ current.community_count || 0 }}</strong><small>个图社区</small></article>
          </div>

          <div class="home-guidance">
            <div class="home-section-heading"><div><p class="eyebrow">COMMON GUIDANCE</p><h2>接下来可以做什么</h2></div></div>
            <div class="guidance-grid">
              <button class="guidance-card" @click="activeTab = 'documents'">
                <span>01 / INGEST</span><strong>{{ current.document_count ? `管理 ${current.document_count} 份资料` : "导入第一批资料" }}</strong><p>上传 PDF、Office 文档、Markdown 或扫描图片，自动解析、OCR、切分并建立向量索引。</p><b>前往文档 →</b>
              </button>
              <button class="guidance-card" @click="activeTab = current.graph_status === 'ready' ? 'graph' : 'documents'">
                <span>02 / GRAPHRAG</span><strong>{{ current.graph_status === "building" ? graphProgressLabel(current) : current.graph_status === "ready" ? "浏览实体关系" : current.graph_status === "error" ? "检查并重新构建" : "构建知识图谱" }}</strong><p>从文本块提取实体与关系，形成可缩放、可拖动的关系网络和全局主题社区。</p><b>{{ current.graph_status === "ready" ? "打开图谱" : "查看构建入口" }} →</b>
              </button>
              <button class="guidance-card" @click="activeTab = current.chunk_count ? 'chat' : 'documents'">
                <span>03 / ASK</span><strong>基于证据开始问答</strong><p>使用向量、图谱局部、图谱全局或混合检索；回答会附带命中的原文与关系证据。</p><b>{{ current.chunk_count ? "开始提问" : "先导入资料" }} →</b>
              </button>
              <button class="guidance-card" @click="activeTab = 'settings'">
                <span>04 / CONFIGURE</span><strong>检查模型与吞吐配置</strong><p>在浏览器中调整模型地址、密钥、OCR、Reranker、切分参数和 GraphRAG 抽取并发。</p><b>打开配置 →</b>
              </button>
            </div>
          </div>
        </section>

        <section v-show="activeTab === 'documents'" id="panel-documents" class="tab-panel" :class="{ active: activeTab === 'documents' }">
          <div class="section-heading"><div><p class="eyebrow">INGESTION PIPELINE</p><h2>构建知识底座</h2><p>原生文本优先；图片和低文本密度 PDF 页面自动进入 OCR，再执行切分、向量化与索引。</p></div><button class="button button-secondary" :class="{ 'button-stop': current.graph_status === 'building' }" :disabled="!current.chunk_count" @click="current.graph_status === 'building' ? cancelGraph() : buildGraph()"><span class="button-icon">⌘</span>{{ current.graph_status === "building" ? "停止构建" : "构建 GraphRAG" }}</button></div>
          <div class="pipeline-strip"><span><i>01</i> 文档解析</span><b>→</b><span><i>02</i> OCR fallback</span><b>→</b><span><i>03</i> Chunk + Embed</span><b>→</b><span><i>04</i> Rerank / Graph</span></div>
          <div v-if="current.graph_status === 'error'" class="graph-build-alert"><strong>上次图谱构建未完成</strong><span>{{ current.graph_error || "未知错误，可重新发起构建。" }}</span></div>
          <div v-if="current.graph_status === 'building'" class="graph-build-progress" role="progressbar" :aria-valuenow="graphProgressPercent" aria-valuemin="0" aria-valuemax="100">
            <div><strong>{{ graphProgressLabel(current) }}</strong><span>{{ graphProgressPercent }}%</span></div>
            <p v-if="current.graph_failed_chunks">{{ current.graph_failed_chunks }} 个文本块抽取失败，任务会继续处理其余内容。</p>
            <div class="graph-progress-track"><span :style="{ width: `${graphProgressPercent}%` }" /></div>
          </div>
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

        <section v-show="activeTab === 'chat'" id="panel-chat" class="tab-panel chat-panel" :class="{ active: activeTab === 'chat' }">
          <div class="chat-main"><div class="chat-toolbar"><div><p class="eyebrow">GROUNDED CONVERSATION</p><h2>知识问答</h2></div><label class="select-wrap">检索模式<select v-model="retrievalMode"><option value="hybrid">混合检索</option><option value="vector">向量检索</option><option value="graph_local">图谱局部检索</option><option value="graph_global">图谱全局检索</option></select></label></div>
            <div ref="messagesElement" class="messages">
              <div v-if="!chatTurns.length" class="chat-welcome"><span class="welcome-index">A/01</span><h3>向你的知识库提问</h3><p>候选文本会先经过 Reranker 精排；回答附带原文、图关系或图社区证据。</p><div class="prompt-suggestions"><button v-for="prompt in ['总结知识库中的核心主题', '有哪些关键实体及其关系？', '比较文档中的主要观点']" :key="prompt" @click="chatInput = prompt">{{ prompt }}</button></div></div>
              <div v-for="(turn, index) in chatTurns" :key="index" class="message" :class="turn.role"><div class="message-label">{{ turn.role === "user" ? "YOU / QUERY" : "INGOT / ANSWER" }}</div><div v-if="turn.role === 'assistant'" class="message-body markdown-body" :class="{ streaming: sending && index === chatTurns.length - 1 }" v-html="renderMarkdown(turn.content)" /><div v-else class="message-body">{{ turn.content }}</div></div>
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

        <section ref="graphPanel" v-show="activeTab === 'graph'" id="panel-graph" class="tab-panel" :class="{ active: activeTab === 'graph', 'is-fullscreen': graphFullscreen, 'is-fullscreen-fallback': graphFullscreenFallback }">
          <div class="graph-toolbar">
            <div><p class="eyebrow">ENTITY RELATIONSHIP MAP</p><h2>知识图谱</h2><p>{{ graph ? `${graph.nodes.length} 个实体 · ${graph.edges.length} 条关系 · ${graph.communities.length} 个图社区` : "尚未构建图谱" }}</p></div>
            <div class="graph-actions">
              <label class="search-box">⌕<input v-model="graphSearch" placeholder="查找实体"></label>
              <button class="button button-secondary graph-refresh-button" @click="loadGraph">刷新</button>
              <button class="button button-ghost graph-fullscreen-button" type="button" :title="graphFullscreen ? '退出全屏（Esc）' : '全屏查看图谱'" @click="toggleGraphFullscreen"><span aria-hidden="true">{{ graphFullscreen ? "↙" : "⛶" }}</span>{{ graphFullscreen ? "退出全屏" : "全屏" }}</button>
            </div>
          </div>
          <div class="graph-layout"><GraphCanvas :nodes="graph?.nodes || []" :edges="graph?.edges || []" :search="graphSearch" /><aside class="community-panel"><div class="evidence-head"><span>图社区</span><small>GLOBAL INDEX</small></div><div class="community-list"><div v-if="!graph?.communities.length" class="evidence-empty">构建完成后，这里会显示用于全局检索的主题社区。</div><article v-for="(community, index) in graph?.communities" :key="community.id" class="community-card"><span>COMMUNITY {{ String(index + 1).padStart(2, "0") }} · {{ community.member_count }} ENTITIES</span><strong>{{ community.title }}</strong><p>{{ community.summary }}</p></article></div></aside></div>
        </section>

        <section v-show="activeTab === 'settings'" id="panel-settings" class="tab-panel" :class="{ active: activeTab === 'settings' }">
          <form v-if="settingsForm && settings" class="settings-form" @submit.prevent="saveSettings()">
            <div class="section-heading settings-heading">
              <div><p class="eyebrow">RUNTIME CONFIGURATION</p><h2>运行配置</h2></div>
              <div class="settings-actions"><button type="button" class="button button-ghost" :disabled="savingSettings" @click="resetSettingsForm">撤销未保存</button><button type="submit" class="button button-primary" :disabled="savingSettings">{{ savingSettings ? "保存中…" : "保存并应用" }}</button></div>
            </div>
            <!-- <div class="settings-security-note"><strong>LOCAL SECRET STORE</strong><span>保存时原子写入本机 <code>.env</code>；该文件已被 Git 忽略。当前密钥永不通过配置 API 返回。</span></div> -->

            <div class="settings-grid settings-grid-editable">
              <article class="setting-card setting-card-form" data-settings-section="embedding" :class="{ editing: editingSettingsSection === 'embedding' }" @pointerdown="beginSettingsEdit('embedding')">
                <div class="setting-card-head"><div><strong>Embedding</strong><small>向量索引与语义检索</small></div><i class="config-status" :class="{ off: !settings.embedding_configured }" /></div>
                <div class="config-fields">
                  <label class="config-field config-field-wide"><span>提供商地址</span><input v-model.trim="settingsForm.embedding.base_url" type="url" required placeholder="https://api.example.com/v1"></label>
                  <label class="config-field config-field-wide"><span>模型</span><input v-model.trim="settingsForm.embedding.model" required placeholder="BAAI/bge-m3"></label>
                  <label class="config-field config-field-wide"><span>API Key <small>不回显</small></span><div class="secret-input"><input v-model="settingsForm.embedding.api_key" :type="secretVisibility.embedding ? 'text' : 'password'" autocomplete="new-password" :placeholder="secretPlaceholder(settings.embedding_configured)"><button type="button" @click="secretVisibility.embedding = !secretVisibility.embedding">{{ secretVisibility.embedding ? "隐藏" : "显示" }}</button></div></label>
                  <label class="config-field"><span>批量大小</span><input v-model.number="settingsForm.embedding.batch_size" type="number" min="1" max="128" required></label>
                  <label class="config-field"><span>超时（秒）</span><input v-model.number="settingsForm.embedding.timeout" type="number" min="5" max="300" step="1" required></label>
                </div>
                <div class="setting-card-footer"><button type="button" class="button setting-save-button" :disabled="savingSettings" @click="saveSettings('embedding')">{{ savingSettingsSection === "embedding" ? "保存中…" : "保存" }}</button></div>
              </article>

              <article class="setting-card setting-card-form" data-settings-section="chat" :class="{ editing: editingSettingsSection === 'chat' }" @pointerdown="beginSettingsEdit('chat')">
                <div class="setting-card-head"><div><strong>Chat / Graph</strong><small>问答、实体关系与社区摘要</small></div><i class="config-status" :class="{ off: !settings.chat_configured }" /></div>
                <label class="toggle-field"><input v-model="settingsForm.chat.use_embedding_provider" type="checkbox"><span>复用 Embedding 的地址和密钥</span></label>
                <div class="config-fields">
                  <label class="config-field config-field-wide"><span>提供商地址</span><input v-model.trim="settingsForm.chat.base_url" type="url" :disabled="settingsForm.chat.use_embedding_provider" placeholder="https://api.example.com/v1"></label>
                  <label class="config-field config-field-wide"><span>模型</span><input v-model.trim="settingsForm.chat.model" required placeholder="Qwen/Qwen3-8B"></label>
                  <label class="config-field config-field-wide"><span>API Key <small>不回显</small></span><div class="secret-input"><input v-model="settingsForm.chat.api_key" :type="secretVisibility.chat ? 'text' : 'password'" autocomplete="new-password" :disabled="settingsForm.chat.use_embedding_provider" :placeholder="secretPlaceholder(settings.chat_configured, settingsForm.chat.use_embedding_provider)"><button type="button" :disabled="settingsForm.chat.use_embedding_provider" @click="secretVisibility.chat = !secretVisibility.chat">{{ secretVisibility.chat ? "隐藏" : "显示" }}</button></div></label>
                  <label class="config-field"><span>温度</span><input v-model.number="settingsForm.chat.temperature" type="number" min="0" max="2" step="0.1" required></label>
                  <label class="config-field"><span>最大输出 Tokens</span><input v-model.number="settingsForm.chat.max_tokens" type="number" min="128" max="32768" required></label>
                  <label class="config-field"><span>超时（秒）</span><input v-model.number="settingsForm.chat.timeout" type="number" min="10" max="600" required></label>
                </div>
                <div class="setting-card-footer"><button type="button" class="button setting-save-button" :disabled="savingSettings" @click="saveSettings('chat')">{{ savingSettingsSection === "chat" ? "保存中…" : "保存" }}</button></div>
              </article>

              <article class="setting-card setting-card-form" data-settings-section="ocr" :class="{ editing: editingSettingsSection === 'ocr' }" @pointerdown="beginSettingsEdit('ocr')">
                <div class="setting-card-head"><div><strong>OCR</strong><small>图片与扫描 PDF 识别</small></div><label class="enabled-toggle"><input v-model="settingsForm.ocr.enabled" type="checkbox"><span>{{ settingsForm.ocr.enabled ? "启用" : "停用" }}</span></label></div>
                <label class="toggle-field"><input v-model="settingsForm.ocr.use_embedding_provider" type="checkbox"><span>复用 Embedding 的地址和密钥</span></label>
                <div class="config-fields" :class="{ muted: !settingsForm.ocr.enabled }">
                  <label class="config-field config-field-wide"><span>提供商地址</span><input v-model.trim="settingsForm.ocr.base_url" type="url" :disabled="settingsForm.ocr.use_embedding_provider" placeholder="https://api.example.com/v1"></label>
                  <label class="config-field config-field-wide"><span>模型</span><input v-model.trim="settingsForm.ocr.model" required placeholder="PaddlePaddle/PaddleOCR-VL-1.5"></label>
                  <label class="config-field config-field-wide"><span>API Key <small>不回显</small></span><div class="secret-input"><input v-model="settingsForm.ocr.api_key" :type="secretVisibility.ocr ? 'text' : 'password'" autocomplete="new-password" :disabled="settingsForm.ocr.use_embedding_provider" :placeholder="secretPlaceholder(settings.ocr_configured, settingsForm.ocr.use_embedding_provider)"><button type="button" :disabled="settingsForm.ocr.use_embedding_provider" @click="secretVisibility.ocr = !secretVisibility.ocr">{{ secretVisibility.ocr ? "隐藏" : "显示" }}</button></div></label>
                  <label class="config-field"><span>并发页数</span><input v-model.number="settingsForm.ocr.concurrency" type="number" min="1" max="8" required></label>
                  <label class="config-field"><span>触发阈值（字符/页）</span><input v-model.number="settingsForm.ocr.min_text_chars" type="number" min="0" max="2000" required></label>
                  <label class="config-field"><span>最大页数</span><input v-model.number="settingsForm.ocr.max_pages" type="number" min="1" max="2000" required></label>
                  <label class="config-field"><span>渲染 DPI</span><input v-model.number="settingsForm.ocr.render_dpi" type="number" min="72" max="300" required></label>
                  <label class="config-field"><span>超时（秒）</span><input v-model.number="settingsForm.ocr.timeout" type="number" min="10" max="600" required></label>
                </div>
                <div class="setting-card-footer"><button type="button" class="button setting-save-button" :disabled="savingSettings" @click="saveSettings('ocr')">{{ savingSettingsSection === "ocr" ? "保存中…" : "保存" }}</button></div>
              </article>

              <article class="setting-card setting-card-form" data-settings-section="rerank" :class="{ editing: editingSettingsSection === 'rerank' }" @pointerdown="beginSettingsEdit('rerank')">
                <div class="setting-card-head"><div><strong>Reranker</strong><small>候选结果二阶段精排</small></div><label class="enabled-toggle"><input v-model="settingsForm.rerank.enabled" type="checkbox"><span>{{ settingsForm.rerank.enabled ? "启用" : "停用" }}</span></label></div>
                <label class="toggle-field"><input v-model="settingsForm.rerank.use_embedding_provider" type="checkbox"><span>复用 Embedding 的地址和密钥</span></label>
                <div class="config-fields" :class="{ muted: !settingsForm.rerank.enabled }">
                  <label class="config-field config-field-wide"><span>提供商地址</span><input v-model.trim="settingsForm.rerank.base_url" type="url" :disabled="settingsForm.rerank.use_embedding_provider" placeholder="https://api.example.com/v1"></label>
                  <label class="config-field config-field-wide"><span>模型</span><input v-model.trim="settingsForm.rerank.model" required placeholder="BAAI/bge-reranker-v2-m3"></label>
                  <label class="config-field config-field-wide"><span>API Key <small>不回显</small></span><div class="secret-input"><input v-model="settingsForm.rerank.api_key" :type="secretVisibility.rerank ? 'text' : 'password'" autocomplete="new-password" :disabled="settingsForm.rerank.use_embedding_provider" :placeholder="secretPlaceholder(settings.rerank_configured, settingsForm.rerank.use_embedding_provider)"><button type="button" :disabled="settingsForm.rerank.use_embedding_provider" @click="secretVisibility.rerank = !secretVisibility.rerank">{{ secretVisibility.rerank ? "隐藏" : "显示" }}</button></div></label>
                  <label class="config-field"><span>候选数</span><input v-model.number="settingsForm.rerank.candidates" type="number" min="2" max="100" required></label>
                  <label class="config-field"><span>超时（秒）</span><input v-model.number="settingsForm.rerank.timeout" type="number" min="5" max="300" required></label>
                </div>
                <div class="setting-card-footer"><button type="button" class="button setting-save-button" :disabled="savingSettings" @click="saveSettings('rerank')">{{ savingSettingsSection === "rerank" ? "保存中…" : "保存" }}</button></div>
              </article>

              <article class="setting-card setting-card-form setting-card-compact" data-settings-section="chunking" :class="{ editing: editingSettingsSection === 'chunking' }" @pointerdown="beginSettingsEdit('chunking')">
                <div class="setting-card-head"><div><strong>Chunking</strong><small>文档切分与召回规模</small></div><i class="config-status" /></div>
                <div class="config-fields">
                  <label class="config-field"><span>单块字符数</span><input v-model.number="settingsForm.chunking.chunk_size" type="number" min="200" max="8000" required></label>
                  <label class="config-field"><span>重叠字符数</span><input v-model.number="settingsForm.chunking.chunk_overlap" type="number" min="0" max="2000" required></label>
                  <label class="config-field"><span>默认 Top K</span><input v-model.number="settingsForm.chunking.default_top_k" type="number" min="1" max="30" required></label>
                </div>
                <div class="setting-card-footer"><button type="button" class="button setting-save-button" :disabled="savingSettings" @click="saveSettings('chunking')">{{ savingSettingsSection === "chunking" ? "保存中…" : "保存" }}</button></div>
              </article>

              <article class="setting-card setting-card-form setting-card-compact" data-settings-section="graph" :class="{ editing: editingSettingsSection === 'graph' }" @pointerdown="beginSettingsEdit('graph')">
                <div class="setting-card-head"><div><strong>GraphRAG</strong><small>实体关系抽取与社区构建</small></div><i class="config-status" /></div>
                <div class="config-fields">
                  <label class="config-field"><span>抽取并发 <small>下次构建生效</small></span><input v-model.number="settingsForm.graph.concurrency" type="number" min="1" max="10" required><small>建议从 3–5 开始；过高可能触发提供商限流</small></label>
                  <label class="config-field"><span>最大文本块</span><input v-model.number="settingsForm.graph.max_chunks" type="number" min="0" required><small>0 表示不限</small></label>
                  <label class="config-field"><span>单块超时（秒）</span><input v-model.number="settingsForm.graph.chunk_timeout" type="number" min="15" max="1800" required></label>
                  <label class="config-field"><span>总任务超时（秒）</span><input v-model.number="settingsForm.graph.build_timeout" type="number" min="60" max="86400" required></label>
                </div>
                <div class="setting-card-footer"><button type="button" class="button setting-save-button" :disabled="savingSettings" @click="saveSettings('graph')">{{ savingSettingsSection === "graph" ? "保存中…" : "保存" }}</button></div>
              </article>
            </div>
          </form>
          <div v-else class="list-empty">正在读取本地配置…</div>
        </section>
      </section>
    </main>
  </div>

  <dialog ref="createModal" class="modal" @cancel.prevent="closeCreateModal" @click.self="closeCreateModal"><form novalidate @submit.prevent="createKnowledgeBase"><div class="modal-top"><span class="eyebrow">NEW KNOWLEDGE BASE</span><button type="button" aria-label="关闭" @click="closeCreateModal">×</button></div><h2>创建知识库</h2><label>名称<input v-model="creatingName" maxlength="80" placeholder="例如：产品技术文档"></label><label>描述（可选）<textarea v-model="creatingDescription" maxlength="500" rows="3" placeholder="这个知识库将用于什么？" /></label><div class="modal-actions"><button type="button" class="button button-ghost" @click="closeCreateModal">取消</button><button class="button button-primary" type="submit" :disabled="!creatingName.trim()">创建知识库</button></div></form></dialog>
  <div ref="toastContainer" popover="manual" class="toast-container" aria-live="polite"><div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.type">{{ toast.message }}</div></div>
</template>
