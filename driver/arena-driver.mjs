#!/usr/bin/env node
/**
 * 七圣召唤 AI 座位的外置司机（driver）。
 *
 * 它做的事（只做这些，别让它变聪明）：
 *   1. 用 SSE 连上桥接服务的某个座位，收 request；
 *   2. 每回合把请求交给"策略"拿到一份 RpcResponse；
 *   3. 本地校验形状 → POST /agent/respond（422 带反馈重试，404 说明已过期）；
 *   4. 把每回合事实写进 JSONL 会话文件（复盘/回放）。
 *
 * 四种模式：
 *   probe  —— 同时听 0/1 两个座位，报告"请求其实落在哪个座位"（seat 配错的自检）
 *   record —— 只听不答，把真实对局录下来（零风险，用来攒提示词素材）
 *   echo   —— 本地冒烟策略（能打就打，否则结束回合），验证协议用
 *   vmaf   —— 把每回合请求发给 VMAF 的 /run，由蓝图决策
 *
 * 用法示例：
 *   node driver/arena-driver.mjs --mode probe
 *   node driver/arena-driver.mjs --mode record --seat 1
 *   node driver/arena-driver.mjs --mode echo --seat 1
 *   node driver/arena-driver.mjs --mode vmaf --seat 1 --blueprint ./blueprints/decide.json
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import util from "node:util";
import { fileURLToPath } from "node:url";
import { ArenaBridge } from "./lib/bridge.mjs";
import { decodeDeckCode } from "./lib/deckcode.mjs";
import { buildNameSlice, compactCatalog } from "./lib/names.mjs";
import { buildCardTextSlice, collectCardTextIds, compactCardText } from "./lib/cardtext.mjs";
import { applyLlmOverrides, envLlmOverrides, injectSecrets } from "./lib/secrets.mjs";
import { createLive } from "./lib/live.mjs";
import { SessionStore, summarizeTurns, estTokens } from "./lib/store.mjs";
import {
  checkShape,
  describeDecision,
  describeRequest,
  echoResponse,
  fallbackResponse,
  feedbackOf,
} from "./lib/policy.mjs";

const ROOT = path.dirname(fileURLToPath(import.meta.url));

/**
 * 解析输入文件路径：先按当前工作目录找，找不到再按 driver/ 目录找。
 * 这样 `--blueprint blueprints/decide.json` 不论从哪个目录启动都能命中。
 */
function resolveInput(raw) {
  const asCwd = path.resolve(raw);
  if (fs.existsSync(asCwd)) return asCwd;
  const asRoot = path.resolve(ROOT, raw);
  if (fs.existsSync(asRoot)) return asRoot;
  return asCwd;
}

/** 输出文件路径：绝对路径原样，相对路径按 driver/ 目录落盘（避免写到启动目录里）。 */
function resolveOut(raw) {
  return path.isAbsolute(raw) ? raw : path.resolve(ROOT, raw);
}

// ---------------------------------------------------------------------------
// 参数
// ---------------------------------------------------------------------------
function parseArgs(argv) {
  const out = {
    seat: 1,
    mode: "echo",
    bridge: "http://127.0.0.1:7717",
    match: "local",
    session: null,
    sessionDir: path.join(ROOT, "sessions"),
    safetyMs: 3000,
    minBudgetMs: 1500,
    maxAttempts: 3,
    payload: true,
    vmaf: "http://127.0.0.1:8000",
    blueprint: path.join(ROOT, "blueprints", "decide.json"),
    decisionNode: null,
    blueprintId: null,
    probeMs: 8000,
    quiet: false,
    showMenu: false,
    exitAfterEnd: 0,
    logFile: null,
    deckCode: null,
    deckJson: null,
    planText: null,
    planFile: null,
    llmModel: null,
    llmApiBase: null,
    llmApiKeyEnv: null,
    llmMaxTokens: null,
    llmTimeoutSeconds: null,
    prepBlueprint: path.join(ROOT, "blueprints", "prep.json"),
    noPrep: false,
    reviewBlueprint: path.join(ROOT, "blueprints", "review.json"),
    noReview: false,
  };
  const need = (i) => {
    const v = argv[i];
    if (v === undefined) throw new Error(`参数 ${argv[i - 1]} 缺少值`);
    return v;
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--seat": out.seat = Number(need(++i)); break;
      case "--mode": out.mode = need(++i); break;
      case "--bridge": out.bridge = need(++i); break;
      case "--match": out.match = need(++i); break;
      case "--session": out.session = need(++i); break;
      case "--session-dir": out.sessionDir = path.resolve(need(++i)); break;
      case "--safety-ms": out.safetyMs = Number(need(++i)); break;
      case "--min-budget-ms": out.minBudgetMs = Number(need(++i)); break;
      case "--max-attempts": out.maxAttempts = Number(need(++i)); break;
      case "--no-payload": out.payload = false; break;
      case "--vmaf": out.vmaf = need(++i); break;
      case "--blueprint": out.blueprint = resolveInput(need(++i)); break;
      case "--blueprint-id": out.blueprintId = need(++i); break;
      case "--decision-node": out.decisionNode = need(++i); break;
      case "--probe-ms": out.probeMs = Number(need(++i)); break;
      case "--quiet": out.quiet = true; break;
      case "--show-menu": out.showMenu = true; break;
      case "--log-file": out.logFile = resolveOut(need(++i)); break;
      case "--exit-on-end": out.exitAfterEnd = Number(need(++i)); break;
      case "--deck-code": out.deckCode = need(++i); break;
      case "--deck": out.deckJson = resolveInput(need(++i)); break;
      case "--plan": out.planText = need(++i); break;
      case "--plan-file": out.planFile = resolveInput(need(++i)); break;
      case "--llm-model": out.llmModel = need(++i); break;
      case "--llm-api-base": out.llmApiBase = need(++i); break;
      case "--llm-api-key-env": out.llmApiKeyEnv = need(++i); break;
      case "--llm-max-tokens": out.llmMaxTokens = Number(need(++i)); break;
      case "--llm-timeout-seconds": out.llmTimeoutSeconds = Number(need(++i)); break;
      case "--prep-blueprint": out.prepBlueprint = resolveInput(need(++i)); break;
      case "--no-prep": out.noPrep = true; break;
      case "--review-blueprint": out.reviewBlueprint = resolveInput(need(++i)); break;
      case "--no-review": out.noReview = true; break;
      case "--review-blueprint-id": out.reviewBlueprintId = need(++i); break;
      case "-h":
      case "--help": out.help = true; break;
      default:
        if (a.startsWith("--")) throw new Error(`未知参数：${a}`);
    }
  }
  if (![0, 1].includes(out.seat)) throw new Error("--seat 只能是 0 或 1");
  if (!["probe", "record", "echo", "vmaf"].includes(out.mode)) {
    throw new Error("--mode 只能是 probe / record / echo / vmaf");
  }
  return out;
}

