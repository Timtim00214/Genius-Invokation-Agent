/**
 * 对局回放台：把 driver/sessions/*.jsonl 里录下来的一局，按时间轴"放映"出来——
 * 每一步行动、AI 的思维链、赛前战术，都能暂停、单步、2s/5s 自动推进。
 *
 * 零依赖（node:http），只读会话文件，不碰对局服务，也不改任何现有文件。
 * 用法：
 *   node driver/tools/replay-viewer.mjs --port 7812 --dir driver/sessions
 * 然后浏览器打开 http://127.0.0.1:7812
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const out = { port: 7812, dir: path.join(ROOT, "..", "sessions"), open: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--port") out.port = Number(argv[++i]);
    else if (a === "--dir") out.dir = path.resolve(argv[++i]);
    else if (a === "--help" || a === "-h") out.help = true;
  }
  return out;
}

const HELP = `对局回放台
  --port <n>   监听端口（默认 7812）
  --dir <dir>  会话目录（默认 driver/sessions）`;

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log(HELP);
  process.exit(0);
}

/* ------------------------------------------------------------------ */
/* 会话解析                                                            */
/* ------------------------------------------------------------------ */

const cache = new Map(); // file -> { mtime, sessions }

function readLines(file) {
  let text = "";
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  const rows = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      rows.push(JSON.parse(t));
    } catch {
      /* 写一半的行 */
    }
  }
  return rows;
}

function listSessionFiles(dir) {
  let names = [];
  try {
    names = fs.readdirSync(dir);
  } catch {
    return [];
  }
  return names
    .filter((n) => n.endsWith(".jsonl") && n !== "live.jsonl")
    .map((n) => path.join(dir, n));
}

function splitSessions(file, rows) {
  const sessions = [];
  let cur = null;
  for (const r of rows) {
    if (r.kind === "header") {
      cur = {
        header: r,
        rows: [],
      };
      sessions.push(cur);
      continue;
    }
    if (!cur) {
      cur = { header: {}, rows: [] };
      sessions.push(cur);
    }
    cur.rows.push(r);
  }
  return sessions;
}

function planOf(session) {
  for (const r of session.rows) {
    if (r.kind === "note" && (r.stage === "prep" || r.plan)) {
      return { plan: r.plan ?? "", deck: r.deck ?? null, chars: (r.plan ?? "").length, row: r };
    }
  }
  return { plan: "", deck: null, chars: 0, row: null };
}

function outcomeOf(session) {
  for (let i = session.rows.length - 1; i >= 0; i--) {
    const r = session.rows[i];
    if (r.kind === "outcome") {
      const m = String(r.why ?? "").match(/winner=(\d)/);
      return { ...r, winner: m ? Number(m[1]) : null };
    }
  }
  return null;
}

function loadFile(file) {
  let st = null;
  try {
    st = fs.statSync(file);
  } catch {
    return [];
  }
  const hit = cache.get(file);
  if (hit && hit.mtime === st.mtimeMs) return hit.sessions;
  const rows = readLines(file);
  const sessions = splitSessions(file, rows);
  cache.set(file, { mtime: st.mtimeMs, sessions });
  return sessions;
}

/* ------------------------------------------------------------------ */
/* 动作的人话描述                                                      */
/* ------------------------------------------------------------------ */

const KIND_NAME = {
  action: "出招 / 打牌",
  rerollDice: "重投骰子",
  switchHands: "换初始手牌",
  chooseActive: "选出战角色",
  selectCard: "选牌",
};

function charName(state, id) {
  if (!state || !Array.isArray(state.characters)) return id;
  const c = state.characters.find((x) => x && x.id === id);
  return c ? c.name ?? id : id;
}

function costText(a) {
  const c = a?.cost ?? a?.requiredCost;
  if (!c) return "";
  if (typeof c.text === "string" && c.text) return c.text;
  const total = c.total;
  const dice = Array.isArray(c.dice) ? c.dice : [];
  if (typeof total === "number" && !dice.length) return "" + total;
  return "";
}

const PREVIEW_WORDS = {
  damage: "伤害",
  heal: "治疗",
  shield: "护盾",
  defeated: "击倒",
  summon: "召唤",
  createSummon: "召唤",
  moveEntity: "生成/放置",
  removeEntity: "移除",
  attach: "附属",
  drawCard: "抽牌",
  createHandCard: "获得手牌",
  modifyEntityVar: "数值变化",
  convertDice: "骰子转换",
  rerollDice: "重投骰子",
  switchActive: "切换出战",
  addEntityVar: "叠加",
  reduceEntityVar: "减少",
};

