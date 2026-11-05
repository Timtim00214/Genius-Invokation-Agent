// replay.mjs — 把录制下来的真实回合重新喂给 VMAF 图，离线看每一步到底算出了什么。
//
// 用途：调提示词/调节点时，不必开游戏、不必等对局。一局里录下来的每一回合
//       （driver --mode record 落盘的 sessions/*.jsonl）都能被反复重放。
//
// 用法：
//   node driver/tools/replay.mjs --list                      # 看这一局录了哪些回合
//   node driver/tools/replay.mjs --id 932                     # 重放第 932 号请求
//   node driver/tools/replay.mjs                              # 默认重放最后一回合
//   node driver/tools/replay.mjs --id 932 --save out.json     # 顺便把 shared 存成文件
//   node driver/tools/replay.mjs --show prompt                # 额外打印拼好的提示词
//
// 说明：meta.remainingMs 用 --budget 模拟（默认 45000），因为录制时不含 deadline。

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readSession, summarizeTurns } from "../lib/store.mjs";
import { checkShape, describeDecision, describeRequest } from "../lib/policy.mjs";
import { ArenaBridge } from "../lib/bridge.mjs";
import { buildNameSlice, compactCatalog } from "../lib/names.mjs";
import { buildCardTextSlice, collectCardTextIds, compactCardText } from "../lib/cardtext.mjs";
import { injectSecrets } from "../lib/secrets.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DRIVER_ROOT = path.resolve(HERE, "..");

function parseArgs(argv) {
  const args = {
    session: path.join(DRIVER_ROOT, "sessions", "rec-pve.jsonl"),
    vmaf: "http://127.0.0.1:8000",
    bridge: "http://127.0.0.1:7717",
    match: "local",
    blueprint: path.join(DRIVER_ROOT, "blueprints", "decide.json"),
    blueprintId: null,
    decisionNode: "build",
    budget: 45000,
    id: null,
    list: false,
    save: null,
    show: [],
    quiet: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === "--session") args.session = next();
    else if (a === "--vmaf") args.vmaf = next();
    else if (a === "--bridge") args.bridge = next();
    else if (a === "--match") args.match = next();
    else if (a === "--blueprint") args.blueprint = next();
    else if (a === "--blueprint-id") args.blueprintId = next();
    else if (a === "--decision-node") args.decisionNode = next();
    else if (a === "--budget") args.budget = Number(next());
    else if (a === "--id") args.id = Number(next());
    else if (a === "--list") args.list = true;
    else if (a === "--save") args.save = next();
    else if (a === "--dump") args.dump = next();
    else if (a === "--show") args.show = next().split(",");
    else if (a === "--quiet") args.quiet = true;
    else if (a === "-h" || a === "--help") {
      console.log(fs.readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n").slice(1, 20).join("\n"));
      process.exit(0);
    } else {
      console.error(`未知参数：${a}`);
      process.exit(2);
    }
  }
  return args;
}

