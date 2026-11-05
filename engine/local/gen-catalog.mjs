#!/usr/bin/env node
/**
 * 从 GTS 源码 + 运行时卡牌数据生成“数值目录”。
 *
 *   node local/gen-catalog.mjs
 *
 * 产物: packages/local-rules/src/catalog.json
 * 上游更新卡牌后重新运行即可（update-upstream 会自动调用）。
 */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const repo = path.resolve(import.meta.dirname, "..");
const dataSrc = path.join(repo, "packages/data/src");
const outFile = path.join(repo, "packages/local-rules/src/catalog.json");

const dataUrl = pathToFileURL(path.join(repo, "packages/data/dist/index.js")).href;
const coreUrl = pathToFileURL(path.join(repo, "packages/core/dist/index.js")).href;
const { default: getData } = await import(dataUrl);
const { CURRENT_VERSION, playSkillOfCard } = await import(coreUrl);

// ---------- 极简 GTS 解析 ----------
function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === "old_versions") continue; // 旧版本定义不参与当前数值
      walk(p, out);
    } else if (e.name.endsWith(".gts")) out.push(p);
  }
  return out;
}

function parseBlocks(src) {
  const lines = src.split(/\r?\n/);
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    const m = /^\s*define\s+(\w+)\s*\{/.exec(lines[i]);
    if (!m) continue;
    let j = i - 1;
    while (j >= 0 && lines[j].trim() === "") j--;
    let doc = "";
    if (j >= 0 && lines[j].trim() === "*/") {
      const end = j;
      let start = j;
      while (start >= 0 && !lines[start].includes("/**")) start--;
      if (start >= 0) doc = lines.slice(start, end + 1).join("\n");
    }
    let depth = 0;
    let end = i;
    for (let k = i; k < lines.length; k++) {
      for (const ch of lines[k]) {
        if (ch === "{") depth++;
        else if (ch === "}") depth--;
      }
      if (depth === 0) {
        end = k;
        break;
      }
    }
    blocks.push({ kind: m[1], doc, body: lines.slice(i, end + 1).join("\n") });
    i = end;
  }
  return blocks;
}

const gtsNames = new Map(); // id -> name
const gtsDescriptions = new Map(); // id -> description
const gtsDamages = new Map(); // skill id -> [{type, value}]
const gtsDamageBonus = new Map(); // card id -> Set<number>
const gtsCardCost = new Map(); // card id -> number (仅用于校验)