function previewText(a) {
  const p = a?.preview;
  if (!Array.isArray(p) || !p.length) return "";
  const bits = [];
  for (const e of p) {
    if (!e) continue;
    const t = String(e.type ?? "");
    const w = PREVIEW_WORDS[t] ?? t;
    bits.push(e.amount != null ? `${w} ${e.amount}` : w);
  }
  return bits.join("；");
}

function dealScene(turn, session) {
  const payload = turn.payload ?? null;
  const detail = payload?.aiDetail ?? null;
  const state = payload?.state ?? null;
  const decision = turn.decision ?? null;
  const kind = turn.rpcKind ?? decision?.kind ?? "";

  if (kind === "action" && detail && Array.isArray(detail.actions)) {
    const idx = Number(decision?.index ?? decision?.chosenActionIndex ?? -1);
    const chosen = detail.actions.find((a) => a && a.index === idx) ?? detail.actions[idx];
    if (chosen) {
      const who = state?.activeCharacter?.name ?? "";
      const name = chosen.skillName ?? chosen.cardName ?? chosen.name ?? `#${idx}`;
      const cost = costText(chosen);
      const diceLabels = Array.isArray(chosen.autoSelectedDiceLabels)
        ? chosen.autoSelectedDiceLabels.join("/")
        : "";
      const pv = previewText(chosen);
      const bits = [pv];
      if (chosen.willBeEffectless) bits.push("（这一下没有效果）");
      return {
        title: name,
        sub: [cost, diceLabels ? `骰 ${diceLabels}` : ""].filter(Boolean).join(" ｜ "),
        detail: bits.filter(Boolean).join("  "),
      };
    }
    return { title: `编号 ${idx}`, sub: "", detail: "" };
  }
  if (kind === "rerollDice") {
    const dice = decision?.dice ?? detail?.diceToReroll ?? [];
    const labels = detail?.diceLabels ?? [];
    if (!Array.isArray(dice) || dice.length === 0)
      return { title: "不重投", sub: "", detail: "保留当前骰面" };
    const shown = dice
      .map((d, i) => labels[i] ?? String(d))
      .filter((x, i, arr) => arr.indexOf(x) === i);
    return {
      title: `重投 ${dice.length} 颗`,
      sub: `骰面 ${dice.join(" / ")}`,
      detail: offeredOf(detail, kind, turn, decision),
    };
  }
  if (kind === "switchHands") {
    const count = Number(decision?.count ?? 0) || (detail?.removedHandIds ?? []).length;
    const hand = Array.isArray(detail?.hand) ? detail.hand : [];
    return {
      title: count ? `换掉 ${count} 张手牌` : "不换手牌",
      sub: "",
      detail: hand.length ? `手牌：${hand.map((c) => c?.name ?? c?.definitionId).join("  ")}` : "",
    };
  }
  if (kind === "chooseActive") {
    const id = detail?.activeCharacterId ?? decision?.activeCharacterId ?? decision?.index;
    return { title: "选择出战角色", sub: charName(state, id) || "", detail: "" };
  }
  if (kind === "selectCard") {
    return { title: "选择一张牌", sub: charName(state, decision?.definitionId) || "", detail: "" };
  }
  return {
    title: KIND_NAME[kind] ?? kind ?? "（未知）",
    sub: "",
    detail: decision ? JSON.stringify(decision) : "",
  };
}

function offeredOf(detail, kind, turn, decision) {
  if (!detail) return "";
  if (kind === "rerollDice") {
    const labels = detail.diceLabels ?? [];
    const dice = detail.dice ?? [];
    if (!Array.isArray(dice) || !dice.length) return "";
    return dice.map((d, i) => `${labels[i] ?? d}(${d})`).join("  ");
  }
  if (kind === "switchHands") {
    const hand = detail.hand ?? [];
    if (!Array.isArray(hand) || !hand.length) return "";
    return hand.map((c) => c?.name ?? c?.definitionId).join("  ");
  }
  return "";
}

