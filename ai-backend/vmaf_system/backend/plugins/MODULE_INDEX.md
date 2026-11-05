# 插件区文件索引（backend/plugins）

> 每新增或修改一个文件，同步更新本表。用途：快速回顾每个文件的职责与现状，
> 避免在多会话开发中丢失上下文。

## 契约层

| 文件 | 状态 | 职责 | 关键对外接口 |
|---|---|---|---|
| `base.py` | ✅ 已验证 | 系统"底层法律"。定义 Pydantic V2 契约体系与节点抽象基类 | `InputSchema`、`OutputSchema`、`Message`、`BaseNode`、`NodeParameterDescriptor` |
| `scanner.py` | ✅ 已验证 | 动态反射扫描器：按文件路径加载 `tools/*.py`，发现并注册 `BaseNode` 子类，导出 Schema 清单 | `PluginScanner`、`PluginRecord`、`ScanReport`、`PluginLoadError`、`get_default_scanner()` |

### base.py 详解

| 成员 | 类型 | 说明 |
|---|---|---|
| `InputSchema` | Pydantic 模型 | 输入占位契约，`extra="forbid"` 落实强类型防御 |
| `OutputSchema` | Pydantic 模型 | 输出占位契约 |
| `Message` | Pydantic 模型 | **跨节点共享消息类型**（契约层统一定义，见下方"架构决定 1"） |
| `BaseNode` | 抽象基类 | 元数据 `node_type`/`description`/`version`；强制绑定 `input_model`/`output_model` |
| `BaseNode.execute` | 异步抽象方法 | `async (inputs, context=None) -> BaseModel`，子类必须实现 |
| `BaseNode.get_schema()` | 类方法 | 导出 `{node_type, description, version, input, output}` |
| `BaseNode.validate_contract()` | 类方法 | 注册期自检：空 `node_type` → `ValueError`；非 BaseModel 绑定 → `TypeError` |
| `NodeParameterDescriptor` | Pydantic 模型 | 参数描述元信息载体（避免散落字典字面量） |

### scanner.py 详解

| 能力 | 说明 |
|---|---|
| 动态加载 | `importlib` 按文件路径加载，脱离 Python 包导入链 |
| 合成包链 | 预注册 `vmaf.plugins.base` / `vmaf.plugins.tools`，使插件内 `from ..base import ...` 可用 |
| 私有文件过滤 | 下划线开头（如 `_template.py`）被跳过，不参与扫描 |
| 抽象过滤 | 仅收录本模块内定义、非抽象、通过契约自检的 `BaseNode` 子类 |
| 冲突检测 | `node_type` 重复时抛 `PluginLoadError` |
| 故障隔离 | 单文件加载失败不影响其它插件，错误汇总进 `ScanReport.errors` |
| 热更新 | 依据文件指纹 `(mtime_ns, size)`，变更时调用 `loader.reload_module` **真正重新执行**改过的文件（决定 20）；未变更时命中缓存零开销 |
| 自动注销 | 文件被删除时自动注销其节点 |

## 业务插件（tools/）

