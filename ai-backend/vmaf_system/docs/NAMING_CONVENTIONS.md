# VMAF 全局命名规范

> **本文件是给下一个接手的智能体看的。** 动手写代码前先读本文件与
> `project_status.md`。命名不只是风格问题——本文件里每一条
> **强制**级规范背后都对应一个真实踩过的坑，违反它会导致隐蔽的运行时失败。

---

## 0. 三句话速览

1. **模块必须按文件路径加载，且只能经由 `utils/loader.py` 的 `load_module_once`**。
2. **包名/模块名必须全局唯一且稳定**：`vmaf.plugins.base`、`vmaf.core.parser`。
3. **凡是跨节点传递的数据类型，必须定义在契约层 `base.py`**，插件以别名复用。

---

## 1. 模块名与包名【强制】

### 1.1 规范模块名表

动态加载时必须**按此表登记到 `sys.modules`**，不得自造名字：

| 文件 | 规范模块名 |
|---|---|
| `backend/plugins/base.py` | `vmaf.plugins.base` |
| `backend/plugins/scanner.py` | `vmaf.plugins.scanner` |
| `backend/plugins/tools/<name>.py` | `vmaf.plugins.tools.<name>` |
| `backend/core/blueprint.py` | `vmaf.core.blueprint` |
| `backend/core/parser.py` | `vmaf.core.parser` |
| `backend/core/context.py` | `vmaf.core.context` |
| `backend/core/engine.py` | `vmaf.core.engine` |
| `backend/utils/loader.py` | `vmaf.utils.loader` |
| `backend/utils/errors.py` | `vmaf.utils.errors` |
| `backend/api/routes.py` | `vmaf.api.routes` |
| `backend/api/schemas.py` | `vmaf.api.schemas` |
| `backend/main.py` | `vmaf.main` |

合成包：`vmaf`（根）→ `vmaf.plugins`、`vmaf.core`、`vmaf.utils`。

### 1.2 为什么这是强制的

Pydantic 按**类身份**（而非结构）做校验。若 `base.py` 在进程内被执行两次，
就会产生两个同名但不同身份的 `Message` / `BaseNode` 类。届时即使各插件都
"从契约层导入 Message"，A 节点产出的 Message 也**无法通过 B 节点的输入校验**，
跨节点连线会全部失败，且报错信息（`Input should be a valid dictionary or
instance of ChatMessage`）极具误导性，极难定位。

因此：

- **禁止**在任何地方直接调用 `importlib.util.spec_from_file_location`。
- **必须**统一走 `load_module_once(module_name, file_path)`。
- 模块名必须是**常量字符串**，不得动态拼接（拼接易产生别名，破坏复用）。

### 1.3 插件目录不设 `__init__.py`

`backend/plugins/tools/` 刻意不设 `__init__.py`，保持"独立脚本仓库"语义：
插件由扫描器按文件路径动态加载，不依赖 Python 包导入链。
这让用户往目录里丢一个脚本即可生效，无需维护包结构。

---

## 2. 插件与节点命名【强制】

### 2.1 文件名 → node_type 的映射

| 文件命名 | `snake_case`，且与 `node_type` **一致** | `read_file.py` → `read_file` |
|---|---|---|
| `node_type` | `snake_case`，全局唯一 | `llm_chat`、`context_compress` |
| 节点类名 | `PascalCase`，以 `Node` 结尾 | `ReadFileNode` |
| 输入模型 | `PascalCase`，以 `Input` 结尾 | `ReadFileInput` |
| 输出模型 | `PascalCase`，以 `Output` 结尾 | `ReadFileOutput` |
| 子模型 | `PascalCase`，描述性名词 | `RegexMatchItem`、`ToolCall` |

`node_type` 唯一性由扫描器在注册时强制检测（`node_type` 冲突 → `PluginLoadError`）。

### 2.2 私有文件前缀

以 `_` 开头的 `.py` 文件被扫描器跳过，用于：

- 共享的内部工具模块（如 `_helpers.py`）
- 模板文件（如 `_template.py`）
- 临时禁用的插件（改名为 `_xxx.py` 即下线，无需删除）

