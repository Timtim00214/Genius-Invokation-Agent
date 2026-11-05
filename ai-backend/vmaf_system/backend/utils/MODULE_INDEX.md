# 通用工具区文件索引（backend/utils）

> 日志、异常与模块加载辅助。每新增或修改一个文件，同步更新本表。

## 现状总览

| 文件 | 状态 | 职责 |
|---|---|---|
| `loader.py` | ✅ 已验证 | **进程级模块加载权威**：保证同一模块全进程只执行一次；受控 `reload_module` 支撑插件热更新 |
| `errors.py` | ✅ 已验证 | **异常结构化**：统一错误码、语义分类、可重试性、节点定位 |
| `expr.py` | ✅ 已验证 | **受限表达式求值器**：AST 白名单，供条件分支安全求值 |
| `logger.py` | ✅ 已验证 | **统一日志**：显式 UTF-8 StreamHandler，解决 Windows 控制台（GBK）中文乱码 |
| `blueprint_store.py` | ✅ 已验证 | **蓝图持久化**：一个蓝图一个 JSON 文件，原子写入 + id 围栏（防路径穿越） |

## loader.py — 进程级加载权威（关键基础设施）

| 函数 | 说明 |
|---|---|
| `load_module_once(name, path)` | 按文件路径加载，**同名模块只执行一次** |
| `reload_module(name, path)` | **受控重载**：摘除 `sys.modules` 缓存后重新执行（插件热更新底层） |
| `ensure_package(name, path, **attrs)` | 确保合成包存在并刷新 `__path__`（幂等） |
| `get_or_create_module(name)` | 获取或创建纯命名空间模块（如合成包根 `vmaf`） |

### 为什么必须有这个模块

Pydantic 按**类身份**（而非结构）做校验。若 `base.py` 在进程内被执行两次，
就会产生两个同名但不同身份的 `Message` / `BaseNode` 类。届时即使各插件都
"从契约层导入 Message"，A 节点产出的 Message 也无法通过 B 节点的输入校验——
因为它们是不同的类对象，跨节点连线会全部失败。

由于 VMAF 的插件与核心区都按文件路径动态加载（脱离包导入链），
不同区域各自加载同一文件极易触发上述问题。因此**全工程必须统一
通过本模块加载**，禁止各处自行调用 `importlib.util.spec_from_file_location`。

已验证：scanner 被独立加载 3 次，三次得到的 `BaseNode` 是同一对象，
跨 scanner 实例的 `context_compress → llm_chat` 直连成功。

### 设计要点

- **先登记后执行**：使模块内相对导入与类型解析可见。
- **失败不留痕**：执行抛异常时从 `sys.modules` 摘除，避免半初始化模块被复用。

### reload_module 的调用围栏（决定 20，不可回退）

`reload_module` 是**唯一**允许让已加载模块重新执行文件的入口，围栏如下：

1. **只允许** `plugins/scanner.py::_discover_node_classes` 在文件指纹变化时调用；
   其它任何路径禁止直接调用。
2. **绝不能**对契约层与核心区模块使用（`_NON_RELOADABLE_MODULES` 清单，
   含 `vmaf.plugins.base` / `vmaf.core.*` / `vmaf.utils.*` / `vmaf.main`）——
   Pydantic 按类身份校验，重载契约层会产生第二份 `Message` 类，
   跨节点连线全部失败。护栏抛 `ValueError`，先于任何文件操作生效。
3. 重载前会清除该文件在 `__pycache__` 下的 `.pyc`：字节码头部记录的源文件
   mtime 只有秒级精度，同一秒内"改文件→重载"会命中旧字节码
   （真实踩过：回归测试同秒改 version 断言仍为旧值）。
4. 重载失败同样"失败不留痕"，由扫描器转为 `PluginLoadError` 做故障隔离；
   此时节点的旧缓存已被摘除，注册表内该节点注销，文件恢复后下次
   扫描自动重新注册。

已验证：改 `llm_chat.py` 的 version（0.2.0→0.2.1）后 `scanner.scan()`
注册表版本立即变为 0.2.1，恢复文件后回到 0.2.0（`test_scanner.py`
`test_hot_reload_reexecutes_changed_code` 等新增 5 项回归）。

## blueprint_store.py — 蓝图持久化

| 成员 | 说明 |
|---|---|
| `BlueprintStore(root=None)` | 仓库本体；默认 `backend/data/blueprints` |
| `save(blueprint, *, name=None, blueprint_id=None)` | 新建或覆盖；返回元信息（id / name / saved_at / 节点与连线数） |
| `load(id)` / `delete(id)` / `list()` | 读取 / 删除 / 列表（按保存时间倒序） |
| `get_default_store()` | 进程级默认仓库单例 |
| `BlueprintNotFoundError` / `InvalidBlueprintIdError` | 404 / 400 语义的领域异常 |

设计要点：

- **一个蓝图一个文件**（`<id>.json`）：便于人工查看与备份，不引入数据库；
- **原子写入**：先写 `.json.tmp` 再 `os.replace`，进程中断不会留下半截 JSON；
- **id 围栏** `^[A-Za-z0-9_-]{1,64}$`：外部输入不可信，从根上杜绝 `../` 穿越；
- **损坏文件隔离**：单个 JSON 解析失败只告警跳过，列表照常返回。

API 层：`GET/POST /blueprints`、`GET/DELETE /blueprints/{id}`；
保存前用 `core.blueprint.Blueprint` 再校验一次（非法结构 422，不落盘）。
专项测试：存储 18 项 + 接口 6 项。