const HELP = `七圣召唤 AI 座位司机

  --seat 0|1              座位号（默认 1；不确定先跑 --mode probe）
  --mode probe|record|echo|vmaf
  --bridge <url>          桥接服务（默认 http://127.0.0.1:7717）
  --session-dir <dir>     会话目录（默认 driver/sessions）
  --session <name>        会话名（默认 s<seat>-<时间>）
  --safety-ms <n>         距 deadline 提前多少毫秒停手（默认 3000）
  --max-attempts <n>      422 后最多试几次（默认 3）
  --vmaf <url>            VMAF 后端（默认 http://127.0.0.1:8000）
  --blueprint <path>      决策蓝图 JSON（本地文件）
  --blueprint-id <id>     从 VMAF 服务端取蓝图（网页里编辑并保存的那份，优先于 --blueprint）
  --decision-node <id>    取哪个节点的输出当决策（默认自动取蓝图汇点）
  --probe-ms <n>          probe 模式听多久（默认 8000）
  --show-menu             每次行动把候选菜单打到日志（调试用，很吵）
  --log-file <path>       同时把日志追加写到文件（可 tail -f 看实时决策）
  --exit-on-end <sec>     对局结束后再等几秒就退出（默认 0 = 不退）
  --deck-code <code>      牌组分享码：解码后设置到座位，并据此写赛前战术
  --deck <path>           牌组 JSON（{characters:[id], cards:[id]} 或分享码字符串）
  --plan <text>           教练给的初始战术（和牌组资料一起交给模型整理成赛前计划）
  --plan-file <path>      同上，从文件读（战术长的时候用）
  --llm-model <name>      覆盖蓝图里的模型（两个座位可以用不同厂商）
  --llm-api-base <url>    覆盖 api_base，配 --llm-model 一起用
  --llm-api-key-env <VAR> 用这个环境变量里的密钥（driver/.env 里写 VAR=... 即可）
  --llm-max-tokens <n>    覆盖 max_tokens（16384 约 60s/手；32768 更慢但思考更久）
  --llm-timeout-seconds <n> 覆盖单次 LLM 超时（默认跟蓝图，上限 600）

这些 --llm-* 也可以写进 driver/.env，作为默认值（命令行优先）：
  SEAT0_LLM_MODEL=gpt-4o            # 带前缀 = 只对这个座位生效
  SEAT0_LLM_API_BASE=https://api.openai.com/v1
  SEAT0_LLM_API_KEY_ENV=OPENAI_API_KEY   # 变量的名字，不是密钥本身
  SEAT1_LLM_MODEL=deepseek-flash
  SEAT1_LLM_API_BASE=https://api.deepseek.com/v1
  SEAT1_LLM_API_KEY_ENV=DEEPSEEK_API_KEY
  LLM_MODEL=deepseek-flash          # 不带前缀 = 两个座位共用的默认值
  OPENAI_API_KEY=sk-...             # 密钥本身照旧写在这里
  --prep-blueprint <path> 备赛蓝图（默认 driver/blueprints/prep.json）
  --no-prep               不跑备赛（老行为）
  --no-payload            会话里不存 payload（省空间；代价=这一局没法 replay）
`;

// ---------------------------------------------------------------------------
// 日志
// ---------------------------------------------------------------------------
const t0 = Date.now();
function stamp() {
  const d = new Date();
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}
let quiet = false;
function log(...args) {
  if (!quiet) console.log(`[${stamp()}]`, ...args);
}
function warn(...args) {
  console.warn(`[${stamp()}] ⚠`, ...args);
}
function fail(...args) {
  console.error(`[${stamp()}] ✖`, ...args);
}

/**
 * 把控制台输出同时追加到文件，便于 `Get-Content -Wait` / `tail -f` 看实时决策。
 * 只影响 console 的四个方法，不改变原有屏幕输出。
 */
function teeLogs(file) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const stream = fs.createWriteStream(file, { flags: "a" });
  const render = (v) => (typeof v === "string" ? v : util.inspect(v, { depth: 3, breakLength: 120 }));
  for (const level of ["log", "info", "warn", "error"]) {
    const original = console[level].bind(console);
    console[level] = (...items) => {
      original(...items);
      try {
        stream.write(items.map(render).join(" ") + "\n");
      } catch {
        /* 写日志失败不该影响对局 */
      }
    };
  }
  return stream;
}

function sessionName(seat) {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `s${seat}-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// ---------------------------------------------------------------------------
// 自检：seat 到底对不对
// ---------------------------------------------------------------------------
/**
 * 上一版常见的坑：连了 0 但 AI 在 1 —— 于是永远收不到请求，
 * 对局每回合都靠页面超时用默认动作，看起来像"AI 不动"。
 * 这里在空闲时用 /status 交叉验证：别的座位有 pending 而本座位没有，就是连错了。
 */
async function seatSelfCheck({ bridge, seat, quietMs = 12_000, receivedAny }) {
  const started = Date.now();
  let warnedAgents = false;
  while (Date.now() - started < quietMs) {
    await sleep(2000);
    if (receivedAny()) return { ok: true, hard: false };
    let st = null;
    try {
      st = (await bridge.status()).json;
    } catch {
      continue;
    }
    if (!st) continue;
    const mine = st.agents?.[seat] ?? 0;
    const other = st.agents?.[seat === 0 ? 1 : 0] ?? 0;
    const pendingMine = (st.pendingRequests ?? []).filter((p) => p.seat === seat).length;
    const pendingOther = (st.pendingRequests ?? []).filter((p) => p.seat !== seat).length;
    if (mine < 1 && !warnedAgents) {
      warnedAgents = true;
      warn(`服务端没记录到本连接（agents[${seat}]=0），SSE 可能没连上或已被替换`);
    }
    if (pendingOther > 0 && pendingMine === 0) {
      fail(
        `座位疑似配错：座位 ${seat === 0 ? 1 : 0} 上有一个待决策请求，而座位 ${seat} 没有任何请求。` +
          `请用 --mode probe 确认座位，或改成 --seat ${seat === 0 ? 1 : 0}`,
      );
      return { ok: false, hard: true };
    }
    if (other > 0 && mine === 0) {
      fail(`座位 ${seat === 0 ? 1 : 0} 上已有 agent 连着，本座位（${seat}）没有——大概率连错了`);
      return { ok: false, hard: true };
    }
  }
  if (!receivedAny()) {
    warn(
      `等了 ${Math.round(quietMs / 1000)} 秒没收到请求。可能原因：` +
        `(a) 座位配错（用 --mode probe 确认）；(b) 对局还没开始/牌组没配好；` +
        `(c) 该座位被勾了"耗时间"；` +
        `(d) 页面的 AI 座位不是 ${seat}（PvE 人类在 0 时 AI 在 1；PvP 两个座位都是人）`,
    );
    return { ok: false, hard: false };
  }
  return { ok: true, hard: false };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));


/** 决策节点：显式指定优先；否则取唯一汇点（没有出边的节点）。 */
function resolveDecisionNode(blueprint, hint) {
  const nodes = blueprint?.nodes ?? [];
  if (hint && nodes.some((n) => n.id === hint)) return hint;
  const sources = new Set((blueprint?.edges ?? []).map((e) => e.source));
  const sinks = nodes.filter((n) => !sources.has(n.id)).map((n) => n.id);
  if (sinks.length === 1) return sinks[0];
  return null;
}

// ---------------------------------------------------------------------------
// VMAF 决策
// ---------------------------------------------------------------------------
/**
 * 从 VMAF 服务端读一份已保存的蓝图，返回规范化后的 JSON 文本。
 *
 * 用途：让"在网页画布上改提示词"这条路径真正生效——用户保存后，
 * 司机下一回合重新拉取，无需重启进程、也不需要手改本地文件。
 *
 * @param {string} vmaf VMAF 后端地址。
 * @param {string} id 蓝图 id。
 * @returns {Promise<string>} 蓝图 JSON 文本。
 */
async function fetchRemoteBlueprint(vmaf, id) {
  const url = `${vmaf.replace(/\/+$/, "")}/blueprints/${encodeURIComponent(id)}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`GET ${url} → ${res.status}`);
  const payload = await res.json();
  const doc = payload?.blueprint ?? payload;
  if (!doc || !Array.isArray(doc.nodes)) throw new Error(`蓝图 ${id} 结构异常`);
  return JSON.stringify(doc);
}

function hasLlmOverrides(args) {
  return Boolean(
    args.llmModel || args.llmApiBase || args.llmApiKeyEnv ||
      args.llmMaxTokens != null || args.llmTimeoutSeconds != null,
  );
}

function describeLlmOverrides(args) {
  const parts = [
    `模型 ${args.llmModel ?? "（蓝图原值）"}`,
    `api_base ${args.llmApiBase ?? "（蓝图原值）"}`,
    `密钥 ${args.llmApiKeyEnv ? `环境变量 ${args.llmApiKeyEnv}` : "（按 api_base 自动）"}`,
    `max_tokens ${args.llmMaxTokens ?? "（蓝图原值）"}`,
    `超时 ${args.llmTimeoutSeconds != null ? `${args.llmTimeoutSeconds}s` : "（蓝图原值）"}`,
  ];
  return `LLM 覆盖（本座位）：${parts.join(" / ")}`;
}

