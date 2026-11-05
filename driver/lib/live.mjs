/**
 * 实时观测：把每一手的"提示词 / 模型原话 / 解析结果 / 最终响应 / 逐节点耗时"
 * 追加到一份 JSONL 里，供 driver/tools/viewer.mjs 边跑边看。
 *
 * 为什么要单独一份文件（而不是复用 sessions/*.jsonl）：
 *   - 会话文件是"给复盘/回放用的"，字段稳定、只记结论；
 *   - 观测文件是"给人看的"，要把可能上百 KB 的提示词原样留下，
 *     两者生命周期和体积差一个量级，混在一起会让复盘变慢。
 *
 * 写入用 append + 单写者，坏行不影响后续读取（viewer 会跳过解析失败的行）。
 */
import fs from "node:fs";
import path from "node:path";

/**
 * 创建观测写入器。
 *
 * @param {object} opts
 * @param {string} opts.dir 目录（一般与 --session-dir 相同）
 * @param {string} opts.session 会话名
 * @param {0|1} opts.seat 座位
 * @returns {{turn: (rec: object) => void, note: (rec: object) => void, file: string}}
 */
export function createLive({ dir, session, seat }) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "live.jsonl");
  let seq = countLines(file);

  const write = (rec) => {
    try {
      const line = JSON.stringify({ v: 1, session, seat, ...rec }) + "\n";
      fs.appendFileSync(file, line, "utf8");
    } catch {
      // 观测失败绝不影响对局
    }
  };

  return {
    file,
    turn(rec) {
      seq += 1;
      const ts = Date.now();
      // slow: 解析器给的毫秒数，viewer 里可以直接画成横条
      write({ kind: "turn", seq, ts, ...rec });
    },
    note(rec) {
      write({ kind: "note", ts: Date.now(), ...rec });
    },
  };
}

function countLines(file) {
  try {
    const text = fs.readFileSync(file, "utf8");
    return text.split("\n").filter((l) => l.trim()).length;
  } catch {
    return 0;
  }
}
