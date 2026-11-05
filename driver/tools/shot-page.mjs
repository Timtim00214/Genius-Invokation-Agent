/**
 * 用无头浏览器给任意本地页面拍一张全页截图（不依赖已有的窗口）。
 *
 * 用法：
 *   node driver/tools/shot-page.mjs --url http://127.0.0.1:7810/ --out driver/samples/viewer.png
 *   node driver/tools/shot-page.mjs --url http://127.0.0.1:7810/ --out a.png --width 1500 --height 1100 --scale 2 --wait 2500
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { findBrowser, launchBrowser, CDP } from "./cdp.mjs";

const args = {
  url: "http://127.0.0.1:7810/",
  out: "driver/samples/shot-page.png",
  width: 1500,
  height: 1000,
  scale: 2,
  wait: 2500,
  port: 9351,
};
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === "--url") args.url = argv[++i];
  else if (a === "--out") args.out = argv[++i];
  else if (a === "--width") args.width = Number(argv[++i]);
  else if (a === "--height") args.height = Number(argv[++i]);
  else if (a === "--scale") args.scale = Number(argv[++i]);
  else if (a === "--wait") args.wait = Number(argv[++i]);
  else if (a === "--port") args.port = Number(argv[++i]);
}

const exe = findBrowser();
if (!exe) {
  console.error("找不到 Chrome/Edge，可执行文件请自行用 CHROME_PATH 指定");
  process.exit(1);
}

const browser = await launchBrowser({ exe, port: args.port, headless: true });
try {
  const version = await (await fetch(`http://127.0.0.1:${args.port}/json/version`)).json();
  const conn = await new CDP(version.webSocketDebuggerUrl).connect();
  const { targetId } = await conn.send("Target.createTarget", { url: "about:blank" });
  await conn.attach(targetId);
  await conn.send("Page.enable");
  await conn.send("Emulation.setDeviceMetricsOverride", {
    width: args.width,
    height: args.height,
    deviceScaleFactor: args.scale,
    mobile: false,
  });
  await conn.send("Page.navigate", { url: args.url });
  await new Promise((r) => setTimeout(r, args.wait));
  const { data } = await conn.send("Page.captureScreenshot", { format: "png" });
  const file = path.resolve(args.out);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  console.log("已保存:", file, `（${args.width}×${args.height} @${args.scale}x，${args.url}）`);
  conn.close();
} finally {
  await browser.close();
}
