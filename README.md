# Ingot

![Version](https://img.shields.io/badge/Version-1.0-blue)![License](https://img.shields.io/badge/License-MIT-green)![Language](https://img.shields.io/badge/Language-中文-red)![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vue.js&logoColor=white)![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)

> 本地优先的 RAG + OCR + GraphRAG 知识库应用 —— 把散落的文档锻造成可追溯的知识网络。

Ingot 是一个可直接在本机运行的全栈知识库应用，提供多知识库管理、常见办公文档解析、扫描件 OCR、向量化检索、二阶段重排、知识图谱构建、图社区摘要、四种检索模式、流式问答和证据回溯，全部数据保存在本地 SQLite。管理端使用 Vue 3 + TypeScript + Vite 构建，生产构建由 FastAPI 在同一个 localhost 端口托管。

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

图片 OCR 不需要额外的本地 OCR 引擎。若要识别**扫描型 PDF**，还需要 PyMuPDF 将 PDF 页面渲染为图片；部分企业或云厂商镜像没有这个包，请使用官方 PyPI 安装可选依赖：

```bash
python -m pip install --index-url https://pypi.org/simple -r requirements-ocr-pdf.txt
```

未安装 PyMuPDF 时，Ingot 仍可处理文本型 PDF、图片和其他支持的文档；只有需要渲染的扫描 PDF 页面会给出明确警告。

### 2. 构建 Vue 管理端

需要 Node.js 20.19+、22.12+ 或更新版本：

```bash
cd frontend
npm install
npm run build
cd ..
```

构建产物会直接写入 `app/static/`，FastAPI 在生产模式下通过同一个 localhost 端口托管这些文件。仓库已提交生产构建，因此首次运行无需 Node；修改前端源码后才需要重新执行上述构建命令。

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

如果 `run.py` 提示端口已被占用，请在 `.env` 中把 `APP_PORT` 改为其他空闲端口（例如 `8001`），并使用对应地址访问。不要让其他服务与 Ingot 同时监听同一端口，否则浏览器可能间歇命中错误服务并显示 404 或“服务不可用”。

进入任一知识库的 **配置** 页，可以直接编辑 Embedding、Chat / Graph、OCR、Reranker 的提供商地址、模型、API Key 和超时/并发参数，也可以调整问答证据片数、Chunking、GraphRAG 与当前知识库的 Security。配置卡片均采用显式提交：点击卡片后进入编辑态，只有点击卡片内的“保存”才会提交最新值；未保存时点击卡片外部，会立即恢复该卡片最后一次已保存的值。顶部“保存并应用”仍可明确提交全部运行配置，“撤销未保存”则恢复全部卡片。配置卡片使用自适应瀑布流排列，卡片高度随内容收口。保存后会原子写入 `.env` 并热更新当前进程，无需重启。API Key 使用密码输入，已有值不会回显，输入框留空会保留原密钥；Chat、OCR 与 Reranker 也可选择复用 Embedding 的地址和 Key。Security 卡片只作用于当前知识库密码，不写入 `.env`。

GraphRAG 的“模型请求并发”也可直接在浏览器中设置（范围 1–1000，建议先用 3–5），同时作用于**实体关系抽取**和**图社区摘要**。保存后从下一次新建或继续构建开始生效；正在执行的这一轮仍使用启动时的并发数。上限 1000 是为高配私有服务或明确支持大并发的提供商预留，并不代表公共 API 都能承受该值。模型请求使用持久连接池和滚动工作池：任一请求完成后立即保存检查点并补充下一个任务，超时块进入队尾而不会阻塞同批健康块。遇到 429、5xx、网络中断或无效响应时，会结合提供商的 `Retry-After`、指数退避和随机抖动重试；明确的提供商限流会临时降低并发，连续成功后逐步恢复。Qwen 图谱抽取与社区摘要默认关闭思考模式，避免为结构化 JSON 任务消耗不必要的推理时间。

也可以直接使用 Uvicorn：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 使用流程

```
创建知识库 → 查看首页引导 → 拖入文档 → 等待索引完成 → （可选）构建 GraphRAG → 提问
```

1. **创建知识库** — 在侧栏点击"新建知识库"，填写名称、描述和访问密码；之后进入该知识库需要先输入密码
2. **查看知识库首页** — 每个知识库都有独立首页，集中显示名称、描述、图谱状态、文档/文本块/实体/关系/社区统计，以及文档、GraphRAG、问答和配置的常用引导
3. **导入文档** — 拖放或点击上传区选择文件，支持批量上传；上传前会对已有文件和待上传文件计算 SHA256，发现重复时会弹出选择保留/丢弃的确认框
4. **等待索引** — 后端自动执行：文档解析 → OCR（如需要）→ 文本切分 → 向量化 → SQLite 存储
5. **构建 GraphRAG**（可选）— 在文档页点击"构建 GraphRAG"，页面会实时显示实体关系抽取、社区摘要和社区向量三个阶段的进度；达到总任务超时后会暂停并保留检查点，可调整配置后继续；主动停止则清空部分结果并从零开始
6. **开始问答** — 在问答页选择检索模式，输入问题，查看带证据的流式回答；模型返回的 Markdown 会安全渲染为标题、列表、表格、引用和代码块。检索证据片数可在配置页的 Chat / Graph 卡片中调整

管理端会为每份文档显示解析方式（原生文本 / OCR / 混合）、OCR 页数、解析警告和索引状态。

侧栏左上角的图标、`INGOT` 与 `RAG STUDIO` 是一个完整的品牌首页按钮。无论当前位于哪个知识库或功能页，点击后都会回到全局欢迎页；欢迎页实时显示服务状态、知识库数和文档数。已有知识库时主操作会显示“创建下一个知识库”，也可继续进入最近使用的知识库或从侧栏选择其他知识库。

管理端会自动同步后台变化：图谱构建期间约每 2.5 秒刷新进度，空闲时约每 8 秒刷新知识库统计和文档状态；切回标签页或窗口重新获得焦点时会立即同步。页面隐藏时暂停轮询以避免无效请求，因此无需手动刷新浏览器。

知识库访问密码按知识库独立保存。创建知识库时必须设置密码；进入受保护知识库前，浏览器会弹出解锁框。解锁令牌只保存在当前浏览器内存中，刷新页面或重启服务后需要重新输入密码。已经从旧版本迁移而来、没有密码的知识库可以直接进入，并可在配置页的 Security 卡片里补设密码；修改密码需要输入旧密码和新密码。

上传重复文件时，Ingot 会按 SHA256 把“已有文件”和“待上传文件”放在同一个弹窗中展示。每个文件右侧都有独立勾选框，默认全部不勾选；未勾选的已有文件会被删除，未勾选的待上传文件会被跳过。“全部保留”会勾选所有重复项，“全部丢弃”会取消所有勾选，最后点击“确认”才执行处理。

知识图谱画布使用与 Neo4j 浏览体验相近的 D3 力导向布局：节点斥力、关系弹簧、碰撞避让与中心约束共同保持图形清晰，重点标签会自动避让；选中节点后只强调一跳邻域、关系方向与关系名称。隐藏页面不会提前在零尺寸画布中布局，因此切换到图谱页时不会再把节点压成一个点。画布支持完整的鼠标浏览操作：左键单击选择节点，按住左键拖动节点并固定到新位置，按住右键拖动平移视口，滚轮以鼠标位置为中心缩放。画布左上角的 `− / 百分比 / ＋` 也可缩放，点击百分比会自动适配全部节点。工具栏的“全屏”按钮会优先使用浏览器原生全屏；如果浏览器拒绝，则自动切换为页面全屏模式，均可通过 `Esc` 或“退出全屏”离开。

首页之外的四个功能页使用统一的标题基线和间距；标签切换带有短促的淡入位移动画，并会遵循操作系统的“减少动态效果”偏好。

### 图谱状态流转

```
empty → stale（文档变更后）→ building → ready
                                 ↓  ↑
                              paused（可继续）
                                 ↓
                         stop → stale（从零重建）

任意阶段的不可恢复错误 → error（从零重试）
```

每个文本块完成抽取后都会写入持久化检查点，并记录尝试次数和最近错误。总任务达到 `GRAPH_BUILD_TIMEOUT` 时进入 `paused`，已经完成的实体、关系和文本块检查点会保留；点击“继续构建”只处理尚未完成或上轮失败的文本块，社区摘要阶段则基于已完成实体图安全重放。服务重启也会把运行中的任务转换为可继续状态，不会永久停留在 `building`。

单块请求先进行有界的 HTTP 重试；仍失败时会被放到当前工作队列末尾，在健康块全部获得执行机会后进入最多 `GRAPH_RETRY_ROUNDS` 轮块级重试，每轮自动降低并发并等待 `GRAPH_RETRY_BACKOFF`。只有所有文本块均成功后才进入社区构建并标记为 `ready`；仍有失败块时会进入 `paused` 并保留错误与检查点，不会再产生“失败很多但显示构建完成”的假成功。

从旧版本升级时，历史的“超过总时限，已自动停止”错误以及没有检查点的中断任务会自动迁移为 `paused`。Ingot 会利用已经落库的实体来源恢复完成标记，保留现有实体关系；无法确认完成的文本块会在继续时安全重试，不会直接清空整张图谱。为兼容浏览器缓存中的旧前端，只要后端发现持久化检查点，即使旧页面仍请求“重新构建”接口，也会自动按检查点继续，绝不会清空后从零开始。

点击“停止构建”会清除部分图谱和检查点，下次从零构建。新增或删除文档会改变图谱输入，因此同样会安全取消当前任务、清空旧图谱与检查点，并标记为“待更新”；向量检索仍然可用。构建期间知识库接口会返回 `graph_stage`、`graph_progress_current`、`graph_progress_total`、`graph_failed_chunks` 和心跳时间。单个文本块持续失败仍会记录为失败块；只有所有文本块均失败等不可恢复情况才进入 `error`。

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
2. 按 `GRAPH_CONCURRENCY` 分批并发调用 Chat 模型抽取实体和关系；只有提供商明确不支持 `response_format` 时才降级为普通 JSON 提示，每批以事务方式写入图数据、尝试次数与文本块检查点
3. 同名实体自动合并（大小写不敏感归一化），关系取最大权重
4. 标签传播算法发现图社区（12 轮迭代）
5. 按同一 `GRAPH_CONCURRENCY` 并发调用 Chat 模型生成社区主题摘要
6. 摘要向量化后存入 communities 表；失败块自动降并发重试，仍失败或总任务超时则暂停并允许从检查点恢复

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
| 文档解析 | pypdf, python-docx, python-pptx, openpyxl, BeautifulSoup4；PyMuPDF（扫描 PDF 可选） |
| 图片处理 | Pillow (EXIF 纠正 + PNG 规范化) |
| 配置管理 | pydantic-settings (`.env` 加载) |
| 前端框架 | Vue 3 (Composition API + `<script setup>`) |
| 前端构建 | Vite 7 + TypeScript 5.9 + vue-tsc |
| 图谱可视化 | Canvas 2D + d3-force（碰撞避让、邻域高亮、方向箭头与自动适配） |
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
└── static/                 # Vite 生产构建输出

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
requirements-ocr-pdf.txt    # 扫描 PDF OCR 的可选渲染依赖
.env.example                # 配置模板（可提交到 Git）
```

### 数据目录

```text
data/
├── ingot.db                # SQLite 数据库
└── uploads/
    └── <knowledge-base-id>/
        └── <uuid>_<filename>   # 原始文件存储
```

`data/` 与 `.venv/` 一样已被 Git 忽略。

若旧版本数据目录中只有 `knowledge_forge.db`，Ingot 会在启动时自动迁移为 `ingot.db`；当两个文件同时存在时不会覆盖新数据库。

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
| `EMBEDDING_TIMEOUT` | `90` | 单次向量请求超时秒数 |

**Chat / 图谱模型：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CHAT_BASE_URL` | 空 | 留空时复用 Embedding 地址 |
| `CHAT_API_KEY` | 空 | 留空时复用 Embedding Key |
| `CHAT_MODEL` | `Qwen/Qwen3-8B` | 问答、实体关系抽取和社区摘要模型 |
| `CHAT_TIMEOUT` | `180` | Chat / Graph 请求超时秒数 |
| `CHAT_TEMPERATURE` | `0.2` | 生成温度 |
| `CHAT_MAX_TOKENS` | `2048` | 最大输出 Tokens |

**OCR 配置：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OCR_ENABLED` | `true` | 是否自动 OCR 图片和扫描 PDF 页 |
| `OCR_BASE_URL` | 空 | 留空时复用 Embedding 地址 |
| `OCR_API_KEY` | 空 | 留空时复用 Embedding Key |
| `OCR_MODEL` | `PaddlePaddle/PaddleOCR-VL-1.5` | 视觉/OCR 模型 |
| `OCR_TIMEOUT` | `240` | 单页 OCR 请求超时秒数 |
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
| `RERANK_TIMEOUT` | `60` | Reranker 请求超时秒数 |

**检索与切分：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CHUNK_SIZE` | `900` | 单个文本块的目标字符数 |
| `CHUNK_OVERLAP` | `160` | 相邻文本块重叠字符数 |
| `DEFAULT_TOP_K` | `6` | 默认返回的文本证据数 |
| `QA_EVIDENCE_COUNT` | `6` | 问答页展示并发送给模型的检索证据片数，可在浏览器配置页调整 |

**GraphRAG：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `GRAPH_CONCURRENCY` | `3` | 实体关系抽取与社区摘要的模型请求并发数（1–1000；建议从 3–5 起步） |
| `GRAPH_MAX_CHUNKS` | `0` | 参与建图的最大块数；`0` 表示不限 |
| `GRAPH_CHUNK_TIMEOUT` | `240` | 单个文本块抽取的总等待上限（秒，包含 JSON 降级重试） |
| `GRAPH_BUILD_TIMEOUT` | `3600` | 单轮 GraphRAG 构建的等待上限（秒）；到达后暂停，可继续下一轮 |
| `GRAPH_RETRY_ROUNDS` | `2` | HTTP 重试后仍失败时的块级重试轮数（0–5）；每轮自动降低并发 |
| `GRAPH_RETRY_BACKOFF` | `2` | 块级重试退避基数秒数（0.1–60），实际等待包含指数增长与随机抖动 |

**应用与存储：**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | 默认仅本机访问；不要在没有认证时改为公网监听 |
| `APP_PORT` | `8000` | 浏览器管理端与 API 的统一端口 |
| `DATA_DIR` | `./data` | SQLite 和上传文件的本地目录 |
| `MAX_UPLOAD_MB` | `50` | 单文件大小上限 |

通过浏览器配置页保存的项目会立即热更新；如果直接在文本编辑器中修改 `.env`，仍需重启应用。

## API 参考

启动后可访问 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) 查看完整 OpenAPI 文档。

