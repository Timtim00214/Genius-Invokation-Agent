# 跟进上游更新 · 详细手册

上游仓库：<https://github.com/piovium/genius-invokation>
本文覆盖：**加新卡 / 平衡性数值调整 / bug 修复 / 小重构** 四种情况，以及冲突处理和验证。

---

## 0. 先理解我们的“三层结构”

| 层 | 内容 | 上游更新时会不会冲突 |
| --- | --- | --- |
| 上游代码 | `packages/` 里的原始实现 | 自动合并，冲突很少 |
| 本地新增 | `local/`、`packages/local-rules/`、`packages/standalone/src/workshop/` | **不会冲突**（上游没有这些文件） |
| 本地补丁 | 4 个文件里的 `[LOCAL PATCH]` 小块 | 只有上游改到这几行才会冲突 |

**关键设计**：卡牌数值不是改 `packages/data` 源码，而是运行时覆盖（`@gi-tcg/local-rules`）。所以上游调整数值、加新卡时，我们**不需要改任何卡牌定义**，只要重新生成数值目录。

当前基线：`5faa0675`（2026-10-23），分支 `local`。

---

## 1. 一次性准备（已经做过；换电脑/重新 clone 时才需要）

```bat
git clone https://github.com/piovium/genius-invokation.git
cd genius-invokation
git remote add upstream https://github.com/piovium/genius-invokation.git
git fetch upstream main
git switch -c local upstream/main
```

如果想把本地改动备份到自己的 GitHub：

```bat
git remote add origin https://github.com/<你的用户名>/genius-invokation-local.git
git push -u origin local
```

以后 `origin` = 你的备份，`upstream` = 官方。

---

## 2. 每次更新：一条命令

```bat
local\update-upstream.cmd
```

它按顺序做：

1. `git fetch upstream main`
2. 列出 `HEAD..upstream/main` 的新提交，问你确认
3. 工作区干净时 `git merge upstream/main`
4. `pnpm-lock.yaml` 变了就自动 `pnpm install --frozen-lockfile`
5. 先构建一次依赖（`pnpm build "standalone..."`），让 core/data/assets-manager 的 dist 更新
6. 重新生成数值目录（`node local/gen-catalog.mjs`）、牌组库（`node local/gen-decks.mjs`）和卡牌文本
7. 再构建一次（把新目录/牌组文本打进产物）
8. 检查本地补丁锚点（`node local/check-patches.mjs`）
9. 跑无头回归测试（`node local/test-local.mjs`）
10. 重新打包便携版

只看有没有更新、不动手：

```bat
local\update-upstream.cmd --check
```

---

## 3. 手工流程（脚本失败时照着做）

```bat
:: 0) 先备份当前状态
git status
git tag backup-2026-10-25

:: 1) 拉取
git fetch upstream main
git log --oneline HEAD..upstream/main
git diff --stat HEAD...upstream/main

:: 2) 合并
git merge upstream/main

:: 3) 如果 package.json / pnpm-lock.yaml 变了
pnpm install --frozen-lockfile

:: 4) 先构建依赖，再生成目录/牌组/文本
pnpm build "standalone..."
node local\gen-catalog.mjs
node local\gen-decks.mjs

:: 5) 再构建一次（把新目录打进产物）
pnpm build "standalone..."

:: 6) 自检
node local\check-patches.mjs
node local\test-local.mjs

:: 7) 重新打包便携版
node local\build-portable.mjs --skip-build
```

> 为什么先生成目录再 build？`local-rules` 会把 `catalog.json` 打包进 `dist`；顺序反了，界面里还是旧数值。

---

## 4. 四种典型更新

### 4.1 只加了新卡 / 新角色

**做什么**：正常合并 + 重新生成目录 + 重新打包。不需要改代码。

**怎么确认生效**：

- `node local/gen-catalog.mjs` 输出里的 `characters` / `cards` 数量比上次多；
- `node local/check-patches.mjs` 显示目录版本 = 核心版本；
- 打开「数值工坊」，搜索新卡名字能搜到；如果显示成 `#123456`，说明 `@name` 注释格式变了，需要检查 `gen-catalog.mjs`。

**旧预设**：按卡牌 id 保存，新卡加入不影响；新卡默认就是原版数值。

### 4.2 平衡性调整（改了血量/费用/伤害）

**做什么**：正常合并 + 重新生成目录 + 重新打包。**不要**去改 `packages/data`。

**旧预设会怎样**：

- 工坊的“默认值”变成新数值；
- 预设里**没改过**的项自动跟随新默认值；
- 预设里**改过**的项仍然是你设的值（例如你把旅行剑加成设成 100，它还是 100）；
- 如果某张卡被重做、技能 id 变了，预设里针对旧技能 id 的覆盖会变成“孤儿”：不报错、也不生效。处理办法：打开预设 → 看“改动 N 项” → 用「恢复原版数值」清空后重新调。

**建议**：每次大版本更新后，打开工坊逐个预设看一眼，尤其是改过 `生命值` 的预设（新版本可能调整了基础血量）。

### 4.3 bug 修复 / 核心逻辑改动

**做什么**：正常合并。绝大多数会自动合并；如果改到了我们打过补丁的文件，见第 5 节。