## errors.py — 异常结构化

| 成员 | 说明 |
|---|---|
| `ErrorCategory` | 语义分类，决定**前端该让用户做什么**；`retryable` 属性标识可否重试 |
| `ErrorCode` | `blueprint.*` / `plugin.*` / `runtime.*` 错误码枚举 |
| `ErrorDetail` | Pydantic 模型，可直接 JSON 序列化回传前端 |
| `VmafError` | 统一异常基类，携带节点/连线/字段定位 |
| `to_error_detail` | 任意原生异常 → 结构化详情 |
| `ISSUE_CODE_MAP` | 解析器短码 → 统一错误码的**唯一权威映射** |
| `issue_to_error_detail` | `ValidationIssue` → `ErrorDetail` |

### 语义分类表

| 分类 | 含义 | 可重试 |
|---|---|---|
| `validation` | 蓝图有问题，用户应改蓝图 | 否 |
| `not_found` | 资源不存在 | 否 |
| `permission` | 越权（如越出写入围栏） | 否 |
| `network` | 网络错误 | **是** |
| `timeout` | 超时 | **是** |
| `contract` | 契约破损（开发者错误） | 否 |
| `internal` | 未归类 | 否 |

### 为什么需要这一层

前端画布要在出错时**高亮是哪条连线/哪个节点**出了问题。若直接把 Python
原生异常文本（`FileNotFoundError: 文件不存在: D:\...\xyz.txt`）丢给前端，
它既无法定位节点，也无法做国际化，更无法区分"该改蓝图"还是"该重试"。

实测输出示例（节点失败）：

```json
{
  "code": "runtime.file_not_found",
  "category": "not_found",
  "retryable": false,
  "node_id": "bad",
  "node_type": "read_file",
  "message": "文件不存在: D:\\不存在的文件\\xyz.txt"
}
```

实测输出示例（连线类型错误，前端据此标红连线）：

```json
{
  "code": "blueprint.type_mismatch",
  "level": "error",
  "edge_id": "e_bad",
  "node_id": "b",
  "field": "text",
  "message": "连线类型不兼容：a.matches（List[RegexMatchItem]）无法流入 b.text（str）…"
}
```

### 原生异常归类

`_NATIVE_MAP` 覆盖 `FileNotFoundError` / `IsADirectoryError` /
`PermissionError` / `TimeoutError` / `ValueError` / `TypeError`；
另按类名关键字识别 `URLError` / `HTTPError` / `*Timeout` 等网络与超时异常。
**超时关键字判定优先于网络关键字**（`ConnectTimeout` → timeout，对用户
更有用）。专项测试 23 项（`tests/test_errors.py`）：归类 / retryable 语义 /
unknown 兜底 / VmafError 上下文填充优先级 / ISSUE_CODE_MAP 翻译 / 批量转换。

## logger.py — 统一日志（Windows 乱码修复）

| 成员 | 说明 |
|---|---|
| `setup_logging(level=INFO, *, force=False, stream=None)` | 安装显式 UTF-8 的 StreamHandler（幂等，`_LOGGING_READY` 保护） |
| `LOG_FORMAT` | 全工程统一格式：`时间 [级别] 名字: 消息` |

### 为什么必须有这个模块

Windows 控制台编码常为 GBK（cp936），`StreamHandler` 默认跟随流的编码，
中文日志会乱码（`扫描 10 个文件` → `æ«æ 10 ä¸ªæä»¶`）。

修复策略分两级：

1. **首选重配**：`sys.stderr` 是 `TextIOWrapper` 时直接
   `reconfigure(encoding="utf-8")`——print、uvicorn 输出同步受益；
2. **降级包装**：流不可重配（如 pytest 的 `EncodedFile`）时包一层
   UTF-8 `TextIOWrapper` 再交给 handler。

调用红线：**仅在进程入口调用一次**（`main.py` 模块级已调用），
其余代码禁止重复调用；测试通过 `force=True` 重装。

已验证：重启后端后启动日志中文完整（`backend-uvicorn.log`）；
`/health` 响应本身一直是正确 UTF-8（PowerShell 里看到的乱码是
控制台显示问题，`RawContentStream` 按字节解码即正常）。
专项测试 6 项（GBK 流写出合法 UTF-8 / 格式 / 幂等 / force 重装 /
调级 / 降级包装）。

## expr.py — 受限表达式求值器

条件分支需要在画布上写 `value > 5` 这样的判断。**绝不能直接用 `eval`**：
蓝图来自前端画布，属不可信输入，`eval` 等于把任意代码执行权交给用户。

本模块用 `ast` 白名单实现：

| 允许 | 禁止 |
|---|---|
| 常量、变量名、比较、布尔运算、算术、`not`、字面量列表/字典 | 函数调用、属性访问、下标、推导式、lambda、赋值、导入 |

变量只能取自调用方显式传入的 `variables`；引用未定义变量会报错并列出可用变量。

```python
>>> evaluate("count > 5 and status == 'ok'", {"count": 8, "status": "ok"})
True
```

安全边界已验证：`__import__('os')`、`open(...)`、`value.__class__`、
`value[0]`、推导式等全部被拒绝（`tests/test_expr.py`，23 项）。

## 待办

- [x] `logger.py`：统一日志格式与 UTF-8 输出（已完成，6 项专项测试）
- [x] 补充 `loader.py` / `errors.py` 的专项测试（loader 5 项 + errors 23 项）
- [ ] 错误码国际化文案表（前端可按 code 取本地化文案）
