# 七圣召唤 AI 司机（arena driver）

把「游戏浏览器里的决策时刻」接到「VMAF 的蓝图」上的中间层。

- **游戏侧**：`engine` 的 AI 对战场把每个决策通过 SSE 推给 agent，agent 用 HTTP 提交回答。
- **策略侧**：`ai-backend/vmaf_system` 用节点图（蓝图）拼提示词、调模型、校验回答。
- **本目录**：负责"什么时候跑一次图""跑不出来怎么办""每回合留下什么记录"。零依赖，Node ≥ 20（只用 `fetch`/`WebSocket`/`fs` 等内置）。

放在两个项目之外，是为了让 `engine` 的 git 保持干净（`local/update-upstream.cmd` 要求工作区无改动）。

## 快速开始

```bash
# 1) 起游戏服务（另开一个终端，或用 Start.cmd）
cd ../engine
node local/server.mjs            # 默认 http://127.0.0.1:7717

# 2) 确认座位（见"座位"一节）
node arena-driver.mjs --mode probe --seat 1

# 3) 只记录、不决策（零风险，先攒真实数据）
node arena-driver.mjs --mode record --seat 1 --session r01

# 4) 接蓝图
node arena-driver.mjs --mode vmaf --seat 1 --blueprint ./blueprints/decide.json
```

打开 `http://127.0.0.1:7717/#arena`，选模式、点"开始对局"。驱动会打印每个决策与提交结果。

## 模式

| mode | 作用 | 会不会提交回答 |
| --- | --- | --- |
| `probe` | 只连 SSE，打印收到的请求、服务端状态，不提交 | 否 |
| `record` | 把每回合请求落盘到会话 JSONL，不提交 | 否 |
| `echo` | 本地随机挑一个合法动作（不调模型），用于验证链路 | 是 |
| `vmaf` | 把请求交给 VMAF 蓝图跑，取回决策后提交 | 是 |

`probe` / `record` 下对局仍会继续——页面等到倒计时结束就用默认动作，所以可以安全观察。

## 座位（最常见的坑）

AI 座位是**连 SSE 时声明的**（`GET /api/arena/agent/stream?seat=N`），提交回答的请求体里没有座位字段。

- PvE（真人 vs AI）：默认 **AI 在座位 1**，真人在座位 0。
- EvE（AI vs AI）：两个座位都是 AI，要开**两个** driver，一个 `--seat 0`，一个 `--seat 1`。
- PvP：没有 agent，连了也收不到请求。

连错的典型表现：**对局照常在走，但 driver 一条请求都收不到**（页面每回合超时用默认动作，看起来像"AI 不动"）。司机因此内置自检，每 2 秒对一次 `/api/arena/status`：

```
✖ 座位疑似配错：座位 1 上有一个待决策请求，而座位 0 没有任何请求。
```

这类硬失败会以**退出码 3** 结束，方便脚本/CI 发现。12 秒完全没请求只给软警告并列出可能原因。

**同一个座位只能开一个 driver。** 桥接把每个请求广播给该座位所有连接，先答者成功、后答者收到 404。多个 driver 抢同一座位时，日志里会出现大量 404 与 `agents={"0":2}`，看起来像超时，其实是被别人答掉了。

## 参数

```
--seat <0|1>            本 driver 负责的座位（默认 1）
--mode <probe|record|echo|vmaf>   默认 echo
--bridge <url>          游戏桥接地址（默认 http://127.0.0.1:7717）
--match <id>            对局 id（默认 local，当前版本固定）
--session <name>        会话名，落盘到 sessions/<name>.jsonl（默认 时间戳）
--session-dir <dir>     会话目录（默认 ./sessions）
--vmaf <url>            VMAF 后端（默认 http://127.0.0.1:8000）
--blueprint <path>      蓝图 JSON，每回合检查 mtime，改完下一回合即生效
--decision-node <id>    从哪个节点的 output 取决策（默认 decide）
--max-attempts <n>      422 后带反馈重试上限（默认 3）
--safety-ms <n>         预留安全余量（默认 3000）
--min-budget-ms <n>     剩余预算低于此值直接兜底（默认 1500）
--show-menu             打印候选行动菜单（调试用，默认关）
--payload               record 模式下连原始 request/state 一起存（体积大）
--exit-on-end <sec>     对局结束后再等 N 秒自动退出（默认 0 = 一直等）
--quiet                 只打结果，不打过程
```

