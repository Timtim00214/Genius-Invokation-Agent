# NOTICE · 来源与改动说明

本项目（Genius Invokation Agent）是以下项目的衍生作品：

- **上游**：[piovium/genius-invokation](https://github.com/piovium/genius-invokation)
- **上游许可证**：GNU Affero General Public License v3.0（见 `LICENSE`，本仓库沿用同一许可证）
- **衍生关系**：`engine/` 目录是上游源码的一个 fork，包含下述修改；`ai-backend/`、`driver/`、`启动.mjs`、`release/build.py` 为本项目新增。

## 对上游的修改（`engine/` 内）

| 文件 | 改动 |
|---|---|
| `proto/action.proto`、`proto/rpc.proto`（及 `packages/typings/proto/` 同名文件） | 新增 `PassAction` / `pass_action = 6`：过一手，交出行动权、不消耗资源、不结束回合 |
| `packages/core/src/base/skill.ts` | 新增 `PassInfo` 并加入 `ActionInfoBase` 联合；`isPass()` |
| `packages/core/src/game.ts` | `availableActions()` 下发 pass（对手已宣告结束时不可用）；执行 pass 后仅切换行动权 |
| `packages/core/src/io.ts`、`packages/core/src/preview.ts` | 序列化与预览支持 pass |
| `packages/standalone/src/arena/defaultResponse.ts` | 超时/让过的默认响应优先「过一手」，其次才「结束回合」 |
| `packages/standalone/src/arena/seatIo.ts` | 新增 `onPendingRequest` / `onPendingSettled`，支持玩家在对局中手动点「过一手」 |
| `packages/standalone/src/arena/Arena.tsx` | 「过一手」按钮、烧绳文案与说明 |
| `packages/standalone/src/arena/aiEvents.ts` | AI 侧事件里显示 pass 的中文名 |
| `local/arena/bridge.mjs`、`packages/standalone/src/arena/bridge.ts` | Windows 友好路径、桥接健壮性、AI 日志顺序等本地改动 |

`engine/packages/standalone/dist/` 是上述改动后的构建产物（已提交，便于直接运行）。

## 新增部分（本项目原创）

`ai-backend/`（节点图策略后端与插件：`action_rank`、`choice_parse`、`choice_map`、`rpc_build`、`game_log`、`prompt_pack`、`llm_chat`、`deck_brief`、`plan_guard` 等）、`driver/`（AI 驱动、决策蓝图、回放台、牌组码解析、赛前备赛）、`启动.mjs`、`release/build.py`，以 AGPL-3.0 同样发布。

## 素材与商标

卡牌插画、卡牌文本、`catalog.json` 等游戏素材版权归 **米哈游（miHoYo / HoYoverse）** 所有，随本项目仅作学习交流用途，不得用于商业目的。本项目与米哈游无任何关联，未获其授权或认可。

## 许可证全文

GNU Affero General Public License v3.0，见仓库根目录 `LICENSE`。
