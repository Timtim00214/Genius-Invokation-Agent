# 交接：2026-10-26 夜间（第四棒起点）

> 一句话：链路、牌效、"最近发生的事"都已进提示词并用真模型跑通；**"剩一堆骰子就宣布结束"已定位为超时兜底策略问题，不是模型犯傻**。今晚从这里继续。

## 一、刚完成的事（第三棒，已验证）

1. **卡片/装备/支援/召唤物效果进提示词**
   - 驱动侧 `driver/lib/cardtext.mjs`：从 `/api/arena/card-text`（1928 条）裁出本手用得到的 id，进 `shared.cardText`；`replay.mjs` 用 `driver/samples/card-text.json`。
   - `plugins/tools/action_rank.py`：菜单行带 `牌面：…` / `说明：…`；`selectCard` 候选也带效果。
   - `plugins/tools/state_brief.py`：手牌 `旧日鏖战〔敌方出战角色失去1点充能。〕`，装备/状态独立成行带效果。
2. **"最近发生的事"（双方动作）进提示词**
   - 新插件 `plugins/tools/game_log.py`：读桥接 `/api/arena/log` 的 `kind=="decision"` 条目，渲染成 `- 对方 打出「旧日鏖战」：敌方出战角色失去1点充能`、`- 我方 使用技能「重钳碎击」：造成2点物理伤害`。
   - 新段插在 `### 往期判断` 之后、`### 当前局面` 之前；预算不足时先丢它，局面/菜单永不丢。
3. **提示词收紧**（治 reasoning 吃满 8192 tokens 导致 reply 为空）
   - system：分析 400 字内、只说三件事；末行单独一行 `[编号]`。task 同步。
   - 真模型回放：39.5s、4740 tokens、`finish_reason=stop`、reply 带 `[16]`，parse 成功。
4. **验证**：`pytest -q` → 386 passed；`/health` → `node_count 27`、错误 0；线上蓝图 **`65a6f4c831bc`**（11 节点 34 边）。
5. **密钥**：`driver/.env` 里已有 `DEEPSEEK_API_KEY`（`driver/lib/secrets.mjs` 启动时自动 load，环境变量优先）。
6. **文档**：`交接-七圣召唤AI对局-2026-10-25.md` 追加了 §17。

## 二、今晚新问题的诊断结论（重点，先看这里）

**现象**：AI 还剩 5–8 个骰子就宣布结束回合，白白亏骰。

**根因：不是模型选的，是超时兜底直接替它宣布结束。**

链路：

1. 页面每手带 `deadline`；`actionSeconds` 默认 **45 秒**（`genius-invokation-dev/packages/standalone/src/arena/Arena.tsx:130`，设置里可改 5–300）。
2. 司机 `driver/arena-driver.mjs:471` 用 `(deadline - now - safetyMs)` 作为预算，`safetyMs` 默认 **3000**（`:150`），超时抛 `The operation was aborted due to timeout`。
3. 出错后走 `fallbackResponse`（`driver/lib/policy.mjs:32`）：**action 分支优先挑 `declareEnd`**（`findIndex(a.action?.$case === "declareEnd" && a.validity === 0)`），没有才挑第一个合法动作。
4. 于是这一手 = 结束回合，骰子全废。

**证据**（`driver/sessions/s1-20261026-133934.jsonl`，今晚这一局）：

- 15 手：其中 **5 手** `note` 写着 `兜底（The operation was aborted due to timeout）`，延迟全部 **43502–43513ms**（正好是 45s 期限－安全余量）。
- 其余手正常：29.2s / 18.5s / 20.5s / 10.6s / 13.9s…；6 条 `review` 短笔记（意图 + 胜率 0.55→0.18）生成正常。
- 结果 `outcome: winner=0`（AI 输），赛前计划 429 字正常。

**所以**：

- "每轮都发一遍完整游戏规则"**不解决**这个问题；问题是决策耗时 > 页面时限，以及兜底策略主动选结束。
- 骰子经济段（B）仍然值得做，但它是"决策质量"问题，和这个 bug 不是一回事。

## 三、今晚接着做什么（按优先级）

1. **改兜底策略（根治"亏骰子"）**：`driver/lib/policy.mjs` 的 action 分支——
   - 只有在"除结束回合外没有任何合法动作"时才选 `declareEnd`；
   - 否则按"造成伤害 > 充能/召唤 > 打出牌 > 切人"挑一个合法动作，带 `autoSelectedDice`；
   - 保证 `fallbackResponse` 永不在还有可打动作时结束回合。
2. **时限对齐**：页面设置里把"行动时限"从 45 调到 **90–120 秒**（上限 300）；或在司机侧把 `safety-ms` 调小、并在提示词里带"剩余时间不足时给短答"。
3. **B：骰子经济与资源代价段**（用户明确"最终也要做"）：
   - 回合结束未用完的骰子作废；当前骰面还能打哪些行动；调和一张牌 = 换 1 个指定元素骰的代价；
   - "规则提示词每轮发"折中方案：只发 3–5 条易忘规则（骰子作废、充能/大招条件、每回合限一次），不发全文。
