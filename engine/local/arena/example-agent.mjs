#!/usr/bin/env node
/**
 * 示例 agent —— 只用来验证接口 / 给你自己的 agent 做参考。
 *
 *   node local/arena/example-agent.mjs --seat 1
 *   node local/arena/example-agent.mjs --seat 0 --delay 1000
 *
 * 行为：能结束回合就结束回合，否则选第一个合法行动。
 * 真正的 LLM agent 只需要替换 decide()。
 */
import process from "node:process";

const args = process.argv.slice(2);
function arg(name, fallback) {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
}
const seat = Number(arg("seat", "1"));
const base = (arg("url", "http://127.0.0.1:7717")).replace(/\/$/, "");
const delay = Number(arg("delay", "0"));
const matchId = arg("match", "local");
const policy = arg("policy", "skill");
const pickDeck = arg("pickDeck", "0") === "1";
const manual = arg("manual", "0") === "1";

function defaultResponse(request) {
  const { $case, value } = request.request;
  switch ($case) {
    case "action": {
      const actions = value.action ?? [];
      let index = -1;
      if (policy === "skill") {
        index = actions.findIndex(
          (a) => a.action?.$case === "useSkill" && a.validity === 0,
        );
      }
      if (index < 0) {
        index = actions.findIndex(
          (a) => a.action?.$case === "declareEnd" && a.validity === 0,
        );
      }
      if (index < 0) index = actions.findIndex((a) => a.validity === 0);
      if (index < 0) index = 0;
      return {
        response: {
          $case: "action",
          value: {
            chosenActionIndex: index,
            usedDice: actions[index]?.autoSelectedDice ?? [],
          },
        },
      };
    }
    case "chooseActive":
      return {
        response: {
          $case: "chooseActive",
          value: { activeCharacterId: value.candidateIds?.[0] ?? 0 },
        },
      };
    case "selectCard":
      return {
        response: {
          $case: "selectCard",
          value: { selectedDefinitionId: value.candidateDefinitionIds?.[0] ?? 0 },
        },
      };
    case "rerollDice":
      return { response: { $case: "rerollDice", value: { diceToReroll: [] } } };
    case "switchHands":
      return { response: { $case: "switchHands", value: { removedHandIds: [] } } };
  }
  throw new Error(`unknown rpc ${$case}`);
}

function describe(request, response) {
  const rpc = request.request;
  if (rpc.$case === "action") {
    const index = response.response.value.chosenActionIndex;
    const action = rpc.value.action?.[index]?.action;
    return `action #${index} ${action?.$case ?? "?"}`;
  }
  return rpc.$case;
}

async function post(path, body) {
  const res = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res;
}

async function handleRequest(id, request) {
  if (manual) {
    console.log(
      `[seat ${seat}] REQUEST id=${id} case=${request.request?.$case}`,
    );
    return;
  }
  await respond(id, request);
}

async function respond(id, request) {
  const response = defaultResponse(request);
  if (delay > 0) await new Promise((r) => setTimeout(r, delay));
  const res = await post("/api/arena/agent/respond", { matchId, id, response });
  console.log(
    `[seat ${seat}] #${id} ${describe(request, response)} -> ${res.status}`,
  );
  if (res.status === 422) {
    const error = await res.json().catch(() => null);
    console.warn(`[seat ${seat}] rejected: ${error?.reason ?? "unknown"}`);
    if (request.request.$case === "action" && error?.availableActions?.length) {
      const fallback =
        error.availableActions.find((a) => a.kind === "declareEnd") ??
        error.availableActions[0];
      const retry = {
        response: {
          $case: "action",
          value: { chosenActionIndex: fallback.index, usedDice: [] },
        },
      };
      const retryRes = await post("/api/arena/agent/respond", {
        matchId,
        id,
        response: retry,
      });
      console.log(`[seat ${seat}] retry #${id} -> ${retryRes.status}`);
    }
  }
}

async function prepareDeck() {
  const library = await fetch(`${base}/api/arena/decks`).then((r) => r.json());
  const deck = library.decks?.[0];
  if (!deck) throw new Error("牌组库为空");
  const res = await post("/api/arena/deck", {
    matchId,
    seat,
    deckId: deck.id,
    note: `示例 agent 选择：${deck.name}`,
  });
  console.log(`[seat ${seat}] pick deck ${deck.id} -> ${res.status}`);
  const startRes = await post("/api/arena/control", {
    matchId,
    action: "start",
  });
  console.log(`[seat ${seat}] request start -> ${startRes.status}`);
}

async function connect() {
  const url = `${base}/api/arena/agent/stream?matchId=${encodeURIComponent(matchId)}&seat=${seat}`;
  console.log(`[seat ${seat}] connecting ${url}`);
  const res = await fetch(url);
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "hello") {
        console.log(
          `[seat ${seat}] connected; pending=${payload.pending?.length ?? 0}`,
        );
        for (const p of payload.pending ?? []) void handleRequest(p.id, p.request);
      } else if (event === "request") {
        void handleRequest(payload.id, payload.request);
      } else if (event === "state") {
        // 最新状态：payload.state
      }
    }
  }
}

async function main() {
  if (pickDeck) {
    try {
      await prepareDeck();
    } catch (e) {
      console.error(`[seat ${seat}] pick deck failed: ${e.message}`);
    }
  }
  for (;;) {
    try {
      await connect();
    } catch (e) {
      console.error(`[seat ${seat}] ${e.message}; retry in 3s`);
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
}

main();
