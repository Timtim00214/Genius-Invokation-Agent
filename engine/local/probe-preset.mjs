import { pathToFileURL } from "node:url";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const dataUrl = pathToFileURL(path.join(repo, "packages/data/dist/index.js")).href;
const coreUrl = pathToFileURL(path.join(repo, "packages/core/dist/index.js")).href;
const rulesUrl = pathToFileURL(path.join(repo, "packages/local-rules/dist/index.js")).href;

const { default: getData } = await import(dataUrl);
const core = await import(coreUrl);
const rules = await import(rulesUrl);
const { Game } = core;
const { applyPresetToData, installPreset, isEmptyPreset, buildHooks, countPresetChanges } = rules;

const data = getData();
console.log("vanilla identity:", applyPresetToData(data, null) === data);
console.log("vanilla preset empty:", isEmptyPreset(rules.VANILLA_PRESET));

const preset = {
  id: "test",
  name: "测试",
  characters: { "1103": { hp: 100 } },
  cards: { "311501": { cost: 0, damageBonus: 100 } },
  skills: { "11031": { damage: 50 } },
};
console.log("changes:", countPresetChanges(preset), "hooks?", !!buildHooks(preset));

const patched = applyPresetToData(data, preset);
const kaeya = patched.characters.get(1103);
console.log("kaeya hp:", kaeya.varConfigs.health.initialValue, kaeya.varConfigs.maxHealth.initialValue);
const sword = patched.entities.get(311501);
const play = core.playSkillOfCard(sword);
console.log("sword cost:", [...play.initiativeSkillConfig.requiredCost.entries()], "costSize:", play.initiativeSkillConfig.computed$costSize);

const deck = { characters: [1103, 1201, 1301], cards: Array(10).fill(311501) };
const state = Game.createInitialState({ data: patched, decks: [deck, deck] });
console.log("initial p0:", state.players[0].characters.map((c) => [c.definition.id, c.variables.health, c.variables.maxHealth]));

// 实战：装备旅行剑 + 普通攻击，伤害应为 50 + 100 = 150（预设同时改了技能基础伤害和武器加成）
installPreset(preset);
const damages = [];
let step = 0;
const io = (who) => ({
  notify: (n) => {
    for (const em of n?.mutation ?? []) {
      if (em.mutation?.$case === "damage") damages.push({ who, value: em.mutation.value.value });
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
        if (who === 0 && step === 0) {
          idx = actions.findIndex((a) => a.action?.$case === "playCard" && a.action.value.cardDefinitionId === 311501 && a.validity === 0);
          if (idx >= 0) step = 1;
        } else if (who === 0 && step === 1) {
          idx = actions.findIndex((a) => a.action?.$case === "useSkill" && a.action.value.skillDefinitionId === 11031 && a.validity === 0);
          if (idx >= 0) step = 2;
        }
        if (idx < 0) idx = actions.findIndex((a) => a.action?.$case === "declareEnd" && a.validity === 0);
        if (idx < 0) idx = 0;
        return { response: { $case: "action", value: { chosenActionIndex: idx, usedDice: actions[idx]?.autoSelectedDice ?? [] } } };
      }
    }
    throw new Error("unknown " + $case);
  },
});
const game = new Game(state);
game.players[0].io = io(0);
game.players[1].io = io(1);
game.onPause = async () => {};
const winner = await game.start();
console.log("winner:", winner, "damages:", JSON.stringify(damages));
console.log("p1 hp:", game.state.players[1].characters.map((c) => [c.definition.id, c.variables.health]));
installPreset(null);
