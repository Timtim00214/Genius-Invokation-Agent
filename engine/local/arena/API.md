# AI 对战接口（本地 HTTP / SSE）

> 这里定义的是**页面（对局宿主）**和**外部 agent** 之间的协议。
> 页面负责跑游戏、显示两个棋盘；agent 可以是任何语言写的独立进程。
> 页面必须保持打开（对局在浏览器里），agent 只负责读状态、做决策、提交行动。

Base URL：`http://127.0.0.1:7717`（端口以 `Start.cmd` / `server.mjs --port` 为准）

---

## 0. 座位与信息可见性

- 座位 `0` = 先手，座位 `1` = 后手。
- 每个座位有自己的 `state`：只包含该玩家可见的信息（对手手牌/骰子隐藏，牌堆隐藏）。
- PvE 模式里，AI 座位的手牌/骰子也会对“观战棋盘”隐藏；但 agent 通过接口拿到的是**自己座位**的完整可见状态。
- PvP 模式没有 AI；两个座位都是人在棋盘上操作。

---

## 1. 对局生命周期

1. 页面配置好模式/牌组/计时，点「开始对局」。
2. 页面打开第二个棋盘窗口，创建 `Game`。
3. 每次轮到某个 AI 座位做选择时，页面调用桥接服务：
   `POST /api/arena/agent/request`，然后等待。
4. 桥接服务通过 SSE 把 `request` 推给该座位的 agent。
5. agent 处理后 `POST /api/arena/agent/respond`。
6. 页面拿到响应，交给 `Game` 继续结算。
7. 超时（页面计时归零）→ 页面自动选择默认行动（通常是「结束回合」）。

如果 agent 没连上，页面会等满计时后自动过；对局不会卡死。

---

## 2. 开局前：牌组库与选牌

AI 可以先浏览牌组库、挑一套牌，给出构造思路（`note`），然后请求开局。

```http
GET /api/arena/decks
```

返回：

```json
{
  "decks": [
    {
      "id": "builtin-1",
      "name": "默认牌组 1（先手示例）",
      "note": "上游 standalone 的默认先手牌组。",
      "characters": [{ "id": 1411, "name": "甘雨" }, ...],
      "cards": [{ "id": 311503, "name": "祭礼剑", "count": 1 }, ...]
    }
  ]
}
```

想要卡牌描述（挑牌用）：

```http
GET /api/arena/card-text
{"version":"v7.1.0","descriptions":{"311501":"角色造成的伤害+1。..."}}
```

提交牌组：

```http
POST /api/arena/deck
{
  "matchId": "local",
  "seat": 1,
  "deckId": "builtin-2",
  "note": "我想用托马+赛诺打雷火，所以选这套"
}
```

也可以直接传 `characters: [id,id,id]` 和 `cards: [id, ...]`（或 `[{id,count}]`）。服务端会校验：

- 3 个不重复角色，且都在当前卡池里；
- 恰好 30 张行动牌；
- 同名卡最多 2 张；
- 秘传牌最多 1 种。

校验失败返回 `422` 和具体原因。

请求开局：

```http
POST /api/arena/control
{"matchId":"local","action":"start"}
```

页面在 `#arena` 配置页每 2 秒轮询 `GET /api/arena/prepare`；当 AI 座位都拿到牌组、且 `startRequested=true` 时自动开局（浏览器需要允许本站弹窗；被拦截时手动点「开始对局」即可，仍会使用 agent 选的牌组）。

查询当前状态：

| 接口 | 说明 |
| --- | --- |
| `GET /api/arena/deck?seat=1` | 某座位已选牌组 |
| `GET /api/arena/prepare` | 双方牌组、`startRequested`、agent 连接数 |

---

## 3. Agent 接入（SSE）

```http
GET /api/arena/agent/stream?matchId=local&seat=1
Accept: text/event-stream
```

### 事件

