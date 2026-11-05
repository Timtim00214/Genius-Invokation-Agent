// names.mjs — 从 catalog 里裁出"这一回合真正用得上"的名字表。
//
// catalog 是 id → 中文名 的静态表，整份约 700KB（155 角色 + 597 卡牌 + 一堆 token），
// 每回合原样塞进 shared 没有意义：局面里出现过的 definitionId 通常只有十几个。
// 这里按当前 state 里出现过的 definitionId 裁一刀，既让 state_brief 能翻译成人话，
// 又不至于把几十万字符灌进引擎的上下文。
//
// 注意：只认正整数 id，因为 catalog 里全是正的 definitionId；实体的负数是实例 id。

const CATEGORIES = ["characters", "cards", "tokens", "skills"];

/** 把整份 catalog 拍平成 {类别: {id: 名字}} 的紧凑名字表。 */
export function compactCatalog(raw) {
  const names = { version: raw?.version ?? null };
  for (const category of CATEGORIES) names[category] = {};
  for (const category of CATEGORIES) {
    for (const item of raw?.[category] ?? []) {
      if (item && typeof item.id === "number" && typeof item.name === "string" && item.name) {
        names[category][item.id] = item.name;
      }
    }
  }
  return names;
}

/** 收集一份 state 里出现过的所有 definitionId。 */
export function collectDefinitionIds(state) {
  const seen = new Set();
  const walk = (node) => {
    if (Array.isArray(node)) {
      for (const item of node) walk(item);
      return;
    }
    if (!node || typeof node !== "object") return;
    for (const [key, value] of Object.entries(node)) {
      if (key === "definitionId" && typeof value === "number" && value > 0) seen.add(value);
      else if (value && typeof value === "object") walk(value);
    }
  };
  walk(state);
  return seen;
}

/** 裁出这一回合的名字表；catalog 为空时返回空表（state_brief 会退化成 id）。 */
export function buildNameSlice(catalog, state) {
  if (!catalog || typeof catalog !== "object") return {};
  const ids = collectDefinitionIds(state);
  const slice = { version: catalog.version ?? null };
  let kept = 0;
  for (const category of CATEGORIES) {
    slice[category] = {};
    const source = catalog[category] ?? {};
    for (const id of ids) {
      const name = source[id];
      if (name) {
        slice[category][id] = name;
        kept++;
      }
    }
  }
  slice.count = kept;
  return slice;
}
