#!/usr/bin/env node
/**
 * 本地增强层的回归测试（无头对局，不需要浏览器）。
 *
 *   node local/test-local.mjs
 *
 * 上游更新后跑一遍，确认：
 *  1. 原版数据没有被改动（identity）
 *  2. 角色生命覆盖生效
 *  3. 技能基础伤害钩子生效（2 -> 100）
 *  4. 武器伤害加成钩子生效（3 -> 102）
 */
import { pathToFileURL } from "node:url";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const dataUrl = pathToFileURL(path.join(repo, "packages/data/dist/index.js")).href;
const coreUrl = pathToFileURL(path.join(repo, "packages/core/dist/index.js")).href;
const rulesUrl = pathToFileURL(path.join(repo, "packages/local-rules/dist/index.js")).href;

const { default: getData } = await import(dataUrl);
const core = await import(coreUrl);
const rules = await import(rulesUrl);
const { Game, playSkillOfCard } = core;
const { applyPresetToData, installPreset, isEmptyPreset } = rules;

const data = getData();
const failures = [];
function check(name, ok, detail = "") {
  console.log(`${ok ? "  ok " : "FAIL "} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(name);
}

// ---------- 1. 原版 identity ----------
check("原版预设为空", isEmptyPreset(rules.VANILLA_PRESET));
check("空预设返回同一份 GameData", applyPresetToData(data, null) === data);

// ---------- 2. 生命覆盖 ----------
{
  const preset = { id: "t", name: "t", characters: { "1103": { hp: 100 } } };
  const patched = applyPresetToData(data, preset);
  const kaeya = patched.characters.get(1103);
  check(
    "生命覆盖写入数据",
    kaeya.varConfigs.maxHealth.initialValue === 100 &&
      kaeya.varConfigs.health.initialValue === 100,
  );
  check("生命覆盖不污染原数据", data.characters.get(1103).varConfigs.maxHealth.initialValue === 10);

  const state = Game.createInitialState({
    data: patched,
    decks: [
      { characters: [1103, 1201, 1301], cards: [] },
      { characters: [1103, 1201, 1301], cards: [] },
    ],
  });
  check(
    "开局生命为 100",
    state.players[0].characters[0].variables.health === 100 &&
      state.players[0].characters[0].variables.maxHealth === 100,
  );
}

// ---------- 3. 费用覆盖 ----------
{
  const preset = { id: "t", name: "t", cards: { "311501": { cost: 0 } } };
  const patched = applyPresetToData(data, preset);
  const play = playSkillOfCard(patched.entities.get(311501));
  check("费用覆盖为 0", play.initiativeSkillConfig.computed$costSize === 0);
  check(
    "费用覆盖不污染原数据",
    playSkillOfCard(data.entities.get(311501)).initiativeSkillConfig.computed$costSize === 2,
  );
}

// ---------- 3b. 骰子类型 / 技能费用覆盖 ----------
{
  const preset = {
    id: "t",
    name: "t",
    cards: { "311501": { dice: { "3": 1, "9": 1 } } },
    skills: {
      "11031": { dice: { "0": 1, "1": 2 } },
      "11032": { cost: 1 },
    },
  };
  check("只改骰子也算非空", !isEmptyPreset(preset));
  const patched = applyPresetToData(data, preset);
  const play = playSkillOfCard(patched.entities.get(311501));
  const cardDice = [...play.initiativeSkillConfig.requiredCost.entries()].sort((a, b) => a[0] - b[0]);
  check(
    "卡牌骰子类型覆盖",
    JSON.stringify(cardDice) === JSON.stringify([[3, 1], [9, 1]]),
    JSON.stringify(cardDice),
  );
  check(
    "卡牌骰子费用统计",
    play.initiativeSkillConfig.computed$costSize === 2 &&
      play.initiativeSkillConfig.computed$diceCostSize === 1,
  );

  const kaeya = patched.characters.get(1103);
  const normal = kaeya.skills.find((s) => s.id === 11031);
  const normalDice = [...normal.initiativeSkillConfig.requiredCost.entries()].sort((a, b) => a[0] - b[0]);
  check(
    "技能骰子类型覆盖",
    JSON.stringify(normalDice) === JSON.stringify([[0, 1], [1, 2]]),
    JSON.stringify(normalDice),
  );
  const elemental = kaeya.skills.find((s) => s.id === 11032);
  const elementalDice = [...elemental.initiativeSkillConfig.requiredCost.entries()];
  check(
    "技能总费用覆盖（保留类型）",
    elementalDice.length === 1 && elementalDice[0][0] === 1 && elementalDice[0][1] === 1,
    JSON.stringify(elementalDice),
  );

  // 0 费用（空 dice 对象）也要能生效
  const zeroPreset = {
    id: "zero",
    name: "zero",
    cards: { "311501": { dice: {} } },
    skills: { "11032": { dice: {} } },
  };
  check("0 费用预设非空", !isEmptyPreset(zeroPreset));
  const zeroData = applyPresetToData(data, zeroPreset);
  const zeroCard = playSkillOfCard(zeroData.entities.get(311501));
  const zeroSkill = zeroData.characters.get(1103).skills.find((s) => s.id === 11032);
  check(
    "0 费用生效",
    zeroCard.initiativeSkillConfig.requiredCost.size === 0 &&
      zeroCard.initiativeSkillConfig.computed$costSize === 0 &&
      zeroSkill.initiativeSkillConfig.requiredCost.size === 0,
  );

  const original = data.characters.get(1103).skills.find((s) => s.id === 11031);
  check(
    "骰子覆盖不污染原数据",
    original.initiativeSkillConfig.requiredCost.get(1) === 1 &&
      original.initiativeSkillConfig.requiredCost.get(0) === 2,
  );
}

// ---------- 对局辅助 ----------
async function duel({ preset, withWeapon = false }) {
  installPreset(preset);
  const state = Game.createInitialState({
    data: applyPresetToData(data, preset),
    decks: [
      { characters: [1103, 1201, 1301], cards: Array(10).fill(311501) },
      { characters: [1103, 1201, 1301], cards: [] },
    ],
  });
  const damages = [];
  let step = 0;
  const io = (who) => ({
    notify: (n) => {
      if (who !== 0) return;
      for (const em of n?.mutation ?? []) {
        if (em.mutation?.$case === "damage" && em.mutation.value.value > 0) {
          damages.push(em.mutation.value.value);
        }
      }
    },
    rpc: async (req) => {
      const { $case, value } = req.request;
      switch ($case) {
        case "rerollDice":
          return { response: { $case: "rerollDice", value: { diceToReroll: [] } } };
        case "switchHands":
          return { response: { $case: "switchHands", value: { removedHandIds: [] } } };
        case "chooseActive":
          return { response: { $case: "chooseActive", value: { activeCharacterId: value.candidateIds[0] } } };
        case "selectCard":
          return { response: { $case: "selectCard", value: { selectedDefinitionId: value.candidateDefinitionIds[0] } } };
        case "action": {
          const actions = value.action ?? [];
          let idx = -1;
          if (who === 0 && withWeapon && step === 0) {
            idx = actions.findIndex(
              (a) =>
                a.action?.$case === "playCard" &&
                a.action.value.cardDefinitionId === 311501 &&
                a.validity === 0,
            );
            if (idx >= 0) step = 1;
          } else if (who === 0 && step <= (withWeapon ? 1 : 0) && step < 2) {
            idx = actions.findIndex(
              (a) =>
                a.action?.$case === "useSkill" &&
                a.action.value.skillDefinitionId === 11031 &&
                a.validity === 0,
            );
            if (idx >= 0) step = 2;
          }
          if (idx < 0) {
            idx = actions.findIndex((a) => a.action?.$case === "declareEnd" && a.validity === 0);
          }
          if (idx < 0) idx = 0;
          return {
            response: {
              $case: "action",
              value: { chosenActionIndex: idx, usedDice: actions[idx]?.autoSelectedDice ?? [] },
            },
          };
        }
      }
      throw new Error("unknown rpc " + $case);
    },
  });
  const game = new Game(state);
  game.players[0].io = io(0);
  game.players[1].io = io(1);
  game.onPause = async () => {};
  await Promise.race([
    game.start(),
    new Promise((_, reject) => setTimeout(() => reject(new Error("对局超时")), 30000)),
  ]);
  return { damages, game };
}

// ---------- 4. 技能基础伤害钩子 ----------
{
  const base = await duel({ preset: null });
  check("原版普攻伤害为 2", base.damages[0] === 2, `实际 ${base.damages[0]}`);
  const hacked = await duel({
    preset: { id: "t", name: "t", skills: { "11031": { damage: 100 } } },
  });
  check("技能覆盖后普攻伤害为 100", hacked.damages[0] === 100, `实际 ${hacked.damages[0]}`);
}

// ---------- 5. 武器伤害加成钩子 ----------
{
  const base = await duel({ preset: null, withWeapon: true });
  check("原版装备旅行剑后普攻为 3", base.damages[0] === 3, `实际 ${base.damages[0]}`);
  const hacked = await duel({
    preset: { id: "t", name: "t", cards: { "311501": { damageBonus: 100 } } },
    withWeapon: true,
  });
  check(
    "武器伤害加成覆盖后普攻为 102",
    hacked.damages[0] === 102,
    `实际 ${hacked.damages[0]}`,
  );
}

installPreset(null);

console.log("");
if (failures.length) {
  console.error(`失败 ${failures.length} 项：${failures.join("、")}`);
  process.exit(1);
}
console.log("全部通过。");
