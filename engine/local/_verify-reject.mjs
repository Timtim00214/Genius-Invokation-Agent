import puppeteer from "../node_modules/.pnpm/puppeteer@19.0.0_supports-color@8.1.1/node_modules/puppeteer/lib/esm/puppeteer/puppeteer.js";

const BASE = "http://127.0.0.1:7717";
async function api(path, body) {
  const res = await fetch(BASE + path, body === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let data = null;
  try { data = JSON.parse(text); } catch { data = text; }
  return { status: res.status, data };
}

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-popup-blocking"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1500, height: 1000 });
page.on("pageerror", (e) => console.log("pageerror:", e.message));
await page.goto(BASE + "/#arena", { waitUntil: "networkidle2" });
await page.evaluate(() => {
  const inputs = [...document.querySelectorAll(".arena-grid3 input")];
  for (const input of inputs.slice(0, 2)) {
    input.value = "5";
    input.dispatchEvent(new InputEvent("input", { bubbles: true }));
  }
  const label = [...document.querySelectorAll(".arena-row label")].find((l) => l.textContent.includes("座位 0 耗时间"));
  const cb = label?.parentElement?.querySelector("input[type=checkbox]");
  if (cb) {
    cb.checked = true;
    cb.dispatchEvent(new Event("change", { bubbles: true }));
  }
});
await new Promise((r) => setTimeout(r, 300));
await page.evaluate(() => {
  [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "开始对局")?.click();
});
await new Promise((r) => setTimeout(r, 2500));

const status = await api("/api/arena/status");
console.log("pending:", JSON.stringify(status.data.pendingRequests));
const pending = (status.data.pendingRequests ?? []).find((p) => p.seat === 1);
if (!pending) throw new Error("no pending request for seat 1");

const bad = await api("/api/arena/agent/respond", {
  matchId: "local",
  id: pending.id,
  response: { response: { $case: "switchHands", value: { removedHandIds: [999999] } } },
});
console.log("invalid response ->", bad.status, JSON.stringify(bad.data).slice(0, 300));

const log = await api("/api/arena/ai/log?seat=1&limit=20");
const rejected = (log.data.events ?? []).filter((e) => e.event?.type === "action_rejected");
console.log("rejected events:", rejected.length, JSON.stringify(rejected.at(-1)?.event ?? null).slice(0, 300));

const good = await api("/api/arena/agent/respond", {
  matchId: "local",
  id: pending.id,
  response: { response: { $case: "switchHands", value: { removedHandIds: [] } } },
});
console.log("valid response ->", good.status, JSON.stringify(good.data).slice(0, 200));

await new Promise((r) => setTimeout(r, 2500));
const lines = await page.evaluate(() =>
  [...document.querySelectorAll(".arena-log-line")].slice(-5).map((e) => e.textContent),
);
console.log("log tail:", lines.join(" | "));
await browser.close();
