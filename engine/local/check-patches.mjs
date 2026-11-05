#!/usr/bin/env node
/**
 * 检查 `[LOCAL PATCH]` 锚点是否还在。
 *
 * 上游重构后如果这里报错，说明需要人工把本地补丁挪到新位置。
 * 详细处理见 local/UPSTREAM.md。
 *
 *   node local/check-patches.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const repo = path.resolve(import.meta.dirname, "..");
const failures = [];

function read(rel) {
  const p = path.join(repo, rel);
  if (!fs.existsSync(p)) {
    failures.push(`${rel} 不存在`);
    return "";
  }
  return fs.readFileSync(p, "utf8");
}

const anchors = [
  {
    name: "核心：数值钩子模块",
    file: "packages/core/src/local_rules.ts",
    needs: ["setLocalRulesHooks", "getLocalRulesHooks"],
  },
  {
    name: "核心：伤害加成钩子",
    file: "packages/core/src/base/skill.ts",
    needs: ["[LOCAL PATCH]", "getLocalRulesHooks"],
  },
  {
    name: "核心：技能基础伤害钩子",
    file: "packages/core/src/runtime/skill_context.ts",
    needs: ["[LOCAL PATCH]", "baseDamage"],
  },
  {
    name: "前端：关闭 analytics",
    file: "packages/standalone/src/index.tsx",
    needs: ["[LOCAL PATCH]"],
  },
  {
    name: "前端：数值工坊入口",
    file: "packages/standalone/src/App.tsx",
    needs: ["数值工坊", "GameMode.Workshop", "GameMode.Arena", "ArenaBoardChild"],
  },
  {
    name: "前端：应用预设",
    file: "packages/standalone/src/StandaloneParent.tsx",
    needs: ["applyPresetToData", "installPreset"],
  },
  {
    name: "AI 对战页",
    file: "packages/standalone/src/arena/Arena.tsx",
    needs: ["window.open", "createAgentSeatIo", "arena-board-"],
  },
  {
    name: "AI 对战弹窗",
    file: "packages/standalone/src/arena/ArenaBoardChild.tsx",
    needs: ["postMessage", "disableAction"],
  },
  {
    name: "AI 桥接服务",
    file: "local/arena/bridge.mjs",
    needs: ["agent/stream", "agent/respond", "agent/request", "ai/log", "decks", "card-text", "validateResponse"],
  },
  {
    name: "AI 结构化事件",
    file: "packages/standalone/src/arena/aiEvents.ts",
    needs: ["buildSnapshot", "mutationEvents", "requestEvent"],
  },
  {
    name: "牌组库",
    file: "local/arena/decks.json",
    needs: ['"decks"'],
  },
  {
    name: "卡牌文本",
    file: "local/arena/card-text.json",
    needs: ['"descriptions"'],
  },
  {
    name: "AI 接口文档",
    file: "local/arena/API.md",
    needs: ["agent/stream", "agent/respond", "ai/log", "action_rejected"],
  },
  {
    name: "数值目录",
    file: "packages/local-rules/src/catalog.json",
    needs: ['"characters"', '"cards"'],
  },
];

console.log("检查本地补丁锚点：\n");
for (const a of anchors) {
  const content = read(a.file);
  const missing = a.needs.filter((n) => !content.includes(n));
  if (missing.length) {
    failures.push(`${a.name}（${a.file}）缺少: ${missing.join(", ")}`);
    console.log(`FAIL  ${a.name}  [${a.file}]`);
  } else {
    console.log(`  ok  ${a.name}`);
  }
}

// 数值目录版本是否与核心当前版本一致
try {
  const catalogPath = path.join(repo, "packages/local-rules/src/catalog.json");
  const catalog = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
  const coreUrl = pathToFileURL(path.join(repo, "packages/core/dist/index.js")).href;
  const { CURRENT_VERSION } = await import(coreUrl);
  if (catalog.version !== CURRENT_VERSION) {
    failures.push(
      `数值目录版本 ${catalog.version} 与核心 ${CURRENT_VERSION} 不一致，请运行 node local/gen-catalog.mjs`,
    );
    console.log(`FAIL  数值目录版本（${catalog.version} != ${CURRENT_VERSION}）`);
  } else {
    console.log(`  ok  数值目录版本 ${catalog.version}`);
  }
  console.log(
    `  --  数值目录：${catalog.counts?.characters ?? "?"} 角色 / ${catalog.counts?.cards ?? "?"} 行动牌`,
  );
} catch (e) {
  failures.push(`读取数值目录失败: ${e.message}`);
}

console.log("");
if (failures.length) {
  console.error("以下位置需要人工检查（见 local/UPSTREAM.md）：");
  for (const f of failures) console.error(` - ${f}`);
  process.exit(1);
}
console.log("所有锚点正常。");