| event | data | 说明 |
| --- | --- | --- |
| `hello` | `{matchId, seat, status, pending}` | 连接成功；`pending` 是当前还没人处理的请求，应该补答 |
| `state` | `{seat, state, seq}` | 最新可见状态（每次通知都会推） |
| `request` | `{id, matchId, seat, request, state, deadline, aiDetail}` | 需要 agent 决策；`aiDetail` 是同一请求的结构化版本（actions/hand/dice），响应时用 `id` |
| `ai_event` | `{seq, at, seat, event}` | 结构化事件（见第 6 节） |
| `responded` | `{id}` | 该请求已被处理（可能是你自己） |
| `timeout` | `{id, seat}` | 该请求已超时 |
| `reset` | `{}` | 页面开了新一局，清空状态 |

示例（`request` 事件）：

```json
{
  "id": 12,
  "matchId": "local",
  "seat": 1,
  "request": {
    "request": {
      "$case": "action",
      "value": {
        "action": [
          {
            "action": { "$case": "useSkill", "value": { "skillDefinitionId": 11031, "targetIds": [], "mainDamageTargetId": -500013 } },
            "requiredCost": [{ "type": 1, "count": 1 }, { "type": 0, "count": 2 }],
            "autoSelectedDice": [1, 2, 2],
            "validity": 0,
            "preview": []
          },
          {
            "action": { "$case": "declareEnd", "value": {} },
            "requiredCost": [],
            "autoSelectedDice": [],
            "validity": 0,
            "preview": []
          }
        ]
      }
    }
  },
  "state": { "currentTurn": 0, "phase": 3, "roundNumber": 2, "player": [ {}, {} ] },
  "deadline": 1792900000000
}
```

> `validity === 0` 表示该行动合法。非法行动不要选，否则会被 `Game` 判 IO 错误。

---

## 4. 提交行动

```http
POST /api/arena/agent/respond
Content-Type: application/json
```

```json
{
  "matchId": "local",
  "id": 12,
  "response": {
    "response": {
      "$case": "action",
      "value": {
        "chosenActionIndex": 0,
        "usedDice": [1, 2, 2]
      }
    }
  }
}
```

`response` 必须是完整的 **`RpcResponse`**（`{ response: { $case, value } }`），不是只传 payload。

### 五种请求的响应

| 请求 `$case` | 响应 `value` | 备注 |
| --- | --- | --- |
| `rerollDice` | `{ diceToReroll: DiceType[] }` | 空数组 = 不重投 |
| `switchHands` | `{ removedHandIds: number[] }` | 空数组 = 不换 |
| `chooseActive` | `{ activeCharacterId: number }` | 从 `candidateIds` 里选 |
| `selectCard` | `{ selectedDefinitionId: number }` | 从 `candidateDefinitionIds` 里选 |
| `action` | `{ chosenActionIndex: number, usedDice: DiceType[] }` | 从 `action` 数组里选；`usedDice` 一般直接用该行动的 `autoSelectedDice` |

`DiceType` 是 protobuf 枚举的数字。注意“费用”和“骰子面”的 8 含义不同：

- 费用：`0=任意(Void), 1..7=冰水雷火风岩草, 8=相同元素(Aligned), 9=充能, 10=秘传`
- 骰子面：`1..7=冰水雷火风岩草, 8=万能(Omni)`

**`usedDice` 可以省略或传空数组**：服务端会自动用所选行动的 `autoSelectedDice` 填好，避免 agent 因为骰子选择错误被判负。

### 4.1 非法操作：422 + 重试

如果提交的行动下标越界、选了 `validity != 0` 的行动、角色不在候选、挑牌不在候选、换牌不在手牌里，服务端返回 `422`，**请求不会结束**，agent 可以在 deadline 前重试：

```json
{
  "error": "invalid response",
  "requestId": 12,
  "reason": "该行动当前不可用：骰子不足",
  "validity": 3,
  "validityLabel": "骰子不足",
  "action": { "index": 4, "kind": "useSkill" },
  "disabledBy": [],
  "availableActions": [
    { "index": 0, "kind": "useSkill", "validity": 0 },
    { "index": 9, "kind": "declareEnd", "validity": 0 }
  ]
}
```

同时会给该座位发一条 `action_rejected` 事件（包含同样的原因）。`disabledBy` 会列出出战角色身上带 `disableSkill` 的状态（比如冻结）；卡牌类的 `CONDITION_NOT_MET` 会带上 `willBeEffectless`，具体限制可由当前状态/卡面判断。

