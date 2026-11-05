#!/usr/bin/env node
/**
 * 跟进上游更新：fetch -> 展示 -> merge -> 装依赖 -> 构建 -> 重新打包
 *
 *   node local/update-upstream.mjs           # 有更新才合并
 *   node local/update-upstream.mjs --check   # 只看不合并
 *   node local/update-upstream.mjs --no-build
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline/promises";

const repo = path.resolve(import.meta.dirname, "..");
const argv = process.argv.slice(2);
const checkOnly = argv.includes("--check");
const noBuild = argv.includes("--no-build");

function sh(cmd, args, opts = {}) {
  return spawnSync(cmd, args, {
    cwd: repo,
    stdio: "inherit",
    shell: process.platform === "win32",
    ...opts,
  });
}
function capture(cmd, args) {
  return spawnSync(cmd, args, { cwd: repo, encoding: "utf8", shell: process.platform === "win32" });
}
function fail(msg) {
  console.error(`\n[错误] ${msg}\n`);
  process.exit(1);
}

// 0. 检查仓库状态
const branch = capture("git", ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.trim();
if (!["local", "main"].includes(branch)) {
  console.log(`[提示] 当前分支是 ${branch}，建议在 local 分支上操作。`);
}
if (!checkOnly) {
  const dirty = capture("git", ["status", "--porcelain"]).stdout.trim();
  if (dirty) {
    fail(`工作区不干净，先提交或 stash：\n${dirty}`);
  }
}

// 1. fetch
console.log("==> git fetch upstream");
let r = sh("git", ["fetch", "upstream", "main"]);
if (r.status !== 0) fail("fetch 失败（检查网络或 upstream remote 是否存在）");

const log = capture("git", ["log", "--oneline", "--date=short", "--format=%h %ad %s", "HEAD..upstream/main"]);
const newCommits = log.stdout.trim();
if (!newCommits) {
  console.log("\n已是最新，上游没有新提交。");
  process.exit(0);
}
const count = newCommits.split("\n").length;
console.log(`\n上游有 ${count} 个新提交：\n`);
console.log(newCommits);
console.log("");

if (checkOnly) {
  console.log("（--check：仅查看，未合并）");
  process.exit(0);
}

const stat = capture("git", ["diff", "--stat", "HEAD...upstream/main"]);
console.log("改动概况：\n" + stat.stdout.split("\n").slice(0, 40).join("\n"));
console.log("");

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const answer = await rl.question("合并到当前分支？[Y/n] ");
rl.close();
if (answer.trim().toLowerCase() === "n") process.exit(0);

// 2. merge
console.log("\n==> git merge upstream/main");
r = sh("git", ["merge", "--no-edit", "upstream/main"]);
if (r.status !== 0) {
  console.error(`
合并冲突了。处理办法：
  1. 运行 git status 看冲突文件（重点看带 [LOCAL PATCH] 的几处）
  2. 保留上游新逻辑，再把我们标记的几行补回去
  3. git add -A && git commit
  4. 然后重新运行: node local/update-upstream.mjs
`);
  process.exit(1);
}

// 3. 依赖是否变化
const lockChanged = capture("git", ["diff", "--name-only", "HEAD@{1}", "HEAD", "--", "pnpm-lock.yaml"]).stdout.trim();
if (lockChanged) {
  console.log("\n==> pnpm-lock.yaml 有变化，重新安装依赖");
  r = sh("pnpm", ["install", "--frozen-lockfile"]);
  if (r.status !== 0) fail("pnpm install 失败");
}

// 4. 先构建一次，让 core/data/assets-manager 的 dist 反映上游最新代码
if (!noBuild) {
  console.log("\n==> 构建依赖（为生成目录做准备）");
  r = sh("pnpm", ["build", "standalone..."]);
  if (r.status !== 0) fail("构建失败");
}

// 5. 生成数值目录 + 牌组库 + 卡牌文本
console.log("\n==> 重新生成卡牌数值目录");
r = sh(process.execPath, [path.join(import.meta.dirname, "gen-catalog.mjs")]);
if (r.status !== 0) fail("数值目录生成失败：上游卡牌定义格式可能变了，请看 local/UPSTREAM.md 第 4.4 节");

console.log("\n==> 重新生成牌组库");
r = sh(process.execPath, [path.join(import.meta.dirname, "gen-decks.mjs")]);
if (r.status !== 0) fail("牌组库生成失败：检查 packages/assets-manager 是否构建成功");

if (!noBuild) {
  // 6. 再构建一次，把新的 catalog / decks / card-text 打进产物
  console.log("\n==> 重新构建前端");
  r = sh("pnpm", ["build", "standalone..."]);
  if (r.status !== 0) fail("构建失败");

  // 7. 自检
  console.log("\n==> 检查本地补丁锚点");
  r = sh(process.execPath, [path.join(import.meta.dirname, "check-patches.mjs")]);
  if (r.status !== 0) fail("本地补丁锚点缺失，请看 local/UPSTREAM.md 第 5 节");

  console.log("\n==> 运行本地回归测试");
  r = sh(process.execPath, [path.join(import.meta.dirname, "test-local.mjs")]);
  if (r.status !== 0) fail("回归测试失败");

  console.log("\n==> 重新打包便携版");
  r = sh(process.execPath, [path.join(import.meta.dirname, "build-portable.mjs"), "--skip-build"]);
  if (r.status !== 0) fail("打包失败");
}

console.log("\n完成。");
