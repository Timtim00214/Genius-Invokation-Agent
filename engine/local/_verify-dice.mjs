import puppeteer from "../node_modules/.pnpm/puppeteer@19.0.0_supports-color@8.1.1/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js";

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-popup-blocking"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1000 });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.goto("http://127.0.0.1:7717/#workshop", { waitUntil: "networkidle2" });
await page.evaluate(() => localStorage.removeItem("gi-tcg-local-presets-v1"));
await page.reload({ waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 600));

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
  if (!ok) throw new Error(`missing button: ${text}`);
  await new Promise((r) => setTimeout(r, 300));
}

async function tab(name) {
  await page.evaluate((name) => {
    const btn = [...document.querySelectorAll(".ws-tabs button")].find((b) => b.textContent.includes(name));
    btn?.click();
  }, name);
  await new Promise((r) => setTimeout(r, 300));
}

async function search(text) {
  await page.evaluate((text) => {
    const input = document.querySelector(".ws-search");
    input.value = text;
    input.dispatchEvent(new InputEvent("input", { bubbles: true }));
  }, text);
  await new Promise((r) => setTimeout(r, 300));
}

async function editFirstCost(values) {
  const opened = await page.evaluate(() => {
    const btn = document.querySelector(".ws-table tbody .ws-cost-btn");
    if (!btn) return false;
    btn.click();
    return true;
  });
  if (!opened) throw new Error("no cost button");
  await new Promise((r) => setTimeout(r, 300));
  const info = await page.evaluate((values) => {
    const inputs = [...document.querySelectorAll(".ws-modal .ws-dice-row input")];
    const before = inputs.map((i) => i.value);
    for (const [index, value] of Object.entries(values)) {
      const input = inputs[Number(index)];
      input.value = String(value);
      input.dispatchEvent(new InputEvent("input", { bubbles: true }));
    }
    return { count: inputs.length, before };
  }, values);
  await page.evaluate(() => {
    document.querySelector(".ws-modal .ws-primary")?.click();
  });
  await new Promise((r) => setTimeout(r, 300));
  return info;
}

// 行动牌：旅行剑 2 相同元素 -> 1 相同元素 + 1 火
await tab("行动牌");
await search("旅行剑");
console.log("card cost modal:", await editFirstCost({ 8: 1, 3: 1 }));
await clickText("保存预设");
let stored = await page.evaluate(() => JSON.parse(localStorage.getItem("gi-tcg-local-presets-v1")));
console.log("card preset:", JSON.stringify(stored.presets.find((p) => !p.builtin)?.cards));

// 技能：霜袭 3 冰 -> 1 冰 + 1 充能
await tab("技能");
await search("霜袭");
console.log("skill cost modal:", await editFirstCost({ 1: 1, 9: 1 }));
await clickText("保存预设");
stored = await page.evaluate(() => JSON.parse(localStorage.getItem("gi-tcg-local-presets-v1")));
console.log("skill preset:", JSON.stringify(stored.presets.find((p) => !p.builtin)?.skills));

// 开局，检查运行时数据
await clickText("用此预设开始对局");
await new Promise((r) => setTimeout(r, 5000));
const runtime = await page.evaluate(() => {
  const g = window.theGame;
  if (!g) return null;
  const card = g.state.data.entities.get(311501);
  const play = card.skills.find((s) => s.skillType === "playCard");
  const skill = g.state.data.characters.get(1103).skills.find((s) => s.id === 11032);
  const toObj = (m) => Object.fromEntries([...m.entries()].sort((a, b) => a[0] - b[0]));
  return {
    cardCost: toObj(play.initiativeSkillConfig.requiredCost),
    cardCostSize: play.initiativeSkillConfig.computed$costSize,
    skillCost: toObj(skill.initiativeSkillConfig.requiredCost),
    skillCostSize: skill.initiativeSkillConfig.computed$costSize,
  };
});
console.log("runtime:", JSON.stringify(runtime));
console.log("errors:", errors.join(" | ") || "(none)");
await page.screenshot({ path: "local/_screenshots/dice-editor.png" });
await browser.close();
