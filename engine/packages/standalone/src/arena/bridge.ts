/**
 * 浏览器 → 本地桥接服务（local/arena/bridge.mjs）的客户端。
 * 页面是对局宿主；这里只负责把状态/请求/日志送出去，把 agent 的响应取回来。
 */
import type { RpcRequest, RpcResponse } from "@gi-tcg/core";

const BASE = "/api/arena";
const DEFAULT_MATCH = "local";

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`arena api ${path} ${res.status}: ${text}`);
  }
  return (await res.json()) as T;
}

export function pushState(seat: 0 | 1, state: unknown, matchId = DEFAULT_MATCH): void {
  void postJson("/state", { matchId, seat, state }).catch(() => {});
}

export function pushConfig(config: unknown, matchId = DEFAULT_MATCH): void {
  void postJson("/state", { matchId, config }).catch(() => {});
}

export function pushLog(
  entry: { text: string; seat?: 0 | 1 | null; kind?: string; detail?: unknown },
  matchId = DEFAULT_MATCH,
): void {
  void postJson("/log", { matchId, ...entry }).catch(() => {});
}

export function resetMatch(matchId = DEFAULT_MATCH): Promise<{ ok: boolean }> {
  return postJson("/reset", { matchId });
}

let aiEventQueue: Promise<void> = Promise.resolve();

/** 等当前 AI 事件队列发完（发 rpc 前调用，保证日志顺序）。 */
export function flushAiEvents(): Promise<void> {
  return aiEventQueue;
}

/** 把结构化 AI 事件送到桥接服务；seat=null 表示双方都可见。串行发送以保证顺序。 */
export function pushAiEvents(
  matchId: string,
  seat: 0 | 1 | null,
  events: any[],
): void {
  if (!events.length) return;
  const payload = { matchId, seat, events };
  aiEventQueue = aiEventQueue.then(async () => {
    try {
      await postJson("/ai/event", payload);
    } catch {
      /* bridge may be down */
    }
  });
}

export interface AiLogEntry {
  seq: number;
  at: number;
  seat: 0 | 1 | null;
  event: any;
}

export async function getAiLog(
  seat: 0 | 1,
  since = 0,
  matchId = DEFAULT_MATCH,
): Promise<{ events: AiLogEntry[]; lastSeq: number } | null> {
  try {
    const res = await fetch(
      `${BASE}/ai/log?matchId=${encodeURIComponent(matchId)}&seat=${seat}&since=${since}`,
    );
    if (!res.ok) return null;
    return (await res.json()) as { events: AiLogEntry[]; lastSeq: number };
  } catch {
    return null;
  }
}

export interface DecisionResult {
  id?: number;
  response?: RpcResponse;
  timeout?: boolean;
}

export async function requestDecision(options: {
  matchId?: string;
  seat: 0 | 1;
  request: RpcRequest;
  state: unknown;
  timeoutMs: number;
  signal?: AbortSignal;
  config?: unknown;
  /** 结构化的请求描述，桥接服务会连同 request 一起推给 agent */
  aiDetail?: unknown;
}): Promise<DecisionResult> {
  return postJson<DecisionResult>(
    "/agent/request",
    {
      matchId: options.matchId ?? DEFAULT_MATCH,
      seat: options.seat,
      request: options.request,
      state: options.state,
      timeoutMs: options.timeoutMs,
      config: options.config,
      aiDetail: options.aiDetail,
    },
    options.signal,
  );
}

export interface ArenaPrepare {
  matchId: string;
  decks: { 0: any | null; 1: any | null };
  startRequested: boolean;
  startRequestedAt: number | null;
  agents: { 0: number; 1: number };
  aiSeq: number;
}

export async function getPrepare(matchId = DEFAULT_MATCH): Promise<ArenaPrepare | null> {
  try {
    const res = await fetch(`${BASE}/prepare?matchId=${encodeURIComponent(matchId)}`);
    if (!res.ok) return null;
    return (await res.json()) as ArenaPrepare;
  } catch {
    return null;
  }
}

export interface ArenaStatus {
  matchId: string;
  seq: number;
  agents: { 0: number; 1: number };
  pending: { 0: number; 1: number };
  logCount: number;
  updatedAt: number;
}

export async function getStatus(matchId = DEFAULT_MATCH): Promise<ArenaStatus | null> {
  try {
    const res = await fetch(`${BASE}/status?matchId=${encodeURIComponent(matchId)}`);
    if (!res.ok) return null;
    return (await res.json()) as ArenaStatus;
  } catch {
    return null;
  }
}

export function agentStreamUrl(seat: 0 | 1, matchId = DEFAULT_MATCH): string {
  const origin = typeof location !== "undefined" ? location.origin : "http://127.0.0.1:7717";
  return `${origin}${BASE}/agent/stream?matchId=${encodeURIComponent(matchId)}&seat=${seat}`;
}