如果 agent 一直不给出合法响应，到 deadline 后页面会按超时默认行动处理，不会卡死。

---

## 5. 只读接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/arena/state?seat=0` | 该座位最新可见状态 |
| `GET /api/arena/ai/log?seat=1&since=0` | 结构化 AI 事件（分页，见第 6 节） |
| `GET /api/arena/ai/snapshot?seat=1` | 最新完整局面 |
| `GET /api/arena/decks` | 牌组库 |
| `GET /api/arena/deck?seat=1` | 某座位已选牌组 |
| `GET /api/arena/prepare` | 双方牌组 + 是否请求开局 |
| `GET /api/arena/card-text` | 卡牌/技能/状态描述（id → 文本） |
| `POST /api/arena/deck` | 提交牌组 |
| `POST /api/arena/control` | `action:"start"` 请求开局 |
| `GET /api/arena/status` | agent 连接数、pending 数、日志数 |
| `GET /api/arena/log` | 最近 500 条操作日志（AI 每次选择都在里面） |
| `GET /api/arena/spectate` | `{status, states:{0,1}, logs}`，观众/调试用 |
| `GET /api/arena/catalog` | 卡牌/技能数值目录（名称、费用骰子、生命、基础伤害），方便 agent 把 id 翻译成中文 |
| `POST /api/arena/reset` | 清空当前对局数据（页面开局时会自动调用） |

`state` 的结构就是 `@gi-tcg/core` 的 `ExposedGameState`（protobuf 导出的 JSON）。关键字段：

```json
{
  "currentTurn": 0,
  "phase": 3,
  "roundNumber": 2,
  "player": [
    {
      "activeCharacterId": -500000,
      "dice": [..],
      "handCard": [{ "id": -500003, "definitionId": 311501, "type": 3, "tags": [] }],
      "character": [{ "id": -500000, "definitionId": 1103, "variables": { "health": 10, "maxHealth": 10, "energy": 2, "maxEnergy": 2, "aura": 0 } }],
      "combatStatus": [], "summon": [], "support": [], "declaredEnd": false
    },
    { "...对手的公开信息..." }
  ]
}
```

> 变量名和卡牌/技能 id 与上游 `@gi-tcg/core` 一致；`GET /api/arena/catalog` 可以查名字和基础数值。

---

## 6. 结构化 AI 日志（给 agent 的事实流）

页面会把每一次牌局变化翻译成结构化事件，送到桥接服务。agent 有两种拿法：

1. SSE `agent/stream` 里的 `ai_event`（实时）；
2. `GET /api/arena/ai/log?seat=1&since=<seq>&limit=200`（补历史/轮询）。

每条记录：

```json
{ "seq": 123, "at": 1792910000000, "seat": 1, "event": { "type": "damage", ... } }
```

`seat: null` 表示公开事件（双方都能看到）。事件日志最多保留 20000 条；用返回的 `lastSeq` 作为下次的 `since`。

事件类型：

| type | 内容 |
| --- | --- |
| `snapshot` | **完整局面**。开局、每回合结束（召唤物结算后）、对局结束各一次 |
| `request` | 轮到你做选择：`actions` / `hand` / `dice` / `candidates`，每个 action 带 `validity`、`cost`、`preview` |
| `decision` | 某方做出的选择（公开） |
| `dice` | 掷骰/消耗/生成/转化；自己的骰子有值，对手只有 `diceCount` |
| `reroll` | 重投结果（重投了几个） |
| `switch_hands` / `choose_active` / `select_card` | 开局换牌/选人/挑牌结果 |
| `skill` | 谁用了/触发了什么技能（`triggeredOn` 说明触发源） |
| `damage` / `heal` | 来源、目标、数值、元素、反应、前后血量、是否击倒 |
| `variable` | 血量/充能/护盾/可用次数等变量变化（`oldValue → value`） |
| `create_entity` / `remove_entity` / `move_entity` | 状态、召唤物、支援、装备的增删移动 |
| `create_attachment` / `transform` / `reset_variables` | 附魔/变身/每回合重置 |
| `switch_active` / `swap_characters` / `player_flag` / `player_status` | 切人/换位/结束宣言/状态 |
| `phase` / `round` / `turn` / `winner` | 阶段/回合/行动方/胜负 |
| `trigger` | 引擎触发的事件名（`handleEvent`） |
| `action_rejected` | agent 提交了非法操作，见第 4 节 |
| `deck_selected` / `start_requested` | 开局前 agent 选牌组 / 请求开局（公开） |
| `match_reset` | 新一局开始；日志会保留之前的选牌记录，用这个事件分段 |

