// 看桥接服务的 aiLog：每个 requestId 出现了几次、时间戳是否重复、和本机 session 的 id 对得上不
import { readFileSync } from "node:fs";

const base = process.env.BRIDGE ?? "http://127.0.0.1:7807";
const seat = process.env.SEAT ?? "0";
const sessionFile = process.env.SESSION ?? "";

const res = await fetch(`${base}/api/arena/ai/log?seat=${seat}&since=0&limit=20000`);
const data = await res.json();
const events = data.events ?? [];
const reqs = events.filter((e) => e.event?.type === "request");
const byId = new Map();
for (const e of reqs) {
  const id = e.event.requestId;
  if (!byId.has(id)) byId.set(id, []);
  byId.get(id).push(e);
}
const dup = [...byId.entries()].filter(([, v]) => v.length > 1);
console.log(`request 事件 ${reqs.length} 个，唯一 requestId ${byId.size} 个，重复 ${dup.length} 个`);
for (const [id, v] of dup.slice(0, 10)) {
  console.log(
    `  id=${id} ×${v.length} at=${v.map((x) => x.at).join(",")} rpc=${v.map((x) => x.event.rpc).join(",")}`,
  );
}
const replays = events.filter((e) => e.event?.type === "request" && e.event.replay);
console.log(`带 replay 标记的 ${replays.length} 个`);

if (sessionFile) {
  const rows = readFileSync(sessionFile, "utf8")
    .trim()
    .split("\n")
    .map((l) => {
      try {
        return JSON.parse(l);
      } catch {
        return null;
      }
    })
    .filter((r) => r && r.kind === "turn");
  const ok = rows.filter((r) => r.ok).map((r) => r.requestId);
  const late = rows.filter((r) => !r.ok && r.rejectReason?.includes("404")).map((r) => r.requestId);
  console.log(`session: 成功 ${ok.length} 条，404 ${late.length} 条`);
  const seen = new Set(byId.keys());
  const ghost = late.filter((id) => !seen.has(id));
  console.log(`404 的 id 里，aiLog 完全没有记录的：${ghost.length} 个 ${ghost.slice(0, 12).join(",")}`);
  const okInLog = ok.filter((id) => seen.has(id));
  console.log(`成功的 id 里，aiLog 有记录的：${okInLog.length}/${ok.length}`);
}
