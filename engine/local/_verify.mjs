import puppeteer from "../node_modules/.pnpm/puppeteer@19.0.0_supports-color@8.1.1/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js";
import path from "node:path";
import fs from "node:fs";

const url = process.argv[2] ?? "http://127.0.0.1:7717/";
const outDir = path.join(import.meta.dirname, "_screenshots");
fs.mkdirSync(outDir, { recursive: true });

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
const logs = [];
page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));
page.on("requestfailed", (r) => logs.push(`[requestfailed] ${r.url()} ${r.failure()?.errorText}`));

await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 }).catch((e) => logs.push(`[goto] ${e.message}`));
await new Promise((r) => setTimeout(r, 2500));
await page.screenshot({ path: path.join(outDir, "home.png"), fullPage: false });

const title = await page.title();
const bodyText = (await page.evaluate(() => document.body.innerText)).slice(0, 800);
console.log("TITLE:", title);
console.log("BODY:", bodyText.replace(/\n+/g, " | "));
console.log("--- console ---");
console.log(logs.slice(0, 40).join("\n"));
await browser.close();
