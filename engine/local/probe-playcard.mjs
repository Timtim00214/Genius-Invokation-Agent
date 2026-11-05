import { pathToFileURL } from "node:url";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const dataUrl = pathToFileURL(path.join(repo, "packages/data/dist/index.js")).href;
const coreUrl = pathToFileURL(path.join(repo, "packages/core/dist/index.js")).href;
const { default: getData } = await import(dataUrl);
const { Game } = await import(coreUrl);

const data = getData();
const deck = { characters: [1103, 1201, 1301], cards: Array(10).fill(311501) };
const state = Game.createInitialState({ data, decks: [deck, deck] });

let logged = false;
const io = (who) => ({
  notify: () => {},
  rpc: async (req) => {
    const { $case, value } = req.request;
    if ($case === "action" && who === 0 && !logged) {
      logged = true;
      for (const [i, a] of (value.action ?? []).entries()) {
        if (a.action?.$case === "playCard") {
          console.log(`playCard[${i}]`, JSON.stringify(a.action.value), "valid", a.validity, "dice", a.autoSelectedDice);
        }
      }
    }
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
        const idx = actions.findIndex((a) => a.action?.$case === "declareEnd" && a.validity === 0);
        return { response: { $case: "action", value: { chosenActionIndex: idx < 0 ? 0 : idx, usedDice: idx < 0 ? [] : actions[idx].autoSelectedDice } } };
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
console.log("winner", winner);