/** 把牌组 id 翻成名字，给日志用（catalog 某一类既可能是 {id: name}，也可能是 {id: {name}}）。 */
function describeDeck(deck, catalog) {
  const nameOf = (category, id) => {
    const items = (catalog ?? {})[category];
    const item = items && (Array.isArray(items)
      ? items.find((x) => Number(x?.id) === Number(id))
      : items[String(id)]);
    if (typeof item === "string") return item;
    if (item && typeof item.name === "string") return item.name;
    return `#${id}`;
  };
  const chars = (deck.characters ?? []).map((id) => nameOf("characters", id)).join("、");
  const cards = (deck.cards ?? []).map((id) => nameOf("cards", id));
  return `${deck.characters?.length ?? 0} 角色（${chars}）／${cards.length} 张行动牌：${cards.join("、")}`;
}

/** 备赛结果：开局前算一次，之后每回合都带进图里（图里用 in.plan 接）。 */
const prep = {
  plan: "",
  deck: null,
  at: 0,
  /** 往期每回合末的胜率与意图：[{round, winrate, intent}] */
  winrates: [],
  /** 上一回合末留下的意图（≤200 字），会随每手提示词带上 */
  intent: "",
  /** 页面推过来的对局配置（限时 / 耗时间 / 让过） */
  config: null,
  /** 紧凑名字表（id → 中文名），复盘时也要用 */
  catalog: null,
  lastReviewRound: -1,
  reviewing: false,
};

/**
 * 从 --deck-code / --deck 解析出牌组。
 *
 * Returns:
 *   {characters: number[], cards: number[], name?: string} | null
 */
async function resolveDeck(args) {
  if (args.deckCode) return decodeDeckCode(args.deckCode.trim());
  if (args.deckJson) {
    const text = (await fs.promises.readFile(args.deckJson, "utf8")).trim();
    if (text.startsWith("{")) {
      const obj = JSON.parse(text);
      return {
        name: obj.name,
        characters: (obj.characters ?? []).map((c) => (typeof c === "object" ? c.id : c)),
        cards: (obj.cards ?? []).map((c) => (typeof c === "object" ? c : c)),
      };
    }
    return decodeDeckCode(text);
  }
  return null;
}