---

## 2.3 控制流节点【强制】

控制流节点仍是**纯函数插件**，通过类属性声明身份，调度由引擎负责：

```python
class BranchNode(BaseNode):
    node_type = "branch"
    control_flow = "branch"      # 声明控制流类型
```

| `control_flow` | 约定 |
|---|---|
| `None` | 普通数据节点（默认） |
| `"branch"` | 输出**必须**含 `route` 字段，取值 `"on_true"` / `"on_false"`；出边 handle 名必须与之一致 |
| `"loop_start"` | 必须与某个 `loop_end` 成对出现，且不得嵌套 |
| `"loop_end"` | 由引擎在迭代后执行，不要在其中写业务逻辑 |

**禁止**在插件内部实现循环或分支跳转（如自己 while 循环遍历下游），
那会绕过引擎的拓扑调度与状态隔离。控制流只有一处实现：引擎。

## 2.4 说明文本【强制】

每个 `Field(...)` 的 `description` 都要让人**看得懂、照着填**：

```
# ✅ 好：说清是什么、什么格式、给例子
path: str = Field(
    ...,
    description="要读取的文件路径。推荐填绝对路径，例如 D:/data/报告.txt；"
                "相对路径按后端进程的工作目录解析",
)

# ❌ 差：只有名词，使用者不知道填什么
path: str = Field(..., description="路径")
```

要求：**是什么 + 什么格式 + 一个示例**。涉及外部服务的字段还要说明
协议与地址形态（如"填服务根或带 /v1 均可"）、密钥为空时的行为。

这条由测试强制：`test_every_input_field_documented` /
`test_every_output_field_documented` 会检查**每个**输入/输出字段都有非空说明。

## 2.5 表达式字段

凡是接受表达式的字段（如 `branch.expression`），**必须**用
`utils/expr.py` 的 `evaluate()` 求值，禁止 `eval` / `exec`。
表达式里可用的变量必须在字段说明中列出（例如"`value` 代表该输入值"）。

## 3. 共享数据类型【强制】

**规则：凡需跨节点传递的数据类型，一律定义在契约层 `base.py`。**

插件内以别名复用，**不得各自定义结构相同的类**：

```python
# ✅ 正确：复用契约层类型
from ..base import BaseNode, Message
ChatMessage = Message      # 别名，二者是同一个类对象

# ❌ 错误：自定义结构相同的类型，会导致跨节点连线失败
class ChatMessage(BaseModel):
    role: str
    content: str
```

当前契约层唯一的共享类型是 `Message`（含 `role` / `content` / `name`）。
新增共享类型（例如未来的 `Document`、`Embedding`）同样必须放进 `base.py`。

**判定口诀**：如果类型 X 会出现在某个节点的 `output_model` 且同时出现在
另一个节点的 `input_model`，那 X 就属于契约层。

---

## 4. 插件内导入契约层的写法

插件脚本内使用相对导入，由扫描器预建的合成包链支撑：

```python
from ..base import BaseNode, Message      # ✅ 推荐
from vmaf.plugins.base import BaseNode    # ✅ 兼容
from base import BaseNode                # ✅ 兼容（旧写法）
```

**禁止** 使用 `sys.path` 拼接后 `import base`，那会绕过加载权威。

---

## 5. 代码内命名

### 5.1 Python

| 对象 | 风格 | 示例 |
|---|---|---|
| 模块/包 | `snake_case` | `context_compress.py` |
| 类 | `PascalCase` | `ExecutionPlan` |
| 函数/方法/变量 | `snake_case` | `get_all_schemas` |
| 常量 | `UPPER_SNAKE_CASE` | `CONTRACT_MODULE_NAME` |
| 私有成员 | `_` 前缀 | `_type_verdict` |
| 类型变量 | `PascalCase` | `Type[BaseModel]` |
| 夹具（pytest） | 与被测类名同名或 `snake_case` | `BaseNode`、`scanner_module` |

### 5.2 私有辅助函数