### 6.1 回合结束快照

每次 `stepRound`（即结束阶段召唤物都结算完之后）会发一条：

```json
{
  "type": "snapshot",
  "reason": "round_end",
  "round": 5,
  "phase": "掷骰",
  "currentTurn": 0,
  "winner": null,
  "me": {
    "seat": 0,
    "activeCharacterId": -500000,
    "activeCharacterName": "凯亚",
    "characters": [
      {
        "id": -500000,
        "definitionId": 1103,
        "name": "凯亚",
        "health": 8,
        "maxHealth": 10,
        "energy": 2,
        "maxEnergy": 2,
        "aura": "冰",
        "defeated": false,
        "tags": { "raw": 0, "disableSkill": false, "shield": false },
        "equipment": [{ "definitionId": 311501, "name": "旅行剑", "variableName": null }],
        "statuses": [{ "definitionId": 106, "name": "冻结", "variableName": "usage", "variableValue": 1 }],
        "entities": []
      }
    ],
    "handCount": 5,
    "hand": [{ "id": -500003, "definitionId": 311501, "name": "旅行剑", "cost": { "text": "2相同元素" } }],
    "diceCount": 8,
    "dice": [8, 3, 1, 1, 7, 7, 2, 5],
    "diceLabels": ["万能", "火", "冰", "冰", "草", "草", "水", "风"],
    "pileCount": 20,
    "supports": [{ "definitionId": 322001, "name": "派蒙", "variableName": "usage", "variableValue": 2 }],
    "summons": [],
    "combatStatuses": [],
    "declaredEnd": false,
    "legendUsed": false,
    "initiativeSkills": [{ "id": 11031, "name": "仪典剑术", "cost": { "1": 1, "0": 2 }, "costText": "1冰+2任意元素" }]
  },
  "opponent": {
    "seat": 1,
    "activeCharacterName": "托马",
    "characters": [ ... ],
    "handCount": 6,
    "diceCount": 8,
    "supports": [ ... ],
    "summons": [ ... ],
    "combatStatuses": [ ... ],
    "declaredEnd": true
  }
}
```

- 对手手牌/骰子只有数量，没有内容；自己的是完整的。
- 角色的 `equipment` / `statuses` 都有 `definitionId`、名字、可见变量（`variableName`/`variableValue`，通常是 usage/shield）。
- `me.initiativeSkills` 给出当前可用技能的费用；`diceLabels` 是骰子面。
- 需要随时拿最新快照：`GET /api/arena/ai/snapshot?seat=1`。

### 6.2 请求事件（轮到你）

```json
{
  "type": "request",
  "seat": 1,
  "rpc": "action",
  "round": 3,
  "phase": "行动",
  "deadline": 1792910000000,
  "attempt": 0,
  "dice": [8, 3, 1],
  "diceLabels": ["万能", "火", "冰"],
  "actions": [
    {
      "index": 0,
      "kind": "useSkill",
      "name": "仪典剑术",
      "skillId": 11031,
      "validity": 0,
      "validityLabel": "合法",
      "cost": { "dice": { "0": 2, "1": 1 }, "total": 3, "text": "2任意元素+1冰" },
      "autoSelectedDice": [1, 2, 2],
      "preview": [{ "type": "damage", "target": "托马", "amount": 2, "element": "物理" }]
    }
  ],
  "validActions": [ ... ],
  "activeCharacter": { "name": "凯亚", "tags": { "disableSkill": true } },
  "disabledBy": [{ "definitionId": 106, "name": "冻结" }]
}
```

