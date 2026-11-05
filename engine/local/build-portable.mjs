#!/usr/bin/env node
/**
 * 生成“发给别人也能双击启动”的便携包。
 *
 *   node local/build-portable.mjs              # 构建 + 打包
 *   node local/build-portable.mjs --skip-build # 跳过 pnpm build（dist 已存在时）
 *   node local/build-portable.mjs --skip-node  # 不带 Node 运行时（对方需自装 Node 18+）
 *
 * 产物: dist-portable/GeniusInvokation-Portable/
 *       dist-portable/GeniusInvokation-Portable.zip
 */
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";

const localDir = import.meta.dirname;
const repoRoot = path.resolve(localDir, "..");
const outRoot = path.join(repoRoot, "dist-portable");
const appName = "GeniusInvokation-Portable";
const appDir = path.join(outRoot, appName);
const cacheDir = path.join(localDir, ".cache");

const NODE_VERSION = "26.12.1";
const NODE_ZIP_URL = `https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-win-x64.zip`;
const NODE_ZIP = path.join(cacheDir, `node-v${NODE_VERSION}-win-x64.zip`);

const args = process.argv.slice(2);
const skipBuild = args.includes("--skip-build");
const skipNode = args.includes("--skip-node");

function run(cmd, cmdArgs, opts = {}) {
  console.log(`\n$ ${cmd} ${cmdArgs.join(" ")}`);
  const r = spawnSync(cmd, cmdArgs, { stdio: "inherit", cwd: repoRoot, shell: process.platform === "win32", ...opts });
  if (r.status !== 0) {
    throw new Error(`命令失败: ${cmd} ${cmdArgs.join(" ")} (exit ${r.status})`);
  }
}

async function download(url, dest) {
  await fsp.mkdir(path.dirname(dest), { recursive: true });
  if (fs.existsSync(dest) && (await fsp.stat(dest)).size > 30_000_000) {
    console.log(`使用缓存: ${dest}`);
    return;
  }
  console.log(`下载 Node.js v${NODE_VERSION} 运行时...`);
  const res = await fetch(url, { redirect: "follow" });
  if (!res.ok) throw new Error(`下载失败: HTTP ${res.status}`);
  const total = Number(res.headers.get("content-length") ?? 0);
  const file = fs.createWriteStream(dest);
  let received = 0;
  let last = 0;
  for await (const chunk of res.body) {
    file.write(chunk);
    received += chunk.length;
    if (total && Date.now() - last > 1000) {
      process.stdout.write(`\r  ${(received / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`);
      last = Date.now();
    }
  }
  await new Promise((r) => file.end(r));
  console.log("\n下载完成");
}

function extractNodeExe(zip, destDir) {
  const tmp = path.join(cacheDir, "node-extract");
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  const winTar = "C:\\Windows\\System32\\tar.exe";
  let r = fs.existsSync(winTar)
    ? spawnSync(winTar, ["-xf", zip, "-C", tmp], { stdio: "inherit" })
    : { status: 1 };
  if (r.status !== 0) {
    console.log("tar 解压失败，改用 PowerShell...");
    run("powershell", ["-NoProfile", "-Command", `Expand-Archive -LiteralPath '${zip}' -DestinationPath '${tmp}' -Force`]);
  }
  const inner = path.join(tmp, `node-v${NODE_VERSION}-win-x64`, "node.exe");
  if (!fs.existsSync(inner)) throw new Error(`解压后找不到 ${inner}`);
  fs.copyFileSync(inner, path.join(destDir, "node.exe"));
  const size = fs.statSync(path.join(destDir, "node.exe")).size;
  console.log(`已放入 node.exe (${(size / 1048576).toFixed(1)} MB)`);
}

const START_CMD = `@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PORT=7717"
if not "%~1"=="" set "PORT=%~1"
if exist "%~dp0node.exe" (
  set "NODE=%~dp0node.exe"
) else (
  set "NODE=node"
)
"%NODE%" server.mjs --root "%~dp0app" --port %PORT%
if errorlevel 1 (
  echo.
  echo 启动失败。请确认 node.exe 存在，或已安装 Node.js 18+。
  pause
  exit /b 1
)
`;

const WORKSHOP_CMD = `@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PORT=7717"
if not "%~1"=="" set "PORT=%~1"
if exist "%~dp0node.exe" (
  set "NODE=%~dp0node.exe"
) else (
  set "NODE=node"
)
"%NODE%" server.mjs --root "%~dp0app" --port %PORT% --hash "#workshop"
if errorlevel 1 (
  echo.
  echo 启动失败。请确认 node.exe 存在，或已安装 Node.js 18+。
  pause
  exit /b 1
)
`;

const ARENA_CMD = `@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PORT=7717"
if not "%~1"=="" set "PORT=%~1"
if exist "%~dp0node.exe" (
  set "NODE=%~dp0node.exe"
) else (
  set "NODE=node"
)
"%NODE%" server.mjs --root "%~dp0app" --port %PORT% --hash "#arena"
if errorlevel 1 (
  echo.
  echo 启动失败。请确认 node.exe 存在，或已安装 Node.js 18+。
  pause
  exit /b 1
)
`;

