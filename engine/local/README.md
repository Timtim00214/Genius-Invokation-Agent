# 本地增强版工作区（local/）

这里放**所有本地新增/修改**，尽量不碰上游代码，方便跟进 `piovium/genius-invokation` 的更新。

**当前进度**

- ✅ 第 1 步：本地运行 + 免安装便携版
- ✅ 第 2 步：数值工坊（查看全部卡牌数值 / 批量修改 / 预设保存切换）
- ✅ 第 3 步：AI 对战场（主窗口 + 弹窗双棋盘、HTTP/SSE 接口、计时/耗时间）
- ✅ 上游跟进机制

---

## 1. 怎么打开（不需要敲命令）

| 想做什么 | 便携版（发给别人） | 本仓库开发 |
| --- | --- | --- |
| 玩游戏 | 双击 `Start.cmd` | 双击 `Start.cmd`（或 `local\start-dev.cmd`） |
| 打开数值工坊 | 双击 `Workshop.cmd` | 双击 `Workshop.cmd`（或 `local\start-workshop.cmd`） |
| 跟进上游 | —— | 双击 `local\update-upstream.cmd` |

便携版在 `dist-portable\GeniusInvokation-Portable\`，压缩包是 `dist-portable\GeniusInvokation-Portable.zip`（约 39 MB，自带 Node，对方不用装环境）。

便携包里的启动脚本故意用英文名（`Start.cmd` / `Workshop.cmd` / `Arena.cmd`）：中文文件名在不同 Windows 的 zip 解压工具里会乱码（实际解压时就出现过把 `AI对战.cmd` 显示成 `AI u.cmd` 的问题）。脚本内容仍是中文提示，`README.txt` 里写了中英对照。

也可以在应用内点：首页「数值工坊」按钮，或直接访问 `http://127.0.0.1:7717/#workshop`。

---

## 2. 数值工坊怎么用

1. 首页点「数值工坊」（或直接双击 `Workshop.cmd`）。
2. 默认是内置的「原版」预设，所有数值都是官方值。
3. 直接改表格里的数值，会自动生成一个「自定义」预设：
   - **角色页**：生命值、最大充能；右侧列出技能与基础伤害。
   - **行动牌页**：打出费用（点费用可以逐项编辑骰子类型/数量）、伤害加成（武器/圣遗物），支持按名称/ID/标签搜索。
   - **技能页**：单一基础伤害可直接改；每个技能也可以点“消耗骰子”逐项编辑费用。多段/复杂伤害只读（避免把多段伤害改乱）。
   - 骰子编号：0=任意元素、1–7=冰/水/火/雷/风/岩/草、8=相同元素、9=充能、10=秘传。填 0 即移除该类骰子；全部为 0 就是 0 费。
4. **批量修改**：先勾选（或「全选筛选结果」），在顶部输入数值，点「设为生命 / 设为费用 / 设为伤害加成…」。批量「设为费用」只改总数，会保留每张卡/每个技能原有的骰子类型。
5. 想复刻「所有武器 100 伤害」：直接点「示例：所有武器 +100」。
6. 点「保存预设」，然后「用此预设开始对局」。
7. 想玩原版：预设下拉切回「原版」，或在首页点「切回原版」。

**预设管理**：新建 / 复制 / 重命名 / 删除 / 导出 JSON / 导入 JSON。预设存在浏览器 `localStorage`，换浏览器或清缓存会丢，重要预设请导出备份。

**保证不影响原版**：

- 选「原版」时，核心钩子是 `null`，`GameData` 原对象直接透传，结算路径与原版完全一致。
- 只有启用带改动的预设，才会克隆数据 / 替换伤害数值。
- 回归测试 `local\test-local.mjs` 会验证：原版普攻 2、装备旅行剑 3、覆盖后 100 / 102。

**已知限制**：

- 数值目录对应当前版本（`v7.1.0`）。首页把「游戏版本」切到很旧的版本时，建议用原版预设。
- 只改结算数值；卡牌描述文字（静态资源）不会跟着变。
- 预设按卡牌/技能 id 保存。上游重做某张卡导致技能 id 变化时，旧预设里的对应项会失效（不报错），可用「恢复原版数值」重来。

---

## 3. 现在怎么构建 / 打包

```bat
pnpm install
pnpm build "standalone..."
node local\build-portable.mjs          :: 含 node.exe，产出 zip
node local\build-portable.mjs --skip-build
```

开发热更新：`cd packages/standalone && pnpm dev`（注意先构建过一次依赖包）。

### 给接收者的源码包

便携 zip 只有构建后的 `app/`，不能改 UI/规则；要改代码发源码包：

```text
dist-portable/GeniusInvokation-Source.zip      # 文件名全 ASCII，Windows 直接解压
dist-portable/GeniusInvokation-Source.tar.gz   # 同内容，保留 Unix 风格
```

