/**
 * 会话存储：append-only JSONL，一局一个文件，一行一条记录。
 *
 * 为什么不进数据库：
 *   - 单机、单写者、一局几百行，JSONL 可 grep / diff / 人读；
 *   - append 语义天然原子（O_APPEND 追加一行），不需要事务；
 *   - 将来要复用（别的任务也存上下文）就把本文件抽成 HTTP 服务，格式即接口。
 *
 * 写分两处、读只一处：
 *   - 司机写"事实"（请求 id、耗时、422、响应码）——只有司机知道这些；
 *   - 图写"意图"（kind=note，例如"我把第 12 回合的召唤物丢掉了"）；
 *   - 注入时只读一份预算内的摘要（summarizeTurns），丢得起、可重建。
 */
import fs from "node:fs";
import path from "node:path";

const VERSION = 1;

/** 估算 token：中文约 1 字 1 token 上下，这里取保守的 chars/1.5。 */
export function estTokens(chars) {
  return Math.ceil((chars ?? 0) / 1.5);
}

export class SessionStore {
  /**
   * @param {object} opts
   * @param {string} opts.dir      会话目录
   * @param {string} opts.session  会话名（文件名）
   * @param {0|1}    opts.seat
   * @param {boolean} [opts.payload] 是否把原始 request/aiDetail 落盘（回放用）
   */
  constructor({ dir, session, seat, payload = true }) {
    this.dir = dir;
    this.session = session;
    this.seat = seat;
    this.payload = payload;
    fs.mkdirSync(dir, { recursive: true });
    this.file = path.join(dir, `${session}.jsonl`);
    // 续写同一会话时，seq 接着上次的走，避免回放时顺序错乱
    this.seq = 0;
    for (const row of readSession(this.file)) {
      if (typeof row.seq === "number" && row.seq > this.seq) this.seq = row.seq;
    }
    this.fd = fs.openSync(this.file, "a");
    this.closed = false;
  }

  get path() {
    return this.file;
  }

  append(row) {
    if (this.closed) return null;
    const record = {
      v: VERSION,
      session: this.session,
      seq: ++this.seq,
      ts: Date.now(),
      ...row,
    };
    fs.writeSync(this.fd, `${JSON.stringify(record)}\n`);
    return record;
  }

  header(data = {}) {
    return this.append({ kind: "header", seat: this.seat, ...data });
  }

  /** 一个回合的完整事实 */
  turn(data) {
    return this.append({ kind: "turn", seat: this.seat, ...data });
  }

  /** 图/司机写的意图备注（回放时人读） */
  note(text, data = {}) {
    return this.append({ kind: "note", seat: this.seat, text, ...data });
  }

  /** 对局收尾（订阅 ai_event 里的 winner） */
  outcome(data = {}) {
    return this.append({ kind: "outcome", seat: this.seat, ...data });
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    try {
      fs.closeSync(this.fd);
    } catch {
      /* ignore */
    }
  }
}

/** 读一整个会话文件，坏行跳过。 */
export function readSession(file) {
  let raw = "";
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  const rows = [];
  for (const line of raw.split("\n")) {
    const text = line.trim();
    if (!text) continue;
    try {
      rows.push(JSON.parse(text));
    } catch {
      /* 半行（进程被杀）忽略 */
    }
  }
  return rows;
}

export function listSessions(dir) {
  try {
    return fs
      .readdirSync(dir)
      .filter((f) => f.endsWith(".jsonl"))
      .map((f) => {
        const full = path.join(dir, f);
        const stat = fs.statSync(full);
        const rows = readSession(full);
        const turns = rows.filter((r) => r.kind === "turn");
        return {
          name: f.replace(/\.jsonl$/, ""),
          file: full,
          bytes: stat.size,
          turns: turns.length,
          mtime: stat.mtimeMs,
          firstTs: rows[0]?.ts ?? null,
          lastTs: rows.at(-1)?.ts ?? null,
        };
      })
      .sort((a, b) => b.mtime - a.mtime);
  } catch {
    return [];
  }
}

const DECISION_TEXT = (d) => {
  if (!d) return "（无决策）";
  const kind = d.kind ?? "?";
  if (kind === "action") return `行动#${d.index}${d.name ? ` ${d.name}` : ""}`;
  if (kind === "chooseActive") return `出战 ${d.name ?? d.id ?? "?"}`;
  if (kind === "selectCard") return `选牌 ${d.name ?? d.definitionId ?? "?"}`;
  if (kind === "rerollDice") return `重投 ${(d.diceLabels ?? d.dice ?? []).join("/") || "无"}`;
  if (kind === "switchHands") return `换牌 ${d.count ?? 0} 张`;
  return JSON.stringify(d).slice(0, 80);
};

/**
 * 把历史回合压成一段可注入的文本。
 * 只保留"我做了什么 + 结果"，不保留请求原文（那是给回放用的，不是给模型看的）。
 */
export function summarizeTurns(rows, { maxTurns = 30, maxChars = 3000 } = {}) {
  const turns = rows.filter((r) => r.kind === "turn");
  const notes = rows.filter((r) => r.kind === "note");
  const picked = turns.slice(-maxTurns);
  const lines = [];
  if (turns.length > picked.length) {
    lines.push(`（更早 ${turns.length - picked.length} 个回合已省略）`);
  }
  for (const t of picked) {
    const bits = [];
    bits.push(`#${t.seq} r${t.round ?? "?"} ${t.rpcKind ?? "?"}`);
    bits.push(DECISION_TEXT(t.decision));
    if (t.ok === false) bits.push(`失败:${(t.rejectReason ?? "unknown").slice(0, 40)}`);
    if (t.attempts > 1) bits.push(`重试${t.attempts - 1}`);
    if (t.latencyMs != null) bits.push(`${t.latencyMs}ms`);
    lines.push(bits.join(" "));
  }
  let text = lines.join("\n");
  if (text.length > maxChars) text = text.slice(text.length - maxChars);
  const noteText = notes
    .slice(-5)
    .map((n) => `- ${n.text}`)
    .join("\n");
  return {
    text,
    stats: {
      turns: turns.length,
      usedTurns: picked.length,
      notes: notes.length,
      chars: text.length,
      estTokens: estTokens(text.length),
      noteText: noteText.slice(0, 600),
    },
  };
}

/** 回放：列出某会话里所有回合，供 replay 工具逐回合重跑。 */
export function loadTurns(file) {
  return readSession(file)
    .filter((r) => r.kind === "turn")
    .map((r) => ({
      seq: r.seq,
      ts: r.ts,
      requestId: r.requestId,
      rpcKind: r.rpcKind,
      round: r.round,
      deadline: r.deadline,
      payload: r.payload ?? null,
      decision: r.decision ?? null,
      latencyMs: r.latencyMs ?? null,
      ok: r.ok,
    }));
}
