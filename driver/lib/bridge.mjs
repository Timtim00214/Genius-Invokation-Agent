/**
 * 七圣召唤桥接服务（engine 的 local/arena/bridge.mjs）的零依赖客户端。
 *
 * 只做四件事：连 SSE、读只读接口、提交响应、读状态（用于 seat 自检）。
 * 不做任何决策——决策在 arena-driver.mjs 里。
 *
 * 协议要点（见 local/arena/API.md）：
 *   GET  /api/arena/agent/stream?matchId=local&seat=N   SSE：hello/request/state/ai_event/…
 *   POST /api/arena/agent/respond {matchId,id,response}  200 成功 / 422 非法（pending 仍在）/ 404 已过期
 *   GET  /api/arena/status  agents{0,1}、pendingRequests[]、config
 *
 * seat 的语义：SSE 的 ?seat=N 就是"我是几号座位"，respond 体里没有 seat，
 * 归属由服务端 pending 记录决定。连错座位 = 永远收不到 request。
 */
import http from "node:http";

const DEFAULT_TIMEOUT = 30_000;

export class ArenaBridge {
  constructor({ base = "http://127.0.0.1:7717", matchId = "local" } = {}) {
    this.base = base.replace(/\/$/, "");
    this.matchId = matchId;
    this.sse = null;
    this.closed = false;
  }

  url(pathname, params = {}) {
    const u = new URL(this.base + pathname);
    u.searchParams.set("matchId", this.matchId);
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) u.searchParams.set(k, String(v));
    }
    return u.toString();
  }

  async get(pathname, params = {}, { timeoutMs = DEFAULT_TIMEOUT } = {}) {
    const res = await fetch(this.url(pathname, params), {
      signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await res.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      json = { raw: text };
    }
    return { status: res.status, json };
  }

  async post(pathname, body, { timeoutMs = DEFAULT_TIMEOUT } = {}) {
    const res = await fetch(`${this.base}${pathname}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await res.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      json = { raw: text };
    }
    return { status: res.status, json };
  }

  status() {
    return this.get("/api/arena/status", {}, { timeoutMs: 5000 });
  }

  prepare() {
    return this.get("/api/arena/prepare", {}, { timeoutMs: 5000 });
  }

  catalog() {
    return this.get("/api/arena/catalog", {}, { timeoutMs: 30_000 });
  }

  cardText() {
    return this.get("/api/arena/card-text", {}, { timeoutMs: 30_000 });
  }

  aiLog(seat, since = 0, limit = 200) {
    return this.get("/api/arena/ai/log", { seat, since, limit }, { timeoutMs: 10_000 });
  }

  /** 对局公开日志（双方每步动作，页面 POST /api/arena/log 写入，环形 500 条）。 */
  gameLog() {
    return this.get("/api/arena/log", {}, { timeoutMs: 10_000 });
  }

  /** 最新一份完整局面快照（me 完整 / opponent 公开） */
  snapshot(seat) {
    return this.get("/api/arena/ai/snapshot", { seat }, { timeoutMs: 10_000 });
  }

  deck(seat) {
    return this.get("/api/arena/deck", { seat }, { timeoutMs: 5000 });
  }

  /**
   * 给某个座位设置牌组。牌组形状见 bridge 的 normalizeDeck：
   * ``{name?, characters: [id], cards: [id] 或 [{id, count}], note?}``。
   */
  setDeck(seat, deck) {
    return this.post(
      "/api/arena/deck",
      { matchId: this.matchId, seat, ...deck },
      { timeoutMs: 10_000 },
    );
  }

  respond(id, response, { timeoutMs = DEFAULT_TIMEOUT } = {}) {
    return this.post("/api/arena/agent/respond", { matchId: this.matchId, id, response }, { timeoutMs });
  }

  /**
   * 订阅某个座位的 SSE。
   * @param {object} opts
   * @param {0|1} opts.seat
   * @param {(event: string, data: any) => void} opts.onEvent
   * @param {(err: Error) => void} [opts.onError]
   * @param {() => void} [opts.onClose]
   * @param {boolean} [opts.reconnect] 断线是否自动重连
   */
  connect({ seat, onEvent, onError, onClose, reconnect = true }) {
    const url = this.url("/api/arena/agent/stream", { seat });
    let retry = 0;
    let stopped = false;

    const open = () => {
      if (this.closed || stopped) return;
      const req = http.get(
        url,
        { headers: { accept: "text/event-stream", "cache-control": "no-cache" } },
        (res) => {
          if (res.statusCode !== 200) {
            let body = "";
            res.setEncoding("utf8");
            res.on("data", (c) => (body += c));
            res.on("end", () => {
              onError?.(new Error(`SSE HTTP ${res.statusCode}: ${body.slice(0, 200)}`));
              schedule();
            });
            return;
          }
          retry = 0;
          let buf = "";
          let event = "message";
          let data = "";
          res.setEncoding("utf8");
          res.on("data", (chunk) => {
            buf += chunk;
            let idx;
            while ((idx = buf.indexOf("\n")) >= 0) {
              let line = buf.slice(0, idx);
              buf = buf.slice(idx + 1);
              if (line.endsWith("\r")) line = line.slice(0, -1);
              if (line === "") {
                if (data) {
                  try {
                    onEvent(event, JSON.parse(data));
                  } catch (e) {
                    onError?.(new Error(`SSE 载荷不是 JSON: ${String(e)}`));
                  }
                }
                event = "message";
                data = "";
                continue;
              }
              if (line.startsWith(":")) continue; // ": connected" / ": keepalive"
              if (line.startsWith("event:")) event = line.slice(6).trim();
              else if (line.startsWith("data:")) data += (data ? "\n" : "") + line.slice(5).trim();
            }
          });
          res.on("end", () => {
            onClose?.();
            schedule();
          });
        },
      );
      req.on("error", (err) => {
        onError?.(err);
        schedule();
      });
      this.sse = req;
    };

    const schedule = () => {
      if (this.closed || stopped || !reconnect) return;
      retry += 1;
      const delay = Math.min(500 * 2 ** Math.min(retry, 4), 5000);
      setTimeout(open, delay);
    };

    open();
    return {
      close: () => {
        stopped = true;
        try {
          this.sse?.destroy();
        } catch {
          /* ignore */
        }
      },
    };
  }

  close() {
    this.closed = true;
    try {
      this.sse?.destroy();
    } catch {
      /* ignore */
    }
  }
}