- `requestId`：对应 SSE `request` 的 `id`；响应时用它。
- `attempt`：本次掷骰阶段里的第几次重投请求（从 0 开始）；卡牌给额外重投时会出现 `attempt > 0`。返回空数组会放弃剩余重投。
- `disabledBy`：当前出战角色身上带 `disableSkill` 的状态（例如冻结）。
- `validity` 非 0 的 action 不要选；选了会被 422 拒绝。

### 6.3 伤害/增益事件

```json
{
  "type": "damage",
  "seat": 0,
  "source": { "id": -500000, "definitionId": 1103, "name": "凯亚" },
  "target": { "id": -500010, "definitionId": 1311, "name": "托马", "healthBefore": 5, "healthAfter": 2 },
  "amount": 3,
  "element": "冰",
  "reaction": "融化",
  "defeated": false
}
```

```json
{
  "type": "variable",
  "entity": { "id": -500010, "definitionId": 1311, "name": "托马" },
  "variable": "health",
  "oldValue": 5,
  "value": 2,
  "direction": 2
}
```

> `variable` 会包含血量变化（和 damage 有重叠），也会包含充能、护盾、次数、每回合标记等；agent 按需过滤即可。

---

## 7. 计时 / 耗时间

- 页面为每次 `rpc` 计时：行动/挑选用「每次行动限时」，其余用「开局限时」。
- 计时归零：页面自动提交默认响应：
  - `action` → 结束回合（`declareEnd`）
  - `chooseActive` / `selectCard` → 第一个候选
  - `rerollDice` / `switchHands` → 空
- 每个座位有「耗时间」开关：开启后页面**不请求 agent**，直接等满计时再自动过。
- agent 也可以故意延迟响应来“烧绳”（在 `deadline` 前提交即可）。
- 整局上限（分钟，0=不限）到达后，按双方剩余生命比例判定。

---

## 8. 最小 Python 示例

```python
import json, threading, urllib.request

BASE = "http://127.0.0.1:7717"
SEAT = 1

def default_response(req):
    rpc = req["request"]
    case = rpc["$case"]
    if case == "action":
        actions = rpc["value"]["action"]
        idx = next((i for i, a in enumerate(actions)
                    if a["action"]["$case"] == "declareEnd" and a["validity"] == 0), 0)
        return {"response": {"$case": "action", "value": {
            "chosenActionIndex": idx,
            "usedDice": actions[idx].get("autoSelectedDice", []),
        }}}
    if case == "chooseActive":
        return {"response": {"$case": "chooseActive", "value": {
            "activeCharacterId": rpc["value"]["candidateIds"][0]}}}
    if case == "selectCard":
        return {"response": {"$case": "selectCard", "value": {
            "selectedDefinitionId": rpc["value"]["candidateDefinitionIds"][0]}}}
    if case == "rerollDice":
        return {"response": {"$case": "rerollDice", "value": {"diceToReroll": []}}}
    return {"response": {"$case": "switchHands", "value": {"removedHandIds": []}}}

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req).read()

def on_event(event, data):
    if event == "request":
        post("/api/arena/agent/respond", {
            "matchId": "local", "id": data["id"],
            "response": default_response(data["request"]),
        })

# SSE 用 urllib 逐行读即可；生产建议用 httpx-sse / requests-sse
with urllib.request.urlopen(f"{BASE}/api/arena/agent/stream?matchId=local&seat={SEAT}") as r:
    event, buf = "message", ""
    for raw in r:
        line = raw.decode("utf-8").rstrip("\n")
        if line == "":
            if buf:
                on_event(event, json.loads(buf))
            event, buf = "message", ""
        elif line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            buf += line[6:]
```

---

## 9. 安全 / 部署

- 桥接服务只监听 `127.0.0.1`，没有鉴权；**不要**把 `--host 0.0.0.0` 暴露到公网。
- agent 的 API key 只应该放在 agent 进程里，不要写进网页或通过接口传。
- 对局在浏览器里；页面关闭 = 对局结束。想要“后台常驻对局”，需要把 `Game` 挪到 Node 进程（当前版本没做，属于下一步可选升级）。