4. 之后仍按交接文档：EvE 双会话观测、流式输出、每动作一句自我提示。

## 四、当前可运行状态（重启用）

```bash
# 1) 图引擎（8100）
cd "1023 - 副本/vmaf_system/backend" && python -m uvicorn main:app --port 8100 --host 127.0.0.1
# 2) 游戏 + 桥接（7807）
cd genius-invokation-dev && node local/server.mjs --port 7807 --no-open
# 3) 观测台（7810）
node driver/tools/viewer.mjs --port 7810 --dir driver/sessions
# 4) 司机（座位 1，蓝图 65a6f4c831bc）
node driver/arena-driver.mjs --mode vmaf --seat 1 \
  --bridge http://127.0.0.1:7807 --vmaf http://127.0.0.1:8100 \
  --blueprint-id 65a6f4c831bc \
  --deck-code FZDByRUNGRCB0WoNFlGgWpEPE0AB9TAPFGCB9kgWGIERCoEQDLFADcQQDPFgacYWDJAA
```

- 改 `.py` 后打一次 `curl http://127.0.0.1:8100/health` 触发热更新。
- 改蓝图用 `node driver/tools/push-blueprint.mjs --file driver/blueprints/decide.json --vmaf http://127.0.0.1:8100`（**默认推 8000 是坑**）。
- 真模型回放：`node driver/tools/replay.mjs --session driver/samples/fixture-turn.jsonl --vmaf http://127.0.0.1:8100 --blueprint-id 65a6f4c831bc --show prompt --dump /tmp/raw.json`（`--dump` 里有 `reasoning_content`）。
- 复盘真对局：直接读 `driver/sessions/*.jsonl` 的 `reasoning` 字段，不需要再花钱跑模型。

## 五、已知小瑕疵（不阻塞）

- 同名技能（如三条「匿叶龙」）菜单行只靠编号区分，可加 "第 N 张"。
- `state_brief` 对手手牌只给张数（规则上本就不可见，正确）。
- 桥接日志被清空时 `logCursor` 已做归零保护，但极端换局场景仍需实测。

---

## 六、第二棒已完成（2026-10-26 20:40，本轮改动，已自测+真模型回放）

蓝图上 §三 的第 1、2 项**已做完**，另外把"重投骰子/换手牌从来没用过"一并修了。

**新蓝图 id：`8b804ed0feb1`**（12 节点 39 边，名"七圣召唤 · 决策（重投链路 + 兜底修正）"）。旧 id 全都还在，别删。

1. **重投/换牌链路（此前从未生效过，`diceToReroll` 永远是 `[]`）**
   - `action_rank` 对 `rerollDice` / `switchHands` 不再回"无需选编号"，改成逐颗/逐张列编号，末项是哨兵（`[4] 不重投` / `[3] 不换`）；新增输出字段 `task` 带"多选用逗号"的回答格式要求。
   - `choice_parse` 新增输出 `numbers: List[int]`（白名单过滤、去重保序、上限 16），`index` 仍是第一个；单选请求不受影响。
   - 新增节点 `choice_map`（纯映射）：位置 → 骰面值（多重集，重复值合法）或手牌 id；哨兵位置自动丢弃。
   - 蓝图加 `pick` 节点 + 5 条边：`parse.numbers`、`in.detail`、`in.request` → `pick` → `build.used_dice`，另加 `rank.task → pack.task`（引擎里连线值覆盖 params）。
   - 真模型回放实测：`[2]` → `{"diceToReroll":[1]}`；换牌 `[0]` → `{"removedHandIds":[-500036]}`。
2. **兜底不再替 AI 结束回合**
   - `rpc_build._action`：先过滤掉 declareEnd，再按"伤害 > 治疗/护盾 > 召唤 > 充能 > 出牌/技能"打分挑最优；只有全是 declareEnd 才结束回合。
   - `driver/lib/policy.mjs` 的 `fallbackResponse` 同样改为打分挑最优（declareEnd 记 −1e9）。
3. **时限**：页面 `actionSeconds` 默认 45→**120**（`Arena.tsx`），VMAF 的 llm 节点 `timeout_seconds` 90→110。dist 已重建，**浏览器要硬刷新**；已开着的对局仍是旧配置，下一局才吃到 120s。
4. 测试：`395 passed`（新增 9 条，含一条"回复文件 → parse → map → build"的整链测试）。`/health` 热更新后 node_count=28、plugin_errors=0。
5. 回放用的三份 fixture：`driver/samples/fixture-turn.jsonl`（行动）、`fixture-reroll.jsonl`、`fixture-switch.jsonl`（后两份本轮手工构造，字段形状与真实 aiDetail 一致）。

