#!/usr/bin/env node
/**
 * 把本地蓝图推到 VMAF 服务端，拿到一个 id。
 *
 * 为什么要这个工具：
 *   提示词要在网页画布上反复改，而司机读的是"服务端那一份"。手动复制 JSON
 *   容易改错，所以用一条命令完成"本地文件 → 服务端"，再把 id 交给司机：
 *
 *     node driver/tools/push-blueprint.mjs --file driver/blueprints/decide.json
 *     node driver/arena-driver.mjs --mode vmaf --seat 0 --blueprint-id <打印出的 id>
 *
 *   --id 已存在时是覆盖（同一份继续迭代），不给则新建。
 *
 * 用法：
 *   node driver/tools/push-blueprint.mjs [--file <path>] [--name <名字>] [--id <id>] [--vmaf <url>]
 *   node driver/tools/push-blueprint.mjs --list
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function parseArgs(argv) {
  const out = {
    file: path.join(ROOT, "blueprints", "decide.json"),
    name: null,
    id: null,
    vmaf: "http://127.0.0.1:8000",
    list: false,
  };
  const need = (i) => {
    const v = argv[i];
    if (v === undefined) throw new Error(`参数 ${argv[i - 1]} 缺少值`);
    return v;
  };
  for (let i = 0; i < argv.length; i++) {
    switch (argv[i]) {
      case "--file": out.file = path.resolve(need(++i)); break;
      case "--name": out.name = need(++i); break;
      case "--id": out.id = need(++i); break;
      case "--vmaf": out.vmaf = need(++i); break;
      case "--list": out.list = true; break;
      case "-h":
      case "--help": out.help = true; break;
      default:
        if (argv[i].startsWith("--")) throw new Error(`未知参数：${argv[i]}`);
    }
  }
  return out;
}

const base = (args) => args.vmaf.replace(/\/+$/, "");

async function listBlueprints(args) {
  const res = await fetch(`${base(args)}/blueprints`);
  if (!res.ok) throw new Error(`GET /blueprints → ${res.status}`);
  const data = await res.json();
  const items = data.items ?? data.blueprints ?? [];
  if (!items.length) {
    console.log("服务端还没有保存任何蓝图。");
    return;
  }
  for (const it of items) {
    console.log(`${it.id}\t${it.name ?? ""}\t${it.updated_at ?? it.updatedAt ?? ""}`);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(fs.readFileSync(fileURLToPath(import.meta.url), "utf8").split("*/")[0]);
    return 0;
  }
  if (args.list) {
    await listBlueprints(args);
    return 0;
  }

  const raw = fs.readFileSync(args.file, "utf8");
  const blueprint = JSON.parse(raw); // 先本地解析：坏 JSON 至少在这里就报，不用等服务端 422
  const body = {
    blueprint,
    name: args.name ?? blueprint.name ?? path.basename(args.file, ".json"),
    ...(args.id ? { id: args.id } : {}),
  };

  const res = await fetch(`${base(args)}/blueprints`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    console.error(`保存失败：HTTP ${res.status}\n${text}`);
    return 1;
  }
  const meta = JSON.parse(text);
  console.log(`已保存：id=${meta.id}  name=${meta.name ?? body.name}  nodes=${blueprint.nodes?.length ?? 0}  edges=${blueprint.edges?.length ?? 0}`);
  console.log(`司机用：--blueprint-id ${meta.id}`);
  return 0;
}

main().then((code) => process.exit(code ?? 0)).catch((err) => {
  console.error(`✖ ${err.message}`);
  process.exit(1);
});