const README = `七圣召唤模拟器 · 便携版
========================

启动文件故意使用英文名，避免不同 Windows 的 zip 中文编码差异。
对应的中文含义：
  Start.cmd    = 启动（进入首页：本地对战 / 数值工坊 / AI 对战 / 摆盘器）
  Workshop.cmd = 数值工坊
  Arena.cmd    = AI 对战

怎么启动
--------
双击 Start.cmd 进入首页。
双击 Workshop.cmd 直接打开数值工坊。
双击 Arena.cmd 直接打开 AI 对战场。
浏览器会自动打开 http://127.0.0.1:7717/
如果浏览器没有自动打开，请手动访问上面的地址。

换端口
------
在解压目录打开命令行：Start.cmd 8080
然后访问 http://127.0.0.1:8080/

AI 对战 / 接自己的 LLM
-----------------------
1. 双击 Arena.cmd，选择模式、座位和计时。
2. 在解压目录另开一个命令行窗口运行：
     node.exe arena\\example-agent.mjs --seat 1
   （真人坐先手 0 时 AI 是 1；真人坐后手 1 时 AI 是 0）
3. AI 接口文档在 arena\\API.md，示例 agent 在 arena\\example-agent.mjs。
4. 便携包不包含 LLM；示例 agent 只用来验证接口。

注意
----
- 卡牌图片从官方静态资源站（static-data.piovium.org）加载，首次打开需要联网；断网时仍可对局，只是部分图片缺失。
- 这是 https://github.com/piovium/genius-invokation 的本地构建版，遵循 AGPL-3.0 许可（见 LICENSE）。
- 关闭那个黑色命令行窗口 = 关闭服务器。
`;

async function main() {
  if (!skipBuild) {
    run("pnpm", ["build", "standalone..."]);
  }
  const dist = path.join(repoRoot, "packages", "standalone", "dist");
  if (!fs.existsSync(path.join(dist, "index.html"))) {
    throw new Error(`找不到构建产物: ${dist}/index.html`);
  }

  await fsp.rm(appDir, { recursive: true, force: true });
  await fsp.mkdir(appDir, { recursive: true });

  console.log("\n复制前端文件...");
  await fsp.cp(dist, path.join(appDir, "app"), { recursive: true });

  console.log("复制启动器...");
  await fsp.copyFile(path.join(localDir, "server.mjs"), path.join(appDir, "server.mjs"));
  await fsp.cp(path.join(localDir, "arena"), path.join(appDir, "arena"), {
    recursive: true,
  });
  // 只使用 ASCII 文件名：zip 的中文文件名在不同 Windows/解压工具下会出现乱码。
  await fsp.writeFile(path.join(appDir, "Start.cmd"), START_CMD.replace(/\n/g, "\r\n"), "utf8");
  await fsp.writeFile(path.join(appDir, "Workshop.cmd"), WORKSHOP_CMD.replace(/\n/g, "\r\n"), "utf8");
  await fsp.writeFile(path.join(appDir, "Arena.cmd"), ARENA_CMD.replace(/\n/g, "\r\n"), "utf8");
  await fsp.writeFile(path.join(appDir, "README.txt"), README.replace(/\n/g, "\r\n"), "utf8");
  await fsp.copyFile(path.join(repoRoot, "LICENSE"), path.join(appDir, "LICENSE"));

  const catalogSrc = path.join(repoRoot, "packages", "local-rules", "src", "catalog.json");
  if (fs.existsSync(catalogSrc)) {
    await fsp.copyFile(catalogSrc, path.join(appDir, "app", "catalog.json"));
  }

  if (!skipNode) {
    await download(NODE_ZIP_URL, NODE_ZIP);
    extractNodeExe(NODE_ZIP, appDir);
  } else {
    console.log("已跳过 Node 运行时（接收者需要自行安装 Node.js 18+）");
  }

  const zipPath = path.join(outRoot, `${appName}.zip`);
  fs.rmSync(zipPath, { force: true });
  console.log("\n压缩为 zip...");
  const winTar = "C:\\Windows\\System32\\tar.exe";
  const tarArgs = ["-a", "-c", "-f", zipPath, "-C", outRoot, appName];
  let r = fs.existsSync(winTar)
    ? spawnSync(winTar, tarArgs, { stdio: "inherit" })
    : { status: 1 };
  if (r.status !== 0) {
    run("powershell", ["-NoProfile", "-Command", `Compress-Archive -LiteralPath '${appDir}' -DestinationPath '${zipPath}' -Force`]);
  }

  const size = fs.existsSync(zipPath) ? (fs.statSync(zipPath).size / 1048576).toFixed(1) : "?";
  console.log(`\n完成！`);
  console.log(`  文件夹: ${appDir}`);
  console.log(`  压缩包: ${zipPath} (${size} MB)`);
}

main().catch((e) => {
  console.error("\n打包失败:", e.message);
  process.exit(1);
});