function makeVmafDecider(args, store, live) {
  let blueprintCache = { text: null, doc: null, at: 0 };
  let catalogCache = null;
  let cardTextCache = null;
  let logCursor = 0;
  let decisionNode = null;
  return async function decideWithVmaf({ request, state, aiDetail, meta, retry }) {
    const fs = await import("node:fs/promises");
    // 回合切换 = 上一回合结束：后台跑一次复盘（意图 + 胜率），不阻塞这一手
    const roundNow = Number(meta?.round ?? 0);
    if (!args.noReview && roundNow > 0 && roundNow > prep.lastReviewRound) {
      // 复盘对象永远是"刚结束的那一回合"：roundNow-1（司机中途重启也不会出现 R-1）
      void runReview({ args, state, round: Math.max(0, roundNow - 1), store, live });
    }
    if (roundNow > 0) prep.lastReviewRound = roundNow;
    // 蓝图每回合重新取一次：网页上改完（保存到服务端或改文件），下一回合就生效
    let blueprint = blueprintCache.doc;
    try {
      const text = args.blueprintId
        ? await fetchRemoteBlueprint(args.vmaf, args.blueprintId)
        : await fs.readFile(args.blueprint, "utf8");
      if (text !== blueprintCache.text) {
        blueprintCache = { text, doc: JSON.parse(text), at: Date.now() };
        const from = args.blueprintId ? `VMAF 服务端 ${args.blueprintId}` : args.blueprint;
        log(`蓝图已载入：${from}（${blueprintCache.doc.nodes?.length ?? 0} 个节点）`);
      }
      blueprint = blueprintCache.doc;
    } catch (e) {
      warn(`读蓝图失败，沿用上一份：${e.message}`);
    }
    if (!blueprint) throw new Error(`蓝图不可用：${args.blueprintId ?? args.blueprint}`);
    // 密钥只从环境变量来：蓝图里留空就按 api_base 猜变量名补上
    // fetchRemoteBlueprint 返回的是"文本"（决策链路要用它做指纹/缓存），
    // 这里直接 POST 给 /run 必须是对象，否则 422 "Input should be a valid dictionary"。
    if (typeof blueprint === "string") blueprint = JSON.parse(blueprint);
    const sealed = injectSecrets(blueprint, process.env);
    if (sealed.injected.length && !blueprintCache.warnedSecret) {
      blueprintCache.warnedSecret = true;
      log(`已从环境变量补 api_key：${sealed.injected.join("、")}`);
    }
    if (sealed.missing.length && !blueprintCache.warnedMissing) {
      blueprintCache.warnedMissing = true;
      warn(`这些 LLM 节点没有 api_key，会是 dry_run：${sealed.missing.join("、")}（设 DEEPSEEK_API_KEY 或 VMAF_LLM_API_KEY）`);
    }
    blueprint = applyLlmOverrides(sealed.blueprint, args);
    if (hasLlmOverrides(args) && !blueprintCache.warnedLlm) {
      blueprintCache.warnedLlm = true;
      log(describeLlmOverrides(args));
    }
    if (!decisionNode) {
      const picked = resolveDecisionNode(blueprint, args.decisionNode);
      if (!picked) {
        throw new Error(`无法确定决策节点：--decision-node 没给，蓝图里有多个汇点（${(blueprint.nodes ?? []).map((n) => n.id).join("、")}）`);
      }
      decisionNode = picked;
      log(`决策节点：${decisionNode}（蓝图汇点）`);
    }

    const rows = store ? store.rows() : [];
    const summary = summarizeTurns(rows, { maxTurns: 30 });
    // 只把本回合局面里出现的名字推进图里（整份名字表有几百 KB，没必要每回合都传）
    const names = buildNameSlice(catalogCache, state);
    if (!catalogCache) {
      // catalog 是 id → 中文名 的静态表（一局内不会变），拉一次就够
      try {
        const bridge = new ArenaBridge({ base: args.bridge, matchId: args.match });
        const got = await bridge.catalog();
        const raw = got?.json ?? null;
        const counts = raw?.counts ?? {};
        catalogCache = compactCatalog(raw);
        prep.catalog = catalogCache;
        const total = Object.values(catalogCache).reduce((n, v) => n + (v && typeof v === "object" ? Object.keys(v).length : 0), 0);
        log(`已载入名字表：${raw?.version ?? "?"}（角色 ${counts.characters ?? "?"} / 卡牌 ${counts.cards ?? "?"}，共 ${total} 个名字）`);
      } catch (e) {
        warn(`拉 catalog 失败（局面里的名字会退化成 id）：${e.message}`);
        catalogCache = {};
      }
    }
    // 卡牌效果文本：和名字表同样按"这一回合出现过的 id"裁一刀（整份 197KB，没必要都传）
    if (!cardTextCache) {
      try {
        const bridge = new ArenaBridge({ base: args.bridge, matchId: args.match });
        const got = await bridge.cardText();
        cardTextCache = compactCardText(got?.json ?? null);
        const count = Object.keys(cardTextCache.descriptions).length;
        log(`已载入卡牌效果文本：${cardTextCache.version ?? "?"}（${count} 条）`);
      } catch (e) {
        warn(`拉 card-text 失败（卡片效果不会进提示词）：${e.message}`);
        cardTextCache = { version: null, descriptions: {} };
      }
    }
    const cardText = buildCardTextSlice(cardTextCache, collectCardTextIds(state, aiDetail));
    // 对局日志：只取"上一手之后新增的决策"，免得每回合把 500 条历史都搬一遍
    let gameLog = [];
    try {
      const bridge = new ArenaBridge({ base: args.bridge, matchId: args.match });
      const got = await bridge.gameLog();
      const logs = Array.isArray(got?.json?.logs) ? got.json.logs : [];
      const maxTime = logs.reduce(
        (max, e) => (typeof e?.time === "number" && e.time > max ? e.time : max),
        0,
      );
      // 桥接重启或换局后 time 会从头开始：游标比现有日志还新就归零，否则整局都看不到回顾
      if (maxTime && maxTime < logCursor) logCursor = 0;
      const fresh = logs.filter((e) => e && typeof e.time === "number" && e.time > logCursor);
      // decision 是双方的"出招摘要"；info 是引擎逐条事件（对手的牌/技能只能从这里看到）。
      // 自己那侧的 info 会和 decision 重复，所以只留对方的。
      gameLog = fresh
        .filter((e) => e && (e.kind === "decision" || (e.kind === "info" && e.seat !== args.seat)))
        .slice(-20);
      logCursor = Math.max(logCursor, maxTime);
    } catch (e) {
      warn(`拉对局日志失败（"最近发生的事"会为空）：${e.message}`);
    }
    const shared = {
      request,
      state,
      aiDetail,
      meta,
      retry: retry ?? null,
      catalog: names,
      cardText,
      gameLog,
      plan: prep.plan,
      deck: prep.deck ?? {},
      winrates: prep.winrates.slice(-40),
      intent: prep.intent,
      config: prep.config ?? {},
      ctx: {
        summary: summary.text,
        stats: summary.stats,
        lastDecision: rows.filter((r) => r.kind === "turn").at(-1)?.decision ?? null,
      },
    };
    const timeoutMs = Math.max(1000, (meta.deadline ?? 0) - Date.now() - args.safetyMs);
    // 非流式调用出结果前没有任何输出，先吱一声，免得看起来像卡死
    log(`  ⏳ 模型出招中（思考型，通常 20–60s，本次限时 ${Math.round(timeoutMs / 1000)}s）`);
    const res = await fetch(`${args.vmaf.replace(/\/$/, "")}/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        blueprint,
        shared,
        strict: false,
        run_id: `${store?.session ?? "s"}-${meta.requestId}`,
      }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const json = await res.json().catch(() => null);
    if (!json) throw new Error(`/run 返回非 JSON（HTTP ${res.status}）`);
    if (json.aborted) throw new Error(`蓝图被中止：${json.abort_reason}`);
    const result = (json.results ?? []).find((r) => r.node_id === decisionNode);
    if (!result) {
      const ids = (json.results ?? []).map((r) => r.node_id).join("、");
      throw new Error(`结果里没有节点 ${decisionNode}（实际跑过：${ids}）`);
    }
    if (!result.ok) throw new Error(`节点 ${decisionNode} 失败：${result.error?.message ?? "unknown"}`);
    const out = result.output ?? {};
    const response = out.response ?? out.decision ?? (out.$case ? out : null);
    const feedback = out.feedback ?? null;
    // 提示词长度取 pack 节点真实产出的 prompt（共享包本身有几万字，不是提示词）
    const results = json.results ?? [];
    const byType = (t) => results.find((r) => r.node_type === t)?.output ?? null;
    const packOut = byType("prompt_pack");
    const prompt = packOut?.prompt ?? "";
    const promptChars = prompt.length || JSON.stringify(shared).length;
    const llmOut = byType("llm_chat");
    const parsed = (results.find((r) => r.node_id === "parse")?.output) ?? null;
    const candidates = aiDetail?.actions?.length ?? aiDetail?.candidates?.length ?? null;
    // 思考内容：优先用 llm_chat 节点直接给的 reasoning（= 服务端 reasoning_content），
    // 老后端没有这个输出字段时退回 raw，两种版本都能跑。
    const reasoning = llmOut?.reasoning || llmOut?.raw?.choices?.[0]?.message?.reasoning_content || "";
    return {
      response,
      feedback,
      stats: {
        promptChars,
        prompt,
        reply: llmOut?.reply ?? "",
        reasoning,
        reasoningTokens: llmOut?.reasoning_tokens ?? 0,
        finishReason: llmOut?.finish_reason ?? null,
        parsed,
        candidates,
        timings: results.map((r) => ({ id: r.node_id, type: r.node_type, ms: r.elapsed_ms, ok: r.ok })),
        runMs: json.total_ms ?? null,
        plan: prep.plan,
      },
    };
  };
}

// ---------------------------------------------------------------------------
// 备赛：牌组 → 牌组速览 → 战术 → 清洗（只跑一次，结果注入每一手）
// ---------------------------------------------------------------------------
/**
 * 开局前跑一次备赛蓝图，把得到的"赛前计划"存进 prep.plan。
 *
 * 顺序上刻意放在连 SSE 之前：此时对局通常还没开始，先设牌组 + 写战术，
 * 等页面开局后第一手就已经带着计划。
 */
async function runPrep({ args, bridge, store, seat }) {
  // 教练给的初始战术：--plan 直接给文本，--plan-file 从文件读（战术长的时候用）
  let tactic = args.planText ? String(args.planText).trim() : "";
  if (!tactic && args.planFile) {
    try {
      tactic = String(await fs.promises.readFile(resolveInput(args.planFile), "utf8")).trim();
    } catch (e) {
      warn(`读 --plan-file 失败：${e.message}`);
    }
  }
  let deck = null;
  try {
    deck = await resolveDeck(args);
  } catch (e) {
    warn(`读牌组失败：${e.message}`);
  }
  // 牌组必须在"有战术就提前返回"之前设进桥接：否则 --plan 一给，页面那边就永远没牌组
  if (deck) {
    prep.deck = deck;
    try {
      const got = await bridge.setDeck(seat, {
        name: deck.name ?? `座位 ${seat} 的牌组`,
        characters: deck.characters,
        cards: deck.cards,
      });
      if (got.status !== 200) {
        warn(`设置牌组失败（HTTP ${got.status}）：${JSON.stringify(got.json).slice(0, 200)}`);
      } else {
        log(`牌组已设到座位 ${seat}：${deck.characters.length} 个角色 / ${deck.cards.length} 张行动牌`);
      }
    } catch (e) {
      warn(`设置牌组失败：${e.message}`);
    }
  } else {
    log("没有给 --deck-code / --deck：跳过设牌组（页面自己选了牌组时不受影响）");
  }

  // 牌组速览要读生命/充能/技能，必须用完整 catalog（不能是裁剪过的名字表）
  let catalog = {};
  try {
    const got = await bridge.catalog();
    catalog = got?.json ?? {};
  } catch (e) {
    warn(`拉 catalog 失败（牌组速览会退化）：${e.message}`);
  }

  if (deck) log(`牌组解析：${describeDeck(deck, catalog)}`);
  if (tactic) log(`初始战术（${tactic.length} 字）：\n${tactic}`);

  const useTacticPlan = (why) => {
    if (!tactic) return false;
    prep.plan = tactic;
    prep.at = Date.now();
    store.note("prep", { source: "cli", plan: tactic, why });
    log(`赛前计划直接用初始战术（${why}，${tactic.length} 字）`);
    return true;
  };

  if (!deck && !tactic) {
    log("既没有牌组也没有初始战术，跳过备赛（这一局不带赛前战术）");
    return;
  }

  let blueprint;
  try {
    blueprint = JSON.parse(await fs.promises.readFile(args.prepBlueprint, "utf8"));
  } catch (e) {
    warn(`读备赛蓝图失败（跳过备赛）：${e.message}`);
    useTacticPlan("备赛蓝图读不到");
    return;
  }
  const sealed = injectSecrets(blueprint, process.env);
  const prepared = applyLlmOverrides(sealed.blueprint, args);
  if (hasLlmOverrides(args)) log(describeLlmOverrides(args));
  const node = resolveDecisionNode(prepared, null) ?? "guard";
  const t = Date.now();
  const res = await fetch(`${args.vmaf.replace(/\/$/, "")}/run`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      blueprint: prepared,
      shared: { deck: deck ?? {}, catalog, plan: tactic },
      strict: false,
      run_id: "prep",
    }),
    signal: AbortSignal.timeout(180_000),
  }).catch((e) => {
    warn(`备赛请求失败：${e.message}`);
    return null;
  });
  if (!res) {
    useTacticPlan("备赛请求失败");
    return;
  }
  const json = await res.json().catch(() => null);
  if (!json) {
    warn(`备赛返回非 JSON（HTTP ${res.status}）`);
    useTacticPlan(`HTTP ${res.status}`);
    return;
  }
  if (json.aborted) {
    warn(`备赛被中止：${json.abort_reason}`);
    useTacticPlan("备赛被中止");
    return;
  }
  const out = (json.results ?? []).find((r) => r.node_id === node)?.output ?? {};
  const plan = String(out.plan ?? out.text ?? "").trim() || tactic;
  if (!plan) {
    const errs = (json.error_details ?? []).map((e) => e.message).join("；");
    warn(`备赛跑完但没有计划文本${errs ? `（${errs}）` : ""}`);
    return;
  }
  prep.plan = plan;
  prep.at = Date.now();
  store.note("prep", {
    source: plan === tactic ? "cli" : node,
    plan,
    tacticChars: tactic.length,
    chars: plan.length,
    elapsedMs: Date.now() - t,
  });
  log(`赛前计划（${plan.length} 字，用时 ${Date.now() - t}ms）：\n${plan}`);
}
// ---------------------------------------------------------------------------
// 回合复盘：意图（≤200 字）+ 胜率（0~1），供下一手参照
// ---------------------------------------------------------------------------
/**
 * 回合结束时跑一次复盘图：让模型写下这回合的意图，并给一个胜率估计。
 *
 * 刻意做成"后台跑、不阻塞决策"：复盘慢一点没关系，它是给下一手当参照用的；
 * 失败也只记一条警告，绝不打断对局。
 *
 * Args:
 *   args: 命令行参数（用 args.reviewBlueprint / args.vmaf）。
 *   state: 当前局面（新回合刚开始的局面，近似"上回合末"）。
 *   round: 被复盘的那个回合号。
 *   store: SessionStore，用来把复盘结果落进会话文件。
 *   live: 观测台句柄（可选），复盘也会显示在观测台上。
 */
async function runReview({ args, state, round, store, live }) {
  if (prep.reviewing) return;
  prep.reviewing = true;
  const started = Date.now();
  try {
    let blueprint;
    try {
      blueprint = args.reviewBlueprintId
        ? await fetchRemoteBlueprint(args.vmaf, args.reviewBlueprintId)
        : JSON.parse(await fs.promises.readFile(resolveInput(args.reviewBlueprint || "blueprints/review.json"), "utf8"));
    } catch (e) {
      warn(`读复盘蓝图失败（跳过本回合复盘）：${e.message}`);
      return;
    }
    // fetchRemoteBlueprint 返回文本（决策链路拿它做指纹/缓存），POST /run 必须是对象
    if (typeof blueprint === "string") blueprint = JSON.parse(blueprint);
    const sealed = injectSecrets(blueprint, process.env);
    const prepared = applyLlmOverrides(sealed.blueprint, args);
    const node = resolveDecisionNode(prepared, null) ?? "parse";
    const res = await fetch(`${args.vmaf.replace(/\/$/, "")}/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        blueprint: prepared,
        shared: {
          state,
          plan: prep.plan,
          deck: prep.deck ?? {},
          catalog: prep.catalog ? buildNameSlice(prep.catalog, state) : {},
          winrates: prep.winrates.slice(-40),
          meta: { round, seat: args.seat, kind: "review" },
        },
        strict: false,
        run_id: `review-r${round}`,
      }),
      signal: AbortSignal.timeout(180_000),
    });
    const json = await res.json().catch(() => null);
    if (!json) {
      warn(`复盘返回非 JSON（HTTP ${res.status}）`);
      return;
    }
    if (json.aborted) {
      warn(`复盘被中止：${json.abort_reason}`);
      return;
    }
    if (!(json.results ?? []).length) {
      warn(`复盘 /run 没返回 results（HTTP ${res.status}）：${JSON.stringify(json).slice(0, 300)}`);
      return;
    }
    const out = (json.results ?? []).find((r) => r.node_id === node)?.output ?? {};
    const intent = String(out.intent ?? "").trim();
    const winrate = typeof out.winrate === "number" ? out.winrate : null;
    if (!intent && winrate === null) {
      const nodes = (json.results ?? []).map((r) => `${r.node_id}:${r.ok ? "ok" : r.skipped ? "skip" : "FAIL"}`).join(" ");
      const reply = (json.results ?? []).find((r) => r.node_type === "llm_chat")?.output?.reply ?? "";
      const note = (json.results ?? []).find((r) => r.node_type === "review_parse")?.output?.note ?? "";
      warn(`复盘跑完但没有意图也没有胜率：${nodes}｜回复 ${String(reply).length} 字 ${JSON.stringify(String(reply).slice(0, 120))}｜note=${note}`);
      return;
    }
    if (intent) prep.intent = intent;
    if (winrate !== null) prep.winrates.push({ round, winrate, intent });
    const elapsed = Date.now() - started;
    const pct = winrate === null ? "?" : Math.round(winrate * 100);
    log(`复盘 R${round}：胜率 ${pct}%（${elapsed}ms）｜${intent.slice(0, 60)}${intent.length > 60 ? "…" : ""}`);
    store?.note("review", { round, intent, winrate, elapsedMs: elapsed });
    live?.note({
      kind: "review",
      round,
      intent,
      winrate,
      elapsedMs: elapsed,
    });
  } catch (e) {
    warn(`复盘失败（不影响对局）：${e.message}`);
  } finally {
    prep.reviewing = false;
  }
}

