# VMAF (Visual Modular Agent Framework) 项目状态与会话交接单

> 本文件是跨会话沟通的**唯一进度真相源**。任何会话开始时必须先读取并校验本文件；
> 任何工程结构偏离、目录调整、契约变更都必须在此文档中同步记录。
>
> 配套文档：
> - **`docs/NAMING_CONVENTIONS.md`** —— 全局命名规范（下个窗口动工前必读）
> - 各目录 `MODULE_INDEX.md` —— 文件级明细（plugins / core / api / utils / tests / frontend）
> - **`README_START.md`** —— 启动脚本使用说明

---

## [当前系统真实目录树]

环境基准：Windows / Python 3.14.6 / Pydantic 2.13.5 / pytest 9.1.1 / Node 24.19 /
Vite 5 / React 18 / @xyflow/react 12 / vitest 2。

```
vmaf_system/
├── project_status.md          # 本文件：唯一进度真相源
├── README_START.md            # start.bat / stop.bat 中文说明与踩坑记录
├── requirements.txt           # pydantic / pytest / pytest-asyncio / fastapi / uvicorn / httpx
├── start.bat                  # 一键启动（纯 ASCII：环境检查/端口检查/就绪轮询/开浏览器）
├── stop.bat                   # 一键停止（结束 8000/5173 端口进程）
├── docs/
│   ├── NAMING_CONVENTIONS.md  # 全局命名规范：模块名表、插件命名、共享类型、陷阱清单
│   └── assets/
│       └── runbar-resize.png  # 执行结果条拖拽调高的 CDP 实测截图
├── backend/
│   ├── main.py                # FastAPI 入口：CORS + 路由挂载 + 启动时扫描插件
│   ├── pytest.ini             # 测试配置（asyncio_mode=strict，7 个自定义 marker）
│   ├── api/
│   │   ├── routes.py          # /health /schema /validate /run + /blueprints 四类接口
│   │   ├── security.py        # 可选鉴权（VMAF_API_KEY → X-API-Key，默认关闭）
│   │   ├── schemas.py         # API 响应契约（隔离引擎内部对象与外部接口）
│   │   └── MODULE_INDEX.md
│   ├── core/
│   │   ├── blueprint.py       # 蓝图契约（含可选 accent：展示元数据，引擎忽略）
│   │   ├── parser.py          # 环检测 + 拓扑排序 + 类型预检 + 循环区间识别
│   │   ├── context.py         # Context Bus：UUID 键控内存数据总线
│   │   ├── engine.py          # 单线程异步串行执行器 + 分支路由 + 循环展开
│   │   └── MODULE_INDEX.md
│   ├── plugins/
│   │   ├── base.py            # 契约层「底层法律」（BaseNode/Message/get_docs）
│   │   ├── scanner.py         # 反射扫描器：按文件路径加载 + 热更新 + 故障隔离
│   │   ├── tools/             # 10 个业务插件（见下表），无 __init__.py
│   │   └── MODULE_INDEX.md
│   ├── utils/
│   │   ├── loader.py          # 进程级模块加载权威（load_module_once + reload_module 受控重载）
│   │   ├── logger.py          # 统一日志（UTF-8 StreamHandler）
│   │   ├── blueprint_store.py # 蓝图持久化（一蓝图一 JSON，原子写入 + id 围栏）
│   │   ├── errors.py          # 异常结构化（ErrorDetail / ISSUE_CODE_MAP）
│   │   ├── expr.py            # 受限表达式求值器（AST 白名单，禁 eval）
│   │   └── MODULE_INDEX.md
│   └── tests/                 # 18 个测试文件 + conftest，296 项全绿
│       ├── conftest.py        # 夹具：所有模块加载走 load_module_once（不可绕过）
│       ├── test_types.py / test_base_contract.py / test_scanner.py
│       ├── test_parser.py / test_engine.py / test_plugins.py
│       ├── test_expr.py / test_control_flow.py / test_api.py
│       └── MODULE_INDEX.md
└── frontend/
    ├── package.json / tsconfig.json / vite.config.ts   # /api 代理到 8000（watch.ignored 已忽略 *.tmp，防 EBUSY 打挂 dev server）
    ├── index.html
    └── src/
        ├── main.tsx / App.tsx        # 主应用：画布+撤销重做+拖放+校验+执行+可拖拽结果条
        ├── types.ts                  # 前后端类型桥梁（$ref/anyOf/可选 type/minItems/accent）
        ├── api.ts                    # API 客户端
        ├── schemaForm.ts             # JSON Schema→表单引擎（$ref 解引用 + enum 默认值回落 + 枚举预检）
        ├── storage.ts                # localStorage：参数记忆/历史、模板组合与展开
        ├── context.ts                # NodeActionsContext（说明面板开关）
        ├── palette.ts                # 节点强调色板：随机取色 + 颜色校验
        ├── connection.ts             # 连线类型预判：本地复刻后端 _type_verdict，松手即提示
        ├── templates.ts              # 模板引用式同步：内容指纹 + 待同步判定 + 参数同步
        ├── runbar.ts                 # 执行结果条纯逻辑：高度约束 + localStorage 持久化
        ├── hooks/useHotkeys.ts       # 快捷键（输入框内不拦截）
        ├── hooks/usePageZoom.ts      # Ctrl/Cmd+滚轮 = 浏览器整页缩放
        ├── styles.css                # 深色主题 + 深色滚动条 + React Flow 适配 + 结果条拖拽条 + 连线预判提示样式
        ├── components/
        │   ├── NodeCard.tsx          # 节点卡片（端点对齐/说明面板可滚动/强调色边框）
        │   ├── NodePalette.tsx       # 左侧模块与「我的模块」
        │   ├── ParamPanel.tsx        # 右侧参数面板 + 历史 + 节点外观（取色）
        │   └── fields.tsx            # 通用小组件
        ├── testSetup.ts              # vitest 全局 setup（每例后 cleanup）
        ├── *.test.ts / *.test.tsx    # 126 项全绿（schemaForm 22 + storage 20 + palette 4 + runbar 8 + connection 24 + templates 13 + 组件 35）
```

业务插件（`backend/plugins/tools/`，共 12 个 node_type，全部验证通过）：

| 文件 | node_type | 架构图位置 |
|---|---|---|
| llm_chat.py | `llm_chat` | 核心/对话（OpenAI 协议；`extra_body`/`extra_headers`/`drop_params` 适配厂商差异；无凭据进 dry_run） |
| model_profile.py | `model_profile` | 输入/模型参数档案（沉淀服务地址/密钥/模型名/采样参数；输出与 llm_chat 输入字段一一对应，连线即生效；密钥打码 `mask` + `ready` 标记） |
| rag_search.py | `rag_search` | 增强/本地检索（BM25 词法检索，中文二元组切词；输出 `context` 直连 chat.system_prompt） |
| read_file.py | `read_file` | 输入/读文件（行切片+体积上限） |
| http_fetch.py | `http_fetch` | 输入/联网（协议白名单+HTML 清洗） |
| regex_extract.py | `regex_extract` | 增强/正则（捕获组+命名组） |
| context_compress.py | `context_compress` | 回路/上下文压缩 |
| tool_use.py | `tool_use` | 动作/tool-use（只解析不执行副作用） |
| edit_file.py | `edit_file` | 动作/改文件（路径围栏+overwrite 开关+dry_run） |
| branch.py | `branch` | **控制流/条件分支**（expression 经 expr.py 求值） |
| loop.py | `loop_start` + `loop_end` | **控制流/循环**（区间自动识别） |

