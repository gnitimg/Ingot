# Knowledge Forge

> 本地优先的 RAG + OCR + GraphRAG 知识库应用 —— 把散落的文档锻造成可追溯的知识网络。

Knowledge Forge 是一个可直接在本机运行的全栈知识库应用，提供多知识库管理、常见办公文档解析、扫描件 OCR、向量化检索、二阶段重排、知识图谱构建、图社区摘要、四种检索模式、流式问答和证据回溯，全部数据保存在本地 SQLite。管理端使用 Vue 3 + TypeScript + Vite 构建，生产构建由 FastAPI 在同一个 localhost 端口托管。

## 核心能力

| 能力 | 说明 |
|---|---|
| **多知识库管理** | 创建、删除多个独立知识库，每个知识库有独立的文档、向量索引和图谱 |
| **全格式文档解析** | PDF、DOCX、PPTX、XLSX、Markdown、TXT、HTML、JSON、CSV 以及常见图片格式 |
| **智能 OCR** | PDF 逐页检测文本密度，低密度页自动渲染后 OCR；图片自动纠正 EXIF 方向并规范化为 PNG |
| **向量检索 + Reranker** | 向量化 + 二阶段精排，Reranker 故障自动回退到向量排序 |
| **GraphRAG** | LLM 抽取实体关系 → 合并同名实体 → 社区发现 → 社区摘要 → 摘要向量化 |
| **四种检索模式** | 向量、图谱局部、图谱全局、混合 |
| **流式问答** | SSE 流式生成答案，展示使用的原文、关系和图社区证据 |
| **可视化图谱** | 浏览器中交互查看实体关系力导向图与社区摘要 |
| **一键配置** | 交互式初始化向导，自动写入 `.env`，支持已有配置热更新 |

## 快速开始

需要 Python 3.11 或更高版本（已在 Python 3.13 上验证）。

### 1. 创建虚拟环境并安装依赖

**Windows PowerShell：**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

**macOS / Linux：**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

如果本机配置的 PyPI 镜像缺少包，可以临时使用官方源：

```bash
python -m pip install --index-url https://pypi.org/simple -r requirements.txt
```

### 2. 构建 Vue 管理端

需要 Node.js 20.19+、22.12+ 或更新版本：

```bash
cd frontend
npm install
npm run build
cd ..
```

构建产物会直接写入 `app/static/`，FastAPI 在生产模式下托管这些文件。仓库也保留了一个无需 Node 即可启动的静态 fallback 页面（`app/static/index.html`），便于后端环境先行验证；正式使用建议完成 Vue 构建。

### 3. 运行交互式初始化

```bash
python init.py
```

向导会依次询问：

1. **Embedding 服务** — API 地址、API Key（隐藏输入）、模型名称
2. **Chat / 图谱模型** — 可复用 Embedding 的地址和 Key
3. **OCR** — 是否启用、模型、最大 OCR 页数
4. **Reranker** — 是否启用、模型
5. **文本切分** — 单块字符数、重叠字符数

配置自动写入 `.env`。API Key 使用 `getpass` 隐藏输入；已有有效配置时，直接回车保留原值。

### 4. 启动应用

```bash
python run.py
```

浏览器打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。如果 `.env` 不存在或仍是占位 Key，`run.py` 会先自动进入初始化向导。

也可以直接使用 Uvicorn：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 使用流程

```
创建知识库 → 拖入文档 → 等待索引完成 → （可选）构建 GraphRAG → 提问
```

1. **创建知识库** — 在侧栏点击"新建知识库"，填写名称和描述
2. **导入文档** — 拖放或点击上传区选择文件，支持批量上传
3. **等待索引** — 后端自动执行：文档解析 → OCR（如需要）→ 文本切分 → 向量化 → SQLite 存储
4. **构建 GraphRAG**（可选）— 在文档页点击"构建 GraphRAG"，后台调用 Chat 模型逐块抽取实体和关系
5. **开始问答** — 在问答页选择检索模式，输入问题，查看带证据的流式回答

管理端会为每份文档显示解析方式（原生文本 / OCR / 混合）、OCR 页数、解析警告和索引状态。

### 图谱状态流转

```
empty → stale（文档变更后）→ building → ready
                                 ↓
                              error（可重试）
```