## 每回合做什么

1. 收到 SSE `request` 事件（`id` + protobuf `request` + `state` + `aiDetail` + `deadline`）。
2. 算预算：`budget = deadline - now - safetyMs`；小于 `minBudgetMs` 就直接用默认动作兜底，不冒险。
3. `vmaf` 模式：`POST :8000/run`，`shared` 里带
   `{request, state, aiDetail, meta:{matchId,seat,requestId,rpcKind,round,deadline,remainingMs,attempt}, retry, ctx:{summary,stats,lastDecision}}`。
4. 从 `results[decisionNode].output` 取 `response`（或 `decision`）。
5. **本地形状校验**（镜像并严于桥接的校验）——形状不对宁可不发，也绝不发可能被判 IO 错误的响应。
6. `POST /api/arena/agent/respond`，`{matchId, id, response}`，`response` 是完整的 `{"response":{"$case":…,"value":…}}`。
7. 按结果分派：`200` 记成功；`422` 把 `{reason, validity, availableActions}` 作为反馈塞进 `shared.retry` 重跑；`404` 记 `late` 并永久封存这一个 id（已经超时或被别人答了）。
8. 落盘一行会话记录（含耗时、尝试次数、决策摘要）。

超时是两层：节点内（`llm_chat` 自己的 `timeout`）与回合级（HTTP 请求超时 = budget）。任何一层超了都会兜底，不会让对局卡住。

## 会话记录

`sessions/<name>.jsonl`，一行一个回合，只追加：

```json
{"v":1,"session":"eve3-s0","seq":42,"ts":1792925125852,"kind":"turn","seat":0,
 "requestId":812,"rpcKind":"action","round":7,"deadline":1792925150000,
 "latencyMs":2100,"ok":true,"attempts":1,
 "decision":{"kind":"action","index":3},"note":null,
 "promptChars":null,"estTokens":null}
```

用它复盘：哪一回合慢、哪些回合重试了、决策是什么、有没有兜底。`lib/store.mjs` 里的 `summarizeTurns()` 能把一局压成若干行文本，喂给下一回合的 prompt：

```bash
node -e "const {readSession,summarizeTurns}=await import('./lib/store.mjs');
console.log(summarizeTurns(readSession('sessions/eve3-s0.jsonl')).text)" --input-type=module
```

> 不要用 `/api/arena/ai/log` 做逐请求审计：它只保留最近 2 万条事件，整局跑完再看已经滚掉了。

## 目录

```
arena-driver.mjs          主程序（四种模式、队列、自检、兜底、对局结束检测）
lib/bridge.mjs            桥接 HTTP + SSE 客户端（手写解析、自动重连）
lib/policy.mjs            默认动作、形状校验、422 反馈整理、文本描述
lib/store.mjs             会话 JSONL 读写与压缩
lib/names.mjs             catalog 拍平 + 按本回合局面裁剪名字表
lib/secrets.mjs           从环境变量给蓝图里的 llm_chat 补 api_key
blueprints/decide.json    VMAF 蓝图（真模型）
blueprints/decide.mock.json 同上，但 api_base 指向假模型（联调用）
tools/cdp.mjs             零依赖 CDP 客户端（开浏览器、点按钮、读页面状态）
tools/e2e-arena.mjs       自测开局器：起页面 → 选模式 → 开一局 → 打印进程
tools/replay.mjs          离线回放：拿录下来的某一回合重跑蓝图，不连游戏
tools/mock-llm.mjs        假模型服务：从提示词里挑第一个编号回出去
tools/inspect-ailog.mjs   分析 ai/log 里同一请求被推了几次
samples/catalog.json      名字表缓存（replay 用）
```

## 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 一条请求都收不到 | 座位错 / 对局没开 / 模式是 PvP | 看自检输出；`--mode probe` 在两座位分别试 |
| 大量 404 | 同座位有第二个 driver，或本回合已超时 | `kill` 掉多余进程；调大 `--action-seconds` |
| `agents` 计数 > 1 | 旧连接没断（进程被杀但连接未关） | 无状态：新连接照常工作，只是可能抢答，重启页面 `/reset` |
| 422 反复 | 模型输出的动作合法性不对 | 看 `reason` 与 `availableActions`，让 prompt 里带 validity 说明 |
| 决策都被记成"兜底" | 预算不够（`action-seconds` 太小或模型太慢） | 调大页面计时，或减少每次调用的节点数 |
| `响应类型不匹配` | 蓝图输出的 `$case` 与请求不一致 | 用 `rpc_build` 节点按 `meta.rpcKind` 生成，不要硬编码 |