有密码的知识库需要先调用解锁接口获得 `access_token`，之后访问该知识库详情、文档、检索、问答和图谱接口时在请求头加入 `X-Ingot-KB-Token: <access_token>`。令牌保存在服务进程内存中，重启后失效；无密码的旧知识库无需该请求头。

### 知识库管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/knowledge-bases` | 列出所有知识库（含文档数、块数、实体数、关系数） |
| `POST` | `/api/knowledge-bases` | 创建知识库（`{name, description, password}`），返回该库的临时访问令牌 |
| `POST` | `/api/knowledge-bases/{id}/unlock` | 使用知识库密码解锁，返回临时访问令牌 |
| `PUT` | `/api/knowledge-bases/{id}/password` | 修改当前知识库密码（`{old_password, new_password}`），需要当前访问令牌 |
| `GET` | `/api/knowledge-bases/{id}` | 获取知识库详情 |
| `DELETE` | `/api/knowledge-bases/{id}` | 删除知识库及其全部文档、图谱和本地文件 |

### 文档管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/knowledge-bases/{id}/documents` | 列出文档（含 SHA256、解析方式、OCR 页数、状态） |
| `POST` | `/api/knowledge-bases/{id}/documents` | 批量上传文档（multipart/form-data），服务端会保存 SHA256 |
| `DELETE` | `/api/knowledge-bases/{id}/documents/{doc_id}` | 删除文档及其全部文本块 |