新增或删除文档后，图谱标记为"待更新"；向量检索仍然可用，GraphRAG 需要重新构建。

## 检索模式详解

| 模式 | 工作方式 | 适合场景 | 是否需要 GraphRAG |
|---|---|---|---|
| **向量检索** | 查询向量化 → 余弦相似度召回 → Reranker 精排 | 精确事实、原文定位 | 否 |
| **图谱局部** | 向量召回种子文本 → 沿关联实体扩展一跳关系和证据 | 人物、产品、技术之间的联系 | 是 |
| **图谱全局** | 在图社区摘要向量中检索主题 | 跨文档总结、主要主题、整体趋势 | 是 |
| **混合检索** | 向量证据 + 局部图关系合并 | 默认模式，兼顾文本细节与关系上下文 | 部分（图谱未就绪自动降级为向量） |

> **提示：** GraphRAG 构建会产生额外的 Chat API 调用。大知识库首次建图前，可以设置 `GRAPH_MAX_CHUNKS` 控制成本；确认效果后再改为 `0` 构建完整图谱。

## 系统架构

### 数据流

```
办公文档 / 图片
       │
       ▼
┌─────────────────────┐
│   文档解析 (parsers) │  PDF逐页检测 → 高密度原生文本 / 低密度OCR渲染
│   + OCR (ocr.py)     │  图片 → EXIF纠正 → PNG规范化 → OCR模型
└─────────┬───────────┘
          │ TextSection[]
          ▼
┌─────────────────────┐
│ 文本切分 (chunker)   │  句子边界感知 + 滑动窗口重叠
└─────────┬───────────┘
          │ TextChunk[]
          ▼
┌─────────────────────┐
│ 向量化 (ai_client)   │  Embedding API → L2归一化
└─────────┬───────────┘
          │ float32[]
          ▼
┌─────────────────────┐
│ SQLite 存储          │  chunks表：文本 + 向量BLOB + 元数据
└─────────┬───────────┘
          │
    ┌─────┴─────┐
    ▼           ▼
 向量检索    GraphRAG 构建
    │           │
    │    ┌──────┴──────┐
    │    ▼             ▼
    │  LLM抽取      社区发现
    │  实体+关系     (标签传播)
    │    │             │
    │    ▼             ▼
    │  upsert        社区摘要
    │  entities      + 向量化
    │  relationships     │
    │    │             │
    │    ▼             │
    │  entity_chunks  │
    │    │             │
    ▼    ▼             ▼
┌──────────────────────────┐
│     检索服务 (retrieval)   │  向量/局部图/全局图/混合
└──────────┬───────────────┘
           │ 检索结果
           ▼
┌──────────────────────────┐
│     问答生成 (SSE)        │  系统提示 + 证据上下文 + 对话历史
└──────────────────────────┘
```

### 核心流程

**文档摄入（Ingestion）：**
1. 文件保存到 `data/uploads/<kb_id>/`，生成唯一存储名
2. 根据文件类型调用对应解析器，提取 `TextSection[]` 和 `OCRTarget[]`
3. `OCRService` 并发处理 OCR 目标页，失败时保留原生文本作为回退
4. `chunk_sections()` 按句子边界切分，保持相邻块重叠
5. `AIClient.embed_batched()` 批量调用 Embedding API，向量 L2 归一化
6. 向量和元数据写入 SQLite chunks 表

**GraphRAG 构建：**
1. 从 chunks 表读取所有已索引文本块（可选 `GRAPH_MAX_CHUNKS` 限制）
2. 每 20 块一批，并发调用 Chat 模型抽取实体和关系（JSON 模式，失败自动降级）
3. 同名实体自动合并（大小写不敏感归一化），关系取最大权重
4. 标签传播算法发现图社区（12 轮迭代）
5. 每个社区调用 Chat 模型生成主题摘要
6. 摘要向量化后存入 communities 表

### 流式问答

问答使用 Server-Sent Events (SSE) 协议，事件流结构：

