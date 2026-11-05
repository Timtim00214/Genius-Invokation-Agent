import fs from "node:fs";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const dataSrc = path.join(repo, "packages/data/src");

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith(".gts")) out.push(p);
  }
  return out;
}

function parseBlocks(src) {
  const lines = src.split(/\r?\n/);
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    const m = /^\s*define\s+(\w+)\s*\{/.exec(lines[i]);
    if (!m) continue;
    let j = i - 1;
    while (j >= 0 && lines[j].trim() === "") j--;
    let doc = "";
    if (j >= 0 && lines[j].trim() === "*/") {
      const end = j;
      let start = j;
      while (start >= 0 && !lines[start].includes("/**")) start--;
      if (start >= 0) doc = lines.slice(start, end + 1).join("\n");
    }
    let depth = 0;
    let end = i;
    for (let k = i; k < lines.length; k++) {
      for (const ch of lines[k]) {
        if (ch === "{") depth++;
        else if (ch === "}") depth--;
      }
      if (depth === 0) {
        end = k;
        break;
      }
    }
    blocks.push({
      kind: m[1],
      startLine: i + 1,
      body: lines.slice(i, end + 1).join("\n"),
      doc,
    });
    i = end;
  }
  return blocks;
}

const files = walk(dataSrc).filter((f) => !f.includes(`${path.sep}old_versions${path.sep}`));
console.log("gts files:", files.length);
const byId = new Map();
let blockCount = 0;
for (const file of files) {
  const src = fs.readFileSync(file, "utf8");
  for (const b of parseBlocks(src)) {
    blockCount++;
    const idm = /@id\s+(\d+)/.exec(b.doc) ?? /\bid\s+(\d+)\s+as\b/.exec(b.body);
    if (!idm) continue;
    const id = Number(idm[1]);
    const since = /since\s+"([^"]+)"/.exec(b.body)?.[1] ?? "v0";
    const name = /@name\s+(.+)/.exec(b.doc)?.[1]?.trim() ?? "";
    const prev = byId.get(id);
    const entry = { id, name, kind: b.kind, since, file: path.relative(dataSrc, file) };
    if (!prev) byId.set(id, [entry]);
    else prev.push(entry);
  }
}
console.log("blocks:", blockCount, "ids:", byId.size);
const dups = [...byId.entries()].filter(([, v]) => v.length > 1);
console.log("duplicate ids:", dups.length);
console.log(
  dups.slice(0, 15).map(([id, v]) => `${id}: ${v.map((e) => `${e.kind}@${e.since}(${e.file})`).join(" | ")}`).join("\n"),
);

// 统计 increaseDamage 取值
const values = new Map();
for (const file of files) {
  const src = fs.readFileSync(file, "utf8");
  for (const b of parseBlocks(src)) {
    if (b.kind !== "card") continue;
    const re = /increaseDamage\((\d+)\)/g;
    let m;
    while ((m = re.exec(b.body))) values.set(m[1], (values.get(m[1]) ?? 0) + 1);
  }
}
console.log("increaseDamage literal distribution:", [...values.entries()].sort((a, b) => Number(a[0]) - Number(b[0])));
