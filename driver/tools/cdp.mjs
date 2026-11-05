/**
 * 零依赖 Chrome DevTools Protocol 客户端（只为自测用）。
 *
 * 项目里没有装 puppeteer（交接包不带 node_modules），但自测又必须真的开一个浏览器
 * 把页面里的对局跑起来。Node 24 自带全局 WebSocket，于是这里手写一个最小 CDP。
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

export const CHROME_CANDIDATES = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  path.join(os.homedir(), ".cache/puppeteer/chrome/win64-1045629/chrome-win/chrome.exe"),
];

export function findBrowser() {
  for (const p of CHROME_CANDIDATES) {
    try {
      if (fs.existsSync(p)) return p;
    } catch {
      /* ignore */
    }
  }
  throw new Error("找不到 Chrome/Edge");
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launchBrowser({ exe = findBrowser(), port = 9333, headless = true, extraArgs = [] } = {}) {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "arena-cdp-"));
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDataDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--disable-popup-blocking",
    "--disable-dev-shm-usage",
    "--window-size=1500,1000",
    ...(headless ? ["--headless=new"] : []),
    ...extraArgs,
    "about:blank",
  ];
  const proc = spawn(exe, args, { stdio: "ignore", detached: false });
  const base = `http://127.0.0.1:${port}`;
  let version = null;
  for (let i = 0; i < 100; i++) {
    try {
      const res = await fetch(`${base}/json/version`, { signal: AbortSignal.timeout(1000) });
      version = await res.json();
      break;
    } catch {
      await sleep(150);
    }
  }
  if (!version) {
    try {
      proc.kill();
    } catch {
      /* ignore */
    }
    throw new Error("CDP 端口没起来（浏览器启动失败？）");
  }
  return {
    base,
    version,
    proc,
    async close() {
      try {
        await fetch(`${base}/json/close/${version.id ?? ""}`).catch(() => {});
      } catch {
        /* ignore */
      }
      try {
        proc.kill();
      } catch {
        /* ignore */
      }
      await sleep(300);
      try {
        fs.rmSync(userDataDir, { recursive: true, force: true });
      } catch {
        /* 文件可能仍被占用 */
      }
    },
  };
}

/** 一条到浏览器的 CDP 连接（flatten 模式，sessionId 区分 target） */
export class CDP {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.id = 0;
    this.pending = new Map();
    this.listeners = new Map();
    this.sessionId = null;
  }

  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", (e) => reject(new Error(`WS 错误：${e?.message ?? e}`)), { once: true });
    });
    this.ws.addEventListener("message", (m) => {
      let msg;
      try {
        msg = JSON.parse(typeof m.data === "string" ? m.data : String(m.data));
      } catch {
        return;
      }
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message}（${msg.error.code}）`));
        else resolve(msg.result);
      } else if (msg.method) {
        const key = msg.method;
        for (const cb of this.listeners.get(key) ?? []) cb(msg.params, msg.sessionId);
      }
    });
    return this;
  }

  on(method, cb) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(cb);
    return this;
  }

  send(method, params = {}, sessionId = this.sessionId) {
    const id = ++this.id;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    this.ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP 超时：${method}`));
        }
      }, 30_000);
    });
  }

  /** 附加到某个页面 target，之后 send/evaluate 默认走它 */
  async attach(targetId) {
    const { sessionId } = await this.send("Target.attachToTarget", { targetId, flatten: true });
    this.sessionId = sessionId;
    await this.send("Runtime.enable", {}, sessionId);
    await this.send("Page.enable", {}, sessionId);
    return sessionId;
  }

  async evaluate(expression, { byValue = true, awaitPromise = true, sessionId = this.sessionId } = {}) {
    const res = await this.send(
      "Runtime.evaluate",
      { expression, returnByValue: byValue, awaitPromise },
      sessionId,
    );
    if (res?.exceptionDetails) {
      throw new Error(
        `页面异常：${res.exceptionDetails.exception?.description ?? res.exceptionDetails.text}`,
      );
    }
    return res?.result?.value;
  }

  async waitFor(expression, { timeoutMs = 30_000, intervalMs = 200 } = {}) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      try {
        const v = await this.evaluate(expression);
        if (v) return v;
      } catch {
        /* 页面可能在导航 */
      }
      await sleep(intervalMs);
    }
    throw new Error(`等待超时：${expression}`);
  }

  close() {
    try {
      this.ws.close();
    } catch {
      /* ignore */
    }
  }
}