**仍然没做**：骰子经济段（§三 第 3 项）、EvE 双会话观测、流式、每动作自我提示。另外 §五 第一条"同名技能只靠编号区分"仍在。

---

## 七、第三棒（pass 与回放台）：2026-10-27

> 这一节是"引擎规则里本来没有的动作"——用户明确要求："什么都不做，过一手，无论规则存不存在都实现一下"。

### 1. 新增动作 `pass`（过一手）：只交行动权，不消耗、不结束回合

改动分布在引擎、页面、VMAF 三处，全部已生效：

| 层 | 文件 | 内容 |
| --- | --- | --- |
| proto | `proto/action.proto` / `proto/rpc.proto`（含 `packages/typings/proto/` 两份副本） | 新增 `message PassAction {}`；`oneof action` 加 `PassAction pass_action = 6;`。改完必须 `node_modules/.bin/buf generate` **且** `cd packages/typings && npx tsdown`，否则 core 报 TS2322。 |
| core | `packages/core/src/base/skill.ts` | `PassInfo`、并入 `ActionInfoBase`、`ActionEventArg.toString()` 返回"过一手"、`isPass()`。 |
| core | `packages/core/src/game.ts` | `availableActions()` 在 `declareEnd` 之前 push `{type:"pass", validity: 对方已宣告结束 ? CONDITION_NOT_MET : OK, isFast:false, 无消耗, 空 preview}`；执行 switch 加 `case "pass": break;`（不 handleEvent，靠统一的 `switchTurn` 换手）。 |
| core | `io.ts` / `preview.ts` | `exposeAction` 的 `case "pass"`、preview 的 `case "pass"`。 |
| 页面 | `packages/standalone/src/arena/defaultResponse.ts` | `defaultRpcResponse(req, {preferPass})`：**人类座位先找 passAction（白送），找不到才 declareEnd**；AI 座位强制 `preferPass:false`（两边都 pass 会互踢皮球）。 |
| 页面 | `packages/standalone/src/arena/seatIo.ts` | 人类座位多一条 `manual` 竞速分支：页面上点「过一手」立刻提交；新增 `onPendingRequest/onPendingSettled` 回调；日志文案改成"默认响应优先过一手"。 |
| 页面 | `packages/standalone/src/arena/Arena.tsx` | 人类回合显示「过一手（交出行动权）」按钮；面板文案重写为"烧绳＝等归零由默认响应接管（优先过一手）；立刻＝马上默认响应"。 |
| VMAF | `plugins/tools/action_rank.py` / `rpc_build.py` | AI 侧过滤 `kind=="passAction"`（不给模型这个选项，避免双方互过卡死）。 |

**规则边界**：对手已经宣告结束时 pass 非法（否则双方可以无限互过），这时才回落到结束回合。

**已验证**：引擎级脚本（真牌组、脚本化 IO）`passSeen/passUsed=1、actionCount=5、turn 0→1`；真机 PvE 冒烟 20 手、round 0→3、`declaredEnd [false,false]`——人类连续 8 次超时都没有再结束回合，回合是靠 AI 结束推进的。`tsc --noEmit` 与 `vite build` 均通过。

### 2. 对局回放台 `driver/tools/replay-viewer.mjs`（端口 7812）

```bash
node driver/tools/replay-viewer.mjs --port 7812 --dir driver/sessions
# 或者直接 node 启动.mjs（会自动带上，结束时会打印这个地址）
```

- 左边列出所有会话（按"同一次对局"分组，可 **Ctrl 多选两个座位** 合成一条时间轴）；右边大卡显示"第 N 手：谁 / 什么动作 / 花了哪些骰子"，下面是**逐字的思维链**；底部播放条：⏮ / ▶ 播放 / ⏭、速度（手动、每 2s、每 5s、每 8s）、进度条；键盘 ←/→/空格。底部还会并排显示两个 AI 的**赛前战术**，"接下去"预览后面几手。
- 只读会话文件，不碰现有服务；`?keys=文件名#序号,...` 可直接分享某一局的链接。
- 目前 `decision` 里能读到 `skillName/cardName/cost/preview/hand` 等字段，所以动作标题是中文的（如"使用技能「越祓雷草之轮」"）。

### 3. 这一节踩过的坑

- `buf generate` 只更新 `packages/typings/proto`，**core 用的是 `packages/typings/dist`**，漏掉 `npx tsdown` 就会 TS2322。
- `defaultRpcResponse` 不能在所有座位都 preferPass，否则两个 AI 座位可以互相无限"过一手"。
- 引擎里 `isFast:false` 的动作由统一后置 `switchTurn` 换手；pass 千万不要自己 mutate，否则会连换两次手。
- 页面侧 `clientIo.cancelRpc?.()` 只在人类按钮抢答时调用，别在超时分支漏掉，否则棋盘会残留一个悬空请求。