---

## [架构红线与契约备忘]

### 四条设计红线（不可违背）

1. **纯数据驱动**：前端只产出 `{nodes, edges}` JSON 蓝图；后端引擎读 JSON 按图调度；
   业务全在插件层。
2. **契约驱动（Pydantic V2）**：所有模块 IO 必须是 `BaseModel` 子类；
   `model_json_schema()` 导出给前端；运行时双重校验（输入 + 输出"双保险"）。
3. **无全局变量纯函数**：插件 = `输入实例 -> 输出实例`，无副作用、无跨调用状态；
   状态只在 Context Bus（UUID 键控）里流转。
4. **单机确定性**：单线程异步串行；拓扑排序确定性——**纯手写 Kahn 算法**
   （`parser.py::_topological_sort`，**未使用 NetworkX**，避免引入网络图论依赖），
   入度为 0 的候选按节点 id 排序打破并列，同一蓝图多次解析顺序必须一致。

### 关键签名（直接复用，禁止重新发明）

```python
# backend/plugins/base.py —— 契约层
class BaseNode(ABC):
    node_type: ClassVar[str]                       # 全局唯一注册键
    description: ClassVar[str]
    version: ClassVar[str]
    input_model: ClassVar[Type[BaseModel]]
    output_model: ClassVar[Type[BaseModel]]
    control_flow: ClassVar[Optional[str]]         # None|"branch"|"loop_start"|"loop_end"
    @abstractmethod
    async def execute(self, inputs: BaseModel, context: dict | None = None) -> BaseModel: ...
    @classmethod
    def get_schema(cls) -> dict                    # node_type/description/version/control_flow/input/output
    @classmethod
    def get_docs(cls) -> dict                      # usage/inputs/outputs（面向人的说明书）
    @classmethod
    def validate_contract(cls) -> None             # 注册期自检

class Message(BaseModel):                          # 跨节点共享消息类型（唯一）
    role: Literal["system","user","assistant","tool"]
    content: str
    name: Optional[str] = None

# backend/core/blueprint.py —— 节点契约（展示元数据单列，引擎不读）
class BlueprintNode(BaseModel):
    id: str; type: str; params: dict
    label: Optional[str]; position: Optional[dict]
    accent: Optional[str]        # #rrggbb，仅前端配色用；与 position 同类

# backend/utils/loader.py —— 进程级加载权威（全部动态加载必须走它）
def load_module_once(module_name: str, file_path: Path) -> ModuleType
def reload_module(module_name: str, file_path: Path) -> ModuleType
    # 受控重载：仅允许 scanner 在指纹变化时调用；_NON_RELOADABLE_MODULES
    # 保护契约层/核心区（对 vmaf.plugins.base 重载直接 ValueError）；
    # 重载前清 __pycache__（.pyc 源 mtime 秒级精度，同秒改动会命中旧字节码）
def ensure_package(package_name, package_path=None, **attrs) -> ModuleType

# backend/plugins/scanner.py
class PluginScanner:
    def scan(self, force_reload=False) -> ScanReport      # 热更新/故障隔离/自动注销
    def get_all_schemas(self) -> list[dict]               # 含 file 与 docs 字段
    def get_registry(self) -> dict[str, PluginRecord]
    def instantiate(self, node_type: str) -> BaseNode
def get_default_scanner() -> PluginScanner

# backend/core/parser.py
class BlueprintParser:
    def __init__(self, registry: dict) -> None            # 接受 {type: node_class}
    def parse(self, blueprint: Blueprint) -> ExecutionPlan
# ExecutionPlan: .order .resolved_nodes .issues .entry_nodes .exit_nodes .loop_regions .valid

# backend/core/engine.py
class GraphEngine:
    def __init__(self, registry: dict, strict: bool = False) -> None
    async def run(self, blueprint, shared=None, run_id=None) -> RunReport  # 从不抛异常
# RunReport: .ok .results .errors .error_details .aborted .abort_reason .to_dict()

# backend/utils/expr.py —— 条件表达式（禁 eval）
def evaluate(expression: str, variables: Mapping) -> Any
def safe_compare(expression: str, variables: Mapping) -> bool

# backend/utils/errors.py
ISSUE_CODE_MAP: dict      # 解析器短码 -> 统一错误码的唯一权威映射
def to_error_detail(exc, *, node_id=None, node_type=None, edge_id=None, field=None) -> ErrorDetail
def issue_to_error_detail(issue) -> ErrorDetail

# backend/plugins/tools/llm_chat.py —— v0.2.0 厂商适配逃生舱（勿拆，理由见 plugins/MODULE_INDEX 决定 3）
_RESERVED_BODY_KEYS = frozenset({"messages", "model", "stream"})  # extra_body/drop_params 禁触
_PROTECTED_HEADERS = frozenset({"content-type", "content-length", "host"})
_AUTH_HEADER_NAMES = frozenset({"authorization", "x-api-key", "api-key"})  # 任一非空即关 dry_run
class ChatInput(BaseModel):        # model_config = {"extra": "forbid"}：未知顶层参数立即报错
    extra_body: Dict[str, Any]     # 合并到请求体【顶层】（同 OpenAI SDK extra_body 语义）
    extra_headers: Dict[str, str]  # 可覆盖 Authorization；换行符注入在 model_validator 拦截
    drop_params: List[str]         # 在 extra_body 合并之后执行 payload.pop(key, None)
def _build_payload(inputs: ChatInput) -> Dict[str, Any]
    # 组装顺序：核心字段 -> 可选字段（None 不写） -> extra_body 合并 -> drop_params 删除
```

前端关键契约（`frontend/src`）：

```ts
// palette.ts —— 节点配色（新增，勿在别处自造色板）
export const NODE_COLORS: readonly string[]            // 9 色，刻意不含红（红色=运行失败）
export function randomNodeColor(avoid?: string | null): string
export function normalizeAccent(value: unknown): string | null

// runbar.ts —— 执行结果条高度（本窗口新增，勿在组件里散写约束）
export const RUNBAR_DEFAULT_HEIGHT = 320    // 展开默认高度
export const RUNBAR_MIN_HEIGHT = 96         // 最小高度
export const RUNBAR_COLLAPSED_HEIGHT = 40   // 收起高度
export const RUNBAR_HEIGHT_KEY = 'vmaf.ui.runbarHeight.v1'
export function maxRunbarHeight(viewportHeight: number): number   // 视口 85% 上限
export function clampRunbarHeight(value: number, viewportHeight: number): number
export function loadRunbarHeight(): number | null
export function saveRunbarHeight(height: number): void

// hooks/usePageZoom.ts —— Ctrl/Cmd+滚轮恢复浏览器整页缩放
export function usePageZoom(enabled = true): void

// 节点 data 约定的展示字段（非 params，绝不进 Pydantic 校验）
//   data.accent?: string | null   —— 边框/选中光晕颜色
//   data.showDocs?: boolean       —— 该节点的说明面板是否展开
```