```
event: meta      ← 检索元数据（来源、关系、社区、Reranker 状态、警告）
event: delta     ← 逐 token 生成的回答文本
event: done      ← 生成完成
event: error     ← 错误信息
```

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端框架 | FastAPI + Uvicorn |
| AI 客户端 | httpx (async) + 指数退避重试 |
| 数据存储 | SQLite (WAL 模式) + NumPy 余弦搜索 |
| 文档解析 | pypdf, PyMuPDF, python-docx, python-pptx, openpyxl, BeautifulSoup4 |
| 图片处理 | Pillow (EXIF 纠正 + PNG 规范化) |
| 配置管理 | pydantic-settings (`.env` 加载) |
| 前端框架 | Vue 3 (Composition API + `<script setup>`) |
| 前端构建 | Vite 7 + TypeScript 5.9 + vue-tsc |
| 图谱可视化 | Canvas 2D 力导向图 (自实现，零依赖) |
| 测试 | pytest + pytest-asyncio |

## 数据库 Schema

SQLite 数据库包含 7 张表，通过外键级联删除保证数据一致性：

```sql
knowledge_bases    ──┐
  id, name, description, graph_status, graph_error, created_at, updated_at
                     │
documents           ──┤── ON DELETE CASCADE
  id, kb_id, filename, stored_path, file_type, size_bytes, chunk_count,
  status, extraction_method, page_count, ocr_page_count, warning, error
                     │
chunks              ──┤── ON DELETE CASCADE
  id, document_id, kb_id, content, chunk_index, page_number,
  token_count, metadata_json, embedding (BLOB), embedding_dim
                     │
entities            ──┤── ON DELETE CASCADE
  id, kb_id, name, normalized_name, entity_type, description
  UNIQUE(kb_id, normalized_name)
                     │
entity_chunks       ──┤── 多对多关联
  entity_id, chunk_id, mention_count
                     │
relationships       ──┤── ON DELETE CASCADE
  id, kb_id, source_id, target_id, predicate, description,
  weight, evidence_chunk_id
  UNIQUE(kb_id, source_id, target_id, predicate)
                     │
communities         ──┘── ON DELETE CASCADE
  id, kb_id, title, summary, member_count,
  embedding (BLOB), embedding_dim
```

### 关键设计

- **向量存储**：embedding 以 `float32` BLOB 存储，检索时加载到 NumPy 矩阵做精确余弦搜索
- **实体合并**：`normalized_name` 使用大小写不敏感 + 空白规范化，通过 `ON CONFLICT` 实现 upsert
- **图谱状态机**：`empty → stale → building → ready / error`，服务重启时自动将中断的 `building` 标记为 `error`
- **迁移兼容**：`initialize()` 自动检测并添加缺失列，无需手动迁移

## 项目结构

```text
app/
├── main.py                 # FastAPI 路由、静态应用托管和流式 SSE
├── config.py               # pydantic-settings 配置（.env 加载）
├── database.py             # SQLite schema、迁移和数据访问
├── schemas.py              # Pydantic 请求模型
├── services/
│   ├── ai_client.py        # Embedding / Chat / OCR / Rerank API 客户端
│   ├── parsers.py          # 多格式文档解析（PDF 逐页检测、图片 OCR 准备）
│   ├── ocr.py              # 并发 OCR 服务，失败保留原生文本回退
│   ├── chunker.py          # 句子边界感知文本切分
│   ├── ingestion.py        # 上传、解析、切分、向量化完整管线
│   ├── graph_rag.py        # 实体关系抽取、社区发现、摘要构建
│   └── retrieval.py        # 四种检索模式 + Reranker + 证据组装
└── static/                 # Vite 生产构建输出 / 静态 fallback 页面

frontend/
├── src/
│   ├── App.vue             # Vue 3 主工作台（知识库/文档/问答/图谱/配置）
│   ├── components/
│   │   └── GraphCanvas.vue # Canvas 2D 力导向图谱可视化
│   ├── api.ts              # HTTP 客户端封装
│   ├── types.ts            # TypeScript 类型定义
│   └── main.ts             # Vue 应用入口
├── index.html              # HTML 模板
├── vite.config.ts          # 构建到 app/static，开发代理到 :8000
├── tsconfig.json           # TypeScript 配置
└── package.json            # 前端依赖

tests/
├── test_database.py        # SQLite 级联删除、图谱数据查询、列迁移
├── test_api.py             # FastAPI 生命周期、静态页面、CRUD
├── test_chunker.py         # 文本切分大小限制、元数据传递
├── test_graph_rag.py       # JSON 解析、实体归一化、社区发现
├── test_ocr.py             # 图片 OCR 预处理、元数据保留、未配置回退
├── test_ai_client.py       # Reranker 响应解析、无效索引过滤
└── test_init.py            # .env 解析、配置检查

init.py                     # 交互式配置向导
run.py                      # 首次配置检查与启动入口
requirements.txt            # Python 依赖
.env.example                # 配置模板（可提交到 Git）
```

