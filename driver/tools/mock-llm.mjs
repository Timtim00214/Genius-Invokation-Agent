#!/usr/bin/env node
/**
 * 假模型服务：在没有 api_key 的情况下验证"整条链路"能不能跑通。
 *
 * 它实现 OpenAI `/chat/completions` 的最小形状，行为刻意**笨**：
 * 从提示词的"可选动作"里挑第一个 `[编号]` 回出去，只回一个数字。
 * 这样能验证：节点组装 → HTTP 调用 → 解析编号 → 拼 RpcResponse → 形状校验，
 * 而不用花真实 token、也不用等模型。
 *
 *   node driver/tools/mock-llm.mjs --port 8110 [--index 3] [--delay 200] [--verbose]
 */

import http from "node:http";

const args = parseArgs(process.argv.slice(2));
const PORT = Number(args.port ?? 8110);
const DELAY = Number(args.delay ?? 0);
const FORCED = args.index === undefined ? null : Number(args.index);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--verbose") {
      out.verbose = true;
      continue;
    }
    if (!arg.startsWith("--")) continue;
    out[arg.slice(2)] = argv[++i];
  }
  return out;
}

/** 从提示词里挑一个编号：优先"可选动作"段的第一个 [N]。 */
function pickIndex(text) {
  if (FORCED !== null) return FORCED;
  const section = String(text).split("### 可选动作")[1] ?? String(text);
  const match = section.match(/\[(\d{1,3})\]/);
  return match ? Number(match[1]) : 0;
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", async () => {
    if (req.method !== "POST" || !req.url.includes("/chat/completions")) {
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { message: "not found" } }));
      return;
    }
    let body = {};
    try {
      body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { message: "bad json" } }));
      return;
    }
    const messages = body.messages ?? [];
    const joined = messages.map((m) => m.content ?? "").join("\n");
    const index = pickIndex(joined);
    if (DELAY) await new Promise((r) => setTimeout(r, DELAY));
    if (args.verbose) {
      console.log(`↩ ${body.model ?? "?"} ← ${messages.length} 条消息（${joined.length} 字符）→ 编号 ${index}`);
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        id: `mock-${Date.now()}`,
        object: "chat.completion",
        model: body.model ?? "mock",
        choices: [
          {
            index: 0,
            message: { role: "assistant", content: String(index) },
            finish_reason: "stop",
          },
        ],
        usage: { prompt_tokens: Math.ceil(joined.length / 2), completion_tokens: 1, total_tokens: Math.ceil(joined.length / 2) + 1 },
      }),
    );
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`假模型已就绪：http://127.0.0.1:${PORT}/v1（挑第一个编号${FORCED === null ? "" : `，固定 ${FORCED}`}${DELAY ? `，延迟 ${DELAY}ms` : ""}）`);
});