### 规范模块名表（动态加载的 sys.modules 键，全局唯一且稳定）

`vmaf.plugins.base` / `vmaf.plugins.scanner` / `vmaf.plugins.tools.<文件名>` /
`vmaf.core.{blueprint,parser,context,engine}` / `vmaf.utils.{loader,errors,expr}` / `vmaf.main`

### 历史架构决定（不可回退，理由见各 MODULE_INDEX）

1. `Message` 上提契约层（Pydantic 按类身份校验，结构相同 ≠ 可连）。
2. 契约层全进程唯一：一切加载走 `load_module_once`，**禁止**直接
   `importlib.spec_from_file_location`（会产生第二份 base.py → 跨节点连线全部失败）。
3. 类型预检严格判定（`_type_verdict`），不设"无法判定就放行"兜底；Union 判定必须先于容器判定。
4. 共享类型导出为 `$ref`（`#/$defs/Message`），前端 `schemaForm.ts::deref` 负责解引用（配套，缺一不可）。
5. 对外错误码统一 `<子系统>.<问题>`（`blueprint.*`/`plugin.*`/`runtime.*`），从 `ISSUE_CODE_MAP` 取。
6. 节点业务失败 HTTP 200（错误在 `error_details`）；蓝图 JSON 非法 422。
7. 控制流 = 节点元数据（`control_flow` 标记）+ 引擎调度；插件本身仍是纯函数。
8. 条件表达式只用 `utils/expr.py` 的 AST 白名单（禁 eval/exec）。
9. 循环 = `loop_start`/`loop_end` 标记配对 + 解析器静态区间识别（配对/禁嵌套/禁泄漏三项校验）
   + 引擎展开；循环体与 loop_end 从 `order` 摘除。
10. 模块说明书 `get_docs()` 从 Pydantic Field description 自动推导——**测试强制每个输入/输出字段必须有说明**。
11. 前端：复合模块 = 快照式模板（localStorage），插入时展开为真实节点；参数记忆按 node_type 共享。
12. `.bat` 必须纯 ASCII（中文 + cmd 编码互斥，详见 README_START.md 踩坑记录）。
13. **展示元数据与业务参数分离**：`position` / `accent` 放蓝图节点上、随蓝图传输，
    但**绝不进 `params`**——`params` 要过插件的 `input_model`（`extra=forbid`），
    混进去会直接 422。引擎与解析器都不读这两个字段。
14. **滚轮分层（已定型）**：普通滚轮在画布/卡片上 = 缩放画布；在 `.node-docs`
    内 = 滚动面板（靠 React Flow 的 `noWheelClassName="nowheel"`）；Ctrl/Meta+滚轮
    = 整页缩放（`usePageZoom` 在 window 捕获阶段 `stopPropagation`，不 `preventDefault`）。
    以后任何"内部可滚动、不该触发画布缩放"的浮层，加 `nowheel` 即可。
15. **说明面板开关固定在卡片标题左侧**：卡片展开时向右变宽，放右侧会把按钮推出
    可视区（曾导致"展开了收不回去"的真实缺陷）。
16. **厂商差异 = 稳定核心 + 显式逃生舱**：`llm_chat` 只把 `messages` / `model`
    等通用字段纳入契约；厂商专有参数走 `extra_body`（合并到请求体顶层）、
    额外请求头走 `extra_headers`、不兼容字段用 `drop_params` 移除。
    `ChatInput` 顶层 `extra="forbid"`，禁止静默丢弃未知参数；`temperature` /
    `max_tokens` 留空不下发。跨协议厂商（Anthropic / Gemini）另写适配插件，
    不把 `llm_chat` 做成万能节点。
17. **前端枚举默认值红线（本窗口真实缺陷）**：后端 `Literal` / `enum` 字段
    **应尽量给默认值**；不给的话（如契约层 `Message.role`），前端
    `schemaForm.ts::defaultValueOf` 会回落到**第一个合法候选值**，
    绝不产出空字符串；`validateParams` 对顶层与 object-array 子字段做
    **枚举合法性检查**。历史上 `role:''` 能骗过"必填非空"预检，
    在后端 Literal 处才失败，用户只看到"0/1 节点成功"。
18. **组件内的持久化逻辑抽成纯函数模块**（如 `runbar.ts`）：React 组件
    目前没有组件级测试，抽出来才能被 vitest 覆盖；交互（事件绑定）留在
    组件里，换算/约束/存储全部进纯模块。
19. **拖拽/指针交互必须走 Pointer Capture + ref 记值**：`pointermove` 里
    `setState` 是异步的，`pointerup` 读 state 会拿到旧值（曾会导致落盘
    高度差一截）；拖拽期间给 `body` 加类统一光标并禁选文字，卸载时兜底清理。

### 快捷键约定（frontend/src/hooks/useHotkeys.ts）

`Ctrl+Z` 撤销 / `Ctrl+Y`·`Ctrl+Shift+Z` 重做 / `Ctrl+D` 复制选中 / `Ctrl+A` 全选 /
`Ctrl+S` 导出 / `Ctrl+Enter` 运行 / `Delete`·`Backspace` 删除 / `Esc` 取消选择。
**输入框内不拦截**（`isEditableTarget`），否则编辑参数会误撤销画布。

---

## [已完成且验证的模块]

**验证基线（开工前先跑，必须全绿）**：

```bash
cd backend && python -m pytest -q      # 296 passed
cd frontend && npm test                # 126 passed
cd frontend && npx tsc --noEmit         # 无输出
cd frontend && npx vite build           # 成功
```

- 突变测试已证明套件有效性（注入"容器↔标量放行"突变 → 5 项立即失败）
- 前端测试构成：`schemaForm.test.ts` 22 + `storage.test.ts` 16 +
  `palette.test.ts` 4 + `runbar.test.ts` 8