## 与 VMAF 的约定

`vmaf` 模式期望蓝图最后一个节点（`--decision-node`）输出：

```json
{"response": {"response": {"$case": "action", "value": {"chosenActionIndex": 3}}}}
```

也接受 `{"decision": {"chosenActionIndex": 3}}`（由 `rpc_build` 之类节点补成完整形状）。缺字段时 driver 会本地校验失败并重试，最终兜底。

## 密钥：不写进蓝图

蓝图会被提交、导出、拖拽，把 `api_key` 写进去迟早泄漏；而 `llm_chat` 留空 api_key 时是 dry_run（根本不发请求）。
所以密钥只在运行时补：司机加载蓝图后，对每个 `llm_chat` 节点按 `api_base` 猜变量名——

| api_base 含 | 依次尝试 |
| --- | --- |
| `api.deepseek.com` | `DEEPSEEK_API_KEY` → `VMAF_LLM_API_KEY` → `LLM_API_KEY` |
| `api.openai.com` | `OPENAI_API_KEY` → 同上 |
| `openrouter.ai` | `OPENROUTER_API_KEY` → 同上 |

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."      # PowerShell
$env:DEEPSEEK_API_KEY = "sk-..."      # cmd
export DEEPSEEK_API_KEY=sk-...        # bash
```

没设也没关系：司机只会警告"这些 LLM 节点没有 api_key，会是 dry_run"，整局仍能跑（全兜底），不会崩。

## 假模型联调（不需要密钥）

```bash
node driver/tools/mock-llm.mjs --port 8110 --verbose        # 另开一个终端
node driver/arena-driver.mjs --mode vmaf --seat 1   --blueprint driver/blueprints/decide.mock.json   --vmaf http://127.0.0.1:8100 --bridge http://127.0.0.1:7807
```

它只做一件事：从提示词的"可选动作"段里挑第一个 `[编号]` 回出去。用来验证
"节点组装 → HTTP → 解析 → 拼 RpcResponse → 桥接接受"这条链路，不用花 token。
实测一局 AI vs AI：座位 0 请求 60 / 成功 60 / 422 0 / 兜底 0，9 回合分出胜负。

## 离线回放（不连游戏也能验证蓝图）

```bash
node driver/tools/replay.mjs --list                       # 列出录下来的回合
node driver/tools/replay.mjs --id 932                     # 重跑某一回合
node driver/tools/replay.mjs --id 932 --show prompt        # 顺便把提示词打出来
node driver/tools/replay.mjs --blueprint driver/blueprints/decide.mock.json
```

它读会话 JSONL 里的原始 `request/state/aiDetail`，用**该回合之前**的记录拼 `ctx.summary`，
再 `POST /run`，逐节点打印耗时与产出，最后做本地形状校验。
改完蓝图先在这里过一遍，再去实战——比开局试错快得多。

## 决策节点

`--decision-node` 可以不给：司机会取蓝图里**唯一没有出边的节点**（汇点）当决策节点；
蓝图有多个汇点时会报错并列出所有节点 id。

## 在网页上改提示词（推荐的工作流）

打法文字写在 `playbook` 节点的参数里，不用改代码。三步：

```bash
# 1) 把本地蓝图推到 VMAF 服务端，拿到 id
node driver/tools/push-blueprint.mjs --vmaf http://127.0.0.1:8100 \
  --file driver/blueprints/decide.json --name "七圣召唤 · 单回合决策（提示词槽版）"
#    → 已保存：id=0d0f96e727a2 …

# 2) 打开画布（dev server 要指向装了新插件的后端）
cd "ai-backend/vmaf_system/frontend"
VMAF_API_TARGET=http://127.0.0.1:8100 npx vite --port 5173
#    浏览器打开 http://127.0.0.1:5173 → 顶栏"从服务端读取" → 选上一步的 id
#    → 点"2. 打法提示词"节点，在右侧参数面板里写 → 保存到服务端（同一 id 即覆盖）

# 3) 司机直接读服务端那一份，改完下一回合生效，不用重启
node driver/arena-driver.mjs --mode vmaf --seat 0 \
  --bridge http://127.0.0.1:7807 --vmaf http://127.0.0.1:8100 \
  --blueprint-id 0d0f96e727a2 --session s0
