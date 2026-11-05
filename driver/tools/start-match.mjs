/**
 * 在“已经开着的浏览器”里重新开一局（不新起浏览器）。
 *
 * 用途：e2e-arena.mjs 会自己拉起浏览器并常驻；演示中途想换一局、改限时/回合上限时，
 * 用这个脚本直接驱动现有浏览器，省得再抢 9344 端口。
 *
 * 用法：
 *   node driver/tools/start-match.mjs --action-seconds 180 --init-seconds 180 --max-rounds 40 --open-popup
 */
import process from "node:process";
import { CDP } from "./cdp.mjs";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const args = {
  cdp: 9344,
  url: "http://127.0.0.1:7807",
  mode: "eve",
  actionSeconds: 180,
  initSeconds: 180,
  maxRounds: 40,
  openPopup: true,
};
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  const v = () => argv[++i];
  if (a === "--cdp") args.cdp = Number(v());
  else if (a === "--url") args.url = v();
  else if (a === "--mode") args.mode = v();
  else if (a === "--action-seconds") args.actionSeconds = Number(v());
  else if (a === "--init-seconds") args.initSeconds = Number(v());
  else if (a === "--max-rounds") args.maxRounds = Number(v());
  else if (a === "--no-popup") args.openPopup = false;
}

const version = await (await fetch(`http://127.0.0.1:${args.cdp}/json/version`)).json();
const conn = await new CDP(version.webSocketDebuggerUrl).connect();

const list = await (await fetch(`http://127.0.0.1:${args.cdp}/json/list`)).json();
const main = list.find((t) => t.type === "page" && t.url.includes("/#arena") && !t.url.includes("#arena-board"));
if (!main) {
  console.error("没有找到主页面（#arena）。先用 e2e-arena.mjs 拉起浏览器。");
  process.exit(1);
}
const targets = [];
conn.on("Target.targetCreated", (p) => targets.push(p.targetInfo));
await conn.send("Target.setDiscoverTargets", { discover: true });
await conn.attach(main.id);

console.log("[start] 刷新主页面……");
await conn.send("Page.navigate", { url: `${args.url}/#arena` });
await sleep(600);
await conn.send("Page.reload", { ignoreCache: true });
await conn.waitFor(`document.querySelector(".arena h1")?.textContent`, { timeoutMs: 30_000 });

const modeLabel = args.mode === "eve" ? "AI vs AI" : args.mode === "pvp" ? "真人 vs 真人" : "真人 vs AI";
await conn.evaluate(`(() => {
  const btn = [...document.querySelectorAll(".arena-mode")].find((b) => b.textContent.includes(${JSON.stringify(modeLabel)}));
  if (btn) btn.click();
  return "ok";
})()`);
console.log("[start] 模式:", modeLabel);

const timers = await conn.evaluate(`(() => {
  const inputs = [...document.querySelectorAll(".arena-grid3 input")];
  const set = (el, v) => {
    const proto = el instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(v));
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
  };
  const out = [];
  for (const [i, el] of inputs.entries()) {
    if (i === 0) { set(el, ${args.actionSeconds}); out.push("行动限时=" + el.value); }
    else if (i === 1) { set(el, ${args.initSeconds}); out.push("开局限时=" + el.value); }
  }
  const rounds = inputs[3];
  if (rounds && ${args.maxRounds} > 0) { set(rounds, ${args.maxRounds}); out.push("回合上限=" + rounds.value); }
  return out;
})()`);
console.log("[start] 计时:", JSON.stringify(timers));

if (args.openPopup) {
  const pop = await conn.evaluate(`(() => {
    const label = [...document.querySelectorAll("label")].find((l) => l.textContent.includes("自动打开对方棋盘"));
    if (!label) return "no-label";
    const box = (label.nextElementSibling && label.nextElementSibling.type === "checkbox")
      ? label.nextElementSibling
      : [...label.parentElement.querySelectorAll('input[type="checkbox"]')].pop();
    if (!box) return "no-box";
    if (!box.checked) box.click();
    const all = [...document.querySelectorAll('input[type="checkbox"]')];
    const instantOn = all.filter((b, i) => i < all.length - 1 && b.checked).length;
    return "checked=" + box.checked + " 其它勾选数=" + instantOn;
  })()`);
  console.log("[start] 自动打开对方棋盘:", pop);
}

await sleep(300);
const clicked = await conn.evaluate(`(() => {
  const btn = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "开始对局");
  if (!btn) return "no-button";
  btn.click();
  return "ok";
})()`);
console.log("[start] 点击开始对局:", clicked);

await sleep(4000);
const boards = targets.filter((t) => (t.url ?? "").includes("#arena-board"));
console.log("[start] 棋盘窗口数:", boards.length);
console.log("[start] 页面保持打开，可以直接看。");
conn.close();