| 层 | 状态 |
|---|---|
| 契约层 base.py（含 get_docs/control_flow） | ✅ 296 项测试含专项 |
| 扫描器 scanner.py（热更新/故障隔离/契约唯一） | ✅（热更新≠重载模块，见中断点债务） |
| 10 个业务插件（含 branch/loop 控制流） | ✅ 端到端 HTTP 实测通过 |
| **llm_chat v0.2.0（本窗口：厂商逃生舱）** | ✅ 17 项专项测试 + 本地 mock 端到端 + **DeepSeek 真实接口验证** |
| 解析器 parser.py（环/拓扑/类型预检/循环区间） | ✅ |
| 引擎 engine.py（三级校验/失败传播/分支路由/循环展开） | ✅ |
| 数据总线 context.py（UUID 隔离/只读视图） | ✅ |
| errors.py / expr.py / loader.py | ✅ 专项齐备（expr 23 + errors 23 + loader 5） |
| API 层（/health /schema /validate /run + Swagger） | ✅ 18 项测试 + curl 实测 |
| **schemaForm.ts（本窗口：enum 默认值回落 + 枚举预检）** | ✅ 22 项测试含回归 |
| **runbar.ts + RunResultBar（本窗口：结果条可拖拽调高）** | ✅ 8 项测试 + CDP 真实拖拽实测（320→460px） |
| 前端全套（画布/拖放/快捷键/撤销重做/模板/参数记忆/说明面板/配色） | ✅ tsc + vitest(50) + build |
| start.bat / stop.bat / README_START.md | ✅ 真实运行验证（服务起来、端口检查、浏览器打开） |

**里程碑**：最小对话 AI 全链路已由用户真实验收——画布上单节点 `llm_chat`，
`api_base=https://api.deepseek.com` + `model=deepseek-flash`，真实回复与
`raw.choices[0].message.reasoning_content`（思考链）均完整拿到。

控制流 HTTP 实测记录：branch 走 `on_true` 时 false 分支节点标记 skipped；
`loop_start(iterations=2)` 循环体跑 2 轮、`loop_end.iterations_done=2`；
未配对 loop_start → 解析期 `LOOP_UNPAIRED` 拒绝执行；危险表达式 → "禁止的语法：Call"。

### 本窗口 UI 修复记录（均含根因，勿重复踩）

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| 1 | Ctrl+滚轮无法缩放网页 | React Flow 对 wheel 统一 `preventDefault()`，浏览器整页缩放被吞；画布又卡在 0.5~2 的缩放上下限，到边界毫无反馈 | `hooks/usePageZoom.ts`：window **捕获阶段**对带 Ctrl/Meta 的 wheel `stopPropagation`（**不** `preventDefault`） |
| 2 | 滚动条是白色亮条 | 只设了背景色，原生滚动条沿用浅色配色 | `styles.css` 顶部：`:root{color-scheme:dark}` + `scrollbar-color`(Firefox) + `::-webkit-scrollbar-*`(Chromium) |
| 3 | 端点掉到文字上方、被裁一半 | `FieldHandle` 内联 `position:relative; top:auto; transform:none`，端点变块级元素；卡片 `overflow:hidden` 又裁掉探出的半边 | 删内联 style 恢复 `absolute + translate(±50%,-50%)`；`.field{position:relative}` 以"字段行"为基准居中；卡片 `overflow:visible` |
| 4 | 卡片宽度全等、字段名截断 | `width:210px` 写死 + `white-space:nowrap` + ellipsis | `width:max-content` + min/max-width；字段 `white-space:normal` + `overflow-wrap:break-word`；说明文字 `width:0;min-width:100%` 只换行不撑宽；两列 `flex:1 1 auto` |
| 5 | 展开说明后「收不回去」 | 卡片 219→360 向右长，`space-between` 把 × 顶到最右，按钮被 `.react-flow{overflow:hidden}` 裁掉或滑到参数面板底下，点击打在别处 | 开关移到标题左侧（`justify-content:flex-start`），卡片变宽不再移动它 |
| 6 | 同类节点难区分 | 所有卡片同一描边色 | `palette.ts` + `data.accent`；边框走 `--node-accent`；参数面板「节点外观」9 色 + 自定义取色；错误/成功态仍优先覆盖 |
| 7 | 说明面板里滚轮缩放画布 | React Flow 统一 `preventDefault()` | `.node-docs` 加 `nowheel`（`noWheelClassName`），事件落到类内即放行 |
| 8 | 厂商参数"填了但不生效"（本窗口） | `ChatInput` 未设 `extra="forbid"`，Pydantic 默认 ignore 静默丢弃未知顶层字段 | 顶层改 `forbid`；厂商参数统一走 `extra_body` / `extra_headers` / `drop_params`；17 项专项测试 |
| 9 | 新建 llm_chat 直接运行就"0/1 节点成功"（本窗口） | `Message.role` 是无默认值的 `Literal`，前端 `defaultValueOf` 对 string 产出 `''`；`''` 骗过"必填非空"预检，后端 Literal 拒绝 | `defaultValueOf` 枚举回落第一个候选值；`validateParams` 加顶层+子字段枚举检查（4 项回归测试） |
| 10 | 部分中转站拒绝 `temperature` / `max_tokens`（本窗口） | 原实现恒定下发 0.7 / 2048 | 两字段改可空、留空不下发；`drop_params` 兜底 |
| 11 | 模型 400 时看不到真实拒因（本窗口） | 错误信息只含状态码；部分中转站还用 HTTP 200 包 `{"error":{...}}` | HTTPError 附带响应正文（截 500 字）；200+error 对象主动抛出 `error.message` |

关键实测数值（CDP 在真实页面量，不是推算）：
- 端点圆心 Y = 字段行中心 Y（311 = 311）；卡片/说明/两列右边缘 1150 / 1148 / 1148
- 卡片宽度随内容变化：llm_chat 219 / context_compress 213 / edit_file 191（CSS px）
- 展开说明前后开关按钮 `x` 位移 **0**
- 面板内滚轮：`scrollTop` 0→260→520，画布 scale 恒为 1.019；标题栏滚轮 scale 1.019→1.462

### 服务与端口事实（本窗口踩过的坑）

- 后端真实路由是 `/health` `/schema` `/validate` `/run`，**不带** `/api` 前缀；
  `/api` 是前端 Vite 代理加的。直接 curl 后端请省略 `/api`。
- `npm run dev` 默认只监听 localhost，`127.0.0.1:5173` 可能连不上（curl exit 7）。
  需要双栈访问时用 `npm run dev -- --host 0.0.0.0`。
- **改了 Pydantic 契约（如给 `BlueprintNode` 加字段）必须重启 uvicorn**，否则老进程
  仍按旧模型校验，前端会收到莫名的 422。
- 编辑器写文件走"临时文件 + 重命名"（`.文件名.<pid>.<uuid>.tmpdir/文件名.tmp`），
  chokidar 会立即去 watch 这个转瞬即逝的临时文件，抛
  `EBUSY: resource busy or locked`；该异常在 Node FS 事件循环里无人接管，
  **会直接把 dev server 打挂**（本窗口复发两次，exit code 1）。
  已从根上处理：`frontend/vite.config.ts` 的 `server.watch.ignored` 忽略
  `**/*.tmpdir/**`、`**/*.tmp`、`**/.*.tmp`、`**/dist/**`。
  实测：连续制造/删除 3 组 tmpdir 后 dev server 仍存活，`hmr update` 正常触发。
  若仍复发（极端锁占用），再加 `server.watch.usePolling = true` 兜底。
- 后端重复启动会以 exit 3 + `winerror 10048`（端口占用）退出——那是**已有实例在跑**，
  不是启动失败；先查 `Get-NetTCPConnection -LocalPort 8000` 再决定是否重启。

