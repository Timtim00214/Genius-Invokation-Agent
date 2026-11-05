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
await page.setViewport({ width: 1440, height: 900 });
const logs = [];
page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));

await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 });

// 点击“开始对局”，等待先手弹窗
const popupPromise = new Promise((resolve) => browser.once("targetcreated", resolve));
await page.evaluate(() => {
  const btn = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "开始对局");
  btn?.click();
});
const target = await Promise.race([popupPromise, new Promise((r) => setTimeout(() => r(null), 8000))]);
if (target) {
  const popup = await target.page();
  if (popup) {
    popup.on("console", (m) => logs.push(`[popup:${m.type()}] ${m.text()}`));
    popup.on("pageerror", (e) => logs.push(`[popup:pageerror] ${e.message}`));
    await popup.setViewport({ width: 900, height: 750 });
    await new Promise((r) => setTimeout(r, 5000));
    await popup.screenshot({ path: path.join(outDir, "board0.png") });
  }
} else {
  logs.push("[warn] 没有检测到弹窗");
}
await new Promise((r) => setTimeout(r, 2000));
await page.screenshot({ path: path.join(outDir, "board1.png") });

console.log(logs.slice(0, 60).join("\n"));
await browser.close();