for (const file of walk(dataSrc)) {
  const src = fs.readFileSync(file, "utf8");
  for (const b of parseBlocks(src)) {
    const idMatch = /\bid\s+(\d+)\s+as\b/.exec(b.body);
    const docId = /@id\s+(\d+)/.exec(b.doc);
    const id = idMatch ? Number(idMatch[1]) : docId ? Number(docId[1]) : null;
    if (id === null || Number.isNaN(id)) continue;
    const name = /@name\s+(.+)/.exec(b.doc)?.[1]?.trim();
    if (name) gtsNames.set(id, name);
    const description = parseDescription(b.doc);
    if (description) gtsDescriptions.set(id, description);

    if (b.kind === "card") {
      const set = gtsDamageBonus.get(id) ?? new Set();
      for (const m of b.body.matchAll(/increaseDamage\((\d+)\)/g)) set.add(Number(m[1]));
      if (set.size) gtsDamageBonus.set(id, set);
      const costMatch = /@cost\s+?(\d+)/.exec(b.doc);
      if (costMatch) gtsCardCost.set(id, Number(costMatch[1]));
    } else if (b.kind === "skill") {
      const damages = [];
      for (const m of b.body.matchAll(/:damage\(\s*DamageType\.(\w+)\s*,\s*(\d+)/g)) {
        damages.push({ type: m[1], value: Number(m[2]) });
      }
      if (damages.length) gtsDamages.set(id, damages);
    }
  }
}

// ---------- 组装目录 ----------
function parseDescription(doc) {
  if (!doc) return "";
  const match = /@description\s*\n([\s\S]*?)\*\/\s*$/.exec(doc);
  if (!match) return "";
  return match[1]
    .split("\n")
    .map((line) => line.replace(/^\s*\*\s?/, "").trimEnd())
    .join("\n")
    .trim();
}

const data = getData();
const characters = [];
const cards = [];
const tokens = [];
const allSkills = new Map(); // id -> {id, name, type}
let missingNames = 0;

for (const [id, ch] of data.characters) {
  for (const sk of ch.skills) {
    const prev = allSkills.get(sk.id);
    const name = gtsNames.get(sk.id) ?? prev?.name ?? "";
    if (!prev || (!prev.name && name)) {
      allSkills.set(sk.id, { id: sk.id, name, type: sk.skillType ?? "unknown" });
    }
  }
  const name = gtsNames.get(id) ?? `#${id}`;
  if (!gtsNames.has(id)) missingNames++;
  const skills = ch.skills
    .filter((sk) => sk.initiativeSkillConfig)
    .map((sk) => {
      const costMap = sk.initiativeSkillConfig?.requiredCost;
      const costDice = costMap ? Object.fromEntries(costMap) : {};
      return {
        id: sk.id,
        name: gtsNames.get(sk.id) ?? sk.skillType ?? "skill",
        type: sk.skillType,
        damage: gtsDamages.get(sk.id) ?? [],
        cost: Object.values(costDice).reduce((a, b) => a + b, 0),
        costDice,
      };
    });
  characters.push({
    id,
    name,
    tags: [...ch.tags],
    hp: ch.varConfigs.maxHealth?.initialValue ?? 0,
    energy: ch.varConfigs.maxEnergy?.initialValue ?? 0,
    skills,
  });
}

for (const [id, et] of data.entities) {
  for (const sk of et.skills ?? []) {
    if (!allSkills.has(sk.id)) {
      allSkills.set(sk.id, {
        id: sk.id,
        name: gtsNames.get(sk.id) ?? "",
        type: sk.skillType ?? "triggered",
      });
    }
  }
  if (!["eventCard", "equipment", "support"].includes(et.type)) {
    tokens.push({
      id,
      name: gtsNames.get(id) ?? `#${id}`,
      type: et.type,
      tags: [...et.tags],
    });
    continue;
  }
  const name = gtsNames.get(id) ?? `#${id}`;
  if (!gtsNames.has(id)) missingNames++;
  const cost = playSkillOfCard(et)?.initiativeSkillConfig.requiredCost;
  const costDice = cost ? Object.fromEntries(cost) : {};
  const costTotal = Object.values(costDice).reduce((a, b) => a + b, 0);
  const bonus = [...(gtsDamageBonus.get(id) ?? [])].sort((a, b) => a - b);
  cards.push({
    id,
    name,
    type: et.type,
    tags: [...et.tags],
    cost: costTotal,
    costDice,
    damageBonus: bonus,
  });
}

characters.sort((a, b) => a.id - b.id);
cards.sort((a, b) => a.id - b.id);

const catalog = {
  version: CURRENT_VERSION,
  generatedAt: new Date().toISOString(),
  counts: {
    characters: characters.length,
    cards: cards.length,
    tokens: tokens.length,
    skills: allSkills.size,
  },
  characters,
  cards,
  tokens,
  skills: [...allSkills.values()].sort((a, b) => a.id - b.id),
};

const check = process.argv.includes("--check");
if (check) {
  console.log("check mode: not writing");
} else {
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  fs.writeFileSync(outFile, JSON.stringify(catalog, null, 0));
}

console.log(`version: ${catalog.version}`);
console.log(
  `characters: ${characters.length}, cards: ${cards.length}, tokens: ${tokens.length}, skills: ${allSkills.size}`,
);
console.log(`missing names: ${missingNames}`);
console.log(
  `damageBonus examples:`,
  cards.filter((c) => c.damageBonus.length).slice(0, 5).map((c) => `${c.name}(${c.id})=+${c.damageBonus.join("/")}`),
);
const textFile = path.join(repo, "local/arena/card-text.json");
fs.mkdirSync(path.dirname(textFile), { recursive: true });
fs.writeFileSync(
  textFile,
  JSON.stringify({
    version: catalog.version,
    descriptions: Object.fromEntries(gtsDescriptions),
  }),
);
console.log(
  `card text: ${gtsDescriptions.size} entries, ${(fs.statSync(textFile).size / 1024).toFixed(0)} KB -> ${path.relative(repo, textFile)}`,
);
console.log(`output: ${path.relative(repo, outFile)} (${fs.existsSync(outFile) ? (fs.statSync(outFile).size / 1024).toFixed(0) : "?"} KB)`);
