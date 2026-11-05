import puppeteer from "../node_modules/.pnpm/puppeteer@19.0.0_supports-color@8.1.1/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js";
import path from "node:path";
import fs from "node:fs";

const url = process.argv[2] ?? "http://127.0.0.1:7717/";
const outDir = path.join(import.meta.dirname, "_screenshots");
fs.mkdirSync(outDir, { recursive: true });

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-popup-blocking"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1000 });
const logs = [];
page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));
await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 });
await page.evaluate(() => localStorage.removeItem("gi-tcg-local-presets-v1"));
await page.reload({ waitUntil: "networkidle2" });

async function clickText(text, selector = "button") {
  const ok = await page.evaluate(
    (text, selector) => {
      const el = [...document.querySelectorAll(selector)].find((b) => b.textContent.trim() === text);
      if (!el) return false;
      el.click();
      return true;
    },
    text,
    selector,
  );
  if (!ok) throw new Error(`找不到按钮: ${text}`);
  await new Promise((r) => setTimeout(r, 300));
}

console.log("home has workshop:", await page.evaluate(() => document.body.innerText.includes("数值工坊")));
await page.screenshot({ path: path.join(outDir, "home2.png") });

// 进入工坊
await page.evaluate(() => {
  const buttons = [...document.querySelectorAll("button")];
  const btn = buttons.filter((b) => b.textContent.trim() === "数值工坊").pop();
  btn?.click();
});
await new Promise((r) => setTimeout(r, 800));
await page.screenshot({ path: path.join(outDir, "workshop.png") });
console.log("workshop title:", await page.evaluate(() => document.querySelector(".ws h1")?.textContent));
console.log("rows:", await page.evaluate(() => document.querySelectorAll(".ws-table tbody tr").length));

// 全选所有角色 -> 生命 100
await clickText("全选筛选结果");
await page.evaluate(() => {
  const input = document.querySelector(".ws-batch input");
  input.value = "100";
  input.dispatchEvent(new InputEvent("input", { bubbles: true }));
});
await clickText("设为生命");
console.log("after batch toast:", await page.evaluate(() => document.querySelector(".ws-toast")?.textContent));

// 所有武器 +100
await clickText("示例：所有武器 +100");
// 保存
await clickText("保存预设");
await new Promise((r) => setTimeout(r, 400));
const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("gi-tcg-local-presets-v1")));
console.log("active:", stored.activeId, "presets:", stored.presets.map((p) => `${p.name}:${Object.keys(p.characters ?? {}).length}chars/${Object.keys(p.cards ?? {}).length}cards`));
await page.screenshot({ path: path.join(outDir, "workshop-edited.png") });

// 开始对局
const popupPromise = new Promise((resolve) => browser.once("targetcreated", resolve));
await clickText("用此预设开始对局");
const target = await Promise.race([popupPromise, new Promise((r) => setTimeout(() => r(null), 10000))]);
let popup = null;
if (target) {
  popup = await target.page();
  if (popup) {
    popup.on("console", (m) => logs.push(`[popup:${m.type()}] ${m.text()}`));
    popup.on("pageerror", (e) => logs.push(`[popup:pageerror] ${e.message}`));
    await popup.setViewport({ width: 1000, height: 820 });
  }
} else {
  logs.push("[warn] 未检测到弹窗");
}
await new Promise((r) => setTimeout(r, 6000));
const gameInfo = await page.evaluate(() => {
  const game = window.theGame;
  if (!game) return null;
  return {
    phase: game.state.phase,
    p0: game.state.players[0].characters.map((c) => ({
      id: c.definition.id,
      hp: c.variables.health,
      maxHp: c.variables.maxHealth,
      defHp: c.definition.varConfigs.maxHealth.initialValue,
    })),
    p1: game.state.players[1].characters.map((c) => ({
      id: c.definition.id,
      maxHp: c.variables.maxHealth,
      defHp: c.definition.varConfigs.maxHealth.initialValue,
    })),
  };
});
console.log("gameInfo:", JSON.stringify(gameInfo));
await page.screenshot({ path: path.join(outDir, "workshop-game.png") });
if (popup) await popup.screenshot({ path: path.join(outDir, "workshop-game-popup.png") });

console.log("--- console ---");
console.log(logs.slice(0, 40).join("\n"));
await browser.close();
