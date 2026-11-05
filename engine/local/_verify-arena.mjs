import puppeteer from "../node_modules/.pnpm/puppeteer@19.0.0_supports-color@8.1.1/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js";
import path from "node:path";
import fs from "node:fs";

const url = process.argv[2] ?? "http://127.0.0.1:7717/#arena";
const outDir = path.join(import.meta.dirname, "_screenshots");
fs.mkdirSync(outDir, { recursive: true });

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-popup-blocking"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1500, height: 1000 });
const logs = [];
page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));
await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 });
await new Promise((r) => setTimeout(r, 800));
console.log("setup title:", await page.evaluate(() => document.querySelector(".arena h1")?.textContent));
await page.screenshot({ path: path.join(outDir, "arena-setup.png") });
const mode = process.argv[3] ?? "pve";
if (mode === "stall") {
  await page.evaluate(() => {
    const inputs = [...document.querySelectorAll(".arena-grid3 input")];
    for (const input of inputs.slice(0, 2)) {
      input.value = "5";
      input.dispatchEvent(new InputEvent("input", { bubbles: true }));
    }
    const label = [...document.querySelectorAll(".arena-row label")].find((l) => l.textContent.includes("座位 0 耗时间"));
    const cb = label?.parentElement?.querySelector("input[type=checkbox]") ?? label?.querySelector("input");
    if (cb) {
      cb.checked = true;
      cb.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  await new Promise((r) => setTimeout(r, 300));
}
const modeLabel = mode === "eve" ? "AI vs AI" : mode === "pvp" ? "真人 vs 真人" : null;
if (modeLabel) {
  await page.evaluate((label) => {
    const btn = [...document.querySelectorAll(".arena-mode")].find((b) => b.textContent.includes(label));
    btn?.click();
  }, modeLabel);
  await new Promise((r) => setTimeout(r, 300));
}

const popupPromise = new Promise((resolve) => browser.once("targetcreated", resolve));
await page.evaluate(() => {
  const btn = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "开始对局");
  btn?.click();
});
const target = await Promise.race([popupPromise, new Promise((r) => setTimeout(() => r(null), 15000))]);
let popup = null;
if (target) {
  popup = await target.page();
  if (popup) {
    popup.on("console", (m) => logs.push(`[popup:${m.type()}] ${m.text()}`));
    popup.on("pageerror", (e) => logs.push(`[popup:pageerror] ${e.message}`));
    await popup.setViewport({ width: 1200, height: 900 });
    await new Promise((r) => setTimeout(r, 2000));
    console.log("popup url:", popup.url());
    console.log("popup title:", await popup.evaluate(() => document.querySelector(".arena-popup-title")?.textContent));
  }
} else {
  logs.push("[warn] 没有检测到棋盘弹窗");
}

// 等 AI 走几步
await new Promise((r) => setTimeout(r, mode === "stall" ? 30000 : 15000));
const info = await page.evaluate(() => {
  const g = window.theGame;
  if (!g) return null;
  return {
    phase: g.state.phase,
    round: g.state.roundNumber,
    winner: g.state.winner,
    p0: g.state.players[0].characters.map((c) => [c.definition.id, c.variables.health]),
    p1: g.state.players[1].characters.map((c) => [c.definition.id, c.variables.health]),
  };
});
console.log("game:", JSON.stringify(info));
console.log("log lines:", await page.evaluate(() => document.querySelectorAll(".arena-log-line").length));
console.log("log tail:", await page.evaluate(() =>
  [...document.querySelectorAll(".arena-log-line")].slice(-8).map((e) => e.textContent).join(" | ")));
console.log("timer text:", await page.evaluate(() => document.querySelector(".arena-main .arena-timer")?.textContent ?? "(none)"));
console.log("status:", await page.evaluate(() => document.querySelector(".arena-agent-live")?.textContent ?? "(none)"));
await page.screenshot({ path: path.join(outDir, "arena-main.png") });
if (popup) await popup.screenshot({ path: path.join(outDir, "arena-popup.png") });

console.log("--- console ---");
console.log(logs.slice(0, 40).join("\n"));
await browser.close();