---

## [当前中断点 (Breakpoint)]

**无未完成代码，无 TODO 残留**。最近一次改动是
`backend/plugins/tools/llm_chat.py`（版本升至 `0.2.0`），为厂商差异加上三个
显式逃生舱，并修正"静默丢弃未知参数"的缺陷：

- `extra_body`：厂商专有请求参数，合并到请求体**顶层**（与 OpenAI SDK 语义一致）。
  例：DeepSeek 的 `{"reasoning_effort":"high","thinking":{"type":"enabled"}}`。
- `extra_headers`：额外 HTTP 头，可覆盖 `Authorization`（中转站自定义鉴权）；
  `Content-Type` / `Content-Length` / `Host` 为协议保留头，禁止覆盖。
- `drop_params`：在 `extra_body` 合并**之后**移除字段，用于中转站不接受
  `temperature` / `max_tokens` 等参数的场景。
- `temperature` / `max_tokens` 改为可空，**留空即不下发**，由服务端默认值决定；
  `ChatInput` 设 `extra="forbid"`，拼错顶层参数会立即报错而不是静默忽略。
- 消息中未填写的 `name` 不再序列化为 `null`（严格中转站可能拒绝多余键）。
- 新增 17 项专项测试（透传/请求头/删除字段/保留键/未知参数/头注入/HTTP 错误正文/
  dry_run 字段清单），后端 **201 项全绿**；前端 126 项、`tsc --noEmit`、`vite build`
  四条基线全绿；`/validate` 与 `/run` 已用本地 mock 服务端到端验证
  （请求路径、Authorization、X-Title、字段增删均符合预期）。
- 响应侧厂商字段无需新契约：完整响应体仍在 `ChatOutput.raw`。

### 本窗口修复的第二个真实缺陷（"0/1 节点成功"）

- **现象**：新加一个 `llm_chat` 节点、什么都没改就直接点运行，页面显示
  "✗ 执行完成但有节点失败 · 0/1 节点成功"，总耗时 0.1ms。
- **根因**：`llm_chat.messages` 的元素是**契约层共享类型 `Message`**，它的
  `role` 是 `Literal[...]` 且**没有 Pydantic 默认值**。前端 `schemaForm.ts`
  的 `defaultValueOf` 看到 `type: 'string'` 就产出空字符串 `''`，
  于是新建节点的默认消息是 `{"role": "", "content": ""}`。
  后端 `Literal` 校验拒绝空字符串，报
  `Input should be 'system', 'user', 'assistant' or 'tool'`。
  而前端本地预检只判断"必填且为空"，`''` 非空，所以**前端认为一切正常**，
  按钮不禁用、卡片不标红，用户完全看不到原因。
- **修复**（前端，两处）：
  1. `defaultValueOf`：枚举字段（含共享类型子字段）一律回落到**第一个合法候选值**
     （`role` → `system`），不再产出空字符串；
  2. `validateParams`：新增顶层与 `object-array` 子字段的**枚举合法性检查**，
     即使值非空、只要不在候选列表内就就地提示
     （`Role 必须是以下之一：system / user / assistant / tool`）。
- **验证**：`schemaForm.test.ts` 新增 4 项回归测试（含"无默认值 enum 不得产出 ''"）；
  前端 126 项全绿；用修复后的默认参数实测 `/run` 成功进入 dry_run。
- **红线备忘**：后端 `Literal` / `enum` 字段**必须给默认值**，否则前端默认值
  推导会退回空字符串；契约层 `Message.role` 保持无默认值（语义上不该替用户选角色），
  因此这个兜底必须由前端承担。

### 本窗口新增：执行结果条可拖拽调高

- **需求**：底部「执行成功」结果栏内容较长，固定 320px 上限不够用，希望鼠标拖拽拉长。
- **实现**：新增 `frontend/src/runbar.ts`（纯逻辑）+ `RunResultBar` 交互改造：
  - 展开后顶部 9px 拖拽条，向上拖变高、向下拖变矮；
  - 最小 96px、最大视口 85%，窗口缩小自动收敛；
  - 松手写入 `localStorage["vmaf.ui.runbarHeight.v1"]`（拖拽过程中不写盘）；
  - 支持 `↑`/`↓` 键盘调整（Shift 大步长）与双击复位 320px；
  - 拖拽用 Pointer Capture，拖到画布上也不丢事件；拖拽期间统一光标并禁选文字。
- **布局修正**：结果条改为 `flex-column`——标题行固定、内容区独立滚动，
  原来 `overflow:auto` 在整条上，滚动结果时标题行会被一起滚走。
- **验证**：`runbar.test.ts` 8 项（约束/持久化/脏数据降级）；前端 126 项全绿；
  另用 headless Chrome + CDP 真实拖拽实测：高度 320 → 460px，落盘值 `460`，
  `is-resizing-runbar` 正确清理（截图 `docs/assets/runbar-resize.png`）。

**当前状态（本窗口封存时点）**：后端（8000，已加载 `llm_chat 0.2.0`）与前端
（5173）均已重启在运行，四条基线全绿（后端 201 / 前端 50 / tsc / build）。
注意：Vite 5 默认只监听 localhost，`http://localhost:5173` 可访问而
`http://127.0.0.1:5173` 可能 curl 不通（exit 7）——浏览器用 localhost 即可。
**代码现场已收尾**：所有文件语法闭合、无未完成逻辑、无 TODO 残留——
最后一个功能"执行结果条可拖拽调高"已完成并验证（CDP 实测 320→460px，
截图 `docs/assets/runbar-resize.png`），无半成品。

**本窗口三项产出（全部验证通过）**：
1. `llm_chat` v0.2.0 厂商适配逃生舱（`extra_body` / `extra_headers` /
   `drop_params`），含 17 项专项测试；**DeepSeek 真实接口已由用户验收**
   （`reasoning_content` 完整保留在 `ChatOutput.raw`）。
2. 前端 enum 默认值回落 + 枚举预检（修复"0/1 节点成功"真实缺陷，4 项回归）。
3. 执行结果条可拖拽调高（`runbar.ts` + `RunResultBar`，8 项测试 + CDP 实测）。

**发现的必须优先处理的新债务**：扫描器的"热更新"只更新文件指纹与注册表，
但 `load_module_once` 按模块名复用 `sys.modules` 缓存，**不会重新执行改过的
插件文件**。因此修改插件后必须重启后端（`README_START.md` 已改为如实说明）。
修复方向：给 `loader.py` 增加受控的 `reload_module(name, path)`，仅由扫描器在
指纹变化时调用，并补"改版本号后注册表版本确实变化"的回归测试。
**→ 本债务已在本窗口修复，见下节「本窗口产出：插件热更新真正生效」。**

