#!/usr/bin/env node
/**
 * 生成 AI 可浏览的“牌组库”。
 *
 *   node local/gen-decks.mjs
 *
 * 产物：local/arena/decks.json
 * - 两个上游 standalone 的默认牌组（解码成完整牌表）
 * - 若干自动生成的通用牌组（3 角色 + 30 张牌，最多 2 张同名）
 */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const repo = path.resolve(import.meta.dirname, "..");
const catalogPath = path.join(repo, "packages/local-rules/src/catalog.json");
const outPath = path.join(repo, "local/arena/decks.json");
const assetsUrl = pathToFileURL(
  path.join(repo, "packages/assets-manager/dist/index.js"),
).href;

const catalog = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
const charactersById = new Map(catalog.characters.map((c) => [c.id, c]));
const cardsById = new Map(catalog.cards.map((c) => [c.id, c]));

const BUILTIN_DECKS = [
  {
    id: "builtin-1",
    name: "默认牌组 1（先手示例）",
    code: "FZDByRUNGRCB0WoNFlGgWpEPE0AB9TAPFGCB9kgWGIERCoEQDLFADcQQDPFgacYWDJAA",
    note: "上游 standalone 的默认先手牌组。",
  },
  {
    id: "builtin-2",
    name: "默认牌组 2（后手示例）",
    code: "FdHxNj8TAWDQxFkMFkAhyWIYCYDA45wOCUDQ5J0PGEAh9IIPGWDh9p4YC6FgirYRCxAA",
    note: "上游 standalone 的默认后手牌组。",
  },
];

function groupCards(ids) {
  const counts = new Map();
  for (const id of ids) counts.set(id, (counts.get(id) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([id, count]) => ({
      id,
      name: cardsById.get(id)?.name ?? `#${id}`,
      count,
    }));
}

function deckFromArrays(id, name, note, characters, cards, code) {
  return {
    id,
    name,
    note,
    ...(code ? { code } : {}),
    characters: characters.map((cid) => ({
      id: cid,
      name: charactersById.get(cid)?.name ?? `#${cid}`,
    })),
    cards: groupCards(cards),
  };
}

let assetsManager = null;
async function getAssetsManager() {
  if (!assetsManager) {
    ({ DEFAULT_ASSETS_MANAGER: assetsManager } = await import(assetsUrl));
  }
  return assetsManager;
}

const decks = [];
for (const entry of BUILTIN_DECKS) {
  try {
    const manager = await getAssetsManager();
    const deck = manager.decode(entry.code);
    decks.push(
      deckFromArrays(
        entry.id,
        entry.name,
        entry.note,
        deck.characters,
        deck.cards,
        entry.code,
      ),
    );
  } catch (e) {
    console.error(`解码 ${entry.id} 失败：`, e.message);
  }
}

// 自动生成通用牌组：3 角色 + 15 种通用牌各 2 张
const generic = catalog.cards.filter(
  (c) =>
    !["legend", "talent", "weapon"].some((t) => c.tags.includes(t)) &&
    c.cost > 0 &&
    c.cost <= 2 &&
    ["artifact", "food", "place", "ally", "item"].some((t) =>
      c.tags.includes(t),
    ),
);
const pools = [
  ["A", [1121, 1211, 1311]],
  ["B", [1103, 1201, 1301]],
  ["C", [1411, 2102, 1510]],
  ["D", [2304, 1311, 1404]],
];
let p = 0;
for (const [suffix, chars] of pools) {
  const validChars = chars.filter((id) => charactersById.has(id));
  if (validChars.length !== 3) continue;
  const picked = generic.slice(p * 15, p * 15 + 15);
  p++;
  if (picked.length < 15) break;
  const cards = picked.flatMap((c) => [c.id, c.id]);
  decks.push(
    deckFromArrays(
      `generic-${suffix.toLowerCase()}`,
      `通用牌组 ${suffix}`,
      "自动生成：3 角色 + 15 种通用行动牌各 2 张；没有秘传/天赋/武器限制。",
      validChars,
      cards,
    ),
  );
}

fs.writeFileSync(
  outPath,
  JSON.stringify({ version: 1, generatedAt: new Date().toISOString(), decks }, null, 2),
);
console.log(`decks: ${decks.length}`);
for (const d of decks) {
  const cardCount = d.cards.reduce((a, c) => a + c.count, 0);
  console.log(`- ${d.id}: ${d.name} / ${d.characters.length} 角色 / ${cardCount} 张牌`);
}
console.log(`output: ${path.relative(repo, outPath)}`);