**必须做**：`node local/check-patches.mjs` + `node local/test-local.mjs` + 手动开一局（普通模式 + 工坊预设模式各一局）。

### 4.4 大重构（目录/接口/构建方式变了）

**症状**：`pnpm build` 报错、`check-patches` 说锚点缺失、`test-local` 导入失败。

**处理**：

1. 先看 `git diff HEAD...upstream/main -- <报错文件>`，理解上游怎么改的；
2. 我们的补丁都很小，按新结构挪过去即可：
   - 伤害钩子：找 `ModifyDamage1EventArg.increaseDamage` 和 `SkillContext.damage` 的新位置；
   - 前端入口：找 `App.tsx` 的 `GameMode` 和按钮位置；
   - 预设应用：找 `StandaloneParent.createGame` 里 `createInitialState` 的位置。
3. 改完重新跑 `check-patches` / `test-local`。

---

## 5. 冲突解决（唯一需要动脑的地方）

先看哪些文件冲突：

```bat
git status --short
```

本地补丁只在这几处，冲突基本不会超出这个范围（`packages/standalone/src/arena/`、`local/arena/`、`packages/local-rules/` 都是新增文件，不会冲突）：

| 文件 | 我们改了什么 | 合并原则 |
| --- | --- | --- |
| `packages/standalone/src/index.tsx` | 注释掉 Vercel Analytics | 保留上游新代码，把 `inject()` 再注释掉 |
| `packages/core/src/base/skill.ts` | `increaseDamage` 里调用钩子 | 保留上游算法，把 `const actualValue = ...` 两行加回去 |
| `packages/core/src/runtime/skill_context.ts` | `damage()` 开头调用钩子 | 保留上游逻辑，在 `heal` 分支后加回覆盖 |
| `packages/standalone/src/App.tsx` | 工坊入口 / 预设显示 / AI 对战入口与弹窗 | 保留上游 UI，重新插入 `GameMode.Workshop`、`GameMode.Arena`、对应按钮和 `<Match>` |
| `packages/standalone/src/StandaloneParent.tsx` | `applyPresetToData` | 保留上游 `createGame`，把 `data:` 换成 `applyPresetToData(...)` |
| `packages/standalone/package.json` | 加 `@gi-tcg/local-rules` 依赖 | 通常自动合并 |

**操作建议**：

```bat
:: 放弃这次合并，回到干净状态
git merge --abort

:: 或者逐个文件解决后
git add -A
git commit
```

解决冲突时不要凭记忆：直接看冲突块里的 `[LOCAL PATCH]` 注释，把**注释下面那几行**保留下来即可。

---

## 6. 验证清单（每次更新后）

```bat
node local\check-patches.mjs    :: 锚点 + 目录版本
node local\test-local.mjs       :: 无头对局：2→100、3→102
pnpm build "standalone..."
node local\build-portable.mjs --skip-build
```

浏览器里再点一遍：

- [ ] 首页「开始对局」→ 双方棋盘正常
- [ ] 首页「数值工坊」→ 表格有数据、搜索正常
- [ ] 工坊里「示例：所有武器 +100」→ 保存 → 用此预设开局 → 武器伤害明显变高
- [ ] 切回「原版」→ 开局 → 数值恢复
- [ ] 便携版：`Start.cmd` 和 `Workshop.cmd` 都能打开

---

## 7. 回滚

```bat
:: 回到合并前的备份标签
git reset --hard backup-2026-10-25

:: 或者只回滚某次提交
git revert <commit>
```

便携版直接重新打包即可。

---

## 8. 长期维护建议

- **每次合并前打 tag**：`git tag backup-2026-10-25`；
- **发布自己的版本**：`git tag local-v1.0`，推到 `origin`；
- **不要在上游文件里做大改**：新功能优先放 `local/` 或新 package；
- **数值相关永远走预设**，不要改 `packages/data`；
- 上游大概每 1~2 个月一个大版本，合并本身通常 5 分钟，验证 10 分钟。

---

## 9. FAQ

**Q：双击 `update-upstream.cmd` 提示工作区不干净？**
先 `git status` 看是什么；确认后用 `git stash` 暂存，更新完 `git stash pop`。不要把 `node_modules`、`dist` 提交进去（已被 `.gitignore` 忽略）。

**Q：pnpm install 报 Node 版本不对？**
上游要求 Node 26。便携版自带 `node.exe`；本机开发建议装 Node 26（或把便携版的 `node.exe` 放到 PATH 里）。Node 24 目前也能跑，但上游以后可能用新 API。

**Q：`pnpm build` 报 `result.json` 找不到？**
先完整构建依赖：`pnpm build "standalone..."`（不要只 `cd packages/standalone && pnpm build`）。

**Q：图片 404 / 黑块？**
图片来自 `static-data.piovium.org`，新卡刚出时官方静态资源可能还没更新；过几天再开，或忽略（不影响结算）。

**Q：数值工坊里改了但开局没效果？**
检查三点：① 预设是否保存并处于“当前预设”；② 首页是否显示该预设；③ 是不是选了旧游戏版本（工坊目录只对应当前版本）。

**Q：能把预设分享给别人吗？**
可以。工坊右上角「导出」生成 JSON；对方「导入」即可。预设只存 id 和数值，不包含卡牌代码。
