/**
 * AI 对战的本地桥接服务（零依赖）。
 *
 * 角色分工：
 * - 浏览器页面是“对局宿主”，负责跑 Game、显示双棋盘；
 * - 本模块只做消息中转：把 AI 座位的 rpc 请求推给外部 agent，把 agent 的响应送回去；
 * - 同时保存最新状态/操作日志，供观众或调试读取。
 *
 * 挂载在 /api/arena/*，由 local/server.mjs 调用。
 */

import fs from "node:fs";
import nodePath from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = nodePath.dirname(fileURLToPath(import.meta.url));
const MAX_BODY = 8 * 1024 * 1024;
const KEEPALIVE_MS = 15000;

function json(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-cache",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  });
  res.end(body);
}

async function readJson(req) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY) throw new Error("body too large");
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sseWrite(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

const VALIDITY_LABELS = {
  0: "合法",
  1: "条件不满足",
  2: "没有可选目标",
  3: "骰子不足",
  4: "充能不足",
  5: "被技能/状态禁用",
  6: "本局秘传牌已用过",
};

function readJsonFile(paths) {
  for (const file of paths.filter(Boolean)) {
    try {
      if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, "utf8"));
    } catch {
      /* ignore */
    }
  }
  return null;
}

function catalogCandidates(appRoot) {
  return [
    appRoot ? nodePath.join(appRoot, "catalog.json") : null,
    nodePath.resolve(__dirname, "..", "..", "packages/local-rules/src/catalog.json"),
  ];
}

function decksCandidates(appRoot) {
  return [
    appRoot ? nodePath.join(appRoot, "decks.json") : null,
    nodePath.resolve(__dirname, "decks.json"),
  ];
}

