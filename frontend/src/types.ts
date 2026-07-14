export type GraphStatus = "empty" | "stale" | "building" | "paused" | "ready" | "error";
export type RetrievalMode = "vector" | "graph_local" | "graph_global" | "hybrid";

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  graph_status: GraphStatus;
  graph_error?: string;
  graph_stage?: string;
  graph_progress_current?: number;
  graph_progress_total?: number;
  graph_failed_chunks?: number;
  graph_started_at?: string;
  graph_heartbeat_at?: string;
  document_count: number;
  chunk_count: number;
  entity_count: number;
  relationship_count: number;
  community_count?: number;
  created_at: string;
  updated_at: string;
}

export interface DocumentItem {
  id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  chunk_count: number;
  status: "processing" | "ready" | "error";
  extraction_method: "pending" | "native" | "ocr" | "hybrid";
  page_count: number;
  ocr_page_count: number;
  warning?: string;
  error?: string;
  created_at: string;
}

export interface PublicSettings {
  embedding_base_url: string;
  embedding_model: string;
  embedding_configured: boolean;
  embedding_batch_size: number;
  embedding_timeout: number;
  chat_base_url: string;
  chat_model: string;
  chat_configured: boolean;
  chat_uses_embedding_provider: boolean;
  chat_timeout: number;
  chat_temperature: number;
  chat_max_tokens: number;
  ocr_enabled: boolean;
  ocr_base_url: string;
  ocr_model: string;
  ocr_configured: boolean;
  ocr_uses_embedding_provider: boolean;
  ocr_timeout: number;
  ocr_concurrency: number;
  ocr_min_text_chars: number;
  ocr_max_pages: number;
  ocr_render_dpi: number;
  rerank_enabled: boolean;
  rerank_base_url: string;
  rerank_model: string;
  rerank_configured: boolean;
  rerank_uses_embedding_provider: boolean;
  rerank_candidates: number;
  rerank_timeout: number;
  chunk_size: number;
  chunk_overlap: number;
  default_top_k: number;
  max_upload_mb: number;
  graph_concurrency: number;
  graph_max_chunks: number;
  graph_chunk_timeout: number;
  graph_build_timeout: number;
}

export interface SettingsUpdate {
  embedding: {
    base_url: string;
    model: string;
    api_key: string;
    batch_size: number;
    timeout: number;
  };
  chat: {
    base_url: string;
    model: string;
    api_key: string;
    use_embedding_provider: boolean;
    timeout: number;
    temperature: number;
    max_tokens: number;
  };
  ocr: {
    enabled: boolean;
    base_url: string;
    model: string;
    api_key: string;
    use_embedding_provider: boolean;
    timeout: number;
    concurrency: number;
    min_text_chars: number;
    max_pages: number;
    render_dpi: number;
  };
  rerank: {
    enabled: boolean;
    base_url: string;
    model: string;
    api_key: string;
    use_embedding_provider: boolean;
    candidates: number;
    timeout: number;
  };
  chunking: {
    chunk_size: number;
    chunk_overlap: number;
    default_top_k: number;
  };
  graph: {
    concurrency: number;
    max_chunks: number;
    chunk_timeout: number;
    build_timeout: number;
  };
}

export interface SourceChunk {
  id: string;
  filename: string;
  content: string;
  page_number?: number;
  score: number;
  score_type?: string;
}

export interface GraphFact {
  id: string;
  source: string;
  target: string;
  predicate: string;
  description: string;
  weight: number;
}

export interface Community {
  id: string;
  title: string;
  summary: string;
  member_count: number;
  score?: number;
}

export interface GraphNode { id: string; name: string; type: string; description: string; mentions: number; }
export interface GraphEdge { id: string; source: string; target: string; label: string; description: string; weight: number; }
export interface GraphData { status: GraphStatus; error?: string; nodes: GraphNode[]; edges: GraphEdge[]; communities: Community[]; }

export interface ChatTurn { role: "user" | "assistant"; content: string; }
export interface EvidenceMeta {
  mode: RetrievalMode;
  sources: SourceChunk[];
  facts: GraphFact[];
  communities: Community[];
  rerank_used: boolean;
  warnings: string[];
}
