/**
 * 实时观测面板：边打边看"提示词 / 模型原话 / 解析结果 / 最终响应 / 逐节点耗时"。
 *
 * 零依赖（node:http 手写 SSE），只读 driver/sessions/live.jsonl。
 * 用法：
 *   node driver/tools/viewer.mjs --port 7810 --dir driver/sessions
 * 然后浏览器打开 http://127.0.0.1:7810
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const out = { port: 7810, dir: path.join(ROOT, "..", "sessions"), open: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--port") out.port = Number(argv[++i]);
    else if (a === "--dir") out.dir = path.resolve(argv[++i]);
    else if (a === "--help" || a === "-h") out.help = true;
  }
  return out;
}

const HELP = `实时观测面板
  --port <n>   监听端口（默认 7810）
  --dir <dir>  会话目录（含 live.jsonl，默认 driver/sessions）`;

/** 读取整份 live.jsonl，跳过坏行。 */
function readAll(file) {
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
      // 写一半的行，下次再读
    }
  }
  return rows;
}

const PAGE = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>七圣召唤 · AI 观测台</title>
<style>
  :root {
    --bg: #0b0f14; --panel: #131a22; --panel2: #1a2430; --line: #24303d;
    --text: #e6edf3; --dim: #8b9bb0; --accent: #4fc3f7; --ok: #3ddc97;
    --warn: #f5a623; --bad: #ff6b6b; --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.6 "Microsoft YaHei", system-ui, sans-serif; }
  header { position: sticky; top: 0; z-index: 5; display: flex; gap: 14px; align-items: center;
           padding: 12px 18px; background: #0d131aee; border-bottom: 1px solid var(--line);
           backdrop-filter: blur(8px); }
  h1 { font-size: 15px; margin: 0; letter-spacing: .5px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--bad); }
  .dot.on { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
  .spacer { flex: 1; }
  .stat { font-family: var(--mono); font-size: 12px; color: var(--dim); }
  .stat b { color: var(--text); font-weight: 600; }
  select { background: var(--panel2); color: var(--text); border: 1px solid var(--line);
           border-radius: 6px; padding: 4px 8px; }
  main { display: grid; grid-template-columns: 320px 1fr; gap: 16px; padding: 16px; align-items: start; }
  .list { display: flex; flex-direction: column; gap: 6px; max-height: calc(100vh - 90px); overflow: auto; }
  .item { padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel);
          cursor: pointer; }
  .item:hover { border-color: #35506b; }
  .item.active { border-color: var(--accent); background: #142434; }
  .item .row1 { display: flex; gap: 8px; align-items: center; font-family: var(--mono); font-size: 12px; }
  .tag { padding: 1px 6px; border-radius: 5px; font-size: 11px; background: var(--panel2); color: var(--dim); }
  .tag.ok { background: #12352a; color: var(--ok); }
  .tag.bad { background: #3a1c1c; color: var(--bad); }
  .tag.act { background: #14304a; color: var(--accent); }
  .item .row2 { color: var(--dim); font-size: 12px; margin-top: 2px; overflow: hidden;
                text-overflow: ellipsis; white-space: nowrap; }
  .detail { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .card > h2 { margin: 0; padding: 9px 12px; font-size: 13px; font-weight: 600; color: var(--dim);
               background: var(--panel2); display: flex; gap: 8px; align-items: center; }
  .card > .body { padding: 12px; }
  pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: var(--mono);
        font-size: 12.5px; line-height: 1.55; color: #cbd8e6; max-height: 460px; overflow: auto; }
  pre.small { max-height: 180px; }
  .prompt pre { background: #0a1016; padding: 10px; border-radius: 8px; border: 1px solid var(--line); }
  .kv { display: grid; grid-template-columns: 96px 1fr; gap: 4px 10px; font-family: var(--mono); font-size: 12.5px; }
  .kv .k { color: var(--dim); }
  .bars { display: flex; flex-direction: column; gap: 4px; }
  .bar { display: grid; grid-template-columns: 110px 1fr 64px; gap: 8px; align-items: center;
         font-family: var(--mono); font-size: 11.5px; color: var(--dim); }
  .bar .fill { height: 7px; border-radius: 4px; background: linear-gradient(90deg, #2b6cb0, #4fc3f7); }
  .empty { color: var(--dim); padding: 40px; text-align: center; }
  .pill { font-family: var(--mono); font-size: 11px; padding: 1px 7px; border-radius: 99px;
          background: #10202e; color: var(--accent); }
</style>
</head>
<body>
<header>
  <span class="dot" id="dot"></span>
  <h1>七圣召唤 · AI 观测台</h1>
  <select id="session"><option value="">全部会话</option></select>
  <span class="spacer"></span>
  <span class="stat">决策 <b id="n">0</b></span>
  <span class="stat">平均 <b id="avg">–</b> ms</span>
  <span class="stat">提示词 <b id="chars">–</b> 字</span>
  <span class="stat">兜底 <b id="fb">0</b></span>
  <span class="stat">422 <b id="rj">0</b></span>
  <span class="stat">估计胜率 <b id="wr">–</b></span>
</header>
<main>
  <div class="list" id="list"></div>
  <div class="detail" id="detail"><div class="empty">等第一手决策…</div></div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let rows = [];
let selected = null;
let session = "";
let autoPicked = false;

function esc(s) {
  return String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function fmtMs(v) {
  if (v == null) return "–";
  return Math.round(v);
}

function turnsOf() {
  return rows.filter((r) => r.kind === "turn" && (!session || r.session === session));
}

function reviewsOf() {
  return rows.filter((r) => r.kind === "review" && (!session || r.session === session));
}

function notesOf() {
  return rows.filter((r) => r.kind === "note" && (!session || r.session === session));
}

function renderSessions() {
  const names = [...new Set(rows.map((r) => r.session).filter(Boolean))];
  // 首次打开自动选中最新一局，免得历史（含兜底）混进统计
  if (!autoPicked && !session && names.length) {
    session = names[names.length - 1];
    autoPicked = true;
  }
  const cur = session;
  $("session").innerHTML = '<option value="">全部会话</option>' +
    names.map((n) => '<option value="' + esc(n) + '">' + esc(n) + "</option>").join("");
  $("session").value = cur;
}

function renderStats() {
  const turns = turnsOf();
  $("n").textContent = turns.length;
  const lat = turns.filter((t) => t.latencyMs != null);
  $("avg").textContent = lat.length ? Math.round(lat.reduce((a, b) => a + b.latencyMs, 0) / lat.length) : "–";
  const chars = turns.filter((t) => t.promptChars).map((t) => t.promptChars);
  $("chars").textContent = chars.length ? Math.round(chars.reduce((a, b) => a + b, 0) / chars.length) : "–";
  $("fb").textContent = turns.filter((t) => t.fallback).length;
  $("rj").textContent = turns.filter((t) => t.rejected).length;
  const revs = reviewsOf().filter((r) => typeof r.winrate === "number");
  $("wr").textContent = revs.length ? Math.round(revs.at(-1).winrate * 100) + "%" : "–";
}

function renderList() {
  const turns = turnsOf().slice().reverse();
  if (!turns.length) {
    $("list").innerHTML = '<div class="empty">还没有决策</div>';
    return;
  }
  $("list").innerHTML = turns
    .map((t) => {
      const idx = t.parsed && t.parsed.index != null ? t.parsed.index : "–";
      const direct = t.rpc === "rerollDice" || t.rpc === "switchHands";
      const ok = t.parsed && t.parsed.ok;
      const tag = direct
        ? '<span class="tag">直答</span>'
        : ok
          ? '<span class="tag ok">合法</span>'
          : '<span class="tag bad">兜底</span>';
      return (
        '<div class="item' + (selected === t.seq ? " active" : "") + '" data-seq="' + t.seq + '">' +
        '<div class="row1"><span class="tag act">' + esc(t.rpc || "?") + "</span>" +
        '<span>#' + t.seq + "</span><span>回合 " + esc(t.round ?? "?") + "</span>" +
        tag + '<span class="spacer"></span><span class="tag">' + fmtMs(t.latencyMs) + "ms</span></div>" +
        '<div class="row2">选 ' + esc(idx) + " · " + esc(t.decisionLabel || "") + "</div></div>"
      );
    })
    .join("");
  for (const el of document.querySelectorAll(".item")) {
    el.onclick = () => {
      selected = Number(el.dataset.seq);
      render();
    };
  }
}

function renderDetail() {
  const t = turnsOf().find((r) => r.seq === selected) || turnsOf().at(-1);
  if (!t) {
    $("detail").innerHTML = '<div class="empty">等第一手决策…</div>';
    return;
  }
  selected = t.seq;
  const timings = (t.timings || []).filter((x) => x.ms != null);
  const maxMs = Math.max(1, ...timings.map((x) => x.ms));
  const bars = timings
    .map(
      (x) =>
        '<div class="bar"><span>' + esc(x.id) + " · " + esc(x.type) + "</span>" +
        '<div class="fill" style="width:' + Math.max(2, (x.ms / maxMs) * 100) + '%"></div>' +
        "<span>" + fmtMs(x.ms) + "ms</span></div>"
    )
    .join("");
  const plan = t.plan ? '<div class="card"><h2>赛前计划（每手都带）</h2><div class="body"><pre class="small">' + esc(t.plan) + "</pre></div></div>" : "";
  const planNotes = notesOf().filter((n) => n.kind === "note" && n.plan);
  const planFromNote = !t.plan && planNotes.length
    ? '<div class="card"><h2>赛前计划（本会话）</h2><div class="body"><pre class="small">' + esc(planNotes.at(-1).plan) + "</pre></div></div>"
    : "";
  const revs = reviewsOf();
  const reviewCard = revs.length
    ? '<div class="card"><h2>复盘：意图与胜率（每回合末自动生成）</h2><div class="body">' +
      '<div class="kv"><span class="k">最新意图</span><span>' + esc(revs.at(-1).intent || "–") + "</span>" +
      '<span class="k">最新胜率</span><span>' + (typeof revs.at(-1).winrate === "number" ? Math.round(revs.at(-1).winrate * 100) + "%" : "–") + "</span></div>" +
      '<div class="bars">' +
      revs.map((r) => {
        const w = typeof r.winrate === "number" ? r.winrate : 0;
        return '<div class="bar"><span>R' + esc(r.round) + "</span>" +
          '<div class="fill" style="width:' + Math.max(2, w * 100) + '%"></div>' +
          "<span>" + (typeof r.winrate === "number" ? Math.round(r.winrate * 100) + "%" : "–") + "</span></div>";
      }).join("") +
      "</div></div></div>"
    : "";

  $("detail").innerHTML =
    '<div class="card"><h2>这一手 <span class="pill">' + esc(t.rpc || "?") + "</span>" +
    '<span class="pill">回合 ' + esc(t.round ?? "?") + "</span>" +
    '<span class="pill">' + fmtMs(t.latencyMs) + " ms</span>" +
    '<span class="pill">提示词 ' + esc(t.promptChars ?? "–") + " 字</span></h2>" +
    '<div class="body"><div class="kv">' +
    '<span class="k">请求 id</span><span>' + esc(t.requestId ?? "–") + "</span>" +
    '<span class="k">候选数</span><span>' + esc(t.candidates ?? "–") + "</span>" +
    '<span class="k">解析编号</span><span>' + esc(t.parsed?.index ?? "–") +
      (t.rpc === "rerollDice" || t.rpc === "switchHands" ? "（这类请求不需要编号）" : t.parsed?.ok ? "（合法）" : "（不合法/兜底）") + "</span>" +
    '<span class="k">解析说明</span><span>' + esc(t.parsed?.reason || "–") + "</span>" +
    '<span class="k">最终响应</span><span>' + esc(JSON.stringify(t.decision ?? {})) + "</span>" +
    "</div></div></div>" +
    reviewCard + plan + planFromNote +
    '<div class="card prompt"><h2>发给模型的提示词 <span class="pill">' + esc(t.promptChars ?? "–") + " 字</span></h2>" +
    '<div class="body"><pre>' + esc(t.prompt || "（这一手没有提示词）") + "</pre></div></div>" +
    (t.reasoning
      ? '<div class="card think"><h2>模型的思考过程（reasoning）</h2><div class="body"><pre class="dim">' +
        esc(t.reasoning) + "</pre></div></div>"
      : "") +
    '<div class="card"><h2>模型原话（未加工）</h2><div class="body"><pre>' + esc(t.reply ?? "（无）") + "</pre></div></div>" +
    '<div class="card"><h2>逐节点耗时</h2><div class="body"><div class="bars">' + (bars || "（无）") + "</div></div></div>";
}

function render() {
  renderStats();
  renderList();
  renderDetail();
}

function setRows(next) {
  rows = next;
  renderSessions();
  render();
}

$("session").onchange = (e) => {
  autoPicked = true;
  session = e.target.value;
  selected = null;
  render();
};

const es = new EventSource("/api/stream");
es.onopen = () => $("dot").classList.add("on");
es.onerror = () => $("dot").classList.remove("on");
es.onmessage = (ev) => {
  try {
    const payload = JSON.parse(ev.data);
    if (payload.type === "snapshot") setRows(payload.rows);
    else if (payload.type === "row") {
      rows.push(payload.row);
      renderSessions();
      if (payload.row.kind === "turn" || payload.row.kind === "review") {
        render();
      }
    }
  } catch {}
};
fetch("/api/rows").then((r) => r.json()).then((j) => setRows(j.rows || []));
</script>
</body>
</html>`;

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log(HELP);
  process.exit(0);
}
const FILE = path.join(args.dir, "live.jsonl");

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  if (url.pathname === "/") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(PAGE);
    return;
  }
  if (url.pathname === "/api/rows") {
    res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ rows: readAll(FILE) }));
    return;
  }
  if (url.pathname === "/api/tail") {
    // 便于命令行调试：只要最后 N 条
    const n = Number(url.searchParams.get("n") || 20);
    const rows = readAll(FILE).slice(-n);
    res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(rows, null, 2));
    return;
  }
  if (url.pathname === "/api/stream") {
    res.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
      connection: "keep-alive",
    });
    const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
    let seen = 0;
    const all = readAll(FILE);
    seen = all.length;
    send({ type: "snapshot", rows: all });
    const timer = setInterval(() => {
      const rows = readAll(FILE);
      if (rows.length < seen) {
        seen = 0;
        send({ type: "snapshot", rows });
      }
      for (let i = seen; i < rows.length; i++) send({ type: "row", row: rows[i] });
      seen = rows.length;
    }, 500);
    // SSE 心跳，避免代理/浏览器判定空闲断开
    const beat = setInterval(() => res.write(": ping\n\n"), 15000);
    req.on("close", () => {
      clearInterval(timer);
      clearInterval(beat);
    });
    return;
  }
  res.writeHead(404).end("not found");
});

server.listen(args.port, "127.0.0.1", () => {
  console.log(`观测台：http://127.0.0.1:${args.port}`);
  console.log(`数据：${FILE}`);
});
