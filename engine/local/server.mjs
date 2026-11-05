#!/usr/bin/env node
/**
 * 零依赖静态服务器 —— 用于运行打包好的七圣召唤模拟器（standalone）。
 *
 * 用法:
 *   node local/server.mjs                 # 服务 packages/standalone/dist
 *   node local/server.mjs --port 7717
 *   node local/server.mjs --root some/dir --no-open
 *
 * 之后还会在这里挂载 AI 对战所需的 /api 接口（见 local/README.md）。
 */
import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { createArenaBridge } from "./arena/bridge.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const args = { port: 7717, host: "127.0.0.1", open: true, hash: "" };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--port" || a === "-p") args.port = Number(argv[++i]);
    else if (a === "--host") args.host = argv[++i];
    else if (a === "--root") args.root = argv[++i];
    else if (a === "--hash") args.hash = argv[++i] ?? "";
    else if (a === "--no-open") args.open = false;
    else if (a === "--help" || a === "-h") args.help = true;
    else if (!a.startsWith("-") && !args.root) args.root = a;
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log(`用法: node server.mjs [--port 7717] [--host 127.0.0.1] [--root <目录>] [--hash "#workshop"] [--no-open]`);
  process.exit(0);
}

const root = path.resolve(
  args.root ?? path.join(__dirname, "..", "packages", "standalone", "dist"),
);

if (!fs.existsSync(path.join(root, "index.html"))) {
  console.error(`\n[错误] 找不到 ${path.join(root, "index.html")}`);
  console.error(`请先构建前端：`);
  console.error(`  pnpm build "standalone..."`);
  console.error(`或指定 --root 指向已构建的 dist 目录。\n`);
  process.exit(1);
}

const arenaBridge = createArenaBridge({ root });

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".wasm": "application/wasm",
  ".txt": "text/plain; charset=utf-8",
};

function safeResolve(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0].split("#")[0]);
  const p = path.normalize(path.join(root, decoded));
  if (!p.startsWith(root)) return null;
  return p;
}

const server = http.createServer(async (req, res) => {
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("X-Content-Type-Options", "nosniff");

  // AI 对战桥接（/api/arena/*）
  if (await arenaBridge.handle(req, res)) {
    return;
  }

  if (req.url === "/api/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, app: "genius-invokation", root }));
    return;
  }

  let filePath = safeResolve(req.url || "/");
  if (!filePath) {
    res.writeHead(403).end("Forbidden");
    return;
  }
  try {
    let stat = await fsp.stat(filePath).catch(() => null);
    if (stat?.isDirectory()) {
      filePath = path.join(filePath, "index.html");
      stat = await fsp.stat(filePath).catch(() => null);
    }
    if (!stat) {
      // SPA 回退：没有扩展名的路径交给前端路由
      if (!path.extname(filePath)) {
        filePath = path.join(root, "index.html");
        stat = await fsp.stat(filePath).catch(() => null);
      }
    }
    if (!stat) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("404 Not Found");
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, {
      "Content-Type": MIME[ext] ?? "application/octet-stream",
      "Content-Length": stat.size,
    });
    fs.createReadStream(filePath).pipe(res);
  } catch (e) {
    res.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("500 Internal Server Error\n" + String(e));
  }
});

server.on("error", (e) => {
  if (e.code === "EADDRINUSE") {
    console.error(`\n[错误] 端口 ${args.port} 已被占用。请用 --port 换一个端口，或关闭占用它的程序。\n`);
  } else {
    console.error(e);
  }
  process.exit(1);
});

server.listen(args.port, args.host, () => {
  const url = `http://${args.host}:${args.port}/${args.hash ?? ""}`;
  console.log("");
  console.log("  七圣召唤模拟器已启动");
  console.log(`  地址: ${url}`);
  console.log("  按 Ctrl+C 停止");
  console.log("");
  if (args.open && process.env.GI_NO_OPEN !== "1") openBrowser(url);
});

function openBrowser(url) {
  const cmd =
    process.platform === "win32"
      ? ["cmd", ["/c", "start", "", url]]
      : process.platform === "darwin"
        ? ["open", [url]]
        : ["xdg-open", [url]];
  try {
    const child = spawn(cmd[0], cmd[1], { stdio: "ignore", detached: true });
    child.on("error", () => {});
    child.unref();
  } catch {
    /* ignore */
  }
}

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 500).unref();
  });
}