### 检索与问答

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/knowledge-bases/{id}/search` | 执行检索，返回来源、关系和社区证据（不生成答案） |
| `POST` | `/api/knowledge-bases/{id}/chat` | 检索并通过 SSE 流式回答（支持对话历史） |

### 图谱

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/knowledge-bases/{id}/graph/rebuild` | 无检查点时从零构建；发现未完成检查点时自动续建（兼容旧前端，返回 202） |
| `POST` | `/api/knowledge-bases/{id}/graph/resume` | 从持久化检查点继续暂停的构建（返回 202） |
| `POST` | `/api/knowledge-bases/{id}/graph/cancel` | 停止构建并清空部分图谱与检查点（返回 202） |
| `GET` | `/api/knowledge-bases/{id}/graph` | 获取实体、关系和社区（`?limit=500`） |

### 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 服务状态和模型配置 |
| `GET` | `/api/settings` | 运行配置（不包含 API Key） |
| `PUT` | `/api/settings` | 从本机保存并热更新模型/检索配置；API Key 不回显 |

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
| `test_database.py` | SQLite 级联删除、图谱数据查询、列迁移兼容、知识库密码哈希与文档 SHA256 回填 |
| `test_database_graph_progress.py` | 图谱进度、心跳字段与中断任务暂停恢复迁移 |
| `test_api.py` | FastAPI 生命周期、知识库 CRUD、知识库解锁/改密、图谱启动/停止、配置热更新与密钥不回显 |
| `test_chunker.py` | 文本切分大小限制、元数据传递、overlap 校验 |
| `test_graph_rag.py` | Markdown 围栏 JSON 解析、实体归一化、社区发现、进度完成、单块超时与总超时检查点恢复 |
| `test_ocr.py` | 图片 OCR 预处理（EXIF + PNG）、元数据保留、未配置回退 |
| `test_ai_client.py` | Reranker 响应解析、无效索引过滤 |
| `test_init.py` | `.env` 原子更新、配置检查、旧数据库迁移与端口预检 |