function reviewOf(session) {
  const out = [];
  for (const r of session.rows) {
    if (r.kind === "note" && r.stage && r.stage !== "prep") out.push(r);
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* 帧构建                                                              */
/* ------------------------------------------------------------------ */

function buildSessionIndex() {
  const files = listSessionFiles(args.dir);
  const list = [];
  for (const file of files) {
    const sessions = loadFile(file);
    sessions.forEach((s, i) => {
      const h = s.header ?? {};
      const outcome = outcomeOf(s);
      const plan = planOf(s);
      const turns = s.rows.filter((r) => r.kind === "turn");
      if (!turns.length) return;
      list.push({
        key: `${path.basename(file)}#${i}`,
        file: path.basename(file),
        seat: h.seat ?? null,
        mode: h.mode ?? null,
        matchId: h.matchId ?? h.sessionId ?? path.basename(file),
        startedAt: h.ts ?? h.startedAt ?? null,
        turns: turns.length,
        winner: outcome?.winner ?? null,
        rounds: outcome?.rounds ?? null,
        planChars: plan.chars,
        ended: !!outcome,
      });
    });
  }
  list.sort((a, b) => String(b.startedAt ?? "").localeCompare(String(a.startedAt ?? "")));
  return list;
}

function buildFrames(keys) {
  const files = listSessionFiles(args.dir);
  const picked = [];
  for (const file of files) {
    const base = path.basename(file);
    const sessions = loadFile(file);
    sessions.forEach((s, i) => {
      const key = `${base}#${i}`;
      if (!keys.includes(key)) return;
      picked.push({ key, file: base, session: s });
    });
  }
  const frames = [];
  const plans = {};
  const seats = [];
  let matchId = null;
  for (const { file, session } of picked) {
    const h = session.header ?? {};
    const seat = typeof h.seat === "number" ? h.seat : 0;
    if (matchId == null) matchId = h.matchId ?? file;
    seats.push({ seat, file, mode: h.mode ?? null, deck: h.deck ?? null });
    const p = planOf(session);
    plans[seat] = { plan: p.plan, deck: p.deck, chars: p.chars, summary: p.row?.summary ?? "" };
    for (const r of session.rows) {
      if (r.kind === "turn") {
        const scene = dealScene(r, session);
        frames.push({
          ts: r.ts ?? null,
          seat,
          round: r.round ?? null,
          kind: r.rpcKind ?? r.decision?.kind ?? null,
          title: scene.title,
          sub: scene.sub,
          detail: scene.detail,
          reasoning: r.reasoning ?? null,
          note: r.note ?? null,
          latencyMs: r.latencyMs ?? null,
          finishReason: r.finishReason ?? null,
          promptChars: r.promptChars ?? null,
          decision: r.decision ?? null,
          hasPayload: !!r.payload,
        });
      } else if (r.kind === "note" && r.stage && r.stage !== "prep") {
        frames.push({
          ts: r.ts ?? null,
          seat,
          round: r.round ?? null,
          kind: "review",
          title: r.stage === "review" ? "回合复盘" : String(r.stage),
          sub: "",
          detail: [r.summary, r.intent, r.winRate != null ? `胜率 ${r.winRate}%` : ""]
            .filter(Boolean)
            .join(" ｜ "),
          reasoning: null,
          note: null,
          latencyMs: null,
          finishReason: null,
          promptChars: null,
          decision: null,
          hasPayload: false,
        });
      }
    }
  }
  frames.sort((a, b) => String(a.ts ?? "").localeCompare(String(b.ts ?? "")) || (a.seat ?? 0) - (b.seat ?? 0));
  return { matchId, seats, plans, frames };
}

/* ------------------------------------------------------------------ */
/* HTTP                                                                */
/* ------------------------------------------------------------------ */

const PAGE = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>七圣召唤 · 对局回放</title>
<style>
  :root {
    --bg:#0a0e13; --panel:#121a22; --panel2:#1a2430; --line:#24303d; --text:#e6edf3;
    --dim:#8b9bb0; --accent:#4fc3f7; --ok:#3ddc97; --warn:#f5a623; --bad:#ff6b6b;
    --p0:#7bd3a0; --p1:#f2a65a; --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.65 "Microsoft YaHei", system-ui, sans-serif; }
  header { position:sticky; top:0; z-index:9; display:flex; gap:12px; align-items:center; padding:10px 18px;
           background:#0c1218ee; border-bottom:1px solid var(--line); backdrop-filter:blur(8px); }
  h1 { font-size:15px; margin:0; letter-spacing:.5px; }
  .spacer { flex:1; }
  button, select { background:var(--panel2); color:var(--text); border:1px solid var(--line);
                   border-radius:7px; padding:5px 11px; cursor:pointer; font:inherit; }
  button:hover, select:hover { border-color:#3c5a78; }
  button.primary { background:#14304a; border-color:#2b6a96; color:var(--accent); }
  button:disabled { opacity:.4; cursor:default; }
  main { display:grid; grid-template-columns:330px 1fr; gap:16px; padding:16px; align-items:start; }
  .list { display:flex; flex-direction:column; gap:6px; max-height:calc(100vh - 96px); overflow:auto; }
  .item { padding:8px 10px; border:1px solid var(--line); border-radius:9px; background:var(--panel); cursor:pointer; }
  .item:hover { border-color:#35506b; }
  .item.on { border-color:var(--accent); background:#132334; }
  .item .r1 { display:flex; gap:8px; align-items:center; font-family:var(--mono); font-size:12px; }
  .item .r2 { color:var(--dim); font-size:12px; margin-top:2px; }
  .tag { padding:1px 6px; border-radius:5px; font-size:11px; background:var(--panel2); color:var(--dim); }
  .tag.ok { background:#12352a; color:var(--ok); }
  .tag.bad { background:#3a1c1c; color:var(--bad); }
  .tag.s0 { background:#123a2a; color:var(--p0); }
  .tag.s1 { background:#3a2a12; color:var(--p1); }
  .stage { display:flex; flex-direction:column; gap:12px; min-width:0; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:11px; overflow:hidden; }
  .card > h2 { margin:0; padding:9px 13px; font-size:13px; font-weight:600; color:var(--dim);
               background:var(--panel2); display:flex; gap:10px; align-items:center; }
  .card > .body { padding:13px; }
  .now .head { display:flex; gap:12px; align-items:baseline; padding:16px 18px 6px; }
  .now .title { font-size:22px; font-weight:700; letter-spacing:.5px; }
  .now .sub { color:var(--dim); font-size:13px; }
  .now .detail { padding:0 18px 14px; color:#cfe3f2; }
  .think { padding:14px 18px 18px; font-family:var(--mono); font-size:12.5px; line-height:1.7;
           color:#b9cbdc; white-space:pre-wrap; word-break:break-word; max-height:44vh; overflow:auto; }
  .think.empty { color:var(--dim); font-family:inherit; }
  .bar { display:flex; gap:10px; align-items:center; padding:10px 14px; background:var(--panel);
         border:1px solid var(--line); border-radius:11px; position:sticky; bottom:12px; z-index:8; }
  input[type=range] { flex:1; accent-color:var(--accent); }
  .meta { display:flex; gap:14px; flex-wrap:wrap; font-family:var(--mono); font-size:11.5px; color:var(--dim); }
  .meta b { color:var(--text); font-weight:600; }
  .plans { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .plan .body { font-size:13px; color:#cbd8e6; white-space:pre-wrap; max-height:26vh; overflow:auto; }
  .upcoming { font-family:var(--mono); font-size:12px; color:var(--dim); display:flex; flex-direction:column; gap:3px; }
  .upcoming .row { display:flex; gap:8px; }
  .upcoming .row.cur { color:var(--accent); }
  .empty { color:var(--dim); padding:40px; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>七圣召唤 · 对局回放</h1>
  <span class="meta" id="headMeta"></span>
  <span class="spacer"></span>
  <span class="meta" id="pos">0 / 0</span>
</header>
<main>
  <div class="list" id="list"></div>
  <div class="stage">
    <div class="card now" id="now"><div class="empty">左边点一局开始。按住 Ctrl 再点另一个座位，就能把两个 AI 的同一场对战拼成一条时间轴。</div></div>
    <div class="bar">
      <button id="prev" title="上一步（←）">⏮</button>
      <button id="play" class="primary" title="播放/暂停（空格）">▶ 播放</button>
      <button id="next" title="下一步（→）">⏭</button>
      <select id="speed">
        <option value="0">手动（按 → 推进）</option>
        <option value="2000">每 2 秒一步</option>
        <option value="5000" selected>每 5 秒一步</option>
        <option value="8000">每 8 秒一步</option>
      </select>
      <input type="range" id="slider" min="0" max="0" value="0" />
    </div>
    <div class="card"><h2>接下去</h2><div class="body upcoming" id="upcoming"></div></div>
    <div class="plans" id="plans"></div>
  </div>
</main>
<script>
var state = { sessions: [], picked: [], frames: [], plans: {}, pos: -1, timer: null, playing: false, speed: 5000 };
var $ = function (id) { return document.getElementById(id); };

function el(tag, cls, text) {
  var d = document.createElement(tag);
  if (cls) d.className = cls;
  if (text != null) d.textContent = text;
  return d;
}
function hhmmss(ts) {
  if (!ts) return "";
  var d = typeof ts === "number" ? new Date(ts) : new Date(String(ts));
  if (!isNaN(d.getTime())) return d.toTimeString().slice(0, 8);
  var m = String(ts).match(/T(\\d\\d:\\d\\d)/);
  return m ? m[1] : String(ts);
}
function seatTag(seat) {
  return el("span", "tag " + (seat === 1 ? "s1" : "s0"), "座位 " + seat);
}

function renderList() {
  var box = $("list");
  box.innerHTML = "";
  state.sessions.forEach(function (s) {
    var d = el("div", "item" + (state.picked.indexOf(s.key) >= 0 ? " on" : ""));
    var r1 = el("div", "r1");
    r1.appendChild(seatTag(s.seat == null ? 0 : s.seat));
    r1.appendChild(el("span", "tag", s.mode || "?"));
    r1.appendChild(el("span", null, s.turns + " 手"));
    if (s.ended) r1.appendChild(el("span", "tag " + (s.winner === s.seat ? "ok" : "bad"),
      s.winner === s.seat ? "胜" : "负"));
    var r2 = el("div", "r2", hhmmss(s.startedAt) + " · " + s.file + (s.planChars ? " · 战术 " + s.planChars + " 字" : ""));
    d.appendChild(r1); d.appendChild(r2);
    d.onclick = function (ev) {
      if (ev.ctrlKey || ev.metaKey) {
        var i = state.picked.indexOf(s.key);
        if (i >= 0) state.picked.splice(i, 1); else state.picked.push(s.key);
      } else {
        state.picked = [s.key];
      }
      renderList();
      if (state.picked.length) loadFrames();
    };
    box.appendChild(d);
  });
}

function loadFrames() {
  fetch("/api/frames?keys=" + encodeURIComponent(state.picked.join(",")))
    .then(function (r) { return r.json(); })
    .then(function (data) {
      state.frames = data.frames || [];
      state.plans = data.plans || {};
      state.pos = -1;
      $("slider").max = Math.max(0, state.frames.length - 1);
      $("headMeta").textContent = "对局 " + (data.matchId || "") + " · " + state.frames.length + " 帧";
      renderPlans();
      showNext();
      if (state.frames.length && state.pos < 0) {
        state.pos = 0;
        renderFrame(0, false);
      }
    });
}

function renderPlans() {
  var box = $("plans");
  box.innerHTML = "";
  [0, 1].forEach(function (seat) {
    var p = state.plans[seat];
    if (!p) return;
    var card = el("div", "card plan");
    var h = el("h2", null);
    h.appendChild(seatTag(seat));
    h.appendChild(el("span", null, "赛前战术" + (p.chars ? "（" + p.chars + " 字）" : "")));
    var body = el("div", "body", p.plan || "（这一局没有记录战术）");
    card.appendChild(h); card.appendChild(body);
    box.appendChild(card);
  });
}

function showNext() {
  var i = state.pos + 1;
  $("pos").textContent = (state.pos + 1) + " / " + state.frames.length;
  $("slider").value = Math.max(0, state.pos);
  var up = $("upcoming");
  up.innerHTML = "";
  state.frames.slice(i, i + 6).forEach(function (f, k) {
    var row = el("div", "row" + (k === 0 ? " cur" : ""));
    row.appendChild(el("span", null, "R" + (f.round == null ? "?" : f.round)));
    row.appendChild(el("span", null, "座位" + f.seat));
    row.appendChild(el("span", null, f.kind === "review" ? "复盘" : f.title));
    up.appendChild(row);
  });
  if (i >= state.frames.length) up.appendChild(el("div", null, "—— 全剧终 ——"));
}

function renderFrame(idx, reveal) {
  var f = state.frames[idx];
  if (!f) return;
  var now = $("now");
  now.innerHTML = "";
  var head = el("div", "head");
  head.appendChild(seatTag(f.seat));
  head.appendChild(el("span", "tag", "第 " + (f.round == null ? "?" : f.round) + " 回合"));
  head.appendChild(el("span", "title", f.detail && f.kind === "review" ? f.title : f.title));
  var sub = el("div", "sub", [f.sub, f.latencyMs ? (Math.round(f.latencyMs / 100) / 10) + "s" : "",
    f.finishReason ? "finish=" + f.finishReason : ""].filter(Boolean).join(" · "));
  head.appendChild(sub);
  now.appendChild(head);
  if (f.detail) now.appendChild(el("div", "detail", f.detail));

  var thinkTitle = el("h2", null);
  thinkTitle.appendChild(el("span", null, "思维链" + (f.reasoning ? "（" + f.reasoning.length + " 字）" : "")));
  var card = el("div", "card");
  card.appendChild(thinkTitle);
  var think = el("div", "think");
  if (!f.reasoning) {
    think.className = "think empty";
    think.textContent = f.hasPayload === false ? "（这一帧没有记录思维链）" : "（没有记录思维链）";
    card.appendChild(think);
  } else if (!reveal) {
    think.textContent = f.reasoning;
    card.appendChild(think);
  } else {
    card.appendChild(think);
    var full = f.reasoning;
    var t0 = performance.now();
    var dur = Math.max(1200, Math.min(9000, full.length * 6));
    var step = function (t) {
      var k = Math.min(1, (performance.now() - t0) / dur);
      think.textContent = full.slice(0, Math.ceil(full.length * k));
      think.scrollTop = think.scrollHeight;
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  now.appendChild(card);
  showNext();
}

function go(delta) {
  var i = state.pos + delta;
  if (i < 0) i = 0;
  if (i > state.frames.length - 1) i = state.frames.length - 1;
  state.pos = i;
  renderFrame(i, true);
}

function play() {
  if (!state.frames.length) return;
  if (state.speed === 0) { go(1); return; }
  if (state.playing) { stop(); return; }
  state.playing = true;
  $("play").textContent = "⏸ 暂停";
  if (state.pos < 0) go(1);
  state.timer = setInterval(function () {
    if (state.pos >= state.frames.length - 1) { stop(); return; }
    go(1);
  }, state.speed);
}
function stop() {
  state.playing = false;
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  $("play").textContent = "▶ 播放";
}

$("prev").onclick = function () { stop(); go(-1); };
$("next").onclick = function () { stop(); go(1); };
$("play").onclick = play;
$("speed").onchange = function (e) {
  state.speed = Number(e.target.value);
  $("play").textContent = state.speed === 0 ? "▶ 下一步" : "▶ 播放";
  if (state.playing) { stop(); play(); }
};
$("slider").oninput = function (e) { stop(); state.pos = Number(e.target.value); renderFrame(state.pos, false); };
document.addEventListener("keydown", function (e) {
  if (e.key === "ArrowRight") { stop(); go(1); }
  else if (e.key === "ArrowLeft") { stop(); go(-1); }
  else if (e.key === " ") { e.preventDefault(); play(); }
});

fetch("/api/sessions").then(function (r) { return r.json(); }).then(function (data) {
  state.sessions = data.sessions || [];
  var fromUrl = new URLSearchParams(location.search).get("keys");
  if (fromUrl) state.picked = fromUrl.split(",").filter(Boolean);
  else if (state.sessions.length) state.picked = [state.sessions[0].key];
  renderList();
  if (state.picked.length) loadFrames();
});
</script>
</body>
</html>`;

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  const json = (obj) => {
    const body = JSON.stringify(obj);
    res.writeHead(200, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
    res.end(body);
  };
  if (url.pathname === "/") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(PAGE);
    return;
  }
  if (url.pathname === "/api/sessions") {
    json({ dir: args.dir, sessions: buildSessionIndex() });
    return;
  }
  if (url.pathname === "/api/frames") {
    const keys = String(url.searchParams.get("keys") || "").split(",").filter(Boolean);
    json(buildFrames(keys));
    return;
  }
  res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
  res.end("not found");
});

server.listen(args.port, "127.0.0.1", () => {
  console.log(`对局回放台已启动：http://127.0.0.1:${args.port}`);
  console.log(`会话目录：${args.dir}`);
});