// ---------------------------------------------------------------------------
// probe 模式
// ---------------------------------------------------------------------------
async function runProbe(args) {
  const bridge = new ArenaBridge({ base: args.bridge, matchId: args.match });
  const hits = new Map([
    [0, []],
    [1, []],
  ]);
  const conns = [0, 1].map((seat) =>
    bridge.connect({
      seat,
      onEvent: (event, data) => {
        if (event === "hello") {
          log(`seat ${seat} 连上（aiSeq=${data.aiSeq}，pending=${data.pending?.length ?? 0}）`);
        }
        if (event === "request") {
          hits.get(seat).push(data);
          log(`★ seat ${seat} 收到请求 id=${data.id} rpc=${data.request?.request?.$case}`);
        }
      },
      onError: (e) => warn(`seat ${seat} SSE: ${e.message}`),
      reconnect: true,
    }),
  );
  const st = (await bridge.status().catch(() => ({ json: null }))).json;
  if (st) {
    log(
      `当前状态：agents=${JSON.stringify(st.agents)} pending=${JSON.stringify(st.pending)} ` +
        `decks=${JSON.stringify(st.decks)} startRequested=${st.startRequested}`,
    );
  }
  log(`听 ${args.probeMs}ms……（两个座位都听，谁收到请求谁就是 AI 座位）`);
  await sleep(args.probeMs);
  conns.forEach((c) => c.close());
  bridge.close();

  const n0 = hits.get(0).length;
  const n1 = hits.get(1).length;
  console.log("");
  console.log(`结果：seat 0 收到 ${n0} 个请求，seat 1 收到 ${n1} 个请求`);
  if (n1 > n0) return { seat: 1 };
  if (n0 > n1) return { seat: 0 };
  if (n0 === 0 && n1 === 0) {
    warn("两个座位都没收到请求：先确认对局真的开始了（页面在 #arena → 选模式 → 开始对局）");
    return { seat: null };
  }
  warn("两个座位都在收请求：这是 AI vs AI（EvE），需要为每个座位各起一个司机");
  return { seat: 0, both: true };
}

