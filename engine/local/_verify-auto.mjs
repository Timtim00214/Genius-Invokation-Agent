import puppeteer from "../node_modules/.pnpm/puppeteer@19.0.0_supports-color@8.1.1/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js";

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-popup-blocking"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1500, height: 1000 });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const popupPromise = new Promise((resolve) => browser.once("targetcreated", resolve));
await page.goto("http://127.0.0.1:7717/#arena", { waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 5000));
const target = await Promise.race([popupPromise, new Promise((r) => setTimeout(() => r(null), 12000))]);
const popup = target ? await target.page() : null;
if (popup) {
  await popup.setViewport({ width: 1200, height: 900 });
  console.log("popup:", await popup.evaluate(() => document.querySelector(".arena-popup-title")?.textContent));
}
const info = await page.evaluate(() => {
  const g = window.theGame;
  return {
    hasGame: !!g,
    phase: g?.state.phase,
    p1: g?.state.players[1].characters.map((c) => c.definition.id),
    p0: g?.state.players[0].characters.map((c) => c.definition.id),
    body: document.body.innerText.slice(0, 120),
  };
});
console.log("info:", JSON.stringify(info));
console.log("errors:", errors.join(" | ") || "(none)");
await page.screenshot({ path: "local/_screenshots/arena-auto.png" });
await browser.close();