### 数据目录

```text
data/
├── knowledge_forge.db      # SQLite 数据库
└── uploads/
    └── <knowledge-base-id>/
        └── <uuid>_<filename>   # 原始文件存储
```

`data/` 与 `.venv/` 一样已被 Git 忽略。

## 环境变量参考

### `.env` 与 `.env.example`

这两个文件的用途不同：

- `.env.example` 会提交到 Git。它只包含公开的默认值和 API Key 占位符，供其他使用者复制参考。
- `.env` 只用于当前机器，包含真实 API Key。它已经写入 `.gitignore`，不会被 Git 跟踪。

请不要把真实 Key 写进 `.env.example`、README、源码、截图或提交记录。建议提交前执行：

```bash
git status --short
git check-ignore .env
```

第二条命令应输出 `.env`。如果密钥曾经进入 Git 历史，仅删除文件并不够，还需要立即在 SiliconFlow 控制台撤销并重新生成 Key。

### 完整配置项

**Embedding 服务：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `EMBEDDING_BASE_URL` | `https://api.siliconflow.cn/v1` | OpenAI 兼容 API 根地址 |
| `EMBEDDING_API_KEY` | 无 | 必填，SiliconFlow API Key |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 向量模型 |
| `EMBEDDING_BATCH_SIZE` | `16` | 每次请求的文本数 |

**Chat / 图谱模型：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CHAT_BASE_URL` | 空 | 留空时复用 Embedding 地址 |
| `CHAT_API_KEY` | 空 | 留空时复用 Embedding Key |
| `CHAT_MODEL` | `Qwen/Qwen3-8B` | 问答、实体关系抽取和社区摘要模型 |

**OCR 配置：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OCR_ENABLED` | `true` | 是否自动 OCR 图片和扫描 PDF 页 |
| `OCR_BASE_URL` | 空 | 留空时复用 Embedding 地址 |
| `OCR_API_KEY` | 空 | 留空时复用 Embedding Key |
| `OCR_MODEL` | `PaddlePaddle/PaddleOCR-VL-1.5` | 视觉/OCR 模型 |
| `OCR_CONCURRENCY` | `2` | 单文档 OCR 并发页数 |
| `OCR_MIN_TEXT_CHARS` | `80` | PDF 页面低于该有效字符数时触发 OCR |
| `OCR_MAX_PAGES` | `100` | 单文档最大 OCR 页数，控制成本与时长 |
| `OCR_RENDER_DPI` | `144` | 扫描 PDF 页渲染清晰度 |

**Reranker 配置：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RERANK_ENABLED` | `true` | 是否启用二阶段精排 |
| `RERANK_BASE_URL` | 空 | 留空时复用 Embedding 地址 |
| `RERANK_API_KEY` | 空 | 留空时复用 Embedding Key |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Reranker 模型 |
| `RERANK_CANDIDATES` | `18` | 向量召回后送入精排的候选数 |

**检索与切分：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CHUNK_SIZE` | `900` | 单个文本块的目标字符数 |
| `CHUNK_OVERLAP` | `160` | 相邻文本块重叠字符数 |
| `DEFAULT_TOP_K` | `6` | 默认返回的文本证据数 |

**GraphRAG：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `GRAPH_CONCURRENCY` | `3` | 图谱抽取并发请求数 |
| `GRAPH_MAX_CHUNKS` | `0` | 参与建图的最大块数；`0` 表示不限 |

**应用与存储：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | 默认仅本机访问；不要在没有认证时改为公网监听 |
| `APP_PORT` | `8000` | 浏览器管理端与 API 的统一端口 |
| `DATA_DIR` | `./data` | SQLite 和上传文件的本地目录 |
| `MAX_UPLOAD_MB` | `50` | 单文件大小上限 |

