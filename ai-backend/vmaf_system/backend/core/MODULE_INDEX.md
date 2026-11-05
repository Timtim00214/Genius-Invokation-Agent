# 核心区文件索引（backend/core）

> 图解析与执行引擎调度。每新增或修改一个文件，同步更新本表。

## 现状总览

| 文件 | 状态 | 职责 |
|---|---|---|
| `blueprint.py` | ✅ 已验证 | 蓝图数据契约：前后端之间唯一的"法律"，解析期拦截非法输入 |
| `parser.py` | ✅ 已验证 | 蓝图解析器：环检测 + 拓扑排序 + **连线类型静态预检** |
| `context.py` | ✅ 已验证 | 内存数据总线（Context Bus）：以节点 UUID 隔离各节点输出 |
| `engine.py` | ✅ 已验证 | 串行执行器：按拓扑序单线程异步执行，三级校验 + 失败传播 |

## blueprint.py — 蓝图契约

| 成员 | 说明 |
|---|---|
| `BlueprintNode` | 单个节点：`id` / `type` / `params` / `label` / `position` / `accent` |
| `BlueprintEdge` | 单条连线：`source.source_handle → target.target_handle` |
| `Blueprint` | 完整蓝图，含结构自检与查询方法 |

结构自检（Pydantic `model_validator`）拦截：重复节点 id、重复连线 id、
悬空连线（端点不存在）、自环。**不检测**环（那是拓扑排序的职责）。

`position` 与 `accent` 都是**纯展示元数据**：引擎从不读取，解析器也不参与校验，
仅用于前端还原布局与配色。二者都随蓝图导出/导入，保证"所见即所存"。

## parser.py — 解析器

产物 `ExecutionPlan`：`order`（执行顺序）、`resolved_nodes`、`issues`、
`entry_nodes` / `exit_nodes`，通过 `.valid` 判定能否执行。

错误码一览：

| 错误码 | 级别 | 触发条件 |
|---|---|---|
| `UNKNOWN_NODE_TYPE` | error | 节点类型未在插件仓库注册 |
| `UNKNOWN_OUTPUT_FIELD` | error | 源节点输出模型中无该字段 |
| `UNKNOWN_INPUT_FIELD` | error | 目标节点输入模型中无该字段 |
| `TYPE_MISMATCH` | error | 连线两端类型不兼容（含具体原因） |
| `CYCLE` | error | 蓝图有环，无法拓扑排序 |
| `MISSING_REQUIRED_INPUT` | error | 必填输入既无连线也无静态参数 |
| `NULLABLE_FLOW` | warning | 源值可能为 None，接入必填目标时运行期才会失败 |
| `LOOP_UNPAIRED` | error | loop_start 找不到 loop_end，或孤立 loop_end、或两端无连通路径 |
| `LOOP_AMBIGUOUS` | error | 保留错误码（交叉/歧义场景兜底），当前栈配对下不再产生 |
| `LOOP_NESTED` | error | 嵌套区间交叉（A 开、B 开、A 关、B 关）；完全包含的嵌套是**合法**的 |
| `LOOP_LEAK` | error | 直接循环体节点绕过本循环的 loop_end 直连循环外节点 |

### 循环区间识别（支持嵌套）

`_detect_loops()` 分三步静态计算区间：

1. **栈式配对**：按拓扑序扫描，`loop_start` 压栈、`loop_end` 弹栈配对。
   压栈时栈顶即父循环，天然得到父子关系与嵌套深度；
   剩余在栈内的是无终点者，没配上栈的是孤立终点，均报 `LOOP_UNPAIRED`。
2. **逐区间校验**：终点必须从起点可达（拓扑相邻 ≠ 连通）；
   完整循环体 = 起点可达 ∩ 能到达终点；完整体内出现的子循环要求其终点
   也在体内，否则判为交叉嵌套 `LOOP_NESTED`。
3. **直接循环体**：完整体减去各子循环的（体 + 终点）——子循环由运行时
   在嵌入的 `loop_start` 处递归展开；泄漏检查只针对直接体，
   允许连向子循环内部（那是子循环自己的接线）。

`_collapse_loops()` 把所有区间的直接体与 loop_end 从 `order` 摘除，
主序列只留 `loop_start`（含外层和顶层），循环展开交给引擎。

**摘除而非重排的原因**：保持主序列是"扁平的拓扑序"，
循环的展开语义交给引擎，解析器只负责静态结构校验。

示例结构：`loop(s1 -> [a, s2] -> e1)` 内嵌 `loop(s2 -> [b] -> e2)`。

### 类型兼容判定（`_type_verdict`）

严格判定，**不对无法判定的情况默认放行**（早期实现有此缺陷，见下）。

