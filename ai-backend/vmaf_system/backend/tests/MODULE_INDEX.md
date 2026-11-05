# 测试套件索引（backend/tests）

> 常驻测试，取代此前的临时验证脚本。任何改动核心文件后都必须重跑。

## 运行方式

```bash
cd backend
python -m pytest -q                 # 全量（当前 201 项）
python -m pytest -m types -q        # 只跑类型判定（改 parser.py 必须先跑）
python -m pytest -m engine -q       # 只跑引擎
python -m pytest -m api -q          # 只跑接口层
```

## 本地联调（真实 HTTP）

```bash
cd backend
python -m uvicorn main:app --port 8000
# 浏览器打开 http://127.0.0.1:8000/docs 查看 Swagger UI
```

## 文件清单

| 文件 | 标记 | 用例数 | 覆盖内容 |
|---|---|---|---|
| `conftest.py` | — | — | 共享夹具，**模块加载的唯一入口** |
| `test_types.py` | `types` | 29 | 类型兼容判定矩阵（21 例）+ 回归用例 |
| `test_base_contract.py` | `contract` | 14 | 契约层：抽象约束、契约自检、Schema 导出、共享消息类型 |
| `test_scanner.py` | `scanner` | 17 | 扫描器：发现、缓存、热更新、故障隔离、契约层唯一性 |
| `test_parser.py` | `parser` | 20 | 解析器：环检测、拓扑排序确定性、类型预检、必填检查 |
| `test_engine.py` | `engine` | 12 | 引擎：端到端、失败传播、严格模式、Bus 隔离 |
| `test_plugins.py` | `plugin` | 50 | 各业务节点的安全围栏行为（含 llm_chat 厂商透传/删字段/保留键/错误正文 17 项） |
| `test_api.py` | `api` | 18 | HTTP 接口：schema 导出、校验预检、执行、结构化错误 |
| `test_control_flow.py` | `plugin` | 18 | 控制流：分支路由、循环区间、字段说明完整性 |
| `test_expr.py` | `expr` | 23 | 受限表达式：AST 白名单与攻击面 |

## 关键设计：conftest 是模块加载的唯一入口

`conftest.py` 中所有模块加载都经由 `utils/loader.load_module_once`，
与生产代码完全一致。

**这一点不能妥协**：若测试自行用 `importlib` 加载契约层，就会在
`sys.modules` 中产生第二份 `base.py`，得到不同身份的 `Message` 类，
届时"跨节点直连"等测试会因 Pydantic 按类身份校验而失败——
而这恰恰是生产环境中被小心规避的陷阱。测试若绕开加载权威，
就会测出一个生产环境里根本不存在的假象。

## 最有价值的一批用例

`test_types.py` 的类型判定矩阵曾抓出"类型预检形同虚设"的重大缺陷
（早期实现末尾有 `return True` 兜底，导致 `List[RegexMatchItem] -> str`
被判为兼容）。**任何改动 `_type_verdict` 的行为都必须先跑 `-m types`。**

## 突变测试验证（套件有效性证明）

对 `parser.py` 注入"容器↔标量互连被放行"的突变后，5 项测试立即失败：

- `test_type_verdict_matrix[容器 -> 标量 必须拒绝]`
- `test_type_verdict_matrix[标量 -> 容器 必须拒绝]`
- `test_types_compatible_matches_verdict`
- `test_parser.py::test_type_mismatch_rejected`
- `test_engine.py::test_invalid_plan_aborts_execution`

证明套件**确实能抓到真实缺陷**，而非仅仅覆盖代码行。

## 待办

- [ ] 补充 `utils/loader.py` 专项测试（加载唯一性、失败不留痕）
- [ ] 补充 `utils/errors.py` 专项测试（原生异常归类、错误码映射）
- [ ] 补充 `utils/logger.py`（待其创建）
- [ ] 覆盖率统计（`pytest-cov`）
