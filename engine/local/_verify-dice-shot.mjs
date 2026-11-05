import puppeteer from "../node_modules/.pnpm/puppeteer@19.0.0_supports-color@8.1.1/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js";

const browser = await puppeteer.launch({ headless: true, args: ["--no-sandbox"] });
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1000 });
await page.goto("http://127.0.0.1:7717/#workshop", { waitUntil: "networkidle2" });
await page.evaluate(() => localStorage.removeItem("gi-tcg-local-presets-v1"));
await page.reload({ waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 700));
await page.evaluate(() => {
  const tab = [...document.querySelectorAll(".ws-tabs button")].find((b) => b.textContent.includes("行动牌"));
  tab?.click();
});
await new Promise((r) => setTimeout(r, 300));
await page.evaluate(() => {
  const input = document.querySelector(".ws-search");
  input.value = "旅行剑";
  input.dispatchEvent(new InputEvent("input", { bubbles: true }));
});
await new Promise((r) => setTimeout(r, 300));
await page.evaluate(() => document.querySelector(".ws-table tbody .ws-cost-btn")?.click());
await new Promise((r) => setTimeout(r, 400));
await page.screenshot({ path: "local/_screenshots/dice-editor.png" });
console.log("modal:", await page.evaluate(() => document.querySelector(".ws-modal h3")?.textContent));
await browser.close();