修改 `.env` 后需要重启应用。

## API 参考

启动后可访问 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) 查看完整 OpenAPI 文档。

### 知识库管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/knowledge-bases` | 列出所有知识库（含文档数、块数、实体数、关系数） |
| `POST` | `/api/knowledge-bases` | 创建知识库（`{name, description}`） |
| `GET` | `/api/knowledge-bases/{id}` | 获取知识库详情 |
| `DELETE` | `/api/knowledge-bases/{id}` | 删除知识库及其全部文档、图谱和本地文件 |

### 文档管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/knowledge-bases/{id}/documents` | 列出文档（含解析方式、OCR 页数、状态） |
| `POST` | `/api/knowledge-bases/{id}/documents` | 批量上传文档（multipart/form-data） |
| `DELETE` | `/api/knowledge-bases/{id}/documents/{doc_id}` | 删除文档及其全部文本块 |

### 检索与问答

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/knowledge-bases/{id}/search` | 执行检索，返回来源、关系和社区证据（不生成答案） |
| `POST` | `/api/knowledge-bases/{id}/chat` | 检索并通过 SSE 流式回答（支持对话历史） |

### 图谱

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/knowledge-bases/{id}/graph/rebuild` | 后台重建 GraphRAG（返回 202） |
| `GET` | `/api/knowledge-bases/{id}/graph` | 获取实体、关系和社区（`?limit=500`） |

### 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 服务状态和模型配置 |
| `GET` | `/api/settings` | 运行配置（不包含 API Key） |

## 测试

```bash
# 后端测试
python -m pytest -q

# 前端类型检查和构建
cd frontend && npm run typecheck && npm run build
```

测试不调用真实模型 API，覆盖以下场景：

| 测试文件 | 覆盖内容 |
|---|---|
| `test_database.py` | SQLite 级联删除、图谱数据查询、列迁移兼容 |
| `test_api.py` | FastAPI 生命周期、静态页面托管、知识库 CRUD |
| `test_chunker.py` | 文本切分大小限制、元数据传递、overlap 校验 |
| `test_graph_rag.py` | Markdown 围栏 JSON 解析、实体归一化、社区发现算法 |
| `test_ocr.py` | 图片 OCR 预处理（EXIF + PNG）、元数据保留、未配置回退 |
| `test_ai_client.py` | Reranker 响应解析、无效索引过滤 |
| `test_init.py` | `.env` 解析、配置完整性检查 |

## 成本控制建议

OCR 和 GraphRAG 都会产生额外模型调用。以下参数可以控制吞吐与成本：

- **`OCR_MAX_PAGES`** — 限制单文档最大 OCR 页数（默认 100）
- **`OCR_CONCURRENCY`** — 控制 OCR 并发数（默认 2）
- **`GRAPH_MAX_CHUNKS`** — 限制参与建图的文本块数（默认 0 = 不限）
- **`GRAPH_CONCURRENCY`** — 控制图谱抽取并发数（默认 3）

建议首次建图时设置 `GRAPH_MAX_CHUNKS=100`，确认效果后再改为 `0` 构建完整图谱。

## 运行边界与扩展方向

**当前限制：**

- 向量检索在单进程内使用 NumPy 对 SQLite 中的归一化向量做精确余弦搜索，适合个人或团队级中小型知识库
- DOCX/PPTX 中嵌入的纯图片目前不会逐图 OCR，后续可以通过 `OCRTarget` 适配器扩展
- 删除知识库会同时删除对应 SQLite 记录和 `data/uploads` 下的原始文件，操作不可恢复

**扩展方向：**

- 数十万以上文本块建议把向量层替换为 pgvector、Qdrant 或 Milvus，并把图谱构建放入独立任务队列
- 公网部署需增加身份认证、HTTPS、上传内容安全扫描、反向代理限流和数据库备份
- 可通过替换 `AIClient` 中的 API 调用适配其他 OpenAI 兼容的模型服务

## 安全注意事项

- 本应用默认只监听 `127.0.0.1`，仅本机可访问
- API Key 只保存在本地 `.env`，前端和 API 都不会返回密钥
- 如果要对公网开放，请增加身份认证、HTTPS 和访问控制
- 建议提交前用 `git check-ignore .env` 确认密钥未被跟踪
