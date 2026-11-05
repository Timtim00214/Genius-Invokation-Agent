#!/usr/bin/env node
/**
 * 一键冷启动：游戏服务(7807) + VMAF 后端(8100) + 观测台(7810) + AI 司机
 *
 * 用法（在仓库根目录）：
 *   node 启动.mjs                     # 默认：真人坐先手(座位0)，AI 坐后手(座位1)
 *   node 启动.mjs --human 1           # 真人坐后手，AI 坐先手
 *   node 启动.mjs --open              # 顺带用默认浏览器打开对局页
 *   node 启动.mjs --no-driver         # 只起三个服务，自己另开司机
 *   node 启动.mjs --deck-code XXX --plan "先手铺雷，留一张匿叶龙"   # 给 AI 指定牌组/战术
 *
 * 进程都在前台、输出带 [server]/[vmaf]/[viewer]/[driver] 前缀，Ctrl+C 一起退出。
 */
import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const GAME_DIR = path.join(ROOT, "engine");
const VMAF_DIR = path.join(ROOT, "ai-backend", "vmaf_system", "backend");
const PY = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");

const BRIDGE_PORT = 7807;
const VMAF_PORT = 8100;
const VIEWER_PORT = 7810;
const REPLAY_PORT = 7812;
const BRIDGE = `http://127.0.0.1:${BRIDGE_PORT}`;
const VMAF = `http://127.0.0.1:${VMAF_PORT}`;

// decks.json 里的两套内置牌组（AI 用哪套取决于它坐哪边）
const BUILTIN_DECK = {
  0: "FdHxNj8TAWDQxFkMFkAhyWIYCYDA45wOCUDQ5J0PGEAh9IIPGWDh9p4YC6FgirYRCxAA",
  1: "FZDByRUNGRCB0WoNFlGgWpEPE0AB9TAPFGCB9kgWGIERCoEQDLFADcQQDPFgacYWDJAA",
};

function parseArgs(argv) {
  const out = {
    human: 0,
    deckCode: null,
    plan: null,
    planFile: null,
    blueprintId: "8b804ed0feb1",
    driver: true,
    viewer: true,
    replayViewer: true,
    open: false,
    llmModel: null,
    llmApiBase: null,
    llmApiKeyEnv: null,
    llmMaxTokens: null,
    llmTimeoutSeconds: null,
    session: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const need = () => {
      const v = argv[++i];
      if (v == null) {
        console.error(`参数 ${a} 需要一个值`);
        process.exit(2);
      }
      return v;
    };
    switch (a) {
      case "--human": out.human = Number(need()) === 1 ? 1 : 0; break;
      case "--deck-code": out.deckCode = need(); break;
      case "--plan": out.plan = need(); break;
      case "--plan-file": out.planFile = need(); break;
      case "--blueprint-id": out.blueprintId = need(); break;
      case "--session": out.session = need(); break;
      case "--llm-model": out.llmModel = need(); break;
      case "--llm-api-base": out.llmApiBase = need(); break;
      case "--llm-api-key-env": out.llmApiKeyEnv = need(); break;
      case "--llm-max-tokens": out.llmMaxTokens = need(); break;
      case "--llm-timeout-seconds": out.llmTimeoutSeconds = need(); break;
      case "--no-driver": out.driver = false; break;
      case "--no-viewer": out.viewer = false; break;
      case "--no-replay-viewer": out.replayViewer = false; break;
      case "--open": out.open = true; break;
      case "-h":
      case "--help": out.help = true; break;
      default:
        console.error(`不认识的参数：${a}（用 --help 看用法）`);
        process.exit(2);
    }
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log(
    [
      "用法：node 启动.mjs [选项]",
      "  --human 0|1            真人坐哪边（默认 0=先手）；AI 自动坐另一边",
      "  --deck-code <码>        AI 的牌组分享码（默认用内置牌组）",
      "  --plan <文本>           AI 的初始战术（也可以 --plan-file 读文件）",
      "  --blueprint-id <id>     决策蓝图 id（默认 8b804ed0feb1）",
      "  --open                  起好后用默认浏览器打开对局页",
      "  --no-driver             只起 7807/8100/7810，不启动 AI 司机",
      "  --no-viewer             不起观测台",
      "  --no-replay-viewer      不起对局回放台",
      "  --llm-model / --llm-api-base / --llm-api-key-env / --llm-max-tokens / --llm-timeout-seconds",
      "                          只给这个 AI 座位换模型/厂商（两个座位两套 API 时用）",
    ].join("\n"),
  );
  process.exit(0);
}

const children = [];
function start(name, cmd, cmdArgs, cwd) {
  const child = spawn(cmd, cmdArgs, { cwd, env: process.env, stdio: ["ignore", "pipe", "pipe"] });
  children.push({ name, child });
  const label = `[${name}]`.padEnd(9);
  const pipe = (stream, isErr) =>
    stream.on("data", (buf) => {
      for (const line of String(buf).split(/\r?\n/)) {
        if (line.trim()) (isErr ? console.error : console.log)(`${label} ${line}`);
      }
    });
  pipe(child.stdout, false);
  pipe(child.stderr, true);
  child.on("exit", (code) => {
    if (code !== 0 && code !== null) console.error(`${label} 退出，code=${code}`);
  });
  return child;
}

async function healthy(url, path0 = "/") {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    const res = await fetch(`${url}${path0}`, { signal: ctrl.signal });
    clearTimeout(timer);
    return res.ok || res.status < 500;
  } catch {
    return false;
  }
}