模块内私有函数以 `_` 前缀，且**必须**写 Google 风格 Docstring
（含 Args / Returns / Raises），这是全工程的统一要求。

---

## 5.3 错误码命名【强制】

对外错误码一律 `<子系统>.<问题>`，定义在 `utils/errors.py` 的 `ErrorCode`：

| 前缀 | 用于 |
|---|---|
| `blueprint.*` | 蓝图解析期问题（cycle / type_mismatch / missing_required_input） |
| `plugin.*` | 插件注册期问题（load_failed / contract_broken / type_conflict） |
| `runtime.*` | 运行期问题（file_not_found / network_error / timeout …） |
| `internal.unknown` | 兜底 |

**重要**：解析器内部使用短码（`TYPE_MISMATCH`），对外必须翻译为长码
（`blueprint.type_mismatch`）。翻译映射 `ISSUE_CODE_MAP` 定义在
`utils/errors.py`，是**唯一权威**——`engine.py` 与 `api/routes.py`
都从该处取用，不得各自维护一份，否则前端要做双重映射。

新增错误码时：同步更新 `ErrorCode` 枚举、语义分类，以及必要时的
`ISSUE_CODE_MAP`。

## 5.4 HTTP 状态码约定

| 情形 | 状态码 |
|---|---|
| 节点业务失败 | **200**（错误汇总进 `error_details`） |
| 蓝图 JSON 结构非法 | 422 |
| 服务不可用 | 5xx |

理由：HTTP 状态码表达"请求是否被正确处理"。节点失败已被成功处理并
结构化返回，不是服务器错误。前端据此高亮节点与连线，而非弹无意义报错。

## 5.5 前端命名

| 对象 | 风格 | 示例 |
|---|---|---|
| 源文件 | `camelCase.ts` / 组件 `PascalCase.tsx` | `schemaForm.ts`、`NodeCard.tsx` |
| React 组件 | `PascalCase` | `NodePalette`、`ParamPanel` |
| 变量/函数 | `camelCase` | `parseSchema`、`coerceValue` |
| 类型/接口 | `PascalCase` | `NodeSchema`、`FieldSpec` |
| 常量 | `UPPER_SNAKE_CASE` | `OFFLINE_HINT` |
| CSS 类 | `kebab-case` | `node-card`、`palette-item` |
| 测试文件 | `<被测>.test.ts` | `schemaForm.test.ts` |

### 前端类型命名对照后端

前端 `src/types.ts` 与后端 Pydantic 契约一一对应，改后端契约时须同步：

| 后端 | 前端 |
|---|---|
| `BlueprintNode` | `BlueprintNode` |
| `BlueprintEdge` | `BlueprintEdge` |
| `ErrorDetail` | `ErrorDetail` |
| `ValidationIssue` | `ValidationIssue` |

注意大小写差异：后端 `node_type` / `source_handle`（snake_case，
由 Pydantic 决定）在前端也保持 snake_case，**不要转成驼峰**，
否则序列化对不上。

## 6. 文档与注释语言

- **所有 Docstring 与注释使用中文**，采用 Google 风格。
- 必须包含类型标注（Type Hints）。
- 模块顶部 Docstring 需说明：职责、设计红线、契约（输入输出类型）。
- 错误信息面向用户时也必须中文，且**必须说明具体原因**，
  不要只说"类型不匹配"，要说清是哪一端、什么类型、为什么。

```
# ✅ 好
连线类型不兼容：n1.matches（List[RegexMatchItem]）无法流入 n2.text（str）
（容器 List[RegexMatchItem] 不能直接作为标量 str 使用）

# ❌ 差
类型不匹配
```

---

## 7. 测试命名

| 对象 | 风格 | 示例 |
|---|---|---|
| 测试文件 | `test_<子系统>.py` | `test_parser.py` |
| 测试函数 | `test_<被测行为>` | `test_cycle_detected` |
| 回归测试 | 函数内注释说明缺陷背景 | 见 `test_types.py` |
| 夹具文件 | `conftest.py` | `tests/conftest.py` |

**回归测试必须写明缺陷背景**，否则后来者会以为那是冗余代码而删掉。例如：