**已知技术债务（按优先级）**：~~插件热更新未真正重载模块~~（已修复）→
~~统一日志~~（已修复，`logger.py` + 6 项专项）→
~~`errors.py` 缺专项测试~~（已补 23 项）→ ~~连线时类型预判提示~~（已完成：connection.ts + 24 项测试）→
~~循环嵌套支持~~（已完成：栈配对 + 递归展开）→ ~~前端组件级测试~~（已完成 25 项）→
~~蓝图持久化到后端~~（已完成）→ ~~模板引用式同步~~（已完成：指纹 + 一键同步）→
~~服务鉴权~~（已完成：VMAF_API_KEY，默认关闭）→ ~~RAG 节点~~（已完成：rag_search）。

### 本窗口产出：插件热更新真正生效（决定 20，已验证）

- **修复**：
  1. `utils/loader.py` 新增 `reload_module(name, path)`：摘除 `sys.modules`
     旧缓存后重新执行文件；`_NON_RELOADABLE_MODULES` 护栏禁止重载契约层
     与核心区（对 `vmaf.plugins.base` 直接 `ValueError`，先于任何文件操作）；
  2. 重载前清除该文件的 `__pycache__/*.pyc`——**真实踩坑**：`.pyc` 头部的
     源 mtime 只有秒级精度，同一秒内"改文件→重载"会命中旧字节码，
     表现为重载后仍是旧代码（回归测试断言 version 仍旧值才暴露）；
  3. `plugins/scanner.py::_discover_node_classes`：模块已执行过且指纹变化时
     改走 `reload_module`，首次加载仍走 `load_module_once`（零开销不变）；
  4. 重载失败摘除登记、扫描器故障隔离；此时节点暂时注销是预期行为，
     文件恢复后下次扫描自动重新注册。
- **测试**：`test_scanner.py` 新增 5 项（version 双向变化 0.2.0↔0.2.1 /
  缓存摘除重执行 / 失败不留痕 / 护栏拒绝受保护模块 / 坏代码隔离后恢复）；
  后端 **206 项全绿**。
- **效果**：修改插件后无需重启后端，触发 `scanner.scan()` 即生效
  （API 层每次请求会扫描；若接文件监听可后续再接 `rescan_file`）。

### 本窗口产出 2：model_profile 模型参数档案节点（用户新诉求）

- **需求（用户原话）**："写一个用来保存接入模型参数的模块，把它接到 chat
  模块上就能直接用，而不用在 chat 里面填参数。"
- **实现**：新增 `backend/plugins/tools/model_profile.py`（node_type
  `model_profile`，v0.1.0）：
  - 输入契约即"要沉淀的配置"：`api_base` / `api_key` / `model` /
    `system_prompt` / `temperature` / `max_tokens` / `timeout_seconds` /
    `extra_body` / `extra_headers` / `drop_params`，语义与 `ChatInput` 一致；
  - **输出字段与 `llm_chat.ChatInput` 的同名输入字段逐一同名同型**（测试
    `test_profile_output_fields_align_with_chat_input` 强制校验），因此
    `profile.xxx -> chat.xxx` 连线全部通过解析器类型预检；
  - 引擎规则"连线值优先于静态参数"（`engine.py::_execute_node`），所以
    接线即生效，chat 上未连线的字段仍用自己的默认值——可以只接需要的
    几条线（如只接 api_base + api_key）；
  - 密钥只随执行总线在本蓝图内流转；输出附 `mask`（打码摘要，如
    `sk-***7890`，短密钥整体打码）与 `ready`（是否已配置凭据）；
  - 输入侧 `model_validator` 提前拦截空请求头名与换行符注入。
- **测试**：`tests/test_model_profile.py` 新增 8 项（契约对齐 / 全连线
  预检通过 / 打码 / 短密钥 / 透传 / 坏头拦截 / 端到端 dry_run /
  未连线回归）；后端 **214 项全绿**；注册表计数类测试同步 10→11。
- **验证**：`/run` HTTP 端到端实测（profile 填 api_base/model/temperature/
  extra_headers → 连 5 条线到 llm_chat）→ `ok: true`，chat 输出
  `model: verify-model`（来自档案而非默认值），无密钥正确进入 dry_run。

### 本窗口产出 3：统一日志 logger.py（Windows 中文乱码修复）

- **问题**：Windows 控制台编码常为 GBK，`StreamHandler` 跟随流编码，
  中文日志乱码（`扫描 10 个文件` → `æ«æ…`）。
- **实现**：新增 `backend/utils/logger.py`：
  - `setup_logging(level=INFO, *, force=False, stream=None)`：安装显式
    UTF-8 的 StreamHandler；首选 `sys.stderr.reconfigure(encoding="utf-8")`
    （print/uvicorn 同步受益），流不可重配时降级包 UTF-8 `TextIOWrapper`；
  - 幂等保护 `_LOGGING_READY`：仅在进程入口调用一次（`main.py` 模块级
    已调用，替换原 `logging.basicConfig`），`force=True` 供测试重装；
  - 重装时清掉 root 旧 handler，避免与 uvicorn 预挂的 handler 双份输出。
- **测试**：`tests/test_logger.py` 新增 6 项（GBK 流写出合法 UTF-8 /
  格式 / 幂等 / force 重装清旧 / 调级生效 / 不可重配流的降级包装）；
  `pytest.ini` 新增 `logger` marker；后端 **220 项全绿**。
- **验证**：重启后端后 `backend-uvicorn.log` 启动日志中文完整无乱码；
  确认 `/health` 响应本身一直是正确 UTF-8（PowerShell 显示乱码是控制台
  编码问题，非服务端问题——`RawContentStream` 按 UTF-8 字节解码即正常）。

### 本窗口产出 4：errors.py 专项测试（技术债清偿）

- **范围**：新增 `tests/test_errors.py`（23 项），此前该模块仅有间接覆盖：
  - 原生异常归类：`_NATIVE_MAP` 六类逐一参数化 + 类名关键字识别
    （`URLError` / `HTTPError` / `*Timeout`）+ unknown 兜底不炸；
  - 语义确认（含新发现）：**超时关键字判定优先于网络关键字**
    （`ConnectTimeout` → `runtime.timeout` 而非 network_error），
    已按实际行为固化进测试并记录到 MODULE_INDEX；
  - `VmafError.to_detail` 全字段落地；调用方上下文**只填空缺不覆盖**；
  - `ISSUE_CODE_MAP` 核心短码翻译完整性；`to_error_list` 批量转换。
- **基线**：后端 **243 项全绿**（220+23）。

### 本窗口产出 5：连线类型预判提示（前端，根除"跑起来才发现接错"）

- **问题**：类型预检只在后端 `/validate`（400ms 防抖）与执行前发生，
  用户连错线后往往点了「运行」才看到"0/1 节点成功"，不知错在哪。