源码包约 18 MB，不含 `node_modules`、`.git`、构建产物。接收者解压后：

```bat
pnpm install --frozen-lockfile
pnpm build "standalone..."
node local/server.mjs
```

包内 `SOURCE-README.txt` 写了完整步骤；`packages/server/public` 原为符号链接，包内用空目录 + 说明代替（只开发 standalone 不需要它）。

### 给接手开发者/Agent 的完整交接包

上面那个 18 MB 源码包是“轻量源码”，**故意不带** `.git`、`dist-portable`、预构建 `dist`；如果接手者要 `git log`、`update-upstream`、直接启动，应该用：

```text
dist-portable/GeniusInvokation-Dev.zip     # 约 140 MB
```

它包含完整 `.git`（`local` 分支、`upstream` remote、全部提交）、预构建 `packages/standalone/dist`、便携 zip、`README-FIRST.txt`；仍然不带 `node_modules`（880 MB、3401 个符号链接，跨机器复制必坏）。解压后先看 `README-FIRST.txt`：

```bat
Start.cmd                 :: 不装依赖也能启动（dist 已带）
pnpm install --frozen-lockfile
pnpm build "standalone..."
node local\server.mjs
```

`node_modules` 必须在目标机器重新安装；不要把原机器的 `node_modules` 复制过去。

---

## 4. 本地改动约定

`git` 已初始化，当前分支 `local`，基线是上游 `5faa0675`（2026-10-23，也就是你下载的 zip 对应的提交）。

- `upstream` remote = <https://github.com/piovium/genius-invokation>
- 本地改动分两类：
  1. **新增文件**：`local/`、`packages/local-rules/`、`packages/standalone/src/workshop/`（上游没有，不会冲突）。
  2. **对上游文件的修改**：一律用 `[LOCAL PATCH]` 注释标记，且只有几行。清单：
     - `packages/standalone/src/index.tsx`：关闭 Vercel Analytics
     - `packages/core/src/base/skill.ts`：`increaseDamage` 钩子
     - `packages/core/src/runtime/skill_context.ts`：技能基础伤害钩子
     - `packages/standalone/src/App.tsx`：工坊入口 / 预设显示
     - `packages/standalone/src/StandaloneParent.tsx`：应用预设
- 不要直接改 `packages/data` 里的卡牌定义来做“改数值”；数值修改走运行时覆盖层。

查看改了什么：

```bat
git diff upstream/main...local --stat
git log --oneline --graph --all -20
```

---

## 5. 跟进上游更新

**详细手册见 [UPSTREAM.md](./UPSTREAM.md)**，覆盖新卡、数值调整、bug 修复、重构和冲突处理。

最短流程：

```bat
local\update-upstream.cmd
```

它做：fetch → 列新提交 → merge → 装依赖 → **重新生成数值目录** → 构建 → 检查补丁锚点 → 无头回归测试 → 重新打包便携版。

只看不动：`local\update-upstream.cmd --check`。

上游更新后如果本地补丁位置变了：

```bat
node local\check-patches.mjs
```

会明确指出哪个文件缺了哪个锚点。

---

## 6. AI 对战场（接口 + 观战，已完成）

**和原版本地模拟一致：还是两个窗口，一个窗口一个棋盘。**

- **主窗口**：一个棋盘 + 计时 + 实时操作日志；PvE 时这里就是真人操作盘。
- **弹窗**：另一个棋盘；PvE / EvE 时这就是 AI 的棋盘，**始终可见**，人类可以一直盯着它。
- PvP 关掉弹窗按原版规则判负（先手方退赛）；PvE / EvE 关掉弹窗只是少看一块棋盘，对局继续，右上角可以重新打开。
- PvE 会在弹窗里隐藏 AI 的手牌和骰子（防止真人偷看），但 AI 的每一次选择都会实时写进主窗口日志，伤害/召唤/切换等场面变化也在弹窗棋盘上。
- EvE 是纯观战：双方手牌都可见，适合演示和评测。

**打开方式（不用敲命令）**

| 场景 | 便携版 | 开发版 |
| --- | --- | --- |
| 进 AI 对战场 | 双击 `Arena.cmd` | 双击 `Arena.cmd`（或 `local\start-arena.cmd`） |

也可以首页点「AI 对战」，或直接访问 `http://127.0.0.1:7717/#arena`。

**三种模式**

- `真人 vs 真人`：AI 关闭，主窗口后手、弹窗先手，和原版一样。
- `真人 vs AI`：真人永远在主窗口；AI 在弹窗。
- `AI vs AI`：两边都走接口，主窗口和弹窗各显示一个 AI 棋盘。

**计时 / 耗时间**

- 每次行动、开局选人/重投分别限时（默认 45s / 60s，可改 5–300s）。
- 每个座位一个「耗时间」开关：开启后不提交行动，等计时归零自动结束回合（和在线平台超时一致）。
- 整局上限（分钟，0=不限）到达后按双方剩余生命比例判定。
- LLM 思考慢就把行动限时调大；agent 也可以故意延迟到 deadline 再提交。

