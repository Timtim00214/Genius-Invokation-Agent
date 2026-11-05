# 接口区文件索引（backend/api）

> 暴露给前端的 HTTP 路由。每新增或修改一个文件，同步更新本表。

## 现状总览

| 文件 | 状态 | 职责 |
|---|---|---|
| `schemas.py` | ✅ 已验证 | API 层数据契约：请求/响应模型，隔离内部重构与外部接口 |
| `routes.py` | ✅ 已验证 | 路由实现：`/health` `/schema` `/validate` `/run` + `/blueprints` 持久化 |
| `security.py` | ✅ 已验证 | **可选鉴权**：`VMAF_API_KEY` 设置后要求 `X-API-Key`，默认关闭 |
| `../main.py` | ✅ 已验证 | 服务启动入口（位于 `backend/main.py`），装配应用与 CORS |

## 接口清单

| 方法 | 路径 | 用途 | 关键行为 |
|---|---|---|---|
| GET | `/` | 服务信息与接口清单 | — |
| GET | `/health` | 健康状态与插件概况 | 有插件失败时 `status=degraded`，服务仍可用 |
| GET | `/schema` | 全部节点规格清单 | 前端据此动态渲染卡片，**零业务硬编码** |
| POST | `/validate` | 只校验不执行 | 保存前预检，返回执行顺序 + 结构化问题 |
| POST | `/run` | 执行蓝图 | **节点失败不返回 5xx**，错误汇总进响应 |
| GET | `/blueprints` | 列出已保存蓝图 | 按保存时间倒序，单文件损坏不影响列表 |
| POST | `/blueprints` | 保存蓝图（新建/覆盖） | 保存前用 `Blueprint` 校验，非法结构 422；带 `id` 即覆盖 |
| GET | `/blueprints/{id}` | 读取一份蓝图 | 不存在 404、id 非法 400（防路径穿越） |
| DELETE | `/blueprints/{id}` | 删除一份蓝图 | 不存在 404 |

### 错误处理约定

- **节点失败 → 200**：节点级错误汇总在 `errors` / `error_details`，
  HTTP 状态仍为 200。前端据此高亮节点与连线，而非弹一个无意义的报错。
- **蓝图 JSON 结构非法 → 422**：如重复 id、悬空连线、自环。
- **服务不可用才是 5xx**：节点业务失败不属于服务器错误。

## 可选鉴权（security.py）

默认**关闭**：未设置环境变量 `VMAF_API_KEY` 时行为完全不变（单机用法零配置）。
设置后所有数据/执行接口要求请求头 `X-API-Key`，否则 401：

| 路径 | 是否需要密钥 |
|---|---|
| `/` `/health` `/docs` `/openapi.json` `/redoc` | 否（探活与文档，不含敏感数据） |
| `/schema` `/validate` `/run` `/blueprints*` | 是 |

实现要点：中间件在**每次请求**现读环境变量（改配置无需重启）、
`hmac.compare_digest` 常量时间比较、`OPTIONS` 预检放行（否则浏览器全挂）、
安装幂等（`app.state` 标记）。401 响应形状与其它错误一致
（`{"detail": {"message": ...}}`），前端会提示去顶部「密钥」填写。

验证：`tests/test_api_security.py` 11 项；另用真实 uvicorn 实例
（`VMAF_API_KEY=test-key-123`，端口 8011）实测：health 200、
无密钥/错密钥 401、正确密钥 200。

## 为什么单独有 schemas.py

引擎内部对象（`RunReport` / `ExecutionPlan`）会随重构变动，而 HTTP 响应是
**外部契约**必须稳定。这一层做隔离，使内部重构不必担心破坏前端。

## 设计约束

1. **零业务硬编码**：路由层只做数据转发，不写任何具体业务字段。
2. **复用单例扫描器**：通过 `plugins.scanner.get_default_scanner()` 获取，
   避免多实例导致插件被重复加载。
3. **错误码统一**：对外一律使用 `utils/errors.py` 的编码体系。

## 已解决的分层问题

| 问题 | 处理 |
|---|---|
| 解析器用短码（`TYPE_MISMATCH`），errors.py 用长码（`blueprint.type_mismatch`），两套编码 | 映射表 `ISSUE_CODE_MAP` 上提至 `utils/errors.py` 作为**唯一权威**，`engine.py` 与 `api/routes.py` 均从该处取用 |
| 直接透传短码会导致前端做双重映射 | `/validate` 经 `_issue_to_dict` 翻译后输出统一编码 |

## 待办

- [x] 蓝图持久化（保存/读取/列举/删除，落盘 `backend/data/blueprints`）
- [x] 鉴权（`VMAF_API_KEY` 可选启用，默认关闭）
- [ ] `/schema` 支持按节点类型过滤（前端仅拉取所需）
- [ ] `/run` 支持流式返回（逐节点进度，供长时间任务）