- **实现**：
  1. 新增 `frontend/src/connection.ts`——**纯函数模块**，把后端
     `parser.py::_type_verdict` 的判定规则复刻到前端：`$ref` 按模型名做
     名义判定、`anyOf`（Optional）按联合类型处理、列表递归元素、数值
     `integer→number` 放宽、`unknown` 判不出即放行；
  2. `App.tsx::onConnect` 松手即调用 `checkConnection`，不兼容时在画布
     底部弹出提示条（6 秒自动消失／可手动关闭），并写明两端字段名、
     类型名与原因，例如：
     `连线类型不兼容：model（文本(string)）→ max_tokens（整数(integer)）；文本(string) 不能作为 整数(integer) 使用`；
  3. 画布上该连线**当场变红虚线**（不必等 `/validate` 返回）；同时把原因
     并入**目标节点**的 `localErrors`，参数面板能就地看到。
- **刻意放宽的一处**：`Literal`/`enum` 在后端被判为"非标量"，但 JSON Schema
  里它与普通 string 无法区分，且运行时其值确实能通过 `str` 校验，故前端
  按基础类型处理（后端仍是唯一权威，`/validate` 结果覆盖本地判断）。
- **测试**：新增 `connection.test.ts` **24 项**（descriptorOf 归一化 /
  后端判定矩阵逐条对齐 / unknown 放行 / 字段级提示文案 / 缺失字段不误报）；
  前端 **74 项全绿**，`tsc --noEmit` 无输出，`vite build` 成功。
- **不阻断连线**：预判只提示不拦截（删改成本高于误连成本），用户仍可
  保留连线或删除；真正拦截执行的是后端校验。

### 本窗口产出 6：循环嵌套支持（解析器 + 引擎）

- **背景**：此前 `LOOP_NESTED` 直接报"暂不支持循环嵌套"。本次实现完整支持。
- **解析器**（`parser.py::_detect_loops` 重写）：
  1. **栈式配对**：按拓扑序扫描，`loop_start` 压栈、`loop_end` 弹栈。
     压栈时栈顶即父循环 → 父子关系与深度天然得出；栈内剩余 = 无终点，
     无栈可弹 = 孤立终点，均报 `LOOP_UNPAIRED`；
  2. **连通性校验**：拓扑顺序相邻 ≠ 真的连通，终点必须从起点可达，
     否则报 `LOOP_UNPAIRED`（"之间没有连通路径"）；
  3. **直接循环体** = 完整循环体 − 各子循环的（体 + 终点）：
     子循环由运行时在嵌入的 `loop_start` 处递归展开；
  4. 泄漏检查只针对直接体，并放行"连向子循环内部"的合法接线；
  5. `LoopRegion` 新增 `parent_start_id` / `depth` / `children`。
- **引擎**（`engine.py`）：`_run_loop` 拆为递归的 `_run_region`：
  - 循环体内遇到嵌套 `loop_start` 时，先执行该节点、再递归执行子区间；
  - 新增 `_publish_loop_frame`：`loop.index` / `loop.item` 指向最内层，
    新增 `loop.indexes`（由外到内下标，如 `[1, 2]`）与 `loop.depth`；
  - 进入子循环前保存、返回后恢复 `loop.results` / `loop.stopped_early`，
    避免外层误读子循环聚合结果。
- **错误码补齐**：`LOOP_UNPAIRED` / `LOOP_NESTED` / `LOOP_LEAK` /
  `LOOP_AMBIGUOUS` 已加入 `ErrorCode` 与 `ISSUE_CODE_MAP`，
  `/validate` 不再退化为 `internal.unknown`（实测返回
  `blueprint.loop_unpaired`，category=validation）。
- **测试**：`test_control_flow.py` 把"嵌套必须报错"替换为 4 项新测试
  （内层随外层重跑 / 区间结构父子关系 / `loop.indexes` 栈 / 孤立 loop_end）；
  `test_errors.py` 的短码完整性断言加严（缺翻译即失败）。后端 **246 全绿**。
- **HTTP 实测**：`/validate` → valid、order=`['s1']`；`/run` → 外层
  `iterations_done=2`、内层 `iterations_done=3`，全部成功。

### 本窗口产出 7：前端组件级测试（补齐最后一块测试盲区）

- **背景**：此前只测纯函数模块，组件（参数面板/模块面板/节点卡片）
  没有任何渲染级测试，属"改了不报错但可能渲染崩"的盲区。
- **基建**：新增 devDeps `@testing-library/react` / `user-event` / `dom` /
  `jsdom`；`vite.config.ts` 改用 `vitest/config` 的 `defineConfig`，
  加 `test.environment: 'jsdom'` + `setupFiles: src/testSetup.ts`
  （每例后 `cleanup()`，防 DOM 泄漏）。纯逻辑测试在 jsdom 下照常通过，
  无需按文件切换环境。
- **测试**（25 项）：
  - `ParamPanel.test.tsx` 9 项：空态引导、Schema 渲染文本/数字/枚举、
    必填标记、**连线字段只读**、消息编辑器增删、历史恢复/清空、色板回调、
    输出字段说明；
  - `NodePalette.test.tsx` 9 项：数量与条目、加载态、空态、点击添加、
    键盘 Enter 添加、控制流 ◆ 标记、**拖拽写入约定 dataTransfer 键**、
    模板插入与删除互不干扰、模板拖拽；
  - `NodeCard.test.tsx` 7 项：标题/描述/端点、必填与连线后取消必填、
    `?` 开关回调、说明面板含 `nowheel`、错误态标红、控制流徽标、
    强调色 CSS 变量。
- **约束说明**：`NodeCard` 用 React Flow 的 `Handle`，测试包在
  `ReactFlowProvider` 中；jsdom 无法测布局，真实拖拽仍需浏览器实测。
- **基线**：后端 **246** / 前端 **126** / `tsc` / `build` 全绿。

### 本窗口产出 8：蓝图持久化到后端（换机器不丢蓝图）

- **问题**：蓝图只存在浏览器 localStorage 与手工导入/导出的 JSON 里，
  换机器、换浏览器或清缓存即丢失。
- **后端**：
  - 新增 `utils/blueprint_store.py`：一蓝图一文件
    （`backend/data/blueprints/<id>.json`）、**原子写入**
    （`.tmp` → `os.replace`，进程中断不留半截文件）、
    **id 围栏** `^[A-Za-z0-9_-]{1,64}$`（杜绝路径穿越）、
    损坏文件隔离（单文件解析失败只告警跳过）；
  - 新增 4 个接口：`GET/POST /blueprints`、`GET/DELETE /blueprints/{id}`；
    保存前用 `core.blueprint.Blueprint` 再校验一次（非法结构 422 不落盘）；
    `api/schemas.py` 新增 `BlueprintMeta` / 列表 / 详情 / 保存请求四个契约。
- **前端**：
  - `api.ts` 增加 4 个客户端函数；`types.ts` 增加对应类型；
  - 新增 `components/ServerBlueprints.tsx` 对话框：打开即刷新、空态引导、
    错误可见、加载进画布、删除后自动刷新、Esc/遮罩关闭、时间格式化；
  - `App.tsx` 抽出 `applyBlueprint()` 供"导入文件"与"服务端加载"共用；
    顶部新增「存服务端」「服务端蓝图」两个入口；已有服务端 id 时
    「存服务端」为**覆盖保存**；保存成功给出 4 秒绿色提示条。
