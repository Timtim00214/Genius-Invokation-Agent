// cardtext.mjs — 从 /api/arena/card-text 里裁出"这一回合用得上"的卡牌效果文本。
//
// card-text.json 是打包时从 GTS 注释里抠出来的 id → 效果文本表（约 1900 条，
// 197KB）。整份塞进 shared 太重，而一回合真正会出现的实体/手牌通常只有几十个，
// 所以这里跟 names.mjs 用同一套裁剪思路：只保留 state 与 aiDetail.actions 里
// 出现过的 definitionId。
//
// 注意：只认正整数 id（负数都是运行时实例 id，card-text 里没有它们的条目）。

/** 拍平成 {version, descriptions: {id: text}}，丢掉空文本。 */
export function compactCardText(raw) {
  const descriptions = {};
  const source = raw?.descriptions ?? raw ?? {};
  for (const [id, text] of Object.entries(source)) {
    if (typeof text === "string" && text.trim()) descriptions[id] = text;
  }
  return { version: raw?.version ?? null, descriptions };
}

/** 收集一份 state 里出现过的 definitionId（与 names.mjs 的 collectDefinitionIds 同构）。 */
export function collectStateDefinitionIds(state) {
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

/** aiDetail.actions 里会引用的牌/技能/角色 id（手牌之外的"能打什么"）。 */
const ACTION_ID_KEYS = ["cardDefinitionId", "skillId", "characterDefinitionId", "definitionId"];

/** 收集这一手要用的效果文本 id：局面里的 definitionId + 动作里引用的牌/技能 id。 */
export function collectCardTextIds(state, aiDetail) {
  const ids = collectStateDefinitionIds(state);
  for (const action of aiDetail?.actions ?? []) {
    if (!action || typeof action !== "object") continue;
    for (const key of ACTION_ID_KEYS) {
      const value = action[key];
      if (typeof value === "number" && value > 0) ids.add(value);
    }
  }
  return ids;
}

/** 裁出这张小表；card-text 为空时返回空表（插件会退化成只显示名字）。 */
export function buildCardTextSlice(cardText, ids) {
  const slice = { version: cardText?.version ?? null, descriptions: {} };
  const source = cardText?.descriptions ?? {};
  let kept = 0;
  for (const id of ids) {
    const text = source[id];
    if (typeof text === "string" && text) {
      slice.descriptions[id] = text;
      kept++;
    }
  }
  slice.count = kept;
  return slice;
}