export function createArenaBridge(options = {}) {
  const appRoot = options.root ?? null;
  const catalog = readJsonFile(catalogCandidates(appRoot)) ?? {
    cards: [],
    characters: [],
    tokens: [],
    skills: [],
  };
  const catalogCards = new Map((catalog.cards ?? []).map((c) => [c.id, c]));
  const catalogCharacters = new Map((catalog.characters ?? []).map((c) => [c.id, c]));
  const catalogTokens = new Map((catalog.tokens ?? []).map((c) => [c.id, c]));
  const catalogSkills = new Map((catalog.skills ?? []).map((c) => [c.id, c]));
  const deckLibrary = readJsonFile(decksCandidates(appRoot)) ?? { decks: [] };
  /** @type {Map<string, any>} */
  const matches = new Map();

  function getMatch(id) {
    let m = matches.get(id);
    if (!m) {
      m = {
        id,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        seq: 0,
        states: { 0: null, 1: null },
        logs: [],
        aiSeq: 0,
        aiLog: [],
        pending: new Map(),
        nextPendingId: 1,
        agents: { 0: new Set(), 1: new Set() },
        spectators: new Set(),
        config: {},
        decks: { 0: null, 1: null },
        startRequestedAt: null,
      };
      matches.set(id, m);
    }
    return m;
  }

  function appendAiEvent(m, seat, event) {
    const entry = { seq: ++m.aiSeq, at: Date.now(), seat: seat ?? null, event };
    m.aiLog.push(entry);
    if (m.aiLog.length > 20000) m.aiLog.splice(0, m.aiLog.length - 20000);
    if (seat === 0 || seat === 1) {
      broadcastAgents(m, seat, "ai_event", entry);
    } else {
      broadcastAgents(m, 0, "ai_event", entry);
      broadcastAgents(m, 1, "ai_event", entry);
    }
    broadcastSpectators(m, "ai_event", entry);
    m.updatedAt = Date.now();
    return entry;
  }

  function disabledByFor(state, seat) {
    const p = state?.player?.[seat];
    const active = (p?.character ?? []).find((c) => c.id === p.activeCharacterId);
    return (active?.entity ?? [])
      .filter((e) => (catalogTokens.get(e.definitionId)?.tags ?? []).includes("disableSkill"))
      .map((e) => ({
        id: e.id,
        definitionId: e.definitionId,
        name: catalogTokens.get(e.definitionId)?.name ?? `#${e.definitionId}`,
      }));
  }

  function invalid(reason, extra = {}) {
    return { ok: false, reason, ...extra };
  }

  function validateResponse(request, response, state, seat) {
    const rpc = request?.request;
    if (!rpc) return invalid("request 缺失");
    const res = response?.response;
    if (!res) return invalid("response 缺失");
    if (res.$case !== rpc.$case) {
      return invalid(`响应类型不匹配：请求 ${rpc.$case}，响应 ${res.$case}`);
    }
    const value = res.value ?? {};
    switch (rpc.$case) {
      case "action": {
        const actions = rpc.value?.action ?? [];
        const index = value.chosenActionIndex;
        if (!Number.isInteger(index) || index < 0 || index >= actions.length) {
          return invalid(`行动下标越界：${index}（共 ${actions.length} 个）`, {
            availableActions: actions.map((a, i) => ({
              index: i,
              kind: a.action?.$case,
              name: a.action?.$case,
              validity: a.validity,
              validityLabel: VALIDITY_LABELS[a.validity] ?? a.validity,
            })),
          });
        }
        const action = actions[index];
        if (action.validity !== 0) {
          return invalid(
            `该行动当前不可用：${VALIDITY_LABELS[action.validity] ?? action.validity}`,
            {
              validity: action.validity,
              validityLabel: VALIDITY_LABELS[action.validity] ?? action.validity,
              action: {
                index,
                kind: action.action?.$case,
                value: action.action?.value,
                cost: action.requiredCost,
                willBeEffectless: action.action?.value?.willBeEffectless,
              },
              disabledBy: disabledByFor(state, seat),
              availableActions: actions
                .map((a, i) => ({
                  index: i,
                  kind: a.action?.$case,
                  validity: a.validity,
                  validityLabel: VALIDITY_LABELS[a.validity] ?? a.validity,
                }))
                .filter((a) => a.validity === 0),
            },
          );
        }
        return {
          ok: true,
          response: {
            response: {
              $case: "action",
              value: {
                chosenActionIndex: index,
                usedDice: action.autoSelectedDice ?? [],
              },
            },
          },
        };
      }
      case "chooseActive": {
        const candidates = rpc.value?.candidateIds ?? [];
        if (!candidates.includes(value.activeCharacterId)) {
          return invalid(`出战角色 ${value.activeCharacterId} 不在候选里`, {
            candidates,
          });
        }
        return { ok: true, response };
      }
      case "selectCard": {
        const candidates = rpc.value?.candidateDefinitionIds ?? [];
        if (!candidates.includes(value.selectedDefinitionId)) {
          return invalid(`卡牌 ${value.selectedDefinitionId} 不在候选里`, {
            candidates,
          });
        }
        return { ok: true, response };
      }
      case "rerollDice": {
        if (!Array.isArray(value.diceToReroll)) {
          return invalid("diceToReroll 必须是数组");
        }
        const dice = state?.player?.[seat]?.dice ?? [];
        const pool = [...dice];
        for (const d of value.diceToReroll) {
          const index = pool.indexOf(d);
          if (index < 0) {
            return invalid(`要重投的骰子 ${d} 不在当前骰子里`, { dice });
          }
          pool.splice(index, 1);
        }
        return { ok: true, response };
      }
      case "switchHands": {
        if (!Array.isArray(value.removedHandIds)) {
          return invalid("removedHandIds 必须是数组");
        }
        const hand = new Set(
          (state?.player?.[seat]?.handCard ?? []).map((c) => c.id),
        );
        const bad = value.removedHandIds.filter((id) => !hand.has(id));
        if (bad.length) {
          return invalid(`这些牌不在手牌里：${bad.join(", ")}`, {
            hand: [...hand],
          });
        }
        return { ok: true, response };
      }
      default:
        return invalid(`未知 rpc：${rpc.$case}`);
    }
  }

  function normalizeDeck(input) {
    if (!input) return invalid("deck 缺失");
    const characters = [];
    for (const ch of input.characters ?? []) {
      const id = typeof ch === "number" ? ch : ch?.id;
      if (Number.isInteger(id)) characters.push(id);
    }
    const cards = [];
    for (const card of input.cards ?? []) {
      if (typeof card === "number") {
        cards.push(card);
      } else if (card && Number.isInteger(card.id)) {
        const count = Math.max(0, Math.round(card.count ?? 1));
        for (let i = 0; i < count; i++) cards.push(card.id);
      }
    }
    if (characters.length !== 3) {
      return invalid(`角色必须是 3 个，当前 ${characters.length}`);
    }
    if (new Set(characters).size !== 3) {
      return invalid("角色不能重复");
    }
    for (const id of characters) {
      if (!catalogCharacters.has(id)) return invalid(`未知角色 id：${id}`);
    }
    if (cards.length !== 30) {
      return invalid(`行动牌必须是 30 张，当前 ${cards.length}`);
    }
    const counts = new Map();
    for (const id of cards) {
      if (!catalogCards.has(id)) return invalid(`未知卡牌 id：${id}`);
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    for (const [id, count] of counts) {
      if (count > 2) {
        return invalid(`卡牌 ${catalogCards.get(id)?.name ?? id} 超过 2 张（${count}）`);
      }
    }
    const legendCount = [...counts.keys()].filter((id) =>
      (catalogCards.get(id)?.tags ?? []).includes("legend"),
    ).length;
    if (legendCount > 1) {
      return invalid(`秘传牌最多 1 张，当前 ${legendCount} 种`);
    }
    return {
      ok: true,
      deck: {
        name: input.name ?? "自定义牌组",
        note: input.note ?? "",
        characters,
        cards,
        characterInfo: characters.map((id) => ({
          id,
          name: catalogCharacters.get(id)?.name ?? `#${id}`,
        })),
        cardInfo: [...counts.entries()].map(([id, count]) => ({
          id,
          name: catalogCards.get(id)?.name ?? `#${id}`,
          count,
        })),
      },
    };
  }

  function broadcastSpectators(m, event, data) {
    for (const res of m.spectators) {
      try {
        sseWrite(res, event, data);
      } catch {
        m.spectators.delete(res);
      }
    }
  }

  function broadcastAgents(m, seat, event, data) {
    for (const res of m.agents[seat]) {
      try {
        sseWrite(res, event, data);
      } catch {
        m.agents[seat].delete(res);
      }
    }
  }

  function statusOf(m) {
    return {
      matchId: m.id,
      updatedAt: m.updatedAt,
      seq: m.seq,
      agents: { 0: m.agents[0].size, 1: m.agents[1].size },
      pending: {
        0: [...m.pending.values()].filter((p) => p.seat === 0).length,
        1: [...m.pending.values()].filter((p) => p.seat === 1).length,
      },
      pendingRequests: [...m.pending.values()].map((p) => ({
        id: p.id,
        seat: p.seat,
        rpc: p.request?.request?.$case,
        deadline: p.deadline,
      })),
      logCount: m.logs.length,
      config: m.config,
      aiSeq: m.aiSeq,
      aiLogCount: m.aiLog.length,
      decks: { 0: !!m.decks[0], 1: !!m.decks[1] },
      startRequested: !!m.startRequestedAt,
    };
  }

  function resolvePending(m, id, response) {
    const p = m.pending.get(id);
    if (!p) return false;
    clearTimeout(p.timer);
    m.pending.delete(id);
    p.resolve({ response });
    m.updatedAt = Date.now();
    broadcastSpectators(m, "responded", { id, seat: p.seat });
    broadcastAgents(m, p.seat, "responded", { id });
    return true;
  }

  /** @returns {boolean} 是否已处理 */
  async function handle(req, res) {
    const url = new URL(req.url, "http://localhost");
    const path = url.pathname;
    if (!path.startsWith("/api/arena/")) return false;

    res.setHeader("Access-Control-Allow-Origin", "*");
    if (req.method === "OPTIONS") {
      res.writeHead(204, {
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      });
      res.end();
      return true;
    }

    try {
      if (path === "/api/arena/catalog" && req.method === "GET") {
        // 给外部 agent 的卡牌/技能数值目录（只读）
        const candidates = [
          appRoot ? nodePath.join(appRoot, "catalog.json") : null,
          nodePath.resolve(__dirname, "..", "..", "packages/local-rules/src/catalog.json"),
        ].filter(Boolean);
        const file = candidates.find((p) => fs.existsSync(p));
        if (!file) {
          json(res, 404, { error: "catalog.json not found" });
          return true;
        }
        res.writeHead(200, {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-cache",
          "Access-Control-Allow-Origin": "*",
        });
        res.end(fs.readFileSync(file));
        return true;
      }

      if (path === "/api/arena/decks" && req.method === "GET") {
        json(res, 200, { decks: deckLibrary.decks ?? [] });
        return true;
      }

      if (path === "/api/arena/card-text" && req.method === "GET") {
        const file = readJsonFile([
          appRoot ? nodePath.join(appRoot, "card-text.json") : null,
          nodePath.resolve(__dirname, "card-text.json"),
        ]);
        if (!file) {
          json(res, 404, { error: "card-text.json not found" });
          return true;
        }
        json(res, 200, file);
        return true;
      }

      if (path === "/api/arena/deck" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        const seat = Number(url.searchParams.get("seat"));
        const m = getMatch(id);
        json(res, 200, {
          seat,
          deck: seat === 0 || seat === 1 ? m.decks[seat] : null,
          decks: m.decks,
        });
        return true;
      }

      if (path === "/api/arena/deck" && req.method === "POST") {
        const body = await readJson(req);
        const m = getMatch(body.matchId || "local");
        const seat = Number(body.seat);
        if (seat !== 0 && seat !== 1) {
          json(res, 400, { error: "seat must be 0 or 1" });
          return true;
        }
        const source =
          body.deckId
            ? (deckLibrary.decks ?? []).find((d) => d.id === body.deckId)
            : body;
        if (!source) {
          json(res, 404, { error: `deckId not found: ${body.deckId}` });
          return true;
        }
        const result = normalizeDeck({
          ...source,
          name: body.name ?? source.name,
          note: body.note ?? source.note,
        });
        if (!result.ok) {
          json(res, 422, result);
          return true;
        }
        m.decks[seat] = { ...result.deck, selectedAt: Date.now() };
        m.updatedAt = Date.now();
        broadcastAgents(m, 0, "deck_selected", { seat, deck: m.decks[seat] });
        broadcastAgents(m, 1, "deck_selected", { seat, deck: m.decks[seat] });
        broadcastSpectators(m, "deck_selected", { seat, deck: m.decks[seat] });
        appendAiEvent(m, null, {
          type: "deck_selected",
          seat,
          name: result.deck.name,
          characters: result.deck.characterInfo,
          cards: result.deck.cardInfo,
          note: result.deck.note,
        });
        json(res, 200, { ok: true, deck: m.decks[seat] });
        return true;
      }

      if (path === "/api/arena/prepare" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        const m = getMatch(id);
        json(res, 200, {
          matchId: m.id,
          decks: m.decks,
          startRequested: !!m.startRequestedAt,
          startRequestedAt: m.startRequestedAt,
          agents: { 0: m.agents[0].size, 1: m.agents[1].size },
          aiSeq: m.aiSeq,
        });
        return true;
      }

      if (path === "/api/arena/control" && req.method === "POST") {
        const body = await readJson(req);
        const m = getMatch(body.matchId || "local");
        if (body.action === "start") {
          m.startRequestedAt = Date.now();
          m.updatedAt = Date.now();
          broadcastAgents(m, 0, "start_requested", { at: m.startRequestedAt });
          broadcastAgents(m, 1, "start_requested", { at: m.startRequestedAt });
          broadcastSpectators(m, "start_requested", { at: m.startRequestedAt });
          appendAiEvent(m, null, { type: "start_requested", at: m.startRequestedAt });
          json(res, 200, { ok: true, startRequested: true });
          return true;
        }
        if (body.action === "clear") {
          m.decks = { 0: null, 1: null };
          m.startRequestedAt = null;
          m.updatedAt = Date.now();
          json(res, 200, { ok: true });
          return true;
        }
        json(res, 400, { error: `unknown control action: ${body.action}` });
        return true;
      }

      if (path === "/api/arena/ai/event" && req.method === "POST") {
        const body = await readJson(req);
        const m = getMatch(body.matchId || "local");
        const seat =
          body.seat === 0 || body.seat === 1 ? body.seat : null;
        const events = Array.isArray(body.events)
          ? body.events
          : body.event
            ? [body.event]
            : [];
        for (const event of events) appendAiEvent(m, seat, event);
        json(res, 200, { ok: true, lastSeq: m.aiSeq });
        return true;
      }

      if (path === "/api/arena/ai/log" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        const m = getMatch(id);
        const seatParam = url.searchParams.get("seat");
        const seat =
          seatParam === "0" ? 0 : seatParam === "1" ? 1 : null;
        const since = Number(url.searchParams.get("since") || 0);
        const limit = Math.min(
          500,
          Math.max(1, Number(url.searchParams.get("limit") || 200)),
        );
        const events = m.aiLog
          .filter(
            (e) =>
              e.seq > since &&
              (seat === null || e.seat === null || e.seat === seat),
          )
          .slice(-limit);
        json(res, 200, { events, lastSeq: m.aiSeq });
        return true;
      }

      if (path === "/api/arena/ai/snapshot" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        const m = getMatch(id);
        const seatParam = url.searchParams.get("seat");
        const seat =
          seatParam === "0" ? 0 : seatParam === "1" ? 1 : null;
        const entry = [...m.aiLog]
          .reverse()
          .find(
            (e) =>
              e.event?.type === "snapshot" &&
              (seat === null || e.seat === null || e.seat === seat),
          );
        json(res, 200, { snapshot: entry?.event ?? null, seq: entry?.seq ?? 0 });
        return true;
      }

      if (path === "/api/arena/status" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        json(res, 200, statusOf(getMatch(id)));
        return true;
      }

      if (path === "/api/arena/state" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        const seat = url.searchParams.get("seat");
        const m = getMatch(id);
        json(res, 200, seat !== null ? { seat: Number(seat), state: m.states[Number(seat)] } : { states: m.states });
        return true;
      }

      if (path === "/api/arena/state" && req.method === "POST") {
        const body = await readJson(req);
        const m = getMatch(body.matchId || "local");
        if (body.seat === 0 || body.seat === 1) {
          m.states[body.seat] = body.state;
        } else if (body.states) {
          m.states = body.states;
        }
        // 对局配置随时同步（限时/开关），让外部 agent 在第一次请求之前就能知情
        if (body.config && typeof body.config === "object") m.config = body.config;
        m.seq++;
        m.updatedAt = Date.now();
        const payload = { seat: body.seat, state: body.state, seq: m.seq };
        broadcastSpectators(m, "state", payload);
        if (body.seat === 0 || body.seat === 1) broadcastAgents(m, body.seat, "state", payload);
        json(res, 200, { ok: true, seq: m.seq });
        return true;
      }

      if (path === "/api/arena/log" && req.method === "POST") {
        const body = await readJson(req);
        const m = getMatch(body.matchId || "local");
        const entry = {
          time: Date.now(),
          seat: body.seat ?? null,
          text: String(body.text ?? ""),
          kind: body.kind ?? "action",
          detail: body.detail ?? null,
        };
        m.logs.push(entry);
        if (m.logs.length > 500) m.logs.splice(0, m.logs.length - 500);
        m.updatedAt = Date.now();
        broadcastSpectators(m, "log", entry);
        json(res, 200, { ok: true });
        return true;
      }

      if (path === "/api/arena/log" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        const m = getMatch(id);
        json(res, 200, { logs: m.logs });
        return true;
      }

      if (path === "/api/arena/spectate" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        const m = getMatch(id);
        json(res, 200, { status: statusOf(m), states: m.states, logs: m.logs });
        return true;
      }

      // 外部 agent 的 SSE 流：接收 request / state 事件
      if (path === "/api/arena/agent/stream" && req.method === "GET") {
        const id = url.searchParams.get("matchId") || "local";
        const seat = Number(url.searchParams.get("seat"));
        if (seat !== 0 && seat !== 1) {
          json(res, 400, { error: "seat must be 0 or 1" });
          return true;
        }
        const m = getMatch(id);
        res.writeHead(200, {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache, no-transform",
          Connection: "keep-alive",
          "Access-Control-Allow-Origin": "*",
          "X-Accel-Buffering": "no",
        });
        res.write(": connected\n\n");
        sseWrite(res, "hello", {
          matchId: id,
          seat,
          aiSeq: m.aiSeq,
          status: statusOf(m),
          pending: [...m.pending.values()]
            .filter((p) => p.seat === seat)
            .map((p) => ({ id: p.id, request: p.request, deadline: p.deadline })),
        });
        if (m.states[seat]) sseWrite(res, "state", { seat, state: m.states[seat], seq: m.seq });
        m.agents[seat].add(res);
        const ka = setInterval(() => {
          try {
            res.write(": keepalive\n\n");
          } catch {
            /* ignore */
          }
        }, KEEPALIVE_MS);
        req.on("close", () => {
          clearInterval(ka);
          m.agents[seat].delete(res);
        });
        return true;
      }

      // 页面发起一次 AI 决策请求，等待 agent 响应
      if (path === "/api/arena/agent/request" && req.method === "POST") {
        const body = await readJson(req);
        const m = getMatch(body.matchId || "local");
        const seat = Number(body.seat);
        if (seat !== 0 && seat !== 1) {
          json(res, 400, { error: "seat must be 0 or 1" });
          return true;
        }
        if (!body.request || typeof body.request !== "object") {
          json(res, 400, { error: "request is required" });
          return true;
        }
        if (body.state) m.states[seat] = body.state;
        if (body.config) m.config = body.config;
        const timeoutMs = Math.min(Math.max(Number(body.timeoutMs) || 60000, 1000), 10 * 60 * 1000);
        const id = m.nextPendingId++;
        const deadline = Date.now() + timeoutMs;
        const aiDetail = body.aiDetail ?? null;
        const payload = {
          id,
          matchId: m.id,
          seat,
          request: body.request,
          state: m.states[seat],
          deadline,
          aiDetail,
        };
        const result = await new Promise((resolve) => {
          const timer = setTimeout(() => {
            m.pending.delete(id);
            broadcastSpectators(m, "timeout", { id, seat });
            broadcastAgents(m, seat, "timeout", { id });
            resolve({ timeout: true });
          }, timeoutMs);
          m.pending.set(id, {
            id,
            seat,
            request: body.request,
            deadline,
            timer,
            resolve,
          });
          m.seq++;
          m.updatedAt = Date.now();
          broadcastAgents(m, seat, "request", payload);
          if (aiDetail) {
            appendAiEvent(m, seat, {
              ...aiDetail,
              requestId: id,
              seat,
              deadline,
            });
          }
          broadcastSpectators(m, "request", {
            id,
            seat,
            request: body.request,
            deadline,
          });
        });
        json(res, 200, { id, ...result });
        return true;
      }

      // agent 提交行动
      if (path === "/api/arena/agent/respond" && req.method === "POST") {
        const body = await readJson(req);
        const m = getMatch(body.matchId || "local");
        const id = Number(body.id);
        if (!Number.isFinite(id) || !body.response) {
          json(res, 400, { error: "id and response are required" });
          return true;
        }
        const pending = m.pending.get(id);
        if (!pending) {
          json(res, 404, { error: "request not found or already expired", id });
          return true;
        }
        const validation = validateResponse(
          pending.request,
          body.response,
          m.states[pending.seat],
          pending.seat,
        );
        if (!validation.ok) {
          const { ok: _ok, response: _response, ...detail } = validation;
          appendAiEvent(m, pending.seat, {
            type: "action_rejected",
            at: Date.now(),
            seat: pending.seat,
            requestId: id,
            ...detail,
          });
          json(res, 422, {
            error: "invalid response",
            requestId: id,
            ...detail,
          });
          return true;
        }
        if (!resolvePending(m, id, validation.response)) {
          json(res, 404, { error: "request not found or already expired", id });
          return true;
        }
        json(res, 200, { ok: true });
        return true;
      }

      if (path === "/api/arena/reset" && req.method === "POST") {
        const body = await readJson(req);
        const id = body.matchId || "local";
        const m = matches.get(id);
        if (m) {
          for (const p of m.pending.values()) {
            clearTimeout(p.timer);
            p.resolve({ timeout: true });
          }
          m.pending.clear();
          // 保留 agent / 观众的 SSE 连接，只清空对局数据
          m.states = { 0: null, 1: null };
          m.logs = [];
          m.seq = 0;
          // AI 日志保留：里面有开局前的 deck_selected / start_requested，
          // 用 match_reset 标记新一局。
          m.decks = { 0: null, 1: null };
          m.startRequestedAt = null;
          m.config = {};
          m.updatedAt = Date.now();
          appendAiEvent(m, null, { type: "match_reset", at: Date.now() });
          broadcastAgents(m, 0, "reset", {});
          broadcastAgents(m, 1, "reset", {});
          broadcastSpectators(m, "reset", {});
        }
        json(res, 200, { ok: true });
        return true;
      }

      json(res, 404, { error: "unknown arena endpoint", path });
      return true;
    } catch (e) {
      json(res, 400, { error: e instanceof Error ? e.message : String(e) });
      return true;
    }
  }

  return { handle, getMatch, matches };
}
