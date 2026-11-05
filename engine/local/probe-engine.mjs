import { pathToFileURL } from "node:url";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const dataUrl = pathToFileURL(path.join(repo, "packages/data/dist/index.js")).href;
const coreUrl = pathToFileURL(path.join(repo, "packages/core/dist/index.js")).href;

const { default: getData } = await import(dataUrl);
const core = await import(coreUrl);
const { Game, setLocalRulesHooks } = core;

const data = getData();
const charIds = [1103, 1201, 1301];
const chars = charIds.map((id) => data.characters.get(id));
console.log("chars:", chars.map((c) => (c ? { id: c.id, hp: c.varConfigs.maxHealth?.initialValue } : null)));
const weaponIds = [...data.entities].filter(([, e]) => e.tags?.includes("weapon")).slice(0, 10).map(([id]) => id);
const deck = { characters: charIds, cards: weaponIds };
console.log("deck cards exist:", deck.cards.map((id) => !!data.entities.get(id)));

const state = Game.createInitialState({ data, decks: [deck, deck] });
console.log("p0:", state.players[0].characters.map((c) => ({ id: c.definition.id, hp: c.variables.health, max: c.variables.maxHealth, energy: c.variables.maxEnergy })));

let logged = false;
function defaultResponse(req) {
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
      if (!logged && actions.length) {
        logged = true;
        console.log("first action request, first action:", JSON.stringify(actions[0], null, 2));
        console.log(
          "action kinds:",
          actions.map((a, i) => `${i}:${a.action?.$case} validity=${a.validity} cost=${a.requiredCost?.map((r) => r.count).join("/")}`).join(" "),
        );
      }
      let idx = actions.findIndex((a) => a.action?.$case === "declareEnd" && a.validity === 0);
      if (idx < 0) idx = actions.findIndex((a) => a.validity === 0);
      if (idx < 0) idx = 0;
      return { response: { $case: "action", value: { chosenActionIndex: idx, usedDice: [] } } };
    }
  }
  throw new Error("unknown rpc " + $case);
}

function makeIo() {
  return { notify: () => {}, rpc: async (req) => defaultResponse(req) };
}

const game = new Game(state);
game.players[0].io = makeIo();
game.players[1].io = makeIo();
game.onPause = async () => {};
const winner = await Promise.race([
  game.start(),
  new Promise((_, reject) => setTimeout(() => reject(new Error("timeout 60s")), 60000)),
]);
console.log("winner:", winner, "round:", game.state.roundNumber);
