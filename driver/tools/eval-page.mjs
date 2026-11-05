/**
 * 在正在运行的浏览器页面里执行一段 JS（调试用，临时工具）。
 * 用法：node driver/tools/eval-page.mjs --port 9344 --match 7807 --js "document.title"
 */
import process from "node:process";
import { CDP } from "./cdp.mjs";

const args = { port: 9344, match: "", main: true };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--port") args.port = Number(argv[++i]);
  else if (argv[i] === "--match") args.match = argv[++i];
  else if (argv[i] === "--popup") args.main = false;
  else if (argv[i] === "--js") args.js = argv[++i];
}
if (!args.js) {
  console.error("缺 --js");
  process.exit(1);
}

const list = await (await fetch(`http://127.0.0.1:${args.port}/json/list`)).json();
const pages = list.filter(
  (t) => t.type === "page" && (!args.match || t.url.includes(args.match)),
);
const page = args.main
  ? (pages.find((p) => !p.url.includes("#arena-board")) ?? pages[0])
  : (pages.find((p) => p.url.includes("#arena-board")) ?? pages[0]);
if (!page) {
  console.error("没有匹配的页面");
  process.exit(1);
}
const version = await (await fetch(`http://127.0.0.1:${args.port}/json/version`)).json();
const conn = await new CDP(version.webSocketDebuggerUrl).connect();
await conn.attach(page.id);
const out = await conn.send("Runtime.evaluate", {
  expression: args.js,
  returnByValue: true,
  awaitPromise: true,
});
console.log(JSON.stringify(out.result?.value ?? out, null, 2));
conn.close();
