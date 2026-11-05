/**
 * 七圣召唤"牌组分享码"的编解码（零依赖）。
 *
 * 为什么要自己实现：
 *   官方编解码在 `@gi-tcg/assets-manager` 里（`staticEncode` / `staticDecode`），
 *   它依赖浏览器环境的 `btoa/atob` 与一个 597 项的分享码映射表。司机是 Node
 *   进程，又不想为了一个 51 字节的字符串去装整套资源包，所以按源码原样移植：
 *
 *   1. 分享码是 base64 的 51 字节；最后一个字节是"偏移量 last"；
 *   2. 前 50 字节两两交错，各自减 last 取低 8 位，得到 50 个"重排字节"；
 *   3. 每 3 个重排字节拆成 2 个 12 位整数（高 4 位 + 低 8 位），得到 33 个 id；
 *   4. 33 个 id 中前 3 个是角色、后 30 个是行动牌，都是"分享码 id"，
 *      需要用 `share_id.json` 映射回卡牌定义 id。
 *
 * 映射表从仓库里读（`engine/packages/assets-manager/src/data/share_id.json`），
 * 找不到时再退到本目录下的副本。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const CANDIDATES = [
  process.env.VMAF_SHARE_ID_MAP,
  path.join(ROOT, "..", "engine", "packages", "assets-manager", "src", "data", "share_id.json"),
  path.join(ROOT, "data", "share_id.json"),
].filter(Boolean);

let cachedMap = null;

/** 读取"分享码 id → 卡牌定义 id"映射表（只读一次）。 */
function shareIdMap() {
  if (cachedMap) return cachedMap;
  for (const file of CANDIDATES) {
    try {
      const raw = JSON.parse(fs.readFileSync(file, "utf8"));
      cachedMap = new Map(Object.entries(raw).map(([k, v]) => [Number(k), Number(v)]));
      return cachedMap;
    } catch {
      /* 换下一个候选路径 */
    }
  }
  throw new Error(`找不到 share_id.json，可用 VMAF_SHARE_ID_MAP 指定路径（试过：${CANDIDATES.join("、")}）`);
}

/**
 * 把分享码解成 33 个分享码 id。
 *
 * @param {string} src 分享码。
 * @returns {number[]} 33 个整数。
 */
export function decodeRaw(src) {
  const code = String(src).trim();
  const bytes = Array.from(Buffer.from(code, "base64"));
  if (bytes.length !== 51) {
    throw new Error(`分享码长度不对：解码出 ${bytes.length} 字节（应为 51），可能复制不全`);
  }
  const last = bytes.pop();
  const reordered = [];
  for (let i = 0; i < 25; i++) reordered.push((bytes[2 * i] - last) & 0xff);
  for (let i = 0; i < 25; i++) reordered.push((bytes[2 * i + 1] - last) & 0xff);
  reordered.push(0);
  const out = [];
  for (let i = 0; i < 17; i++) {
    out.push((reordered[i * 3] << 4) + (reordered[i * 3 + 1] >> 4));
    out.push(((reordered[i * 3 + 1] & 0x0f) << 8) + reordered[i * 3 + 2]);
  }
  out.pop();
  return out;
}

/**
 * 把 33 个分享码 id 编回分享码。
 *
 * 屏蔽词处理：官方会对 base64 结果做屏蔽词检查，命中就把 `last` 加一再试。
 * 这里只做"尝试 0..255 直到不命中"，与源码行为一致（不含屏蔽词表则直接返回）。
 *
 * @param {number[]} arr 33 个分享码 id。
 * @returns {string} 分享码。
 */
export function encodeRaw(arr) {
  if (arr.length !== 33) throw new Error(`需要正好 33 个 id，当前 ${arr.length}`);
  const padded = [...arr, 0];
  const reordered = [];
  for (let i = 0; i < 17; i++) {
    reordered.push(padded[i * 2] >> 4);
    reordered.push(((padded[i * 2] & 0x0f) << 4) + (padded[i * 2 + 1] >> 8));
    reordered.push(arr[i * 2 + 1] & 0xff);
  }
  for (let last = 0; last < 0xff; last++) {
    const original = [];
    for (let i = 0; i < 25; i++) {
      original.push((reordered[i] + last) & 0xff);
      original.push((reordered[i + 25] + last) & 0xff);
    }
    original.push(last);
    return Buffer.from(original).toString("base64");
  }
  throw new Error("编码失败");
}

/**
 * 解析牌组分享码。
 *
 * @param {string} code 分享码。
 * @returns {{characters: number[], cards: number[]}} 卡牌定义 id。
 */
export function decodeDeckCode(code) {
  const map = shareIdMap();
  const ids = decodeRaw(code).map((shareId) => {
    const id = map.get(shareId);
    if (id === undefined) throw new Error(`分享码里有未知 id ${shareId}（不是本版本卡牌？）`);
    return id;
  });
  return { characters: ids.slice(0, 3), cards: ids.slice(3) };
}

/**
 * 生成牌组分享码。
 *
 * @param {{characters: number[], cards: number[]}} deck 卡牌定义 id。
 * @returns {string} 分享码。
 */
export function encodeDeckCode(deck) {
  const map = shareIdMap();
  const reverse = new Map([...map.entries()].map(([share, id]) => [id, share]));
  const ids = [...deck.characters, ...deck.cards].map((id) => {
    const share = reverse.get(id);
    if (share === undefined) throw new Error(`卡牌 ${id} 没有分享码（不是本版本卡牌？）`);
    return share;
  });
  return encodeRaw(ids);
}

/** 牌组统计（不依赖 catalog，只统计 id 分布）。 */
export function deckCounts(deck) {
  const counts = new Map();
  for (const id of deck.cards) counts.set(id, (counts.get(id) ?? 0) + 1);
  return { characters: deck.characters.length, cards: deck.cards.length, distinctCards: counts.size };
}