async function waitHealth(url, path0, who, timeoutMs = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await healthy(url, path0)) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  console.error(`等待 ${who} 就绪超时（${url}${path0}）`);
  return false;
}

function shutdown(code = 0) {
  for (const { child } of children) {
    try {
      child.kill();
    } catch {
      /* 已经退了 */
    }
  }
  process.exit(code);
}
process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

const AI_SEAT = args.human === 0 ? 1 : 0;

console.log("== 七圣召唤 AI 对战场 · 冷启动 ==");
console.log(`真人座位 ${args.human}，AI 座位 ${AI_SEAT}`);

// 1) 游戏服务
if (await healthy(BRIDGE, "/api/arena/status")) {
  console.log(`[server]  ${BRIDGE} 已在运行，复用`);
} else {
  console.log("[server]  启动 7807 …");
  start("server", "node", ["local/server.mjs", "--port", String(BRIDGE_PORT), "--no-open"], GAME_DIR);
  if (!(await waitHealth(BRIDGE, "/api/arena/status", "游戏服务"))) shutdown(1);
}

// 2) VMAF 后端
if (await healthy(VMAF, "/health")) {
  console.log(`[vmaf]    ${VMAF} 已在运行，复用`);
} else {
  console.log("[vmaf]    启动 8100 …");
  start("vmaf", PY, ["-m", "uvicorn", "main:app", "--port", String(VMAF_PORT), "--host", "127.0.0.1"], VMAF_DIR);
  if (!(await waitHealth(VMAF, "/health", "VMAF"))) shutdown(1);
}

// 3) 观测台
if (args.viewer) {
  if (await healthy(`http://127.0.0.1:${VIEWER_PORT}`, "/")) {
    console.log(`[viewer]  http://127.0.0.1:${VIEWER_PORT} 已在运行，复用`);
  } else {
    console.log("[viewer]  启动 7810 …");
    start(
      "viewer",
      "node",
      ["driver/tools/viewer.mjs", "--port", String(VIEWER_PORT), "--dir", "driver/sessions"],
      ROOT,
    );
  }
}

// 3.5) 对局回放台
if (args.replayViewer) {
  if (await healthy(`http://127.0.0.1:${REPLAY_PORT}`, "/")) {
    console.log(`[replay]  http://127.0.0.1:${REPLAY_PORT} 已在运行，复用`);
  } else {
    console.log("[replay]  启动 7812 …");
    start(
      "replay",
      "node",
      ["driver/tools/replay-viewer.mjs", "--port", String(REPLAY_PORT), "--dir", "driver/sessions"],
      ROOT,
    );
  }
}

// 4) AI 司机
if (args.driver) {
  const deck = args.deckCode || BUILTIN_DECK[AI_SEAT];
  const driverArgs = [
    "driver/arena-driver.mjs",
    "--mode", "vmaf",
    "--seat", String(AI_SEAT),
    "--bridge", BRIDGE,
    "--vmaf", VMAF,
    "--blueprint-id", args.blueprintId,
    "--session", args.session || `pve-s${AI_SEAT}`,
    "--deck-code", deck,
  ];
  if (args.plan) driverArgs.push("--plan", args.plan);
  if (args.planFile) driverArgs.push("--plan-file", args.planFile);
  if (args.llmModel) driverArgs.push("--llm-model", args.llmModel);
  if (args.llmApiBase) driverArgs.push("--llm-api-base", args.llmApiBase);
  if (args.llmApiKeyEnv) driverArgs.push("--llm-api-key-env", args.llmApiKeyEnv);
  if (args.llmMaxTokens) driverArgs.push("--llm-max-tokens", args.llmMaxTokens);
  if (args.llmTimeoutSeconds) driverArgs.push("--llm-timeout-seconds", args.llmTimeoutSeconds);
  console.log(`[driver]  AI 坐座位 ${AI_SEAT}，蓝图 ${args.blueprintId} …`);
  start("driver", "node", driverArgs, ROOT);
}

setTimeout(() => {
  console.log("");
  console.log("---------------------------------------------------------------");
  console.log(`对局页：   ${BRIDGE}   （第一次打开按 Ctrl+Shift+R 硬刷新）`);
  if (args.viewer) console.log(`观测台：   http://127.0.0.1:${VIEWER_PORT}`);
  if (args.replayViewer) console.log(`对局回放： http://127.0.0.1:${REPLAY_PORT}   （打完一局去这里慢慢看）`);
  console.log(`页面上：   对局模式选 PvE，真人坐 ${args.human === 0 ? "先手（座位 0）" : "后手（座位 1）"}`);
  console.log("           先手/后手牌组码填你自己的；AI 那侧的牌组它会自己设好");
  console.log("           点「开始对局」即可。Ctrl+C 停掉这里所有进程。");
  console.log("---------------------------------------------------------------");
  if (args.open) {
    const url = BRIDGE;
    const cmd =
      process.platform === "win32" ? ["cmd", ["/c", "start", "", url]]
      : process.platform === "darwin" ? ["open", [url]]
      : ["xdg-open", [url]];
    spawn(cmd[0], cmd[1], { stdio: "ignore", detached: true }).unref();
  }
}, 1200);

process.stdin.resume();