```

`--blueprint-id` 与 `--blueprint <文件>` 二选一，前者优先。离线验一遍同样支持：

```bash
node driver/tools/replay.mjs --id 932 --vmaf http://127.0.0.1:8100 \
  --blueprint-id 0d0f96e727a2 --show prompt
```

## 实时日志在哪

| 想看什么 | 在哪看 |
| --- | --- |
| 每回合"收到请求 → 决策 → 提交结果"一行一条 | 司机终端；加 `--log-file driver/logs/s0.log` 可另存一份，用 `Get-Content -Wait` 或 `tail -f` 跟 |
| 每个回合的完整事实（请求、决策、耗时、422 理由） | `driver/sessions/<session>.jsonl`，append-only，一行一回合 |
| 这一回合的完整提示词与各节点产出 | `node driver/tools/replay.mjs --id <requestId> --show prompt` |
| 图内部每个节点 ok/耗时/输出 | VMAF 网页底部运行栏；`replay` 也会逐节点打印 |
| 游戏侧发生了什么（抽牌、伤害、回合） | 游戏页面左下对局日志；接口 `/api/arena/log`（最近 500 条）、`/api/arena/ai/log`（结构化事件，最多 2 万条）、`/api/arena/status`（谁连着、有没有待决策） |

## 备赛：牌组码 → 牌组信息 → 赛前战术（每手都带）

两种用法，都在开局前完成，结果会随每一手一起进图（`in.plan`）：

```bash
# 用牌组分享码（游戏里"复制牌组码"得到的那串）
node arena-driver.mjs --mode vmaf --seat 1   --deck-code FZDByRUNGRCB0WoNFlGgWpEPE0AB9TAPFGCB9kgWGIERCoEQDLFADcQQDPFgacYWDJAA   --bridge http://127.0.0.1:7807 --vmaf http://127.0.0.1:8100   --blueprint blueprints/decide.json --session my-s1 --exit-on-end 10

# 用现成的牌组 JSON（{characters:[id], cards:[id]}）
node arena-driver.mjs --mode vmaf --seat 1 --deck driver/decks/my.json ...

# 自己写计划，不让模型写（调试提示词时最省时间）
node arena-driver.mjs ... --plan "前两回合铺场，第三回合开大收掉对方出战角色。"
```

流程（`--prep-blueprint`，默认 `blueprints/prep.json`，6 个节点）：

```
shared_get(deck) ─┐
                  ├→ deck_brief（角色/技能/牌组构成，中文名）─┐
shared_get(catalog)┘                                          ├→ prompt_pack → llm_chat → plan_guard
                                                              ┘
```

- 牌组码解码在 `lib/deckcode.mjs`（与游戏 `sharing.ts` 同一算法，51 字节 → 33 个 id，前 3 角色后 30 牌）；名字表默认读 `engine/packages/assets-manager/src/data/share_id.json`，也可用 `VMAF_SHARE_ID_MAP` 指定。
- 解码出的牌组会 `POST /api/arena/deck` 设到该座位，所以页面开局时座位已经有牌组。
- `plan_guard` 把计划洗到 ≤500 字（去客套/引号、按句截断），每回合由 `decide.json` 的 `in.plan → pack.plan` 注入 `### 赛前计划` 段（该段**永不丢弃**）。
- `--no-prep` 关掉整段备赛，回到"只看局面"的老行为。

## 实时观测台（看提示词与思考过程）

```bash
node driver/tools/viewer.mjs --port 7810 --dir driver/sessions
# 浏览器打开 http://127.0.0.1:7810
```

司机在 vmaf 模式下会把每一手写进 `driver/sessions/live.jsonl`：提示词全文、模型原话、解析到的编号、最终 RpcResponse、逐节点耗时、本手耗时、是否兜底/422、以及赛前计划。页面按会话分组、点左侧任意一手看详情，SSE 实时追加（不用刷新）。

数据是 append-only JSONL，可以直接 `grep`：

```bash
node -e "const fs=require('fs');for(const l of fs.readFileSync('driver/sessions/live.jsonl','utf8').split('
')){if(!l.trim())continue;const r=JSON.parse(l);if(r.kind==='turn')console.log(r.round,r.rpc,r.promptChars,r.decision&&r.decision.index)}"
```