| 规则 | 结论 |
|---|---|
| 任一端是 `Any` / 注解缺失 | 兼容（通配） |
| 注解完全相同 | 兼容 |
| 同为容器族 | 递归比较内部类型参数 |
| 不同容器族（`Dict` → `List`） | **不兼容** |
| 容器 ↔ 标量 | **不兼容** |
| 源为 Union：逐成员判定；含 `None` | 兼容但降级为 `nullable` 警告 |
| 目标为 Union：匹配任一成员 | 兼容 |
| 标量：相等，或 `int → float` | 兼容 |
| 标量：其余（含 `float → int` 收窄） | **不兼容** |

## context.py — 数据总线

| 成员 | 说明 |
|---|---|
| `NodeResult` | 单节点结果：输出、耗时、上游注入值、跳过/失败状态 |
| `ExecutionContext` | 总线本体，UUID 键控 |
| `readonly_view()` | 给插件的**只读快照**，插件无法回写总线 |
| `snapshot()` / `successful` / `failed` | 结果查询 |

## engine.py — 执行器

执行流程：解析 → 计划无效则拒绝执行 → 按拓扑序逐个节点：
`收集上游值 + 静态参数 → input_model 校验 → async execute → output_model 校验 → 存入总线`。

**三级校验**：输入校验（连线值优先于静态参数）、输出校验（双保险，
即便插件已返回 output_model 实例也再校验一次）、总线写入前校验。

**控制流调度**（阶段五新增）：

| 节点 `control_flow` | 引擎行为 |
|---|---|
| `"branch"` | 读取输出 `route`，把其余出边标记为失活；下游节点若入边全部失活则跳过 |
| `"loop_start"` | 展开执行配对区间：每轮重置循环体内的分支状态，注入 `loop.index` / `loop.item` / `loop.indexes` / `loop.depth`；**嵌套时在嵌入的 loop_start 处递归**，进入子循环前保存外层 `loop.*`，返回后恢复 |
| `"loop_end"` | 由引擎在迭代后执行，聚合每轮输出为 `results` |

嵌套循环要点：`_run_region()` 自递归，帧栈记录由外到内的下标；
`loop.index` / `loop.item` 指向最内层，`loop.indexes`（如 `[1, 2]`）
给出完整路径，`loop.depth` 为当前深度。子循环结束时把
`loop.results` / `loop.stopped_early` 恢复为外层值，避免外层误读。

失活判定用 `_all_incoming_inactive`：**只有全部入边都失活才跳过**，
因此两条分支汇聚到同一节点时，只要一侧执行过就会继续。

失败处理：
- 默认模式：失败节点的**全部下游递归标记跳过**，无关分支继续执行。
- 严格模式（`strict=True`）：首个失败即中止。

**报告不抛异常**：所有节点级错误汇总进 `RunReport`，调用方看 `.ok`。

## 架构决定记录

### 决定 1：类型预检必须严格，不能"无法判定就放行"

- **问题**：早期 `_types_compatible` 末尾有一句 `return True` 兜底，
  导致 `List[RegexMatchItem] → str` 这类明显不兼容的连线被判为合法，
  类型预检形同虚设。
- **决策**：改为逐层严格判定（容器递归、Union 处理、数值放宽白名单），
  只在**确有把握**时放行（如 `Any` 通配、注解缺失）。
- **理由**：预检的价值就在于提前暴露错误；宁可严格要求用户显式处理，
  也不能让错误潜伏到运行时。

### 决定 2：Union 判定必须先于"容器 vs 标量"判定

- **问题**：`get_origin(Optional[str])` 返回 `Union`，若让容器分支先执行，
  `Optional[str]` 会被误当成容器，`Optional[str] → str` 被错判为
  "容器不能作为标量"，且 Union 分支永远不可达。
- **决策**：在 `_type_verdict` 中把 Union 判定提到容器判定之前。
- **验证**：已建立 21 例类型判定矩阵覆盖该场景。

### 决定 3：被跳过的节点不得再进入主循环

- **问题**：`_skip_downstream` 递归标记下游为 skipped 后，主循环遍历
  拓扑序时又会执行这些节点一次，产生"已跳过"与"失败"两条重复记录。
- **决策**：主循环执行前先查总线，若该节点已标记 skipped 则跳过。
- **理由**：跳过传播是递归的，主循环是线性的，两者必须去重。

## 已修复缺陷

| 缺陷 | 原因 | 修复 |
|---|---|---|
| 类型预检形同虚设 | `_types_compatible` 末尾 `return True` 兜底 | 改为严格逐层判定 |
| `Optional[str] → str` 被错判为不兼容 | Union 被容器分支抢先拦截 | Union 判定前移 |
| `Dict → List` 漏判为兼容 | 不同 origin 落到兜底分支被放行 | 新增"不同容器族"判定 |
| 跳过节点被执行两次 | 跳过传播与主循环重复登记 | 主循环前检查 skipped 标记 |
| 类型名只显示 `List` | 未展开泛型参数 | `_type_name` 递归展开，显示为 `List[str]` |