function preview(value, limit = 160) {
  let text;
  if (typeof value === "string") text = value;
  else text = JSON.stringify(value);
  if (text == null) return "null";
  text = text.replace(/\s+/g, " ");
  return text.length > limit ? `${text.slice(0, limit)}…（共 ${text.length} 字符）` : text;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (!fs.existsSync(args.session)) {
    console.error(`找不到会话文件：${args.session}`);
    process.exit(2);
  }
  const rows = readSession(args.session);
  const turns = rows.filter((r) => r.kind === "turn");
  const withPayload = turns.filter((t) => t.payload);

  if (args.list) {
    console.log(`会话 ${path.basename(args.session)}：${turns.length} 回合，其中 ${withPayload.length} 回合带原始数据`);
    for (const t of withPayload) {
      const decision = t.decision ? JSON.stringify(describeDecision(t.payload.request, t.decision)) : "（未提交）";
      console.log(`  #${t.requestId} ${String(t.rpcKind).padEnd(12)} 回合=${t.round ?? "?"}  ${decision}`);
    }
    return;
  }

  const target = args.id != null ? withPayload.find((t) => t.requestId === args.id) : withPayload.at(-1);
  if (!target) {
    console.error(args.id != null ? `这一局里没有 requestId=${args.id} 的录制` : "这一局没有带原始数据的回合（录制时是否用了 --no-payload？）");
    process.exit(2);
  }

  // --blueprint-id 优先：直接读网页上正在编辑的那一份，避免"本地文件 vs 服务端"两份不一致
  let blueprint;
  if (args.blueprintId) {
    const res = await fetch(`${args.vmaf.replace(/\/+$/, "")}/blueprints/${encodeURIComponent(args.blueprintId)}`);
    if (!res.ok) throw new Error(`取蓝图 ${args.blueprintId} 失败：HTTP ${res.status}`);
    const payload = await res.json();
    blueprint = payload.blueprint ?? payload;
    console.log(`蓝图：服务端 ${args.blueprintId}（${blueprint.nodes?.length ?? 0} 节点）`);
  } else {
    blueprint = JSON.parse(fs.readFileSync(args.blueprint, "utf8"));
  }
  const stateForNames = target.payload.state ?? null;
  const sealed = injectSecrets(blueprint, process.env);
  if (sealed.injected.length) console.log(`  已从环境变量补 api_key：${sealed.injected.join("、")}`);
  if (sealed.missing.length) console.log(`  ⚠ 这些 LLM 节点没有 api_key（dry_run）：${sealed.missing.join("、")}`);
  blueprint.nodes = sealed.blueprint.nodes ?? blueprint.nodes;
  blueprint.edges = sealed.blueprint.edges ?? blueprint.edges;

  // catalog：局面里只有 definitionId，名字得靠它翻译；玩家 id 一局内不变，缓存到本地文件省一次请求
  let catalog = {};
  const catalogFile = path.join(DRIVER_ROOT, "samples", "catalog.json");
  if (fs.existsSync(catalogFile)) {
    try {
      catalog = JSON.parse(fs.readFileSync(catalogFile, "utf8"));
    } catch {
      catalog = {};
    }
  }
  if (!catalog || !catalog.characters || Object.keys(catalog.characters).length === 0) {
    try {
      const bridge = new ArenaBridge({ base: args.bridge, matchId: args.match });
      const got = await bridge.catalog();
      catalog = compactCatalog(got?.json ?? null);
      fs.mkdirSync(path.dirname(catalogFile), { recursive: true });
      fs.writeFileSync(catalogFile, JSON.stringify(catalog), "utf8");
    } catch (e) {
      console.warn(`  （拉 catalog 失败，局面里的名字会退化成 id：${e.message}）`);
    }
  }
  const earlier = turns.filter((t) => t.seq < target.seq);
  const summary = summarizeTurns(earlier);
  // 卡牌效果文本：与司机同样的裁剪逻辑；没有缓存就现场拉一次并存下来
  let cardText = { version: null, descriptions: {} };
  const cardTextFile = path.join(DRIVER_ROOT, "samples", "card-text.json");
  if (fs.existsSync(cardTextFile)) {
    try {
      cardText = JSON.parse(fs.readFileSync(cardTextFile, "utf8"));
    } catch {
      cardText = { version: null, descriptions: {} };
    }
  }
  if (!cardText?.descriptions || Object.keys(cardText.descriptions).length === 0) {
    try {
      const bridge = new ArenaBridge({ base: args.bridge, matchId: args.match });
      const got = await bridge.cardText();
      cardText = compactCardText(got?.json ?? null);
      fs.mkdirSync(path.dirname(cardTextFile), { recursive: true });
      fs.writeFileSync(cardTextFile, JSON.stringify(cardText), "utf8");
    } catch (e) {
      console.warn(`  （拉 card-text 失败，卡片效果不会进提示词：${e.message}）`);
    }
  }
  const cardSlice = buildCardTextSlice(cardText, collectCardTextIds(stateForNames, target.payload.aiDetail ?? null));
  // 对局日志：会话里存了就直接用（离线回放），否则现场拉一次最近的决策
  let gameLog = Array.isArray(target.payload.gameLog) ? target.payload.gameLog : [];
  if (!gameLog.length) {
    try {
      const bridge = new ArenaBridge({ base: args.bridge, matchId: args.match });
      const got = await bridge.gameLog();
      const logs = Array.isArray(got?.json?.logs) ? got.json.logs : [];
      gameLog = logs.filter((e) => e && e.kind === "decision").slice(-20);
    } catch {
      gameLog = [];
    }
  }
  const meta = {
    matchId: rows[0]?.matchId ?? "local",
    seat: target.seat ?? rows[0]?.seat ?? 1,
    requestId: target.requestId,
    rpcKind: target.rpcKind,
    round: target.round ?? null,
    deadline: null,
    remainingMs: args.budget,
    attempt: 0,
    replay: true,
  };
  const shared = {
    request: target.payload.request,
    state: target.payload.state ?? null,
    aiDetail: target.payload.aiDetail ?? null,
    meta,
    retry: null,
    catalog: buildNameSlice(catalog, stateForNames),
    cardText: cardSlice,
    gameLog,
    ctx: {
      summary: summary.text,
      stats: summary.stats,
      lastDecision: earlier.filter((t) => t.kind === "turn").at(-1)?.decision ?? null,
    },
  };

  console.log(`重放 #${target.requestId} ${target.rpcKind}（${path.basename(args.session)}）`);
  console.log(`  候选/局面：${describeRequest(target.payload.request, target.payload.aiDetail).split("\n")[0]}`);
  if (args.save) {
    fs.writeFileSync(args.save, JSON.stringify(shared, null, 2), "utf8");
    console.log(`  shared 已存：${args.save}`);
  }

  const url = `${args.vmaf.replace(/\/$/, "")}/run`;
  const started = Date.now();
  let json;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ blueprint, shared, strict: false, run_id: `replay-${target.requestId}` }),
      // 超时按 --budget 放大：思考型模型一手 2–4 分钟很常见，固定 120s 会把正常的一次跑判成"连不上"
      signal: AbortSignal.timeout(Math.max(120000, Number(args.budget || 0) + 30000)),
    });
    json = await res.json();
    if (!res.ok) {
      console.error(`/run HTTP ${res.status}: ${preview(json)}`);
      process.exit(1);
    }
  } catch (e) {
    console.error(`请求 VMAF 失败（${url}，等了 ${Math.round((Date.now() - started) / 1000)}s）：${e.message}`);
    process.exit(1);
  }
  const wall = Date.now() - started;

  if (args.dump) {
    fs.writeFileSync(args.dump, JSON.stringify(json, null, 2), "utf8");
    console.log(`  /run 原始结果已存：${args.dump}`);
  }

  console.log(`  图执行：${json.ok ? "ok" : "失败"}${json.aborted ? `（已中止：${json.abort_reason}）` : ""}  总耗时 ${json.total_ms}ms（往返 ${wall}ms）`);
  for (const r of json.results ?? []) {
    const mark = r.skipped ? "跳过" : r.ok ? "ok  " : "失败";
    console.log(`    ${mark} ${String(r.node_id).padEnd(8)} ${String(r.elapsed_ms).padStart(6)}ms  ${r.ok ? preview(r.output) : `✖ ${r.error?.message ?? ""}`}`);
  }
  for (const e of json.error_details ?? []) {
    if (e.level === "error") console.log(`    ! ${e.code}: ${e.message}`);
  }

  const decision = (json.results ?? []).find((r) => r.node_id === args.decisionNode);
  if (!decision) {
    console.error(`结果里没有决策节点 ${args.decisionNode}`);
    process.exit(1);
  }
  if (!decision.ok) {
    console.error(`决策节点失败：${decision.error?.message ?? "unknown"}`);
    process.exit(1);
  }

  const out = decision.output ?? {};
  const response = out.response ?? out.decision ?? null;
  const note = out.note ? `（${out.note}）` : "";
  console.log(`\n  决策：${JSON.stringify(describeDecision(target.payload.request, response))}${note}`);
  const problem = checkShape(target.payload.request, response);
  if (problem) {
    console.error(`  形状校验失败：${problem}`);
    process.exit(1);
  }
  if (!args.quiet) {
    const full = (json.results ?? []).find((r) => r.node_id === "pack");
    if (full?.output?.prompt) {
      console.log(`\n  ---- 提示词（${full.output.prompt.length} 字符）----`);
      if (args.show.includes("prompt")) console.log(full.output.prompt);
      else console.log(full.output.prompt.split("\n").slice(0, 12).join("\n") + "\n  …");
    }
  }
  if (!args.quiet) {
    const llm = (json.results ?? []).find((r) => r.node_id === "llm");
    const reason =
      llm?.output?.reasoning ??
      llm?.output?.raw?.choices?.[0]?.message?.reasoning_content ??
      "";
    if (reason) {
      console.log(`\n  ---- 思考过程（${reason.length} 字符）----`);
      if (args.show.includes("reasoning")) console.log(reason);
      else console.log(reason.slice(0, 400) + (reason.length > 400 ? "…" : ""));
    }
    console.log(`  finish_reason：${llm?.output?.finish_reason ?? "?"}`);
  }
  console.log(`\n  响应 JSON：${JSON.stringify(response)}`);
}

main().catch((e) => {
  console.error(e?.stack ?? String(e));
  process.exit(1);
});