```python
def test_optional_not_treated_as_container(parser_module):
    """回归测试：Optional 不得被误判为容器。

    缺陷背景：get_origin(Optional[str]) 返回 Union，若让容器分支先执行，
    Optional[str] 会被误当成容器，且 Union 分支永远不可达。
    """
```

### pytest 标记

标记定义在 `backend/pytest.ini`：`contract` / `scanner` / `parser` /
`engine` / `types` / `plugin`。按子系统筛选：

```bash
python -m pytest -m types       # 只跑类型判定
python -m pytest -m "not plugin"  # 排除插件测试
```

---

## 8. 状态文档维护

每完成一个文件，同步更新两处：

1. **所属目录的 `MODULE_INDEX.md`**：记录该文件的职责、接口、状态、待办。
2. **根目录 `project_status.md`**：跨会话的唯一进度真相源。

架构决定与已修复缺陷**必须连同原因一起记录**——只记结论不记原因，
下一个智能体会重蹈覆辙。

---

## 9. 常见陷阱清单

| 陷阱 | 后果 | 规避 |
|---|---|---|
| 各处自行 `importlib` 加载契约层 | 跨节点连线全部失败，且报错极具误导性 | 统一走 `load_module_once` |
| 插件各自定义结构相同的消息类 | 同上 | 共享类型放 `base.py` |
| 类型判定末尾写 `return True` 兜底 | 类型预检形同虚设 | 严格判定，见 `test_types.py` |
| Union 判定放在容器判定之后 | `Optional[T]` 被误判为容器 | Union 判定必须前置 |
| 递归跳过与线性主循环重复登记 | 节点被执行两次，结果重复 | 主循环前检查 skipped 标记 |
| 验证写成一次性临时脚本 | 验证逻辑随脚本删除而消失 | 常驻 `backend/tests/` |
| 对外暴露两套错误码 | 前端需做双重映射 | 统一从 `utils/errors.py` 的 `ISSUE_CODE_MAP` 取 |
| 直接返回引擎内部对象给前端 | 内部重构会破坏外部接口 | 经 `api/schemas.py` 包装 |
| 前端不解析 `$ref` | 共享类型退化成裸 JSON 框 | `schemaForm.ts` 的 `deref` 递归解引用 |
| 前端连线不做类型预判 | 连错线后点运行才报"0/1 节点成功"，用户不知错在哪 | `connection.ts` 松手即预判并提示（后端仍是唯一权威） |
| 前端把 `node_type` 转成驼峰 | 与后端序列化对不上 | 保持 snake_case |
| 把 `SchemaProperty.type` 设为必填 | `$ref` 字段没有 type，TS 报错 | `type` 必须可选 |
| 用 `eval` 求值条件表达式 | 任意代码执行漏洞 | 只用 `utils/expr.py` 的 AST 白名单 |
| 在插件内部自己写循环/跳转 | 绕过拓扑调度与状态隔离 | 用 `control_flow` 声明，交给引擎 |
| 快捷键不判断输入框焦点 | 编辑参数时误撤销/删节点 | `useHotkeys` 内 `isEditableTarget` 拦截 |

---

## 10. 后续智能体的开工检查清单

接手时依次确认：

1. 读 `project_status.md`，确认当前阶段与绝对焦点。
2. 读本文件，确认命名规范。
3. 跑后端测试：`cd backend && python -m pytest -q`（当前 **201 项**全绿）。
4. 跑前端测试：`cd frontend && npm test`（当前 **50 项**全绿）。
5. 动手改任何核心文件后重跑测试；改动类型判定逻辑时**必须**先跑
   `python -m pytest -m types`。
6. 完成后更新 `MODULE_INDEX.md` 与 `project_status.md`。

### 端到端联调验证

```bash
# 终端 1：后端
cd backend && python -m uvicorn main:app --port 8000

# 终端 2：前端
cd frontend && npm run dev

# 浏览器打开 http://127.0.0.1:5173
```

冒烟检查：页面显示"已连接 · 7 节点" → 点左侧卡片加节点 → 连线 → 点运行。