**Agent 接口**（详见 [API.md](./arena/API.md)）

| 接口 | 用途 |
| --- | --- |
| `GET /api/arena/decks` | 牌组库（id/名称/角色/30 张牌/说明） |
| `GET /api/arena/card-text` | 卡牌/技能/状态描述（挑牌用） |
| `POST /api/arena/deck` | agent 提交某个座位的牌组（支持 `deckId` 或自定义数组） |
| `POST /api/arena/control` | `action:"start"` 请求开局；页面会自动开始 |
| `GET /api/arena/agent/stream?seat=1` | SSE：`hello` / `state` / `request` / `ai_event` / `timeout` |
| `POST /api/arena/agent/respond` | 提交完整的 `RpcResponse`；非法操作返回 422 可重试 |
| `GET /api/arena/ai/log?seat=1&since=0` | 结构化 AI 事件日志（每回合快照 + 所有变化） |
| `GET /api/arena/ai/snapshot?seat=1` | 最新完整局面 |
| `GET /api/arena/state?seat=0` | 该座位最新原始可见状态 |
| `GET /api/arena/status` | agent 连接数 / pending / 牌组是否就绪 |
| `GET /api/arena/prepare` | 双方牌组 + 是否请求开局 |
| `GET /api/arena/spectate` | 双方状态 + 日志（观众/调试） |
| `GET /api/arena/catalog` | 卡牌/技能数值目录（id → 名字/费用/生命/基础伤害） |

**开局前流程**：agent 先 `GET /decks` 浏览 + `GET /card-text` 读牌面 → 选一套 → `POST /deck {seat, deckId, note}` → `POST /control {action:"start"}`。页面在 `#arena` 配置页轮询，AI 座位牌组就绪且收到 start 请求时自动开局（需允许弹窗；被拦截时手动点开始，仍用 agent 选的牌组）。

**对局中数据**：每个变化都会进 `/api/arena/ai/log`（SSE 也会推 `ai_event`）：
- `request`：轮到你，含所有可行动作、合法性、费用、预览；
- `decision` / `skill` / `damage` / `variable` / `dice` / `reroll` / `create_entity` / `remove_entity` 等实时事件；
- **每回合结束（召唤物结算完）一次完整 `snapshot`**：双方血量/充能/装备/状态/召唤物/支援/出战状态，自己的手牌和骰子，对手的手牌/骰子只给数量；
- `action_rejected`：非法操作（例如被冻结禁用技能、骰子不足）会在 422 里给出原因、`validityLabel`、`disabledBy` 和可用动作，agent 可以在 deadline 前重试。

示例 agent：`local/arena/example-agent.mjs`（只用来验证接口：`--pickDeck 1` 会选牌组并请求开局，`--manual 1` 只收请求不响应，方便调试）。你自己的 agent 直接替换决策函数即可。

**实现方式**：`Game` 跑在浏览器里（页面是对局宿主），Node 侧只做消息中转，所以 agent 只依赖 HTTP/SSE，Python/JS/任何语言都能接；页面关闭 = 对局结束。

---

## 7. 目录说明

```
local/
  server.mjs            零依赖静态服务器（--hash "#workshop" 可直接开工坊）
  start-dev.cmd         启动游戏首页
  start-workshop.cmd    直接打开数值工坊
  start-arena.cmd       直接打开 AI 对战场
  build-portable.mjs    构建 + 打包便携版（含 node.exe / Start.cmd / Workshop.cmd / Arena.cmd）
  update-upstream.mjs   跟进上游（fetch + merge + 目录 + 构建 + 自检 + 打包）
  gen-catalog.mjs       从 GTS 源码生成卡牌数值目录
  check-patches.mjs     检查 [LOCAL PATCH] 锚点是否还在
  test-local.mjs        无头对局回归测试
  UPSTREAM.md           跟进上游的详细手册
  arena/
    bridge.mjs          AI 对战桥接服务（HTTP/SSE，零依赖）
    API.md              agent 接口文档（牌组/结构化日志/422 重试）
    example-agent.mjs   示例 agent（验证接口用）
    decks.json          牌组库（自动生成）
    card-text.json      卡牌/技能/状态描述（自动生成）
  _verify*.mjs          浏览器冒烟测试（headless Chrome 截图）
```

新增的包/目录：

```
packages/local-rules/                 预设类型、数据覆盖、伤害钩子安装、数值目录
packages/local-rules/src/catalog.json 自动生成的卡牌数值目录（勿手改）
packages/standalone/src/workshop/     数值工坊 UI + 本地存储
packages/standalone/src/arena/        AI 对战配置页 / 主窗口 / 弹窗棋盘 / 结构化事件
```

上游原有目录保持原样；对它们的修改都有 `[LOCAL PATCH]` 标记。
