/**
 * 自测用的对局启动器：用 CDP 开浏览器 → #arena → 选模式 → 设短计时 → 开始对局。
 *
 * 之所以要自动化：验证 driver 必须真的有一局在跑，而"点按钮开局"这件事
 * 只能由浏览器里的页面来做（Game 就跑在页面里）。
 *
 * 用法：node driver/tools/e2e-arena.mjs --url http://127.0.0.1:7807 --mode eve --seconds 40
 */
import process from "node:process";
import { launchBrowser, CDP } from "./cdp.mjs";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = { url: "http://127.0.0.1:7807", mode: "eve", seconds: 40, actionSeconds: 5, initSeconds: 8, maxRounds: 0, headful: false, keepOpen: false, openPopup: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const v = () => argv[++i];
    if (a === "--url") out.url = v();
    else if (a === "--mode") out.mode = v();
    else if (a === "--seconds") out.seconds = Number(v());
    else if (a === "--action-seconds") out.actionSeconds = Number(v());
    else if (a === "--init-seconds") out.initSeconds = Number(v());
    else if (a === "--max-rounds") out.maxRounds = Number(v());
    else if (a === "--open-popup") out.openPopup = true;
    else if (a === "--headful") out.headful = true;
    else if (a === "--keep-open") out.keepOpen = true;
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const browser = await launchBrowser({ port: 9344, headless: !args.headful, extraArgs: ["--window-size=1400,950"] });
const cdp = await new CDP(`ws://127.0.0.1:9344/devtools/browser/${browser.version.id ?? ""}`).connect().catch(() => null);

// 用 /json/version 里的 webSocketDebuggerUrl 更稳
const version = await (await fetch(`http://127.0.0.1:9344/json/version`)).json();
const conn = cdp ?? (await new CDP(version.webSocketDebuggerUrl).connect());

const targets = [];
conn.on("Target.targetCreated", (p) => targets.push(p.targetInfo));
conn.on("Target.targetInfoChanged", (p) => targets.push(p.targetInfo));
await conn.send("Target.setDiscoverTargets", { discover: true });

const { targetId } = await conn.send("Target.createTarget", { url: `${args.url}/#arena` });
await conn.attach(targetId);

const errors = [];
conn.on("Runtime.exceptionThrown", (p) => errors.push(p.exceptionDetails?.exception?.description ?? p.exceptionDetails?.text));
conn.on("Runtime.consoleAPICalled", (p) => {
  const text = (p.args ?? []).map((a) => a.value ?? a.description ?? "").join(" ");
  if (/error|warn/i.test(p.type) || /失败|错误/.test(text)) errors.push(`${p.type}: ${text}`);
});

console.log("[e2e] 打开配置页……");
await conn.waitFor(`document.querySelector(".arena h1")?.textContent`, { timeoutMs: 30_000 });
console.log("[e2e] 标题:", await conn.evaluate(`document.querySelector(".arena h1")?.textContent`));

// 1) 选模式
const modeLabel = args.mode === "eve" ? "AI vs AI" : args.mode === "pvp" ? "真人 vs 真人" : "真人 vs AI";
await conn.evaluate(`(() => {
  const btn = [...document.querySelectorAll(".arena-mode")].find((b) => b.textContent.includes(${JSON.stringify(modeLabel)}));
  if (!btn) return "no-button";
  btn.click();
  return "ok";
})()`);
console.log("[e2e] 模式:", modeLabel);

// 2) 把两个计时压短（AI 座位才不会把我们等太久）
const timerInfo = await conn.evaluate(`(() => {
  const inputs = [...document.querySelectorAll(".arena-grid3 input")];
  const set = (el, v) => {
    const proto = el instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(v));
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
  };
  const out = [];
  for (const [i, el] of inputs.entries()) {
    if (i < 2) { set(el, ${args.actionSeconds}); out.push(el.value); }
  }
  // 第 4 个数字输入是「回合上限」（第 3 个是整局上限）
  const rounds = inputs[3];
  if (rounds && ${args.maxRounds} > 0) {
    set(rounds, ${args.maxRounds});
    out.push("回合上限=" + rounds.value);
  }
  return out;
})()`);
console.log("[e2e] 计时输入:", JSON.stringify(timerInfo));

// 2.5) EvE 默认不弹对方棋盘；演示时希望两个都看，就手动勾上「开局自动打开对方棋盘」
if (args.openPopup) {
  const pop = await conn.evaluate(`(() => {
    const label = [...document.querySelectorAll("label")].find((l) => l.textContent.includes("自动打开对方棋盘"));
    if (!label) return "no-label";
    // 同一行里三个 checkbox 的顺序是「座位0 让过 / 座位1 让过 / 自动打开对方棋盘」，
    // 所以必须取该行里紧跟这个 label 的那个，不能取 querySelector 的第一个（那是让过）。
    const boxes = [...label.parentElement.querySelectorAll('input[type="checkbox"]')];
    const box = (label.nextElementSibling && label.nextElementSibling.type === "checkbox")
      ? label.nextElementSibling
      : boxes[boxes.length - 1];
    if (!box) return "no-box";
    // 顺手确认「让过」没被误勾（勾了页面就不发请求给 agent）
    const all = [...document.querySelectorAll('input[type="checkbox"]')];
    const instantOn = all.filter((b, i) => i < all.length - 1 && b.checked).length;
    if (!box.checked) box.click();
    return "checked=" + box.checked + " 其它勾选数=" + instantOn;
  })()`);
  console.log("[e2e] 自动打开对方棋盘:", pop);
}

// 3) 开始对局
await sleep(300);
const clicked = await conn.evaluate(`(() => {
  const btn = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "开始对局");
  if (!btn) return "no-button";
  btn.click();
  return "ok";
})()`);
console.log("[e2e] 点击开始:", clicked);

// 4) 等弹窗/棋盘出现
await sleep(2500);
const boardTarget = targets.find((t) => (t.url ?? "").includes("#arena-board"));
console.log("[e2e] 弹窗 target:", boardTarget?.url ?? "(无)");

// 5) 等对局推进
const started = Date.now();
let last = "";
while (Date.now() - started < args.seconds * 1000) {
  await sleep(3000);
  const info = await conn
    .evaluate(`(() => {
      const g = window.theGame;
      const log = [...document.querySelectorAll(".arena-log-line")].slice(-3).map((e) => e.textContent);
      if (!g) return { log };
      return {
        phase: g.state.phase,
        round: g.state.roundNumber,
        winner: g.state.winner ?? null,
        turn: g.state.currentTurn,
        hp: [0, 1].map((s) => (g.state.players?.[s]?.characters ?? []).map((c) => c.variables?.health)),
        log,
      };
    })()`)
    .catch((e) => ({ error: String(e) }));
  const line = JSON.stringify(info);
  if (line !== last) {
    console.log("[e2e]", line.slice(0, 400));
    last = line;
  }
  if (info?.winner || info?.phase === 5) {
    console.log("[e2e] 对局结束，winner =", info.winner ?? "(平局)");
    break;
  }
}

// 6) 直接读桥接状态（不依赖页面）
const base = args.url.replace(/#.*$/, "");
const status = await (await fetch(`${base}/api/arena/status`)).json().catch(() => null);
console.log("[e2e] bridge status:", JSON.stringify(status));
const aiLog = await (await fetch(`${base}/api/arena/ai/log?seat=1&since=0&limit=5`)).json().catch(() => null);
console.log("[e2e] ai log 末尾:", JSON.stringify(aiLog).slice(0, 300));

if (errors.length) {
  console.log("[e2e] 页面错误:");
  for (const e of errors.slice(0, 10)) console.log("   ", e.slice(0, 300));
}
if (args.keepOpen) {
  console.log("[e2e] --keep-open：窗口保持打开，观察完按 Ctrl+C 结束");
  // 必须留一个活着的 handle，否则 Node 会以 exit 13 直接退出
  await new Promise((resolve) => setInterval(resolve, 2 ** 31 - 1));
}

conn.close();
await browser.close();
console.log("[e2e] 完成");