## 成本控制建议

OCR 和 GraphRAG 都会产生额外模型调用。以下参数可以控制吞吐与成本：

- **`OCR_MAX_PAGES`** — 限制单文档最大 OCR 页数（默认 100）
- **`OCR_CONCURRENCY`** — 控制 OCR 并发数（默认 2）
- **`QA_EVIDENCE_COUNT`** — 控制问答阶段发送给模型和证据栏展示的片数（默认 6）
- **`GRAPH_MAX_CHUNKS`** — 限制参与建图的文本块数（默认 0 = 不限）
- **`GRAPH_CONCURRENCY`** — 控制实体关系抽取和社区摘要并发数（默认 3、可设 1–1000；过高可能触发限流、连接拥塞与重试）
- **`GRAPH_CHUNK_TIMEOUT`** — 防止单个模型请求或降级重试长期占住构建槽位（默认 240 秒）
- **`GRAPH_BUILD_TIMEOUT`** — 防止单轮后台任务无限运行；到达后暂停而不是丢弃进度（默认 3600 秒）
- **`GRAPH_RETRY_ROUNDS` / `GRAPH_RETRY_BACKOFF`** — 控制失败块的自动降并发重试次数和退避节奏（默认 2 轮 / 2 秒）

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
- API Key 只保存在本地 `.env`，前端和 API 都不会返回已有密钥；配置写接口也只接受回环地址请求
- 知识库访问密码使用 PBKDF2-SHA256 加盐哈希后写入 SQLite，不保存明文；解锁令牌只保存在服务进程内存和当前浏览器会话中，服务重启或页面刷新后需要重新解锁
- 知识库密码保护是本机产品级的误操作/隔离保护，不等同于公网多用户认证系统。若要开放到局域网或公网，仍需额外增加用户体系、HTTPS、反向代理鉴权和限流
- 浏览器中的密码框负责遮蔽与防回显；`.env` 仍是本机明文配置文件，并非操作系统密钥链。多人主机或公网部署请改接专用 Secret Manager
- 建议提交前用 `git check-ignore .env` 确认密钥未被跟踪
