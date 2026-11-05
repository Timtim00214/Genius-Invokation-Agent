import { pathToFileURL } from "node:url";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const dataUrl = pathToFileURL(path.join(repo, "packages/data/dist/index.js")).href;
const coreUrl = pathToFileURL(path.join(repo, "packages/core/dist/index.js")).href;
const { default: getData } = await import(dataUrl);
const { Game, setLocalRulesHooks } = await import(coreUrl);

const data = getData();
const deck = { characters: [1103, 1201, 1301], cards: Array(10).fill(311501) };

async function run(hook) {
  setLocalRulesHooks(hook);
  const state = Game.createInitialState({ data, decks: [deck, deck] });
  const damages = [];
  let step = 0;

  const io = (who) => ({
    notify: (n) => {
      for (const em of n?.mutation ?? []) {
        if (em.mutation?.$case === "damage") {
          damages.push({ who, value: em.mutation.value.value, def: em.mutation.value.sourceDefinitionId });
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
  return { winner, damages, p1: game.state.players[1].characters.map((c) => [c.definition.id, c.variables.health]) };
}

const baseline = await run(null);
console.log("baseline:", JSON.stringify(baseline));
const hacked = await run({
  increaseDamage: ({ caller, value }) => (caller.definition.id === 311501 ? 100 : value),
});
console.log("hooked:  ", JSON.stringify(hacked));
setLocalRulesHooks(null);
