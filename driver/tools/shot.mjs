/**
 * 给正在跑的浏览器窗口拍一张截图（CDP Page.captureScreenshot）。
 *
 * 用法：node driver/tools/shot.mjs --port 9344 --out driver/samples/shot.png
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { CDP } from "./cdp.mjs";

const args = { port: 9344, out: "driver/samples/shot.png", target: "", main: false };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--port") args.port = Number(argv[++i]);
  else if (argv[i] === "--out") args.out = argv[++i];
  else if (argv[i] === "--target") args.target = argv[++i];
  else if (argv[i] === "--main") args.main = true;
}

const version = await (await fetch(`http://127.0.0.1:${args.port}/json/version`)).json();
const conn = await new CDP(version.webSocketDebuggerUrl).connect();

const list = await (await fetch(`http://127.0.0.1:${args.port}/json/list`)).json();
const pages = list.filter((t) => t.type === "page" && (args.target ? t.url.includes(args.target) : true));
if (!pages.length) {
  console.error("没有找到页面 target");
  process.exit(1);
}
const page = args.main
  ? pages.find((p) => !p.url.includes("#arena-board")) ?? pages[0]
  : pages.find((p) => p.url.includes("#arena-board")) ?? pages[0];
await conn.attach(page.id);
const { data } = await conn.send("Page.captureScreenshot", { format: "png" });
const file = path.resolve(args.out);
fs.mkdirSync(path.dirname(file), { recursive: true });
fs.writeFileSync(file, Buffer.from(data, "base64"));
console.log("已保存:", file, "（页面:", page.url, "）");
conn.close();
