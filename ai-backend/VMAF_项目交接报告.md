# VMAF 项目交接工作报告

> **交接对象**：下一位接手 VMAF（Visual Modular Agent Framework）的工程师 / 智能体
> **报告版本**：2026-10-25（本窗口封存时点）
> **代码位置**：`vmaf_system/`
> **必读配套**：`vmaf_system/project_status.md`（唯一进度真相源）、`vmaf_system/docs/NAMING_CONVENTIONS.md`（命名与陷阱）、各目录 `MODULE_INDEX.md`（文件级明细）
>
> 本报告的目标：让一个**完全没接触过本项目**的人，读完能独立启动、使用、排错、扩展，并能根据实际业务配置出一个可用的 Agent。
> 文中所有接口、字段、默认值均取自**运行中的服务导出的真实 Schema** 与源码，不是设计稿。

---

## 目录

1. [项目定位与现状](#1-项目定位与现状)
2. [技术栈与运行环境](#2-技术栈与运行环境)
3. [目录结构与文件职责](#3-目录结构与文件职责)
4. [总体架构（图）](#4-总体架构图)
5. [四条设计红线与关键决策](#5-四条设计红线与关键决策)
6. [核心数据契约](#6-核心数据契约)
7. [解析器：蓝图如何变成执行计划](#7-解析器蓝图如何变成执行计划)
8. [执行引擎：一次运行发生了什么](#8-执行引擎一次运行发生了什么)
9. [12 个节点逐个详解（输入/输出/行为）](#9-12-个节点逐个详解)
10. [HTTP API 全表](#10-http-api-全表)
11. [前端架构与模块详解](#11-前端架构与模块详解)
12. [使用流程：从启动到跑通](#12-使用流程从启动到跑通)
13. [实战：如何配置一个 Agent（8 个配方）](#13-实战如何配置一个-agent8-个配方)
14. [控制流专题：判断与循环](#14-控制流专题判断与循环)
15. [故障排查手册](#15-故障排查手册)
16. [测试与质量保证](#16-测试与质量保证)
17. [已知边界与后续路线](#17-已知边界与后续路线)
18. [交接检查清单](#18-交接检查清单)
19. [附录：术语与错误码速查](#19-附录术语与错误码速查)

---

## 1. 项目定位与现状

VMAF 是一个**单机、可视化、纯数据驱动的智能体工作流引擎**。用一句话描述：

> 在浏览器画布上把"节点"用线连起来，导出一份 `{nodes, edges}` 的 JSON 蓝图，后端按图调度、按节点执行，业务能力全部由插件提供。

它不是"聊天框套壳"，而是一个**可编排的 Agent 运行时**：对话、读文件、联网、正则、上下文压缩、工具解析、写文件、条件分支、循环、模型参数档案、本地检索，共 **12 种节点**，可任意组合成工作流。

### 1.1 当前状态（可直接使用）

| 维度 | 状态 |
|---|---|
| 后端测试 | **296 项全绿**（18 个测试文件 + conftest） |
| 前端测试 | **126 项全绿**（10 个测试文件：6 个纯逻辑 + 4 个组件） |
| 类型检查 | `npx tsc --noEmit` 无输出 |
| 生产构建 | `npx vite build` 成功 |
| 服务 | 后端 8000（12 节点、0 插件错误）、前端 5173（`/api` 代理 200） |
| 真实链路 | DeepSeek 真实接口已由用户验收；`/run` 端到端 HTTP 实测通过 |

### 1.2 已完成的里程碑

1. **最小对话闭环**：单节点 `llm_chat` → 真实模型回复（含 `reasoning_content` 思考链）。
2. **控制流**：条件分支（branch）与单层循环（loop），含区间静态识别与失败传播。
3. **厂商适配**：`extra_body` / `extra_headers` / `drop_params` 三逃生舱，兼容 OpenAI 协议中转站。
4. **前端体验**：画布、拖放、撤销重做、快捷键、参数记忆、自定义模块、说明面板、连线类型预判、可拖拽结果条、服务端蓝图。
5. **工程化**：插件热更新（真正重新执行）、统一 UTF-8 日志、蓝图持久化、可选鉴权、RAG 检索节点。
6. **测试体系**：从"只有间接覆盖"到"契约/解析/引擎/插件/API/utils/前端纯逻辑/前端组件"全栈覆盖。

---

## 2. 技术栈与运行环境

### 2.1 环境基准（实测）

| 组件 | 版本 | 用途 |
|---|---|---|
| Python | 3.14.6 | 后端运行时 |
| Pydantic | 2.13.5 | 所有 IO 契约（`BaseModel`） |
| pytest | 9.1.1 | 后端测试（`asyncio_mode=strict`） |
| FastAPI | >=0.115 | HTTP 接口层 |
| uvicorn | >=0.30 | ASGI 服务器 |
| Node.js | 24.19 | 前端构建 |
| React | 18 | UI 框架 |
| @xyflow/react | ^12.3.5 | 画布（节点/连线/缩放/拖拽） |
| Vite | 5 | 开发服务器与构建 |
| vitest | 2 | 前端测试（jsdom 环境） |
| @testing-library/react | ^16 | 组件级测试 |

### 2.2 为什么是这套技术

- **Pydantic V2 作为唯一契约**：节点输入/输出都是 `BaseModel`，通过 `model_json_schema()` 自动导出前端表单；运行时双重校验（输入一次、输出一次），插件是不可信脚本，必须过"安检"。
- **FastAPI**：请求校验由 Pydantic 自动完成，`/docs` 自带 Swagger，联调成本低。
- **React Flow（@xyflow/react）**：只负责"画图"这一件事；前端**不认识任何业务字段**，节点卡片与参数面板全部由 Schema 驱动。
- **零外部硬依赖**：核心引擎与插件只依赖 Pydantic + 标准库。`llm_chat` 用 `urllib` 而非 `httpx`，`rag_search` 自实现 BM25 而不引向量库——保证"单机轻量、可离线、确定性"。

### 2.3 依赖清单

```text
# requirements.txt（运行时核心）
pydantic>=2.0,<3.0
# 测试
pytest>=8.0 / pytest-asyncio>=0.24
# API
fastapi>=0.115 / uvicorn>=0.30 / httpx>=0.27（TestClient 依赖）

# frontend/package.json（dependencies）
@xyflow/react ^12.3.5 / react ^18.3.1 / react-dom ^18.3.1
# devDependencies
vite ^5.4.11 / vitest ^2.1.9 / typescript ^5.6.3
@testing-library/react ^16 / @testing-library/user-event ^14 / @testing-library/dom ^10 / jsdom ^30
```

---

## 3. 目录结构与文件职责

```text
vmaf_system/
├── project_status.md              # 唯一进度真相源（每个窗口开始必读）
├── README_START.md                # start.bat / stop.bat 说明与踩坑记录
├── requirements.txt               # 后端依赖
├── start.bat / stop.bat           # 一键启动/停止（纯 ASCII，见 §15.6）
├── docs/
│   ├── NAMING_CONVENTIONS.md      # 全局命名规范 + 陷阱清单（动工前必读）
│   └── assets/runbar-resize.png   # CDP 实测截图
├── backend/
│   ├── main.py                    # FastAPI 入口：CORS + 路由 + 启动扫描 + 日志 + 鉴权
│   ├── pytest.ini                 # 测试配置（asyncio strict，7 个 marker）
│   ├── data/blueprints/           # 蓝图持久化目录（一蓝图一 JSON）
│   ├── api/
│   │   ├── routes.py              # /health /schema /validate /run + /blueprints
│   │   ├── schemas.py             # API 请求/响应契约
│   │   ├── security.py            # 可选鉴权（VMAF_API_KEY → X-API-Key）
│   │   └── MODULE_INDEX.md
│   ├── core/
│   │   ├── blueprint.py           # 蓝图契约（节点/边/整体校验）
│   │   ├── parser.py              # 环检测 + 拓扑排序 + 类型预检 + 循环区间识别
│   │   ├── context.py             # Context Bus（UUID 键控 + 只读视图）
│   │   ├── engine.py              # 串行执行器 + 分支路由 + 循环展开（递归）
│   │   └── MODULE_INDEX.md
│   ├── plugins/
│   │   ├── base.py                # 契约层"底层法律"：BaseNode / Message / get_docs
│   │   ├── scanner.py             # 反射扫描器：按路径加载 + 热更新 + 故障隔离
│   │   ├── tools/                 # 12 个 node_type 的业务插件（无 __init__.py）
│   │   │   ├── llm_chat.py            # 对话（OpenAI 协议 + 厂商逃生舱）
│   │   │   ├── model_profile.py       # 模型参数档案
│   │   │   ├── read_file.py           # 读文件
│   │   │   ├── http_fetch.py          # 联网抓取
│   │   │   ├── regex_extract.py       # 正则抽取
│   │   │   ├── context_compress.py    # 上下文压缩
│   │   │   ├── tool_use.py            # 工具调用解析（不执行）
│   │   │   ├── edit_file.py           # 写文件（围栏 + dry_run）
│   │   │   ├── rag_search.py          # 本地检索（BM25）
│   │   │   ├── branch.py              # 控制流：条件分支
│   │   │   └── loop.py                # 控制流：loop_start + loop_end
│   │   └── MODULE_INDEX.md
│   ├── utils/
│   │   ├── loader.py              # 进程级加载权威 + 受控 reload_module
│   │   ├── errors.py              # 异常结构化 + 错误码映射
│   │   ├── expr.py                # 受限表达式求值（AST 白名单，禁 eval）
│   │   ├── logger.py              # 统一 UTF-8 日志
│   │   ├── blueprint_store.py     # 蓝图文件仓库（原子写入 + id 围栏）
│   │   └── MODULE_INDEX.md
│   └── tests/                     # 18 个测试文件 + conftest，296 项
└── frontend/
    ├── package.json / tsconfig.json / vite.config.ts   # /api 代理 + watch.ignored + vitest jsdom
    ├── index.html
    ├── MODULE_INDEX.md
    └── src/
        ├── main.tsx / App.tsx       # 主应用：画布 + 撤销重做 + 执行 + 结果条 + 服务端蓝图 + 密钥
        ├── types.ts                 # 前后端类型桥梁（Schema / 蓝图 / 运行结果 / 蓝图元信息）
        ├── api.ts                   # API 客户端（自动带 X-API-Key）
        ├── schemaForm.ts            # JSON Schema → 表单引擎（$ref 解引用 + 枚举回落 + 预检）
        ├── connection.ts            # 连线类型预判（本地复刻 _type_verdict）
        ├── templates.ts             # 模板引用式同步（指纹 + 待同步 + 参数同步）
        ├── storage.ts               # localStorage：参数记忆/历史、模板、API Key
        ├── palette.ts               # 节点强调色板
        ├── runbar.ts                # 执行结果条高度逻辑
        ├── context.ts               # NodeActionsContext
        ├── hooks/useHotkeys.ts      # 快捷键（输入框内不拦截）
        ├── hooks/usePageZoom.ts     # Ctrl/Cmd+滚轮 = 整页缩放
        ├── components/
        │   ├── NodeCard.tsx         # 节点卡片（端点 / 必填 / 说明面板 / 状态）
        │   ├── NodePalette.tsx      # 左侧模块面板 + 我的模块（含待同步按钮）
        │   ├── ParamPanel.tsx       # 右侧参数面板 + 历史 + 外观
        │   ├── fields.tsx           # 通用小组件
        │   └── ServerBlueprints.tsx # 服务端蓝图对话框
        ├── testSetup.ts             # vitest 全局清理
        └── *.test.ts / *.test.tsx   # 126 项
```

**一条重要约定**：`backend/plugins/tools/` 刻意**没有 `__init__.py`**。插件是"独立脚本仓库"——往目录里丢一个 `.py` 文件就自动被发现并注册，不需要维护包结构。文件名即 `node_type`（`rag_search.py` → `rag_search`），以 `_` 开头的文件被扫描器跳过（可作模板/私有模块）。

---

## 4. 总体架构（图）

### 4.1 分层架构

```text
┌────────────────────────────────────────────────────────────────────────────┐
│                           浏览器（React 18 + React Flow）                    │
│                                                                            │
│  NodePalette ──点击/拖拽──▶ App.tsx（画布状态：nodes / edges / 撤销栈）        │
│                                  │                                         │
│  ParamPanel ◀──选中节点──────────┤  schemaForm（Schema → 表单）              │
│  NodeCard ◀──每个节点────────────┤  connection（连线类型预判）               │
│  ServerBlueprints ◀──按钮────────┘  templates（模板指纹/同步）               │
│                                  │                                         │
│                                  ▼  HTTP（/api 前缀，Vite 代理）              │
└──────────────────────────────────┼─────────────────────────────────────────┘
                                   │  JSON
┌──────────────────────────────────▼─────────────────────────────────────────┐
│                        FastAPI（backend/main.py + api/）                    │
│  security 中间件（可选 X-API-Key）  →  routes：/health /schema /validate     │
│                                          /run /blueprints                  │
│                                   │                                        │
│                    ┌──────────────┴───────────────┐                        │
│                    ▼                              ▼                        │
│            BlueprintParser                   GraphEngine                   │
│    环检测/拓扑排序/类型预检/循环区间        三级校验/分支/循环展开/失败传播    │
│                    │                              │                        │
│                    │        ExecutionPlan         │                        │
│                    └──────────────┬───────────────┘                        │
│                                   ▼                                        │
│                        ExecutionContext（Context Bus）                      │
│                      UUID 键控的节点输出 + shared 命名空间                   │
│                                   │                                        │
│                                   ▼  实例化 / execute(inputs, context)      │
│                     ┌──────────────────────────────────┐                   │
│                     │  PluginScanner（注册表 + 热更新）  │                   │
│                     │  tools/*.py（12 个 node_type）    │                   │
│                     └──────────────────────────────────┘                   │
│                                                                            │
│  utils：loader（加载权威） / errors（错误码） / expr（安全表达式） /           │
│         logger（UTF-8 日志） / blueprint_store（蓝图落盘）                   │
└────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 一次 `/run` 的时序

```text
浏览器                      API                    Parser                Engine                Plugins
  │  POST /run {blueprint}    │                       │                     │                     │
  ├──────────────────────────▶│                       │                     │                     │
  │                           ├─ Blueprint(**dict) 结构校验（Pydantic）      │                     │
  │                           ├──────────────────────▶│                     │                     │
  │                           │                       ├─ 节点/边解析         │                     │
  │                           │                       ├─ 类型预检（逐字段）   │                     │
  │                           │                       ├─ 环检测（DFS）       │                     │
  │                           │                       ├─ 拓扑排序（Kahn）    │                     │
  │                           │                       ├─ 循环区间识别（栈）   │                     │
  │                           │◀──────────────────────┤ ExecutionPlan        │                     │
  │                           ├─────────────────────────────────────────────▶│                     │
  │                           │                       │                     ├─ for node in order  │
  │                           │                       │                     ├─ 合并参数+上游值      │
  │                           │                       │                     ├─ input_model 校验    │
  │                           │                       │                     ├────────────────────▶│
  │                           │                       │                     │                     ├ execute()
  │                           │                       │                     │◀────────────────────┤
  │                           │                       │                     ├─ output_model 校验   │
  │                           │                       │                     ├─ 写入 Context Bus    │
  │                           │                       │                     ├─ 分支：失活边        │
  │                           │                       │                     ├─ 循环：递归展开      │
  │                           │◀─────────────────────────────────────────────┤ RunReport            │
  │◀──────────────────────────┤ 200 {ok, results, errors, error_details}     │                     │
  │  渲染结果条 / 节点状态      │                                              │                     │
```

**关键点**：`/run` **永远返回 200**（除非蓝图 JSON 非法 → 422）。节点失败是"业务结果"，不是"服务器错误"，错误信息进 `error_details` 供前端高亮节点/连线。

---

## 5. 四条设计红线与关键决策

### 5.1 四条红线（违背即返工）

1. **纯数据驱动**：前端只产出 `{nodes, edges}`；后端只读 JSON 调度；**业务全在插件层**。前端不写死任何业务字段。
2. **契约驱动（Pydantic V2）**：所有模块 IO 必须是 `BaseModel`；Schema 导出给前端；运行时"双保险"校验（输入 + 输出）。
3. **无全局变量纯函数**：插件 = `输入实例 → 输出实例`，无副作用、无跨调用状态；状态只在 Context Bus 中流转。
4. **单机确定性**：单线程异步串行；**手写 Kahn 拓扑排序**（不引 NetworkX），同层候选按 node id 排序打破并列，同一蓝图多次解析顺序完全一致。

### 5.2 历史决策一览（含"为什么"，不可回退）

| # | 决策 | 原因 |
|---|---|---|
| 1 | `Message` 上提契约层 | Pydantic 按**类身份**校验，两个结构相同的独立类不能互连 |
| 2 | 一切动态加载走 `loader.load_module_once` | 直接 `spec_from_file_location` 会产生第二份 `base.py` → 跨节点连线全失败 |
| 3 | 类型预检严格判定，不设"判不出就放行" | 早期末尾有 `return True` 兜底，导致容器→标量被放行，预检形同虚设 |
| 4 | 共享类型导出 `$ref`，前端 `deref` 解引用 | 否则 `messages` 退化成裸 JSON 框，共享类型在 UI 上白费 |
| 5 | 错误码统一 `<子系统>.<问题>`，`ISSUE_CODE_MAP` 唯一权威 | 避免 `/validate` 与 `/run` 两套编码，前端不用双重映射 |
| 6 | 节点失败 HTTP 200 | 前端据此高亮节点，而非弹无意义报错 |
| 7 | 控制流 = 节点元数据 + 引擎调度 | 插件保持纯函数，循环/分支只有一处实现（引擎） |
| 8 | 表达式只用 `utils/expr.py` 的 AST 白名单 | 蓝图是不可信输入，`eval` = 任意代码执行漏洞 |
| 9 | 循环 = 标记配对 + 静态区间 + 引擎展开 | 用户不用手写迭代器，插件不理解循环 |
| 10 | 模块说明书 `get_docs()` 从 Field description 推导 | 写文档的成本降到 0，测试强制每个字段必须有说明 |
| 11 | 模板 = 快照 + 指纹同步（只同步参数） | 结构被用户改过后对应关系不可靠，强行覆盖会破坏用户意图 |
| 12 | `.bat` 必须纯 ASCII | 中文 + cmd 编码互斥，UTF-8 中文批处理会被截断执行 |
| 13 | 展示元数据（position/accent/template*）放节点上，绝不进 params | params 要过 `extra="forbid"` 的输入模型，混进去直接 422 |
| 14 | 滚轮分层：画布内缩放 / `.node-docs` 内滚动（`nowheel`）/ Ctrl+滚轮整页缩放 | React Flow 统一 `preventDefault`，必须显式放行 |
| 15 | 说明开关固定在卡片标题**左侧** | 卡片展开向右变宽，放右侧会把按钮推出可视区 |
| 16 | 厂商差异 = 稳定核心 + 显式逃生舱 | 不为每家厂商写字段；`extra_body` 顶层合并、`drop_params` 事后删除 |
| 17 | 后端 `Literal`/`enum` 尽量给默认值；前端枚举回落第一个合法值 | 无默认值时前端会产出 `''`，骗过"必填非空"却在后端失败 |
| 18 | 组件内持久化逻辑抽纯函数模块 | React 组件没有组件级测试，抽出来才能被 vitest 覆盖 |
| 19 | 拖拽/指针交互用 Pointer Capture + ref 记值 | `pointermove` 里 `setState` 是异步的，`pointerup` 读 state 会拿到旧值 |
| 20 | 插件热更新 = 受控 `reload_module`（仅扫描器可调） | 旧"热更新"只更新指纹不重执行文件，改插件必须重启；同时必须禁止重载契约层 |
| 21 | 蓝图持久化 = 一蓝图一 JSON + 原子写入 + id 围栏 | 不引数据库；`os.replace` 防半截文件；id 正则杜绝路径穿越 |
| 22 | 鉴权默认关闭，启用后只保护数据接口 | 不破坏单机零配置用法；`/health` 保持匿名便于探活 |
| 23 | RAG 用 BM25 词法检索，不引向量库 | 单机确定性、零外部依赖；语义检索留给未来的独立节点 |

---

## 6. 核心数据契约

### 6.1 蓝图（`backend/core/blueprint.py`）

```python
class BlueprintNode(BaseModel):      # extra="forbid"
    id: str                            # 本蓝图内唯一
    type: str                          # 已注册的 node_type
    params: Dict[str, Any]             # 静态参数（要过插件的 input_model）
    label: Optional[str]               # 画布显示名
    position: Optional[Dict[str, float]]  # 仅前端
    accent: Optional[str]              # #rrggbb，仅前端

class BlueprintEdge(BaseModel):      # extra="forbid"
    id: str
    source: str                        # 源节点 id
    source_handle: str                 # 源节点【输出字段名】
    target: str                        # 目标节点 id
    target_handle: str                 # 目标节点【输入字段名】
    label: Optional[str]

class Blueprint(BaseModel):          # extra="forbid"
    version: str = "0.1.0"
    name: str = "未命名蓝图"
    description: str = ""
    nodes: List[BlueprintNode]
    edges: List[BlueprintEdge]
    # 结构校验：节点/边 id 唯一、边端点必须存在、禁止自环
```

一份最小蓝图：

```json
{
  "name": "最小对话",
  "nodes": [
    {"id": "n1", "type": "llm_chat",
     "params": {"messages": [{"role": "user", "content": "你好"}]}}
  ],
  "edges": []
}
```

### 6.2 节点契约（`backend/plugins/base.py`）

```python
class BaseNode(ABC):
    node_type: ClassVar[str]            # 全局唯一注册键
    description: ClassVar[str]
    version: ClassVar[str]              # 默认 CONTRACT_VERSION="0.1.0"
    input_model: ClassVar[Type[BaseModel]]
    output_model: ClassVar[Type[BaseModel]]
    control_flow: ClassVar[Optional[str]]  # None | "branch" | "loop_start" | "loop_end"

    @abstractmethod
    async def execute(self, inputs, context: Optional[Dict] = None) -> BaseModel: ...

    @classmethod
    def get_schema(cls) -> dict    # node_type/description/version/control_flow/input/output
    @classmethod
    def get_docs(cls)   -> dict    # usage/inputs/outputs（面向人的说明书）
    @classmethod
    def validate_contract(cls) -> None  # 注册期自检：node_type 非空、IO 是 BaseModel
```

**写一个新插件 = 一个文件 + 两个 Pydantic 模型 + 一个类**，不需要动引擎、路由、前端（前端自动出现新卡片）。

```python
# backend/plugins/tools/echo.py —— 最小插件示例
from pydantic import BaseModel, Field
from ..base import BaseNode

class EchoInput(BaseModel):
    text: str = Field(..., description="要回显的文本")

class EchoOutput(BaseModel):
    text: str = Field(..., description="回显结果")

class EchoNode(BaseNode):
    node_type = "echo"
    description = "回显输入文本"
    input_model, output_model = EchoInput, EchoOutput

    async def execute(self, inputs, context=None):
        return EchoOutput(text=inputs.text)
```

放进 `tools/` 后：下次任何 `/schema` 或 `/health` 请求会自动重扫并注册（详见 §8.6 热更新）。

### 6.3 共享消息类型 `Message`

```python
class Message(BaseModel):     # extra="forbid"
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: Optional[str] = None
```

`llm_chat` 的 `messages: List[Message]` 与 `context_compress` 的输入/输出使用**同一个类对象**，因此可以直接连线。**新增跨节点传递的类型，必须放进 `base.py`**（判定口诀：同时出现在 A 的 output 和 B 的 input，就属于契约层）。

### 6.4 错误契约（`backend/utils/errors.py`）

```python
class ErrorDetail(BaseModel):   # 唯一对外表示，可直接 JSON 序列化
    code: str; category: str; message: str; retryable: bool
    node_id/node_type/edge_id/field/detail: Optional[str]

class ErrorCategory(str, Enum):
    VALIDATION / NOT_FOUND / PERMISSION / NETWORK / TIMEOUT / CONTRACT / INTERNAL
    # 只有 NETWORK 与 TIMEOUT 的 .retryable == True
```

`ISSUE_CODE_MAP` 把解析器短码（`TYPE_MISMATCH`）翻译为对外长码（`blueprint.type_mismatch`），是**唯一权威**，`engine.py` 与 `routes.py` 都从它取。

### 6.5 节点总览（12 个）

| node_type | 版本 | 控制流 | 一行职责 |
|---|---|---|---|
| `llm_chat` | 0.2.0 | — | 调 OpenAI 协议模型，返回回复与原始响应 |
| `model_profile` | 0.1.0 | — | 沉淀模型参数，连到 chat 复用 |
| `read_file` | 0.1.0 | — | 读本地文本（行切片 + 体积上限） |
| `http_fetch` | 0.1.0 | — | 抓 http(s)（协议白名单 + HTML 清洗） |
| `regex_extract` | 0.1.0 | — | 正则抽取（捕获组/命名组/修饰符） |
| `context_compress` | 0.1.0 | — | 折叠历史为摘要 + 最近 N 轮 |
| `tool_use` | 0.1.0 | — | 解析模型输出中的工具调用（**不执行**） |
| `edit_file` | 0.1.0 | — | 写文件（路径围栏 + overwrite 开关 + dry_run） |
| `rag_search` | 0.1.0 | — | 本地 BM25 检索，产出 context |
| `branch` | 0.1.0 | branch | 条件分支，只激活一条路径 |
| `loop_start` | 0.1.0 | loop_start | 循环起点（iterations 或 items） |
| `loop_end` | 0.1.0 | loop_end | 循环终点，聚合每轮结果 |

---

## 7. 解析器：蓝图如何变成执行计划

`BlueprintParser.parse(blueprint) -> ExecutionPlan`，执行顺序如下。**任何一步出现 error，引擎一个节点都不跑**（直接 `aborted`）。

### 7.1 步骤

1. **节点解析**：`type` 必须在注册表里，否则 `UNKNOWN_NODE_TYPE`。
2. **边解析**：`source_handle` 必须是源节点 output 的字段（否则 `UNKNOWN_OUTPUT_FIELD`）；`target_handle` 必须是目标 input 的字段（否则 `UNKNOWN_INPUT_FIELD`）；然后做**类型预检**。
3. **环检测**：DFS 找环，有环即 `CYCLE`（DAG 是硬前提）。
4. **拓扑排序**：Kahn 算法；入度为 0 的候选按 id 排序，保证确定性。
5. **循环区间识别**：栈式配对（见 §14.3）。
6. **折叠循环**：把循环体与 `loop_end` 从主序列摘出，只留 `loop_start`。
7. **必填检查**：必填输入既无入边、又不在 params 中 → `MISSING_REQUIRED_INPUT`。

产物 `ExecutionPlan` 字段：`.order`（主序列）、`.resolved_nodes`、`.issues`、`.entry_nodes`、`.exit_nodes`、`.loop_regions`、`.valid`。

### 7.2 类型预检规则（`_type_verdict`）

| 情形 | 结论 |
|---|---|
| 源或目标注解是 `Any` / 缺失 | 兼容 |
| 两端注解完全相同 | 兼容 |
| 同为容器族（`List`/`Dict`） | 递归比较内部参数 |
| 不同容器族（`Dict` → `List`） | 不兼容 |
| 容器 ↔ 标量（`List[X]` → `str`） | 不兼容 |
| 源是 Union（含 `Optional`） | 逐成员判定；含 `None` 降级为 `nullable`（warning，允许执行） |
| 目标是 Union | 任一成员兼容即可 |
| 标量相同，或 `int → float` | 兼容 |
| 其他标量（`float → int`、`str → int`） | 不兼容 |

> **实测语义边界**：`Literal["a","b"] → str` 在**后端**判为不兼容（`Literal` 被视为非标量），但**前端** `connection.ts` 按基础类型处理（运行时值确实是 str）。前端只做提示，后端仍是唯一权威。

`nullable` 是 warning 级：允许执行，但源值可能为 `None`，接到必填目标时运行期才会失败——前端会标黄。

---

## 8. 执行引擎：一次运行发生了什么

### 8.1 节点执行五步（`_execute_node`）

```python
merged = dict(node.params or {})          # 1) 静态参数为底
merged.update(collect_upstream(...))      #    连线值覆盖（! 上游优先）
inputs  = node_class.input_model(**merged)          # 2) 输入校验
output  = await node_class().execute(inputs, ctx)   # 3) 执行
validated = output_model.model_validate(output)     # 4) 输出校验（双保险）
context.store(node_id, node_type, validated, ...)   # 5) 写入总线
```

**"连线值优先于静态参数"是本项目最重要的使用直觉**：节点上填的值只是默认，接上来的线永远赢。

### 8.2 失败传播

- 默认（`strict=False`）：失败节点的**全部下游递归标记 skipped**，无关分支继续执行；`report.ok=False`，错误进 `errors` 与 `error_details`。
- 严格（`strict=True`）：首个失败立即中止。
- 被跳过的节点若又出现在主循环里，会先查总线避免重复登记。

### 8.3 分支路由

`branch` 执行后：

```python
if control_flow == "branch":
    route = output.route            # "on_true" | "on_false"
    for edge in node.outgoing:
        if edge.source_handle != route:
            inactive.add(edge.id)   # 不匹配的出口全部失活
```

下游节点**所有入边都失活**才跳过（两条分支汇聚到同一节点时，有一条活跃就执行）。失活判定在循环每轮会重置。

### 8.4 循环展开（支持任意层嵌套）

```python
async def _run_loop(region, plan, context, inactive):
    return await self._run_region(region, plan, context, inactive, [])

async def _run_region(region, plan, context, inactive, stack):
    # 读 loop_start 的输出/输入 → planned 次数、items
    for index in range(planned):
        inactive.difference_update(body_edges)   # 每轮重置循环体内分支
        stack.append({start, index, item})
        _publish_loop_frame(context, stack)      # 发布 loop.*
        for node_id in region.body_order:        # 直接循环体
            ... 执行或递归（遇到嵌套 loop_start 就递归 _run_region）
        stack.pop(); _publish_loop_frame(...)
    set loop.results / loop.stopped_early
    执行 loop_end（聚合）
    恢复外层 loop.results（嵌套不污染）
```

循环内可用的共享变量（`context["shared"]`）：

| 键 | 含义 |
|---|---|
| `loop.index` | **最内层**当前迭代下标（从 0 起） |
| `loop.item` | 最内层当前元素（`items` 提供时有效） |
| `loop.indexes` | 由外到内的下标列表，如 `[1, 2]` |
| `loop.depth` | 当前嵌套深度（最外层为 1） |
| `loop.results` | 当前区间已完成的聚合结果 |
| `loop.stopped_early` | 是否因失败提前结束 |

### 8.5 Context Bus（`core/context.py`）

- `store(node_id, ...)`：以 UUID 为键保存输出，写入前已校验。
- `get_output(node_id)`：只返回成功结果。
- `set_shared(key, value)` / `get_shared(key)`：跨节点共享（循环变量走这里）。
- `readonly_view()`：给插件的**快照**（`run_id` + `node_outputs` + `shared`），插件改不动总线。
- `snapshot()` / `successful` / `failed`：调试与统计。

### 8.6 插件注册与热更新（`plugins/scanner.py` + `utils/loader.py`）

- `scan()`：遍历 `tools/*.py`（跳过 `_` 开头）→ 计算文件指纹 `(mtime_ns, size)` → 未变则命中缓存；变了则**受控重载**；文件被删则注销其节点。
- 首次加载某文件走 `load_module_once`；**已执行过且指纹变化**时走 `reload_module`（先 `sys.modules.pop`，再清 `__pycache__/*.pyc`，再重新执行）。
  - 为什么清 `.pyc`：`.pyc` 头部的源 mtime 只有**秒级精度**，同一秒内"改文件→重载"会命中旧字节码（本项目真实踩过）。
- `_NON_RELOADABLE_MODULES` 护栏：`vmaf.plugins.base` / `scanner` / `core.*` / `utils.*` / `main` **禁止重载**（否则产生第二份 `BaseNode`/`Message` 类，跨节点连线全部失败），重载它们直接 `ValueError`。
- 触发时机：`main.py` 启动扫描一次；`GET /health`、`GET /schema`、`POST /run` 等每次请求都会 `scan()`（命中缓存时开销极小）。**改插件后无需重启后端**，刷新页面/重新运行即可。

---

## 9. 12 个节点逐个详解

> 以下字段均取自真实 Schema（`GET /schema`）。标注"（必填）"表示没有默认值，必须由参数或连线提供。

### 9.1 `llm_chat` — 对话（核心）

**输入**

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `messages` | `List[Message]` | 必填 | 至少 1 条，通常由上游连线注入 |
| `model` | `str` | `gpt-4o-mini` | 模型标识 |
| `system_prompt` | `Optional[str]` | `None` | 填写后自动插入为第一条 system 消息 |
| `temperature` | `Optional[float]` | `None` | 0~2；留空**不下发** |
| `max_tokens` | `Optional[int]` | `None` | 留空**不下发**（中转站锁死参数时很关键） |
| `api_base` | `str` | `https://api.openai.com/v1` | 可填根/带 `/v1`/完整 `/chat/completions`，自动补全 |
| `api_key` | `Optional[str]` | `None` | 留空且无鉴权头 → **dry_run 演练模式** |
| `timeout_seconds` | `float` | `60.0` | 超时秒数 |
| `extra_body` | `Dict[str, Any]` | `{}` | 厂商专有参数，**合并到请求体顶层**；禁 `messages/model/stream` |
| `extra_headers` | `Dict[str, str]` | `{}` | 额外请求头，可覆盖 `Authorization`；禁 `Content-Type/Length/Host`、禁换行符 |
| `drop_params` | `List[str]` | `[]` | 从最终请求体删除的字段名；禁删 `messages/model/stream` |

**输出**：`reply` / `finish_reason` / `model` / `prompt_tokens` / `completion_tokens` / `raw`（完整响应体）/ `dry_run`。

**请求体组装顺序**（`_build_payload`）：核心字段 → 可选字段（None 不写）→ `extra_body` 合并 → `drop_params` 删除。

**dry_run**：`api_key` 为空且 `extra_headers` 里没有非空的 `Authorization` / `X-API-Key` / `api-key` 时，不发网络请求，返回占位回复并列出"将发送哪些字段"——**这是排查中转站字段兼容问题的第一手段**。

**错误行为**：HTTPError 会把服务端响应正文（截 500 字）附在错误里；HTTP 200 但返回 `{"error": {...}}` 也会主动抛错。

### 9.2 `model_profile` — 模型参数档案

**输入**：`profile_name`(默认 `default`) / `api_base` / `api_key` / `model` / `system_prompt` / `temperature` / `max_tokens` / `timeout_seconds` / `extra_body` / `extra_headers` / `drop_params`（语义同 `llm_chat`）。

**输出**：与 `llm_chat` 的输入字段**同名同型**（`api_base` / `api_key` / `model` / `system_prompt` / `temperature` / `max_tokens` / `timeout_seconds` / `extra_body` / `extra_headers` / `drop_params`）+ `profile_name` / `mask`（如 `sk-***7890`）/ `ready`。

**用法**：把档案节点的输出连到 chat 的**同名字段**（接几条都行）。只接 `api_base + api_key` 也可以，模型名留在 chat 上填。`api_key` 只在本蓝图执行总线内流转。

### 9.3 `read_file` — 读文件

输入：`path`(必填) / `encoding`(`utf-8`) / `start_line` / `end_line`（1 起，含首尾）/ `max_bytes`(5 MB，超限直接报错而非截断)。
输出：`path` / `content` / `line_count` / `total_lines` / `size_bytes` / `truncated`（行切片时为 true）。

### 9.4 `http_fetch` — 联网

输入：`url`(必填，必须 `http(s)://`) / `method`(默认 `GET`) / `headers` / `body` / `timeout_seconds`(20) / `max_bytes`(2 MB) / `strip_html`(true)。
输出：`url` / `status_code` / `content` / `content_type` / `size_bytes` / `final_url` / `truncated`。

### 9.5 `regex_extract` — 正则

输入：`text`(必填) / `pattern`(必填) / `mode`(`findall` | `search`) / `limit`(100) / `flags`(`IGNORECASE` / `MULTILINE` / `DOTALL` 可多选)。
输出：`pattern` / `matches` / `count` / `truncated`。

### 9.6 `context_compress` — 上下文压缩

输入：`messages`(必填) / `max_messages`(20) / `keep_last_turns`(3) / `summary` / `summary_prefix`(`[历史摘要]`)。
输出：`messages`（压缩后）/ `original_count` / `compressed_count` / `dropped_count` / `compressed` / `estimated_tokens`。
策略是**确定性**的：保留 system + 最近 N 轮，更早折叠成一条摘要消息。

### 9.7 `tool_use` — 工具调用解析

输入：`model_output`(必填) / `available_tools`(白名单) / `call_format`(`json_block` | `function_call`) / `allow_unknown`(false)。
输出：`calls` / `count` / `has_calls` / `rejected` / `plain_text`。
**本节点不执行任何副作用**——真正写文件由下游 `edit_file` 执行，便于审计与权限隔离。

### 9.8 `edit_file` — 写文件（危险，默认受保护）

输入：`path`(必填) / `mode`(`append` | `overwrite` | `replace` | `insert_line`) / `content` / `old_text` / `line_number` / `allow_root`（路径围栏根目录）/ `allow_overwrite`(false，覆盖全文必须显式打开) / `create`(true) / `encoding`(`utf-8`) / `dry_run`(false)。
输出：`path` / `changed` / `size_before` / `size_after` / `replacements` / `created` / `dry_run`。

**安全建议**：给会写文件的 Agent 配 `allow_root`（如 `D:/agent-workspace`），先用 `dry_run=true` 试跑。

### 9.9 `rag_search` — 本地检索

输入：`query` / `documents`(必填，`List[str]`) / `top_k`(3) / `min_score`(0) / `chunk_size`(400) / `overlap`(60) / `context_header`。
输出：`results`（每项 `text` / `source_index` / `chunk_index` / `score`）/ `context` / `matched` / `corpus_chunks` / `query_terms`。

实现：英文词元 + 中文字符二元组；BM25(k1=1.5, b=0.75)；分数相同按（文档下标, 片段下标）升序，保证确定性；`query` 为空时按文档顺序取前 K 段；`score<=0` 的片段一律丢弃。

### 9.10 `branch` — 条件分支

输入：`value`（待判定值，表达式里用 `value` 引用）/ `expression`（如 `value > 5`）/ `variables`（额外变量，`Dict`）/ `condition`（直接给布尔，优先级高于 expression）。
输出：`route`（`on_true` / `on_false`）/ `on_true` / `on_false` / `matched`。

表达式走 `utils/expr.py` 的 **AST 白名单**：允许常量/变量/比较/布尔/算术/`not`/字面量容器；**禁止**函数调用、属性访问、下标、推导式、lambda、赋值、import。危险表达式会报"禁止的语法：Call"。

### 9.11 `loop_start` / `loop_end` — 循环

`loop_start` 输入：`iterations`(3) / `items`(可选列表，提供时长度优先) / `value`(透传初值)。
`loop_start` 输出：`iterations_planned` / `item_count` / `value`。
`loop_end` 输入：`value`（来自循环体最后一轮）/ `label`（仅展示）。
`loop_end` 输出：`iterations_done` / `results`（每轮 `{index, outputs}`）/ `last_value` / `stopped_early`。

配对规则（解析期静态完成）：
1. 栈式配对：按拓扑序压栈/弹栈，**内层 loop_end 先关闭**；
2. 孤立的 `loop_end`、无终点的 `loop_start`、起点与终点无连通路径 → `LOOP_UNPAIRED`；
3. 区间交叉（A 开、B 开、A 关、B 关）→ `LOOP_NESTED`；
4. 直接循环体节点绕过本循环 `loop_end` 连到循环外 → `LOOP_LEAK`。

---

## 10. HTTP API 全表

> 直接访问后端时**不带 `/api`**（`/api` 是前端 Vite 代理加的）。Swagger：`http://127.0.0.1:8000/docs`。

### 10.1 `GET /health`

```json
{"status":"ok","version":"0.1.0-alpha","node_count":12,
 "scan_summary":"扫描 11 个文件：新注册 0，热更新 0，移除 0，错误 0","plugin_errors":0}
```
有插件加载失败时 `status="degraded"`，服务仍可用。

### 10.2 `GET /schema`

```json
{"version":"0.1.0-alpha","contract_version":"0.1.0","count":12,
 "nodes":[{"node_type":"llm_chat","description":"…","version":"0.2.0",
           "control_flow":null,"input":{…JSON Schema…},"output":{…},
           "file":"llm_chat.py","docs":{"usage":"","inputs":[…],"outputs":[…]}}],
 "errors":[]}
```

### 10.3 `POST /validate`

请求：`{"blueprint": {…}}`
响应：
```json
{"valid": true, "order": ["n1"], "entry_nodes": ["n1"], "exit_nodes": ["n1"],
 "issues": [{"level":"error|warning","code":"blueprint.type_mismatch",
             "category":"validation","retryable":false,"message":"…",
             "node_id":null,"edge_id":"e1","field":"text"}]}
```
蓝图结构非法（重复 id、悬空边、自环）→ **422**。

### 10.4 `POST /run`

请求：
```json
{"blueprint": {…}, "shared": {}, "strict": false, "run_id": null}
```
响应（**节点失败也是 200**）：
```json
{"run_id":"run-abc12345","ok":false,"aborted":false,"abort_reason":null,
 "total_ms":12.3,
 "results":[{"node_id":"n1","node_type":"llm_chat","ok":true,"skipped":false,
             "error":null,"elapsed_ms":10.1,"output":{…}}],
 "errors":["[n2] 文件不存在: …"],
 "error_details":[{"code":"runtime.file_not_found","category":"not_found",
                   "retryable":false,"node_id":"n2","node_type":"read_file",
                   "edge_id":null,"field":null,"detail":"FileNotFoundError: …",
                   "message":"文件不存在: …"}]}
```

### 10.5 `/blueprints`（持久化）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/blueprints` | — | `{"items":[{"id","name","saved_at","node_count","edge_count"}]}`（按时间倒序） |
| POST | `/blueprints` | `{"blueprint":{…},"name":"可选","id":"可选（给 id 即覆盖）"}` | 元信息 `{id,name,saved_at,node_count,edge_count}` |
| GET | `/blueprints/{id}` | — | `{"meta":{…},"blueprint":{…}}`；不存在 404，id 非法 400 |
| DELETE | `/blueprints/{id}` | — | `{"deleted":true,"id":"…"}`；不存在 404 |

存储位置 `backend/data/blueprints/<id>.json`，内容 `{"id","name","saved_at","blueprint"}`。保存前会用 `Blueprint` 再校验一次，非法结构 422、不落盘。

### 10.6 鉴权（可选）

- 服务端设置环境变量 `VMAF_API_KEY=xxx` 后启用；
- 客户端在请求头带 `X-API-Key: xxx`；
- 受保护：`/schema` `/validate` `/run` `/blueprints*`；匿名：`/` `/health` `/docs` `/openapi.json` `/redoc`；
- 401 响应：`{"detail":{"message":"缺少或错误的 API Key。请在请求头携带 X-API-Key（服务端通过环境变量 VMAF_API_KEY 配置）"}}`；
- 密钥每次请求现读环境变量（改了不用重启）；`OPTIONS` 预检放行。

---

## 11. 前端架构与模块详解

### 11.1 状态模型（`App.tsx`）

| 状态 | 说明 |
|---|---|
| `schemas` | `/schema` 返回的节点规格（全部渲染的源头） |
| `nodes` / `edges` | React Flow 画布状态 |
| `issues` / `valid` | `/validate` 结果（400ms 防抖） |
| `runResult` | `/run` 响应（结果条渲染） |
| `templates` | localStorage 里的「我的模块」 |
| `serverId` / `serverOpen` | 当前蓝图对应服务端 id、对话框开关 |
| `connectHint` | 连线类型预判提示（6 秒自动消失） |
| `hasApiKey` | 是否已配置 X-API-Key |

**节点 `data` 字段约定**（关键）：`data.params` 是**业务参数**（要过插件的 `input_model`）；`data.schema / connectedInputs / localErrors / showDocs / accent / templateId / originId / templateHash` 是**展示元数据**，绝不进 `params`（否则 `extra="forbid"` 直接 422）。

### 11.2 `schemaForm.ts` — Schema → 表单

- `parseSchema(input)` → `FieldSpec[]`：识别 `string/number/integer/boolean/enum/array-string/json/object-array`。
- `deref`：递归解 `$ref`（`#/$defs/Message`），否则共享类型退化成裸 JSON 框。
- `defaultValueOf`：优先 `default`；**枚举无默认值时回落第一个合法候选值**（否则产出 `''` 骗过必填检查、在后端 Literal 处失败）；数组 `minItems>0` 时预置一条空项。
- `validateParams`：必填、`minItems`、**枚举合法性（含 object-array 子字段）**、数值范围、JSON 语法。
- `coerceValue`：把表单字符串转回目标类型（数字、按行数组、JSON 解析）。

### 11.3 `connection.ts` — 连线类型预判

松手即判定（不阻断、只提示）：

- `descriptorOf(prop, root)` 归一化：`$ref`→模型名、`anyOf`→联合、`type: array`→递归元素、无 `type`→`unknown`（放行）。
- `checkTypes(source, target)`：同型兼容、`integer→number` 放宽、列表递归、模型按名判定、union 逐成员、unknown 一律兼容。
- 不兼容时：连线**当场变红虚线** + 画布底部提示条（含两端字段名、人话类型名与原因）+ 原因并入目标节点 `localErrors`（参数面板可见）。
- 刻意放宽：`Literal/enum` 按基础类型处理（见 §7.2 说明）。

### 11.4 `templates.ts` — 模板引用式同步

- `templateHash(template)`：内容指纹（节点类型/标签/参数 + 连线端点；**忽略 id 与位置**；键序稳定）。
- 插入模板时记录 `templateId` / `templateName` / `originId` / `templateHash`。
- `templateUsage(nodes, templates)` → 每个模板 `{total, stale}`。
- `syncTemplateParams(nodes, template)` → 只把 `stale` 实例的 `params` **整体替换**为模板值（深拷贝），保留位置/标签/颜色/连线；无变化时幂等返回原数组。
- 「组合为模块」遇到同名模板会**询问覆盖**（保留模板 id），旧实例随即显示 `⇅ 待同步 N`。

### 11.5 `storage.ts` — localStorage 约定

| 键 | 内容 |
|---|---|
| `vmaf.params.v1.<node_type>` | `{last, history[]}`（最近参数 + 最多 20 条历史） |
| `vmaf.templates.v1` | `ModuleTemplate[]`（`id/name/nodeCount/size/nodes/edges/createdAt[/updatedAt]`） |
| `vmaf.ui.runbarHeight.v1` | 结果条高度（数字） |
| `vmaf.apiKey.v1` | API Key 明文（仅本机） |

所有读写都 try/catch 降级（隐私模式/配额不足不崩）。

### 11.6 其他模块

- `palette.ts`：9 色强调色板（刻意不含红——红色=运行失败），`randomNodeColor(avoid)` 避免撞色，`normalizeAccent` 校验 `#rrggbb`。
- `runbar.ts`：`RUNBAR_DEFAULT_HEIGHT=320` / `MIN=96` / `COLLAPSED=40`、视口 85% 上限、`clampRunbarHeight`、`loadRunbarHeight/saveRunbarHeight`。
- `hooks/useHotkeys.ts`：快捷键；**输入框内不拦截**（`isEditableTarget`）。
- `hooks/usePageZoom.ts`：Ctrl/Cmd+滚轮 = 浏览器整页缩放（window 捕获阶段 `stopPropagation`，不 `preventDefault`）。
- `components/NodeCard.tsx`：端点（`Handle` 绝对定位，`overflow:visible`）、必填星号（连线后取消）、说明面板（`.node-docs.nowheel`）、控制流徽标、运行状态红/绿边框、本地错误提示。
- `components/ParamPanel.tsx`：参数表单（连线字段显示"由上游连线提供，无需填写"）、历史记录、输出字段说明、强调色。
- `components/ServerBlueprints.tsx`：服务端蓝图列表（加载/删除/刷新/Esc 关闭/时间格式化）。

### 11.7 视觉与交互层的三个"坑"

1. 卡片宽度 `max-content` + 字段换行（曾写死 210px 导致字段名被截断）。
2. 说明开关必须在标题**左侧**（卡片展开向右变宽，放右侧会被裁掉/点不到）。
3. 滚轮分层：画布内缩放、`.node-docs` 内滚动（靠 `noWheelClassName="nowheel"`）、Ctrl+滚轮整页缩放。

---

## 12. 使用流程：从启动到跑通

### 12.1 启动

```bat
双击 vmaf_system\start.bat     REM 自动检查环境/端口、启动后端+前端、开浏览器
双击 vmaf_system\stop.bat      REM 结束占用 8000/5173 的进程
```

手动启动（调试用）：

```bash
# 终端 1：后端
cd vmaf_system/backend
python -m uvicorn main:app --port 8000

# 终端 2：前端
cd vmaf_system/frontend
npm run dev
```

访问：画布 `http://127.0.0.1:5173`，Swagger `http://127.0.0.1:8000/docs`。
顶栏出现 **"已连接 · 12 节点"** 即一切正常。

> Vite 5 默认只监听 localhost：`http://localhost:5173` 可访问，`http://127.0.0.1:5173` 用 curl 可能连不上（exit 7）。浏览器用 localhost 即可。需要局域网访问时 `npm run dev -- --host 0.0.0.0`。

### 12.2 画布操作

1. **加节点**：左侧点击模块（阶梯摆放）或拖拽到画布任意位置。
2. **连线**：从右侧输出端点拖到目标的左侧输入端点；`source_handle`/`target_handle` 由端点 `id` 决定。
3. **参数**：单击节点 → 右侧参数面板；被连线覆盖的字段显示"由上游连线提供"。
4. **运行**：顶部「▶ 运行」或 `Ctrl+Enter`；底部结果条显示每个节点的状态/耗时/输出，可拖高、可复制（导出）。
5. **撤销/重做**：`Ctrl+Z` / `Ctrl+Y`（`Ctrl+Shift+Z`）。
6. **组合为模块**：选中 ≥2 个节点 → 「组合为模块」→ 起名 → 左侧「我的模块」出现；可点击/拖拽插入；同名可覆盖并同步。
7. **保存到服务端**：「存服务端」（有 id 时覆盖）；「服务端蓝图」加载/删除。
8. **导入/导出**：导出下载 JSON（同时复制到剪贴板）；导入选择本地 JSON 还原画布。
9. **快捷键**：`Ctrl+D` 复制选中、`Ctrl+A` 全选、`Ctrl+S` 导出、`Delete/Backspace` 删除、`Esc` 取消选择。

### 12.3 三条"看不见的规则"

1. **参数 vs 连线**：连线的值永远覆盖节点上的静态参数。
2. **节点参数要过契约**：`params` 里多一个字段就会 422（`extra="forbid"`）；展示字段（`position/accent/label`）放在节点上而不是 `params` 里。
3. **枚举**：后端 `Literal`/`enum` 字段没有默认值时，前端默认取第一个合法值，不会产出空字符串。

---

## 13. 实战：如何配置一个 Agent（8 个配方）

> 下面每个配方都可以直接贴进 `POST /run`，或在画布上照着连。

### 配方 1：最小对话（直接把参数填在 chat 上）

```json
{"nodes":[{"id":"c","type":"llm_chat","params":{
  "messages":[{"role":"user","content":"用一句话解释什么是图灵机"}],
  "api_base":"https://api.deepseek.com",
  "api_key":"sk-你的密钥",
  "model":"deepseek-chat"}}],"edges":[]}
```
预期：`reply` 为模型回答；`dry_run=false`。没有 api_key 时会返回 `[dry_run] …`。

### 配方 2：模型档案复用（换模型只换连线）

```json
{"nodes":[
  {"id":"p","type":"model_profile","params":{
    "profile_name":"deepseek-main","api_base":"https://api.deepseek.com",
    "api_key":"sk-…","model":"deepseek-chat","temperature":0.3}},
  {"id":"c","type":"llm_chat","params":{"messages":[{"role":"user","content":"你好"}]}}
],"edges":[
  {"id":"e1","source":"p","source_handle":"api_base","target":"c","target_handle":"api_base"},
  {"id":"e2","source":"p","source_handle":"api_key","target":"c","target_handle":"api_key"},
  {"id":"e3","source":"p","source_handle":"model","target":"c","target_handle":"model"},
  {"id":"e4","source":"p","source_handle":"temperature","target":"c","target_handle":"temperature"}]}
```
用途：同一画布放多个档案（不同厂商），切换时只改连线目标；chat 本身保持干净。

### 配方 3：RAG 问答（先检索，再回答）

```json
{"nodes":[
  {"id":"r","type":"rag_search","params":{
    "query":"如何复用模型参数","top_k":2,
    "documents":["模型参数档案节点可以保存 api_base 与 api_key。",
                 "循环节点 loop_start 与 loop_end 成对使用。"]}},
  {"id":"c","type":"llm_chat","params":{
    "messages":[{"role":"user","content":"怎么复用模型参数？"}]}}
],"edges":[
  {"id":"e1","source":"r","source_handle":"context","target":"c","target_handle":"system_prompt"}]}
```
要点：`rag_search.context`（str）→ `llm_chat.system_prompt`（Optional[str]）类型预检通过。检索不到时 `context` 为空字符串，chat 退回普通对话。

### 配方 4：读文件 → 正则 → 对话（代码库问答的极简版）

```text
read_file.content ──▶ regex_extract.text
regex_extract.matches（List）✗ 不能直连 messages（List[Message]）
```
正确接法：`regex_extract.matches` 只能接 `List` 类输入；要喂给模型，先用 `read_file.start_line/end_line` 控制范围，把 `content` 直接接到 `llm_chat` 的 `system_prompt`（文本），或先经过 `context_compress`。

```json
{"nodes":[
  {"id":"f","type":"read_file","params":{"path":"D:/data/notes.md","max_bytes":100000}},
  {"id":"c","type":"llm_chat","params":{"messages":[{"role":"user","content":"总结这份笔记"}]}}],
 "edges":[{"id":"e1","source":"f","source_handle":"content","target":"c","target_handle":"system_prompt"}]}
```

### 配方 5：条件分支（按模型回复长度走不同路径）

```text
branch(expression="value > 100") ──on_true──▶ 节点A（详细路径）
                                 └─on_false─▶ 节点B（简短路径）
```
`value` 由上游连线注入（例如 `regex_extract.count`）；`expression` 里只能出现 `value` 与 `variables` 中的键。未命中那一路的节点被自动 **skipped**（结果条可见）。

### 配方 6：循环批处理（对 3 个文件逐个处理）

```json
{"nodes":[
  {"id":"s","type":"loop_start","params":{"items":["a.txt","b.txt","c.txt"]}},
  {"id":"body","type":"read_file","params":{"path":"D:/data/a.txt"}},
  {"id":"e","type":"loop_end","params":{}}],
 "edges":[
  {"id":"a1","source":"s","source_handle":"value","target":"body","target_handle":"path"},
  {"id":"a2","source":"body","source_handle":"content","target":"e","target_handle":"value"}]}
```
注意：`items` 只有长度参与迭代次数；具体元素通过 `loop.item` 暴露给插件/表达式，**默认不会自动注入到下游参数**——需要对每个元素做不同处理时，用 `branch` 按 `loop.indexes` 分流，或把 `items` 拆成多份蓝图。

### 配方 7：嵌套循环（2 外层 × 3 内层）

```text
loop_start(2) ──▶ loop_start(3) ──▶ 节点 ──▶ loop_end(内) ──▶ loop_end(外) ──▶ 后续
```
解析器会识别为：外层直接体 = `[内层 loop_start]`，内层体 = `[节点]`；运行时外层每轮把内层完整跑一遍。循环内可用 `loop.indexes`（如 `[1,2]`）与 `loop.depth` 区分层级。

### 配方 8：工具调用链（解析意图 → 人工/下游执行写文件）

```text
llm_chat.reply ──▶ tool_use.model_output
                        │ calls（结构化指令）
                        ▼
                  edit_file（真正写盘，配 allow_root + dry_run 先试跑）
```
`tool_use` 只解析不执行：它输出 `calls` / `has_calls` / `rejected` / `plain_text`。真正落盘由 `edit_file` 完成，**危险动作永远单独成节点**，便于加围栏与审计。

### 配方 9（进阶）：连续对话 + 上下文压缩

```text
[历史消息] ──▶ context_compress ──▶ llm_chat.messages
                                      │
                                      ▼ reply
                               （下一轮拼进历史）
```
单次运行内可以这样接；跨轮记忆目前需要在外层重复调用 `/run` 并把历史存进 `shared`（见 §17 的路线）。

---

## 14. 控制流专题：判断与循环

### 14.1 判断（branch）的完整语义

1. **输入优先级**：`condition`（显式布尔）> `expression`（表达式）> `bool(value)`。
2. **表达式求值**：`utils/expr.py` 的 `evaluate(expression, variables)`；变量表 = `{**variables, "value": value}`。
3. **路由**：输出 `route` 为 `"on_true"` / `"on_false"`；引擎把与 `route` 不一致的**出边**标记失活。
4. **跳过判定**：节点**所有入边**都失活才跳过；因此两条分支汇聚到同一节点时，只要有一条执行过就会继续。
5. **失活重置**：循环每轮会重置循环体内部的分支状态，上一轮的判定不影响下一轮。

### 14.2 判断的常见用法

| 场景 | expression 示例 |
|---|---|
| 数值阈值 | `value > 100` |
| 字符串比较 | `status == 'ok'` |
| 组合条件 | `value > 0 and value < 10` |
| 取反 | `not matched` |
| 用变量而非 value | 先连 `variables`，再写 `count >= limit` |

### 14.3 循环的静态识别（解析器）

```text
按拓扑序扫描：
  loop_start → 压栈（此时栈顶 = 父循环）
  loop_end   → 弹栈配对（内层先关）
```

- 栈里剩余的 `loop_start` → 无终点 → `LOOP_UNPAIRED`。
- 没配上栈的 `loop_end` → 孤立终点 → `LOOP_UNPAIRED`。
- 终点必须从起点**可达**（拓扑相邻 ≠ 连通），否则 `LOOP_UNPAIRED`。
- 完整体 = 起点可达 ∩ 能到达终点；**直接循环体 = 完整体 − 各子循环的（体 + 终点）**。
- 子循环终点不在外层完整体内 → 区间交叉 → `LOOP_NESTED`。
- 直接循环体节点连到"体外且非本循环终点、也非子循环内部" → `LOOP_LEAK`。
- 完成后 `_collapse_loops()` 把循环体与 `loop_end` 从主序列摘出，主序只剩 `loop_start`。

### 14.4 循环的运行时语义

| 行为 | 说明 |
|---|---|
| 迭代次数 | `items` 长度优先，否则 `iterations` |
| 轮内顺序 | 循环体按拓扑序执行（含分支） |
| 提前结束 | 体内任一节点失败 → 本轮中断、`stopped_early=true`，`loop_end` 仍会执行以产出聚合 |
| 聚合 | `loop_end.results = [{"index": i, "outputs": {node_id: 输出字典}}, …]` |
| 嵌套 | 子循环在嵌入的 `loop_start` 处递归；进出时保存/恢复外层 `loop.results` |
| 变量 | `loop.index` / `loop.item` / `loop.indexes` / `loop.depth` |

### 14.5 循环的三个反直觉点

1. **循环体输出不自动传递**：每轮只是"重新执行"，节点输出写在总线上，下一轮会被覆盖；需要累积请用 `loop_end.results`。
2. **循环内节点失败会中断该区间**（默认模式），但不会中止整个蓝图的其他分支。
3. **`items` 不自动展开**：它只决定次数，并暴露 `loop.item`；把元素注入下游需要你自己连线/分流。

---

## 15. 故障排查手册

### 15.1 错误码表

| 错误码 | 分类 | 典型原因 | 处理 |
|---|---|---|---|
| `blueprint.cycle` | validation | 连线成环 | 删掉回边 |
| `blueprint.unknown_node_type` | not_found | 节点类型未注册/插件加载失败 | 看 `/health` 的 `plugin_errors`、后端日志 |
| `blueprint.unknown_output_field` / `unknown_input_field` | validation | 连线两端字段名不存在 | 检查端点 `id`（=字段名） |
| `blueprint.type_mismatch` | validation | 类型不兼容 | 看 `message` 里的两端类型名 |
| `blueprint.missing_required_input` | validation | 必填项既没连线也没填 | 补连线或参数 |
| `blueprint.nullable_flow`（warning） | validation | 源可能为 None | 可运行，注意运行期 |
| `blueprint.loop_unpaired` / `loop_nested` / `loop_leak` | validation | 循环标记配对/嵌套/泄漏问题 | 按 message 调整标记位置 |
| `plugin.load_failed` / `contract_broken` / `type_conflict` | contract | 插件语法错/契约破损/类型冲突 | 看 `ScanReport.errors` |
| `runtime.input_validation` | validation | 参数没通过 input_model | 看 message 里的字段与原因 |
| `runtime.output_validation` | contract | 插件返回值不符合 output_model | 插件 bug |
| `runtime.file_not_found` / `is_a_directory` / `permission_denied` | not_found/validation/permission | 路径问题 | 检查路径与权限 |
| `runtime.resource_too_large` | validation | 超过 `max_bytes` | 调大上限或切片 |
| `runtime.network_error` / `runtime.timeout` | network/timeout（**可重试**） | 网络/超时 | 重试或调超时 |
| `runtime.invalid_argument` | validation | 参数值非法 | 看 message |
| `internal.unknown` | internal | 未归类 | 看 `detail` 与后端日志 |

### 15.2 常见现象 → 原因

| 现象 | 原因与处理 |
|---|---|
| 页面"未连接" | 后端没起来/崩了。看后端窗口；`curl http://127.0.0.1:8000/health` |
| 点运行"0/1 节点成功"，耗时 0.1ms | 参数校验失败。多半是枚举空值或必填未填——看结果条里的 `error`；前端现在也会标红 |
| 422 | 蓝图 JSON 结构非法（重复 id/悬空边/自环）或 `params` 多了字段 |
| 401 | 服务端启用了 `VMAF_API_KEY`，前端点「密钥」填同值 |
| 改插件不生效 | 旧版本需重启；**现在**热更新已修复（§8.6），仍不生效就看 `/health` 的 `scan_summary` 是否 `热更新 1` |
| 端口占用 `winerror 10048` | 已有实例在跑；`Get-NetTCPConnection -LocalPort 8000` 确认，或先跑 `stop.bat` |
| dev server 崩（`EBUSY`） | 编辑器"临时文件+重命名"被 chokidar watch；`vite.config.ts` 已忽略 `*.tmpdir/*.tmp` |
| 后端日志中文乱码 | 已用 `setup_logging()` 统一 UTF-8；若仍乱码，多半是终端代码页问题（Windows Terminal 正常） |
| `/health` 里中文乱码但 JSON 正常 | PowerShell 控制台显示编码问题，不是服务端问题（用 UTF-8 解码响应即正常） |

### 15.3 调试三件套

1. **结果条**：每个节点的 `output` 完整 JSON，最直观。
2. **`/validate`**：改完连线立刻看 `issues`（前端也已本地预判）。
3. **dry_run**：`llm_chat` 无密钥、`edit_file` 的 `dry_run=true`，都是"只看不写"的安全试跑。

---

## 16. 测试与质量保证

### 16.1 四条基线（每次改动后必跑）

```bash
cd vmaf_system/backend  && python -m pytest -q      # 296 passed
cd vmaf_system/frontend && npm test                 # 126 passed
cd vmaf_system/frontend && npx tsc --noEmit         # 无输出
cd vmaf_system/frontend && npx vite build           # 成功
```

### 16.2 测试分布

| 文件 | 覆盖 |
|---|---|
| `test_types.py` | 类型判定矩阵（含回归：Optional 不得被当容器） |
| `test_base_contract.py` | 契约层：get_schema/get_docs/validate_contract |
| `test_scanner.py` | 注册/热更新/删除/冲突隔离/契约唯一/reload 护栏（22 项） |
| `test_parser.py` | 环/拓扑/类型预检/必填/入口出口 |
| `test_engine.py` | 三级校验/失败传播/执行顺序/总线 |
| `test_control_flow.py` | 分支/循环/嵌套/`loop.indexes`/文档完整性 |
| `test_plugins.py` | 各插件安全围栏（路径/体积/协议/白名单/dry_run） |
| `test_expr.py` | AST 白名单（23 项，含攻击面） |
| `test_api.py` | `/health` `/schema` `/validate` `/run` |
| `test_api_security.py` | 鉴权（11 项） |
| `test_blueprint_store.py` | 存储（18 项：CRUD/原子/穿越/损坏） |
| `test_blueprints_api.py` | `/blueprints`（6 项） |
| `test_model_profile.py` | 档案契约对齐/打码/端到端（8 项） |
| `test_rag_search.py` | 切词/分片/排序/降级（15 项） |
| `test_logger.py` | UTF-8/幂等/降级（6 项） |
| `test_errors.py` | 归类/翻译/上下文（23 项） |
| `frontend/src/*.test.ts` | schemaForm 22 / storage 20 / palette 4 / runbar 8 / connection 24 / templates 13 |
| `frontend/src/components/*.test.tsx` | ParamPanel 9 / NodePalette 9 / NodeCard 7 / ServerBlueprints 10 |

### 16.3 加测试的三条经验

1. **回归测试必须写缺陷背景**（函数 docstring 写"缺陷背景：…"），否则后人会当冗余删掉。
2. **组件测试**：`NodeCard` 用 `Handle`，必须包 `ReactFlowProvider`；jsdom 测不了布局，拖拽仍需浏览器实测。
3. **改契约先跑 `-m types`**：`python -m pytest -m types`，类型判定是全局影响面最大的一块。

---

## 17. 已知边界与后续路线

### 17.1 明确的边界（不是 bug，是取舍）

| 边界 | 说明 |
|---|---|
| 单线程串行 | 不做并行分支；一个节点跑完才跑下一个（确定性优先） |
| 无跨运行记忆 | 每次 `/run` 是全新 Context；"多轮对话"需外部保存历史再传入 |
| 词法检索 | `rag_search` 是 BM25，不做语义近似；同义改写会漏 |
| 模板只同步参数 | 用户改过结构后不做结构同步（避免覆盖用户意图） |
| 无用户体系 | 鉴权是单一共享密钥，没有账号/多租户 |
| 前端无 e2e | 真实拖拽/缩放靠浏览器手工验证；组件测试只覆盖渲染逻辑 |
| 蓝图并发写 | 同一 id 同时保存以"后写覆盖"为准（单机场景可接受） |

### 17.2 推荐后续路线（按价值排序）

1. **执行结果持久化**：把 `RunReport` 落盘（`data/runs/`），支持历史对比与复现。
2. **流式执行进度**：`/run` 改 SSE/WebSocket，前端逐节点点亮（引擎已串行，事件化成本低）。
3. **`/schema` 过滤**：只拉需要的节点类型，降低首屏体积。
4. **多轮会话状态**：用 `shared` 或服务端会话表保存历史。
5. **语义检索节点**：独立的 `embedding_search`，不污染现有 `rag_search`。
6. **循环体内元素注入**：让 `loop.item` 自动映射到指定输入字段（需要契约层新增机制）。
7. **前端 e2e**：Playwright 覆盖拖拽/连线/撤销。

---

## 18. 交接检查清单

接手后按顺序做这 10 件事：

- [ ] 1. 读 `vmaf_system/project_status.md`（当前状态与"首个原子任务"）。
- [ ] 2. 读 `vmaf_system/docs/NAMING_CONVENTIONS.md`（命名 + 陷阱清单）。
- [ ] 3. 跑四条基线，确认 **296 / 126 / tsc / build** 全绿。
- [ ] 4. 双击 `start.bat`，浏览器看到"已连接 · 12 节点"。
- [ ] 5. 跑通配方 1（最小对话），确认能拿到真实回复。
- [ ] 6. 打开 `http://127.0.0.1:8000/docs`，逐个点开 `/health` `/schema` `/validate` `/run`。
- [ ] 7. 用 `curl` 调一次 `/run`，观察 `results` 与 `error_details` 的字段。
- [ ] 8. 在 `tools/` 里加一个 `echo.py`（§6.2 示例），**不重启后端**，刷新页面看新节点是否出现（验证热更新）。
- [ ] 9. 故意连一条 `model(string) → max_tokens(integer)`，确认前端立刻红虚线 + 提示条。
- [ ] 10. 选 2 个节点「组合为模块」，再改其中一个节点参数，观察「待同步」按钮与同步效果。

**日常修改的收尾动作**：改完 → 跑四条基线 → 同步 `MODULE_INDEX.md` 与 `project_status.md`（含"为什么"，只记结论不记原因等于没记）。

---

## 19. 附录：术语与错误码速查

### 19.1 术语

| 术语 | 含义 |
|---|---|
| 蓝图（Blueprint） | `{nodes, edges}` 的 JSON，前端产出、后端执行 |
| 节点（Node） | 蓝图里的一项：`id/type/params[/label/position/accent]` |
| 边（Edge） | 一条数据流：源节点输出字段 → 目标节点输入字段 |
| handle | 端点 id，等于字段名；前端连线靠它定位 |
| 契约层 | `plugins/base.py`：`BaseNode` / `Message` / `InputSchema` / `OutputSchema` |
| 注册表 | `{node_type: PluginRecord}`，扫描器维护 |
| 执行计划 | `ExecutionPlan`：拓扑序 + 解析后的节点/边 + 循环区间 + 问题列表 |
| Context Bus | `ExecutionContext`：UUID 键控的节点输出 + shared 命名空间 |
| 逃生舱 | `extra_body` / `extra_headers` / `drop_params` 三个厂商适配字段 |
| dry_run | 演练模式：不发请求/不写盘，只返回"将会做什么" |

### 19.2 错误码前缀

| 前缀 | 用途 |
|---|---|
| `blueprint.*` | 解析期问题（环/类型/必填/循环结构） |
| `plugin.*` | 插件注册期问题 |
| `runtime.*` | 运行期问题（校验/文件/网络/超时） |
| `internal.unknown` | 兜底 |

### 19.3 关键文件速查

| 想改什么 | 去哪 |
|---|---|
| 加节点 | `backend/plugins/tools/<name>.py`（不用改别处） |
| 改执行语义 | `backend/core/engine.py` |
| 改类型预检 | `backend/core/parser.py::_type_verdict` + 前端 `connection.ts`（两边要同步改） |
| 改 API | `backend/api/routes.py` + `schemas.py` |
| 加错误码 | `backend/utils/errors.py`（`ErrorCode` + `ISSUE_CODE_MAP`） |
| 改参数表单 | 改插件的 `Field(description=…)`，前端不要动 |
| 改画布交互 | `frontend/src/App.tsx` |
| 改节点卡片外观 | `frontend/src/components/NodeCard.tsx` + `styles.css` |

---

## 结语

VMAF 的核心思想只有一句话：**用数据（JSON + Pydantic 契约）描述一切，用图（DAG）描述执行顺序，用插件描述能力，用引擎描述规则**。理解了这四层，就理解了整个项目。

本报告与 `project_status.md` 互为补充：报告回答"这是什么、怎么用、为什么这样设计"，状态文档回答"现在做到哪、下一步做什么"。两者冲突时，**以代码与状态文档为准**，并顺手修正报告。

祝接手顺利。