- **测试**：存储层 18 项（CRUD/原子写入/覆盖/排序/损坏隔离/穿越防护）
  + 接口 6 项（全生命周期/覆盖/422/404/穿越/删除 404）
  + 对话框 10 项；后端 **296**、前端 **126** 全绿。
- **实测**：真实 HTTP 走完 保存 → 列表 → 读取 → 删除 → 404 全链路；
  `http://localhost:5173/api/blueprints` 经 Vite 代理返回 200。

### 本窗口产出 9：模板引用式同步（快照 → 引用）

- **问题**：`我的模块` 是快照——插入后修改模板对已插入实例毫无影响，
  用户只能删掉重插。
- **实现**（`frontend/src/templates.ts`，纯逻辑）：
  1. `templateHash(template)`：**内容指纹**，只看节点类型/标签/参数与连线
     端点，忽略 id 与位置；参数键序稳定（自实现 `stableStringify`），
     同一内容无论字段顺序都得到同一指纹；
  2. `templateUsage(nodes, templates)`：返回每个模板的
     `{total, stale}`（stale = 实例 `data.templateHash` 落后于最新指纹）；
  3. `syncTemplateParams(nodes, template)`：只把落后实例的 `params`
     **整体替换**（深拷贝）为模板值，保留位置/标签/颜色/连线；
     无变化时返回原数组（幂等）。
- **App 集成**：
  - 插入模板时在节点 `data` 上记录 `templateId/templateName/originId/
    templateHash`（**展示元数据，不进 params**，与 accent 同类）；
    `instantiateTemplate` 返回顺序与 `template.nodes` 一一对应（已写入其
    docstring，作为按 originId 关联的前提）；
  - 「组合为模块」遇到**同名模板**时询问覆盖：保留模板 id 与创建时间，
    旧实例随之变为"待同步"；
  - 左侧「我的模块」显示实例数，落后时出现 `⇅ 待同步 N` 按钮，
    点击即一键刷新参数并给出提示条。
- **取舍**：同步**只动参数**，不做结构同步——用户手工改过连线/删过节点后，
  实例与模板的对应关系无法可靠推断，强行覆盖会破坏用户意图。
- **测试**：`templates.test.ts` **13 项**（指纹稳定性/参数与连线敏感性/
  键序无关/实例统计/同步范围/旧节点兼容/深拷贝）；前端 **126 项全绿**，
  `tsc --noEmit` 无输出，`vite build` 成功；后端 **281** 不受影响。

### 本窗口产出 10：可选服务鉴权（VMAF_API_KEY）

- **问题**：此前无任何鉴权。默认只监听本机，但一旦 `--host 0.0.0.0` 暴露到
  局域网，任何人都能通过 `/run` 触发 `edit_file` 写文件，或通过
  `/blueprints` 读走含密钥的蓝图。
- **后端**（`api/security.py`）：
  - **默认关闭**：未设置 `VMAF_API_KEY` 时行为完全不变（单机零配置）；
  - 设置后要求 `X-API-Key`，用 `hmac.compare_digest` **常量时间比较**；
  - 只保护数据/执行接口，`/` `/health` `/docs` 等保持匿名便于探活；
  - 每次请求**现读环境变量**（改配置无需重启）；`OPTIONS` 预检放行；
    `app.state` 标记保证**安装幂等**；
  - 401 形状与其它错误一致（`detail.message`，说明头名与环境变量名）。
- **前端**：
  - `storage.ts` 新增 `loadApiKey/saveApiKey`（localStorage，读写失败静默降级）；
  - `api.ts` 有密钥时自动附加 `X-API-Key`；401 时提示"去顶部「密钥」填写"；
  - 顶部新增「密钥 / 密钥 ✓」按钮（`prompt` 输入，留空即清除）。
- **测试**：后端 `test_api_security.py` **11 项**（默认关闭/空白等同未配置/
  缺错密钥 401/前缀与多余空格不通过/正确与大小写头放行/匿名路径/
  预检/运行期改密钥/幂等安装）；前端存储 4 项。
- **实测**：临时 uvicorn（`VMAF_API_KEY=test-key-123`，端口 8011）：
  `/health` 200、`/schema` 无密钥与错密钥均 401、正确密钥 200、
  `/blueprints` 200（随后已关闭该临时实例）。

### 本窗口产出 11：RAG 本地检索节点（rag_search）

- **目标**：把"先检索、再回答"接进图里，且**不引入向量库/嵌入模型**这类
  重依赖（单机确定性优先）。
- **实现**（`plugins/tools/rag_search.py`，node_type `rag_search` v0.1.0）：
  - **切词**：英文/数字按单词（小写），CJK 用**字符二元组**（无需分词词典），
    `"可视化"` → `可视` / `视化`；
  - **分片**：先按 `chunk_size` 硬切，相邻片段保留 `overlap` 重叠，
    避免答案恰好被切断；空文档自动跳过但保留原下标；
  - **打分**：BM25（k1=1.5, b=0.75），IDF 平滑（`log(1 + …)`，不出现负分）；
  - **排序确定性**：分数相同按（文档下标, 片段下标）升序，同一输入必得同一顺序；
  - **降级语义**：查询为空 → 按文档顺序取前 K 段（"整段喂给模型"）；
    分数 <= 0 的片段一律丢弃，避免把噪音塞给模型；
  - **输出**：`results`（text/source_index/chunk_index/score）、
    `context`（带片段编号与来源的拼装文本，无命中时为空字符串）、
    `matched`、`corpus_chunks`、`query_terms`（便于排查为何没命中）。
- **接线**：`rag_search.context`（str）→ `llm_chat.system_prompt`
  （Optional[str]）类型预检通过，已用测试与 HTTP 实测双重确认。
- **测试**：`tests/test_rag_search.py` **15 项**（切词/分片重叠/相关性排序/
  top_k/min_score/空查询/空文档/context 拼装/确定性/契约对齐）；
  注册表计数同步 11→12 个节点；后端 **296 项全绿**。
- **HTTP 实测**：`/validate` valid；`/run` → rag 命中 1 片（score≈5.08，
  语料 3 片），chat 收到 2 条消息（rag 的 system + 用户），无密钥正确 dry_run。

---

## [新窗口的首个原子任务]

1. **先读本文档 + `docs/NAMING_CONVENTIONS.md`**（含开工检查清单与陷阱清单）。
2. **跑基线**：后端 **296** / 前端 **126** 必须全绿；若用户报了页面问题，先复现再改。
3. **首个原子任务（按用户当日诉求定）**：
   - **A. 流式执行进度**：`/run` 目前一次性返回；前端想看到"逐节点进度"
     需 SSE/WebSocket，属于体验升级（引擎本身已是串行，事件化成本可控）。
   - **B. `/schema` 支持按节点类型过滤**：前端只拉所需，降低首屏体积。
   - **C. 执行结果持久化**：把 `RunReport` 落盘，便于事后复现与对比。
   - **D. 用户若有新的功能/页面诉求**：以用户原话为准，按红线"先复现再改"。
