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
const cardIds = [...data.entities].filter(([, e]) => e.tags?.includes("weapon")).slice(0, 10).map(([id]) => id);
const deck = { characters: charIds, cards: cardIds };

setLocalRulesHooks({ baseDamage: ({ skillId, value }) => (skillId === 11031 ? 100 : value) });

const state = Game.createInitialState({ data, decks: [deck, deck] });
let firstActionDone = false;
let notifications = 0;

const ioFor = (who) => ({
  notify: (n) => {
    notifications++;
    if (notifications <= 3) console.log(`notify[${who}] keys=`, Object.keys(n ?? {}), "mutation=", n?.mutation?.$case, "mutations=", n?.mutations?.map?.((m) => m.$case ?? m.mutation?.$case));
  },
  rpc: async (req) => {
    const { $case, value } = req.request;
    console.log(`rpc[${who}] ${$case}`);
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
        let idx;
        if (who === 0 && !firstActionDone) {
          idx = actions.findIndex((a) => a.action?.$case === "useSkill" && a.validity === 0);
          if (idx >= 0) firstActionDone = true;
        }
        if (idx === undefined || idx < 0) {
          idx = actions.findIndex((a) => a.action?.$case === "declareEnd" && a.validity === 0);
        }
        if (idx < 0) idx = actions.findIndex((a) => a.validity === 0);
        if (idx < 0) idx = 0;
        console.log(`  -> action ${idx} ${actions[idx]?.action?.$case} valid=${actions[idx]?.validity}`);
        return { response: { $case: "action", value: { chosenActionIndex: idx, usedDice: actions[idx]?.autoSelectedDice ?? [] } } };
      }
    }
    throw new Error("unknown rpc " + $case);
  },
});

const game = new Game(state);
game.players[0].io = ioFor(0);
game.players[1].io = ioFor(1);
game.onPause = async () => {};
try {
  const winner = await Promise.race([
    game.start(),
    new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 20000)),
  ]);
  console.log("winner:", winner, "round:", game.state.roundNumber);
  console.log("p0:", game.state.players[0].characters.map((c) => [c.definition.id, c.variables.health]));
  console.log("p1:", game.state.players[1].characters.map((c) => [c.definition.id, c.variables.health]));
} catch (e) {
  console.log("ERROR:", e?.message?.slice(0, 2000));
}