| 文件 | node_type | 架构图位置 | 状态 | 功能要点 |
|---|---|---|---|---|
| `llm_chat.py` | `llm_chat` | **核心 / 对话** | ✅ 已验证 | 调用 OpenAI 协议模型；稳定核心 + `extra_body`/`extra_headers`/`drop_params` 适配厂商差异；无凭据时 dry_run；token 启发式估算；仅用标准库 urllib |
| `read_file.py` | `read_file` | 输入 / 读文件 | ✅ 已验证 | 读取本地文本；支持行区间切片；体积上限围栏；拒绝目录路径 |
| `http_fetch.py` | `http_fetch` | 输入 / 联网 | ✅ 已验证 | 抓取 http(s)；协议白名单；HTML→纯文本清洗；响应体积上限 |
| `regex_extract.py` | `regex_extract` | 增强 / 正则 | ✅ 已验证 | findall/search 双模式；捕获组与命名组；IGNORECASE/MULTILINE/DOTALL；结果条数上限 |
| `context_compress.py` | `context_compress` | 回路 / 上下文压缩 | ✅ 已验证 | 确定性策略：保留 system + 最近 N 轮，更早折叠为摘要；输出 token 估算 |
| `tool_use.py` | `tool_use` | 动作 / tool-use | ✅ 已验证 | 解析 ```json 块中的工具调用意图；工具名白名单校验；**自身不执行副作用** |
| `edit_file.py` | `edit_file` | 动作 / 改文件 | ✅ 已验证 | append/overwrite/replace/insert_line 四模式；路径围栏 + overwrite 显式开关 + dry_run |
| `model_profile.py` | `model_profile` | 输入 / 模型参数档案 | ✅ 已验证 | 沉淀某模型服务的地址/密钥/模型名/采样参数/厂商逃生舱；输出字段与 `llm_chat` 输入一一对应，连线即生效；密钥打码展示（`mask`）+ `ready` 标记 |
| `rag_search.py` | `rag_search` | 增强 / 本地检索 | ✅ 已验证 | BM25 本地检索（无外部依赖）；中文字符二元组切词；输出命中片段 + `context`，直连 `llm_chat.system_prompt` |
| `branch.py` | `branch` | **控制流 / 条件判断** | ✅ 已验证 | 按表达式把数据导向 on_true / on_false；引擎只激活命中分支 |
| `loop.py` | `loop_start` / `loop_end` | **控制流 / 循环** | ✅ 已验证 | 与 loop_end 配对，引擎把区间重复执行 N 次并汇总结果 |

### 控制流节点约定

节点通过类属性 `control_flow` 声明身份，插件本身仍是纯函数：

| 值 | 含义 | 引擎行为 |
|---|---|---|
| `None` | 普通数据节点 | 正常执行 |
| `"branch"` | 条件分支 | 读取输出 `route`，只保留对应出边 |
| `"loop_start"` | 循环起点 | 展开执行配对区间 N 轮 |
| `"loop_end"` | 循环终点 | 由引擎在迭代完成后填充聚合结果 |

### 说明文本要求（强制）

每个 `Field(...)` 的 `description` 必须写清：**是什么、什么格式、示例**，
不要只写"路径""数量"这类单词。测试会强制校验每个输入/输出字段都有说明：

- `test_control_flow.py::test_every_input_field_documented`
- `test_control_flow.py::test_every_output_field_documented`

### 尚未实现（架构图中留待扩展）

- `RAG`（检索增强）：架构图左侧增强位，待阶段三实现
- `其它...` / `...` 扩展位：由后续插件填补，扫描器已具备自动发现能力

## 架构决定记录

### 决定 1：共享消息类型上提至契约层

- **问题**：`context_compress` 的 `HistoryMessage` 与 `llm_chat` 的 `ChatMessage`
  字段结构完全相同，但 Pydantic 按**类身份**而非结构校验，导致两节点直连时报
  `Input should be a valid dictionary or instance of ChatMessage`。
- **决策**：在 `base.py` 定义统一的 `Message` 模型，各插件以别名复用
  （`ChatMessage = Message`、`HistoryMessage = Message`），不做下游类型放宽。
- **理由**：契约驱动优先于便利。引擎将来要在节点间搬运数据时，共享类型是必要条件；
  若各插件自造类型，图引擎的每一条连线都要写适配层。
- **影响**：`Message` 新增 `tool` 角色与可选 `name` 字段，为 tool-use 与多智能体预留。

### 决定 2：tool-use 与 edit_file 职责分离

- **问题**：工具调用解析与文件写入若合并，对话链路就会带上副作用。
- **决策**：`tool_use` 只解析并输出"该调用什么工具、参数是什么"；真正的写操作
  由独立的 `edit_file` 节点在下游执行。
- **理由**：守住"对话链路无副作用"的纯函数红线；动作节点可单独做权限围栏与审计。

### 决定 3：厂商差异用「显式逃生舱」，不做每厂商字段

- **问题**：OpenAI 兼容厂商（尤其中转站）的可选参数并不一致，例如 DeepSeek
  需要 `reasoning_effort` 与 `thinking`，部分中转站又拒绝 `temperature` /
  `max_tokens`。若为每家厂商写一套字段，前端与文档会被迫跟着发版；若允许
  顶层随意塞参数，Pydantic 默认 `extra="ignore"` 会**静默丢弃**拼错的字段，
  表现为"填了但没生效"，极难排查。
- **决策**：
  1. 核心字段（`messages` / `model`）由契约掌控，禁止覆盖；
  2. 厂商专有请求参数走 `extra_body`，语义与 OpenAI SDK 一致——合并到
     请求体**顶层**，不是发一个名为 `extra_body` 的键；
  3. 额外鉴权/追踪头走 `extra_headers`，允许覆盖 `Authorization`，但
     `Content-Type` / `Content-Length` / `Host` 为协议保留头；
  4. 中转站不接受的字段用 `drop_params` 显式移除，在 `extra_body` 合并
     **之后**执行；
  5. `ChatInput` 设 `extra="forbid"`，拼错顶层参数立即报错。
- **理由**：稳定核心覆盖 80% 的通用场景，逃生舱覆盖长尾差异；新增厂商
  无需改节点代码，也不需要维护"厂商表单"。纯请求开关（温度、seed、推理
  强度）适合透传；`tools` / `stream` 等请求+响应耦合能力不能只靠透传，
  仍需专门的节点支持。
- **影响**：`temperature` / `max_tokens` 改为可空，留空即不下发，由服务端
  默认值决定；`llm_chat` 版本升至 `0.2.0`。响应侧的厂商字段无需新契约，
  完整响应体已保存在 `ChatOutput.raw` 中。

### 决定 20：插件热更新 = 受控 reload_module（修复"假热更新"）

- **问题**：历史上的"热更新"只更新文件指纹与注册表，但
  `load_module_once` 按模块名复用 `sys.modules` 缓存，**不会重新执行**
  改过的插件文件——修改插件必须重启后端才能生效，热更新名存实亡。
- **决策**：在 `utils/loader.py` 新增 `reload_module(name, path)`，先摘除
  `sys.modules` 旧缓存再重新执行；仅允许扫描器在指纹变化时调用；
  用 `_NON_RELOADABLE_MODULES` 清单保护契约层与核心区模块（对
  `vmaf.plugins.base` 重载直接抛 `ValueError`）。
- **理由**：守住"契约层全进程唯一"红线的同时让插件修改即刻生效。
  重载失败摘除登记、由扫描器故障隔离，恢复文件后下次扫描自动重新注册。
- **实现细节**：重载前清除该文件的 `__pycache__` 字节码缓存——`.pyc`
  头部的源 mtime 只有秒级精度，同一秒内"改文件→重载"会命中旧字节码。
- **影响**：修改插件后调用 `scanner.scan()` 即生效，不再需要重启后端。
  回归测试 5 项（版本号双向变化 / 缓存摘除 / 失败不留痕 / 护栏拒绝 /
  坏代码隔离后恢复）。

## 已修复缺陷

| 缺陷 | 原因 | 修复 |
|---|---|---|
| `http_fetch.py` / `edit_file.py` 加载失败 | 把 `model_validator` 误写为 `BaseModel.model_validator`（属性不存在） | 改为 `from pydantic import model_validator` 并以装饰器使用 |
| 节点间消息类型不通 | 见"架构决定 1" | 共享 `Message` 类型 |
| 厂商参数"填了但不生效" | `ChatInput` 未设 `extra="forbid"`，Pydantic 默认静默忽略未知顶层字段 | 顶层改为 `forbid`；厂商参数统一走 `extra_body`，并补透传/保留键/删除字段的专项测试 |
| 部分中转站拒绝 `temperature` / `max_tokens` | 原实现恒定下发 0.7 / 2048 | 两个字段改为可空，留空不下发；另提供 `drop_params` 兜底 |
| 热更新"假生效"（决定 20） | `load_module_once` 复用 `sys.modules` 缓存，改过的插件文件从不重新执行 | 新增受控 `reload_module`，扫描器指纹变化时调用；清 `.pyc` 防同秒旧字节码 |