// ---------------------------------------------------------------------------
// 主循环
// ---------------------------------------------------------------------------
async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(HELP);
    return 0;
  }
  // .env 里可以按座位写 LLM 配置（SEAT0_LLM_MODEL=…），命令行参数优先
  const envKeys = [];
  for (const [field, value] of Object.entries(envLlmOverrides(args.seat))) {
    if (args[field] == null || args[field] === "") {
      args[field] = value;
      envKeys.push(field);
    }
  }
  quiet = args.quiet;
  if (args.logFile) teeLogs(args.logFile);
  if (args.mode === "probe") {
    const r = await runProbe(args);
    return r.seat === null ? 2 : 0;
  }

  const bridge = new ArenaBridge({ base: args.bridge, matchId: args.match });
  const session = args.session ?? sessionName(args.seat);
  const store = new SessionStore({
    dir: args.sessionDir,
    session,
    seat: args.seat,
    payload: args.payload,
  });
  // store.rows() 需要能读到已写的行：加一个轻量缓存
  const rowCache = [];
  const origAppend = store.append.bind(store);
  store.append = (row) => {
    const rec = origAppend(row);
    if (rec) rowCache.push(rec);
    return rec;
  };
  store.rows = () => rowCache;

  store.header({ mode: args.mode, bridge: args.bridge, match: args.match, pid: process.pid });
  log(`会话：${store.path}`);
  log(`模式：${args.mode}　座位：${args.seat}　桥接：${args.bridge}`);
  if (hasLlmOverrides(args)) {
    const src = envKeys.length ? "（部分来自 driver/.env）" : "";
    log(`${describeLlmOverrides(args)}${src}`);
  }

  if (args.mode === "vmaf" && !args.noPrep) {
    try {
      await runPrep({ args, bridge, store, seat: args.seat });
    } catch (e) {
      warn(`备赛整体失败（继续用无计划的决策）：${e.message}`);
    }
  }

  // 对局配置：页面会把限时 / 耗时间 / 让过 推给桥接，司机据此提示（页面设置优先，agent 不能改）
  try {
    const st = await bridge.status();
    const cfg = st?.json?.config ?? null;
    if (cfg && typeof cfg === "object" && Object.keys(cfg).length) {
      prep.config = cfg;
      const stall = Boolean(cfg.stall?.[args.seat]);
      const instant = Boolean(cfg.instant?.[args.seat]);
      log(
        `对局配置：行动限时 ${cfg.actionSeconds ?? "?"}s / 开局限时 ${cfg.initSeconds ?? "?"}s / ` +
          `回合上限 ${cfg.maxRounds ?? "?"} / 整局 ${cfg.matchMinutes ? `${cfg.matchMinutes} 分钟` : "不限时"}` +
          ` / 本座位${stall ? "开了『耗时间』" : "未开耗时间"}${instant ? "、开了『让过』" : ""}`,
      );
      if (instant) warn("本座位开了『让过』：页面不会把请求发给 agent，这个司机会一直收不到请求（先在页面上关掉）");
      if (stall) warn("本座位开了『耗时间』：页面会睡满限时后用默认动作，agent 同样收不到请求（先关掉）");
    } else {
      log("对局配置：桥接还没收到页面推的配置（通常是页面还没开局）");
    }
  } catch (e) {
    warn(`读对局配置失败：${e.message}`);
  }

  const stats = {
    requests: 0,
    ok: 0,
    rejected: 0,
    late: 0,
    fallback: 0,
    vmafErrors: 0,
    latencySum: 0,
    latencyN: 0,
  };
  const seen = new Set();
  const submitted = new Set();
  const byId = new Map();
  let receivedAny = 0;
  let inflight = null;
  const queue = [];
  let stopping = false;
  let latestAiSeq = 0;
  let gameEnded = false;
  let endTimer = null;
  let sawRequest = false;

  /** 记下"对局结束"，并在需要时安排退出。 */
  function markEnded(why) {
    if (gameEnded || endTimer) return;
    gameEnded = true;
    log(`对局结束：${why}`);
    store.outcome({ why, aiSeq: latestAiSeq });
    if (args.exitAfterEnd > 0) {
      endTimer = setTimeout(() => {
        endTimer = null;
        shutdown("对局结束").catch(() => {});
      }, args.exitAfterEnd * 1000);
    }
  }

  /** 新一局开始时撤销"结束→退出"的安排（页面重开、reset 都要走这里）。 */
  function cancelEnded() {
    if (endTimer) {
      clearTimeout(endTimer);
      endTimer = null;
    }
    gameEnded = false;
  }

  const live = args.mode === "vmaf" ? createLive({ dir: args.sessionDir, session, seat: args.seat }) : null;
  const decideVmaf = args.mode === "vmaf" ? makeVmafDecider(args, store, live) : null;

  function pickDecision({ request, aiDetail, attempt, feedback }) {
    if (args.mode === "vmaf") return null; // 走异步分支
    if (args.mode === "echo") {
      // 422 之后按 availableActions 收敛，避免反复选同一个非法项
      if (feedback?.availableIndexes?.length) {
        const idx = feedback.availableIndexes[0];
        return { response: { response: { $case: "action", value: { chosenActionIndex: idx } } } };
      }
      return { response: echoResponse(request, aiDetail) };
    }
    return { response: fallbackResponse(request) };
  }

  async function handleRequest(ev) {
    const id = ev.id;
    if (id == null) return;
    if (seen.has(id)) {
      log(`跳过重复请求 id=${id}（SSE 重连重放）`);
      return;
    }
    // 还在对局中：说明之前判定的"结束"要么是上一局的残局，要么页面又开新局了
    sawRequest = true;
    cancelEnded();
    seen.add(id);
    receivedAny++;
    stats.requests++;

    const request = ev.request;
    // aiDetail / state 也留在内存里：这一手的 payload 要用它们（没有 payload 就没法 replay）
    byId.set(id, {
      request,
      aiDetail: ev.aiDetail ?? null,
      state: ev.state ?? null,
      deadline: ev.deadline ?? null,
    });
    const aiDetail = ev.aiDetail ?? null;
    const rpcKind = request?.request?.$case ?? "?";
    const deadline = ev.deadline ?? Date.now();
    const round = aiDetail?.round ?? aiDetail?.currentRound ?? null;
    const startedAt = Date.now();
    let budget = deadline - startedAt - args.safetyMs;

    log(
      `↳ #${stats.requests} id=${id} ${rpcKind} 回合=${round ?? "?"} ` +
        `预算=${Math.max(0, Math.round(budget / 1000))}s 候选=${aiDetail?.actions?.length ?? aiDetail?.candidates?.length ?? "-"}`,
    );
    if (!quiet && args.showMenu && rpcKind === "action") {
      for (const line of describeRequest(request, aiDetail).split("\n").slice(0, 12)) log(`    ${line}`);
    }

    if (args.mode === "record") {
      store.turn({
        requestId: id,
        rpcKind,
        round,
        deadline,
        latencyMs: 0,
        ok: null,
        attempts: 0,
        decision: null,
        payload: args.payload ? { request, aiDetail, state: ev.state ?? null } : null,
      });
      log(`    已录制（不提交），等待页面超时用默认动作`);
      return;
    }

    if (budget < args.minBudgetMs) {
      stats.fallback++;
      const fb = fallbackResponse(request);
      if (fb) {
        await submit(id, fb, { attempts: 0, startedAt, rpcKind, round, note: "预算不足，直接兜底" });
      }
      return;
    }

    let feedback = null;
    let attempts = 0;
    let response = null;
    let lastError = null;
    let promptStats = null;

    while (attempts < args.maxAttempts) {
      attempts++;
      budget = deadline - Date.now() - args.safetyMs;
      if (budget < args.minBudgetMs) {
        lastError = `预算耗尽（剩 ${Math.max(0, Math.round(budget))}ms）`;
        break;
      }
      try {
        if (args.mode === "vmaf") {
          const res = await decideVmaf({
            request,
            state: ev.state ?? null,
            aiDetail,
            meta: {
              matchId: args.match,
              seat: args.seat,
              requestId: id,
              rpcKind,
              round,
              deadline,
              remainingMs: Math.max(0, budget),
              attempt: attempts - 1,
            },
            retry: feedback,
          });
          response = res.response;
          feedback = res.feedback ?? feedback;
          promptStats = res.stats ?? null;
        } else {
          const picked = pickDecision({ request, aiDetail, attempt: attempts - 1, feedback });
          response = picked.response;
        }
      } catch (e) {
        lastError = e?.message ?? String(e);
        if (args.mode === "vmaf") stats.vmafErrors++;
        warn(`决策失败（第 ${attempts} 次）：${lastError}`);
        // 蓝图根本取不到（路径写错 / id 不存在 / 服务端没起来）属于配置错误：
        // 继续兜底只会打出一局"每手都默认过"的假对局，必须立刻硬失败。
        if (/蓝图不可用/.test(lastError) && stats.ok === 0 && stats.fallback === 0) {
          fail(`蓝图加载失败，停止本局（避免打出一局全是兜底的假对局）：${lastError}`);
          await shutdown("蓝图不可用", 4).catch(() => {});
          return;
        }
        response = null;
      }

      if (!response) {
        // 决策侧失败：兜底，别让对局卡住
        stats.fallback++;
        response = fallbackResponse(request);
        if (!response) return;
        await submit(id, response, { attempts, startedAt, rpcKind, round, note: `兜底（${lastError ?? "无决策"}）` });
        return;
      }

      const shapeError = checkShape(request, response);
      if (shapeError) {
        lastError = `本地形状校验失败：${shapeError}`;
        warn(lastError);
        feedback = { reason: shapeError, availableIndexes: null };
        continue;
      }

      const res = await submit(id, response, {
        attempts,
        startedAt,
        rpcKind,
        round,
        note: null,
        record: false,
        quietOk: true,
        promptStats,
      });
      if (res.status === 200) return;
      if (res.status === 404) {
        return; // submit 内部已记 late
      }
      if (res.status === 422) {
        stats.rejected++;
        const fb = feedbackOf(res.json);
        feedback = fb;
        lastError = fb.reason;
        store.note(`422：${fb.reason}`, { requestId: id, attempt: attempts });
        warn(`422 被拒（第 ${attempts} 次）：${fb.reason}`);
        continue;
      }
      lastError = `respond HTTP ${res.status}: ${JSON.stringify(res.json).slice(0, 200)}`;
      warn(lastError);
      break;
    }

    if (lastError) {
      // 所有尝试失败：最后一次兜底，避免超时被默认动作（默认动作通常是结束回合）
      stats.fallback++;
      const fb = fallbackResponse(request);
      const res = await submit(id, fb, { attempts, startedAt, rpcKind, round, note: `重试用尽：${lastError}` });
      if (res.status !== 200 && res.status !== 404) warn(`兜底提交失败：HTTP ${res.status}`);
    }
  }

  /**
   * 把这一手写进观测文件（viewer 边跑边看提示词与思考过程）。
   * 观测失败不影响对局：createLive 内部吞掉所有异常。
   */
  function recordLive({ id, latencyMs, meta, decision, ok, status = null }) {
    if (!live) return;
    const ps = meta.promptStats ?? {};
    const note = meta.note ?? null;
    live.turn({
      requestId: id,
      rpc: meta.rpcKind ?? null,
      round: meta.round ?? null,
      latencyMs,
      prompt: ps.prompt ?? "",
      promptChars: ps.promptChars ?? null,
      reply: ps.reply ?? "",
      reasoning: ps.reasoning ?? "",
      reasoningChars: (ps.reasoning ?? "").length,
      reasoningTokens: ps.reasoningTokens ?? 0,
      finishReason: ps.finishReason ?? null,
      parsed: ps.parsed ?? null,
      decision: decision ?? null,
      decisionLabel: decision
        ? `${decision.kind ?? ""}${decision.index != null ? ` #${decision.index}` : ""}`.trim()
        : "",
      candidates: ps.candidates ?? null,
      timings: ps.timings ?? [],
      plan: ps.plan ?? prep.plan,
      ok,
      status,
      fallback: Boolean(note && String(note).startsWith("兜底")),
      rejected: status === 422,
      note,
    });
  }

  async function submit(id, response, meta) {
    const t = Date.now();
    const rec = byId.get(id) ?? {};
    let res;
    try {
      submitted.add(id);
      res = await bridge.respond(id, response, {
        timeoutMs: Math.max(2000, (meta.deadline ?? 0) > 0 ? meta.deadline - t : 15000),
      });
    } catch (e) {
      warn(`respond 网络错误：${e.message}`);
      return { status: -1, json: null };
    }
    const latencyMs = Date.now() - (meta.startedAt ?? t);
    if (res.status === 200) {
      stats.ok++;
      stats.latencySum += latencyMs;
      stats.latencyN++;
      const decision = describeDecision(rec.request ?? {}, response);
      store.turn({
        requestId: id,
        rpcKind: meta.rpcKind,
        round: meta.round,
        deadline: meta.deadline ?? rec.deadline ?? null,
        latencyMs,
        ok: true,
        attempts: meta.attempts ?? 1,
        decision,
        note: meta.note ?? null,
        promptChars: meta.promptStats?.promptChars ?? null,
        estTokens: meta.promptStats?.promptChars ? estTokens(meta.promptStats.promptChars) : null,
        // 思考过程全量落盘：复盘时最想看的就是它（live.jsonl 里也有，但那份按体积滚动）
        reasoning: meta.promptStats?.reasoning ?? null,
        reasoningChars: meta.promptStats?.reasoning ? meta.promptStats.reasoning.length : null,
        reasoningTokens: meta.promptStats?.reasoningTokens ?? null,
        finishReason: meta.promptStats?.finishReason ?? null,
        // payload 默认记：没有它 replay.mjs 就复现不了这一手（--no-payload 可关，省空间）
        payload:
          args.payload && rec.request
            ? { request: rec.request, aiDetail: rec.aiDetail ?? null, state: rec.state ?? null }
            : null,
      });
      recordLive({ id, latencyMs, meta, decision, ok: true });
      const think = meta.promptStats?.reasoning ?? "";
      if (think) {
        const head = think.split("\n").map((x) => x.trim()).filter(Boolean)[0] ?? "";
        const tokens = meta.promptStats?.reasoningTokens
          ? `${meta.promptStats.reasoningTokens} token`
          : `约 ${Math.round(think.length / 1.6)} token`;
        log(`🧠 思考 ${think.length} 字（${tokens}）：${head.slice(0, 70)}${head.length > 70 ? "…" : ""}`);
      }
      if (!meta.quietOk) log(`✔ 提交成功 id=${id} 用时 ${latencyMs}ms`);
      else log(`✔ id=${id} 合法 用时 ${latencyMs}ms（第 ${meta.attempts} 次尝试）`);
      return res;
    }
    if (res.status === 404) {
      stats.late++;
      const age = Date.now() - t;
      let pend = null;
      try {
        const st = (await bridge.status()).json;
        pend = {
          mine: (st?.pendingRequests ?? []).filter((p) => p.seat === args.seat).map((p) => p.id),
          other: (st?.pendingRequests ?? []).filter((p) => p.seat !== args.seat).map((p) => p.id),
          agents: st?.agents,
        };
      } catch {
        /* ignore */
      }
      store.turn({
        requestId: id,
        rpcKind: meta.rpcKind,
        round: meta.round,
        latencyMs,
        ok: false,
        attempts: meta.attempts ?? 1,
        decision: describeDecision(rec.request ?? {}, response),
        rejectReason: "404 已过期（页面已用默认动作）",
      });
      recordLive({
        id,
        latencyMs,
        meta,
        decision: describeDecision(rec.request ?? {}, response),
        ok: false,
        status: 404,
      });
      warn(
        `✖ 404：请求 ${id} 在提交前已被解答（${age}ms 后才提交）。原因通常是①本回合超时、页面已用默认动作；` +
          `②同一座位还有另一个 driver 抢答。服务端此刻 pending：本座位 ${JSON.stringify(pend?.mine)} ` +
          `对方 ${JSON.stringify(pend?.other)} agents=${JSON.stringify(pend?.agents)}`,
      );
      return res;    }
    return res;
  }

  const conn = bridge.connect({
    seat: args.seat,
    onEvent: (event, data) => {
      if (event === "hello") {
        log(
          `SSE 已连上座位 ${args.seat}：aiSeq=${data.aiSeq}，服务端 pending=${data.pending?.length ?? 0}，` +
            `agents=${JSON.stringify(data.status?.agents ?? null)}`,
        );
        const others = Number(data.status?.agents?.[args.seat] ?? 0);
        if (others >= 1) {
          warn(
            `⚠ 座位 ${args.seat} 上已经有 ${others} 个 agent 连着（可能是另一个 driver，也可能是自己重连后的旧连接）。` +
              `同一座位开两个 driver 会互相抢请求：输的一方收到 404，看起来像"超时"。务必只留一个。`,
          );
        }
        for (const p of data.pending ?? []) {
          log(`   hello 重放了一个未决请求 id=${p.id}`);
          queue.push({ id: p.id, request: p.request, deadline: p.deadline, aiDetail: null });
        }
        drain();
        return;
      }
      if (event === "state") {
        return;
      }
      if (event === "request") {
        queue.push(data);
        drain();
        return;
      }
      if (event === "responded") {
        const mine = submitted.has(data?.id);
        if (mine) submitted.delete(data?.id);
        log(`↩ 服务端广播：id=${data?.id} 已被应答（${mine ? "本司机" : "另一个 driver"}）`);
        return;
      }
      if (event === "timeout") {
        log(`⌛ 服务端广播：id=${data?.id} 已超时（页面将用默认动作）`);
        return;
      }
      if (event === "reset") {
        log("页面开了新一局：清空已见请求、胜率历史");
        prep.winrates = [];
        prep.intent = "";
        prep.lastReviewRound = -1;
        seen.clear();
        cancelEnded();
        // 页面 reset 会把桥接里的牌组清空；EvE 没有牌组就不会开局，所以这里补回去
        if (prep.deck) {
          void bridge
            .setDeck(args.seat, prep.deck)
            .then(() => log(`新一局：已重新设置牌组（${prep.deck.characters?.length ?? "?"} 角色 / ${prep.deck.cards?.length ?? "?"} 张）`))
            .catch((e) => warn(`新一局重新设置牌组失败：${e.message}`));
        }
        return;
      }
      if (event === "ai_event") {
        const ev2 = data?.event ?? data;
        if (ev2?.seq) latestAiSeq = ev2.seq;
        const type = ev2?.type;
        if (type === "winner") {
          markEnded(`winner=${JSON.stringify(ev2.winner ?? null)}`);
        }
        if (type === "action_rejected") {
          warn(`服务端拒绝：${ev2.reason ?? ""}`);
        }
        if (type === "round") log(`—— 第 ${ev2.round ?? "?"} 回合 ——`);
        return;
      }
    },
    onError: (e) => warn(`SSE 错误：${e.message}`),
    onClose: () => log("SSE 断开，准备重连"),
  });

  async function drain() {
    if (stopping) return;
    if (inflight) return;
    const next = queue.shift();
    if (!next) return;
    inflight = next;
    try {
      await handleRequest(next);
    } catch (e) {
      fail(`处理请求异常：${e?.stack ?? e}`);
    } finally {
      inflight = null;
      if (queue.length) drain();
    }
  }

  // 自检（后台跑，不阻塞主流程）
  const checkPromise = seatSelfCheck({
    bridge,
    seat: args.seat,
    quietMs: 12_000,
    receivedAny: () => receivedAny > 0,
  }).catch((e) => {
    warn(`自检异常：${e.message}`);
    return { ok: false, hard: false };
  });

  let lastStatsKey = "";
  let lastReport = 0;
  const ticker = setInterval(() => {
    const key = `${stats.requests}/${stats.ok}/${stats.rejected}/${stats.late}/${stats.fallback}`;
    const changed = key !== lastStatsKey;
    if (!changed && Date.now() - lastReport < 60_000) return;
    lastStatsKey = key;
    lastReport = Date.now();
    const avg = stats.latencyN ? Math.round(stats.latencySum / stats.latencyN) : 0;
    log(
      `统计：请求 ${stats.requests} 成功 ${stats.ok} 422 ${stats.rejected} 过期 ${stats.late} ` +
        `兜底 ${stats.fallback} VMAF错 ${stats.vmafErrors} 平均 ${avg}ms`,
    );
  }, 5000);

  // 对局结束检测：ai_event 的 winner 只在"下一次请求"时才被冲进日志，
  // 而结束时根本不会再有请求，所以只能主动查 state 的 phase（5 = gameEnd）。
  // 配置知情：页面开局时才把配置推给桥接（司机可能先于页面启动），
  // 所以这里持续补看一次——知道限时/回合上限，也知道本座位有没有被勾"让过/耗时间"。
  let configSeen = prep.config && Object.keys(prep.config).length ? prep.config : null;
  const configWatch = setInterval(async () => {
    if (stopping) return;
    try {
      const st = (await bridge.status()).json;
      const cfg = st?.config;
      if (!cfg || typeof cfg !== "object" || !Object.keys(cfg).length) return;
      const key = JSON.stringify(cfg);
      if (configSeen && JSON.stringify(configSeen) === key) return;
      configSeen = cfg;
      prep.config = cfg;
      const stall = Boolean(cfg.stall?.[args.seat]);
      const instant = Boolean(cfg.instant?.[args.seat]);
      log(
        `对局配置：行动限时 ${cfg.actionSeconds ?? "?"}s / 开局限时 ${cfg.initSeconds ?? "?"}s / ` +
          `回合上限 ${cfg.maxRounds ?? "?"} / 整局 ${cfg.matchMinutes ? `${cfg.matchMinutes} 分钟` : "不限时"}` +
          ` / 本座位${stall ? "开了『耗时间』" : "未开耗时间"}${instant ? "、开了『让过』" : ""}`,
      );
      if (instant) warn("本座位开了『让过』：页面不会把请求发给 agent（先关掉再重开）");
      if (stall) warn("本座位开了『耗时间』：页面会睡满限时后用默认动作（先关掉再重开）");
    } catch {
      /* 桥接还没起来时忽略 */
    }
  }, 5000);

  const watcher = setInterval(async () => {
    // 没参与过这一局就不认 phase=5：那可能是上一局留在桥接里的残局
    if (gameEnded || stopping || !sawRequest) return;
    try {
      const got = await bridge.get("/api/arena/state", { seat: args.seat }, { timeoutMs: 4000 });
      const st = got?.json?.state ?? got?.json ?? {};
      if (Number(st?.phase) !== 5) return;
      markEnded(`phase=gameEnd（第 ${st?.roundNumber ?? "?"} 回合）`);
    } catch {
      /* 页面还没开局时查不到 state，忽略 */
    }
  }, 3000);

  const shutdown = async (sig, exitCode = 0) => {
    if (stopping) return;
    stopping = true;
    log(`收到 ${sig}，收尾……`);
    clearInterval(ticker);
    clearInterval(configWatch);
    clearInterval(watcher);
    conn.close();
    bridge.close();
    store.note(`司机退出（${sig}）`, { stats });
    store.close();
    await checkPromise.catch(() => {});
    const avg = stats.latencyN ? Math.round(stats.latencySum / stats.latencyN) : 0;
    log(
      `总结：请求 ${stats.requests}，成功 ${stats.ok}，422 ${stats.rejected}，过期 ${stats.late}，` +
        `兜底 ${stats.fallback}，平均 ${avg}ms　会话：${store.path}`,
    );
    process.exit(exitCode);
  };
  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));

  // 常驻：等到用户 Ctrl+C，或者对局结束 + 空闲一段时间
  const check = await checkPromise;
  if (check?.hard) {
    await shutdown("座位自检失败", 3);
    return 0;
  }
  log("司机就绪，等待请求（Ctrl+C 退出）");
  await new Promise(() => {});
}

main().catch((e) => {
  fail(e?.stack ?? e);
  process.exit(1);
});
