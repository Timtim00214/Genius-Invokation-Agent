import puppeteer from "../node_modules/.pnpm/puppeteer@19.0.0_supports-color@8.1.1/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js";

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-popup-blocking"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1500, height: 1000 });
const errors = [];
page.on("pageerror", (e) => errors.push(`main: ${e.message}`));
await page.goto("http://127.0.0.1:7717/#arena", { waitUntil: "networkidle2" });
await page.evaluate(() => {
  [...document.querySelectorAll(".arena-mode")].find((b) => b.textContent.includes("真人 vs 真人"))?.click();
});
await new Promise((r) => setTimeout(r, 300));

const popupPromise = new Promise((resolve) => browser.once("targetcreated", resolve));
await page.evaluate(() => {
  [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "开始对局")?.click();
});
const target = await Promise.race([popupPromise, new Promise((r) => setTimeout(() => r(null), 12000))]);
const popup = target ? await target.page() : null;
if (!popup) throw new Error("no popup");
popup.on("pageerror", (e) => errors.push(`popup: ${e.message}`));
await popup.setViewport({ width: 1200, height: 900 });
await new Promise((r) => setTimeout(r, 4000));

async function clickConfirm(p) {
  return p.evaluate(() => {
    const btn = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "确定");
    if (!btn) return false;
    btn.click();
    return true;
  });
}

console.log("before:", await page.evaluate(() => window.theGame?.state.phase));
console.log("main confirm:", await clickConfirm(page));
console.log("popup confirm:", await clickConfirm(popup));
await new Promise((r) => setTimeout(r, 4000));
console.log("after:", await page.evaluate(() => window.theGame?.state.phase));
console.log("round:", await page.evaluate(() => window.theGame?.state.roundNumber));
console.log("errors:", errors.join(" | ") || "(none)");
await browser.close();
