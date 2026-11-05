/**
 * 给蓝图里的 LLM 节点补 api_key（从环境变量读，不写进蓝图文件）。
 *
 * 蓝图是会被提交、会被拖拽、会被导出的东西，把密钥写进去迟早泄漏；
 * 而 llm_chat 节点留空 api_key 时是 dry_run（不发请求），所以两边都不合适。
 * 折中办法：运行时由司机按 api_base 猜环境变量名，只在参数为空时补。
 */

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** 本地密钥文件（driver/.env）：不提交、不写进蓝图。 */
const DOTENV_PATH = fileURLToPath(new URL("../.env", import.meta.url));

const ENV_BY_HOST = [
  ["api.deepseek.com", ["DEEPSEEK_API_KEY"]],
  ["api.openai.com", ["OPENAI_API_KEY"]],
  ["openrouter.ai", ["OPENROUTER_API_KEY"]],
];

const ENV_FALLBACK = ["VMAF_LLM_API_KEY", "LLM_API_KEY"];

/**
 * 读本地的 `driver/.env`（KEY=VALUE，`#` 开头是注释），补进 `process.env`。
 *
 * 为什么要有这个：密钥不该进蓝图，也不该要求用户每次开终端都 export 一遍。
 * 文件放在 driver/ 下、不进任何仓库，司机和 replay 一启动就能用。
 * 已有的环境变量优先（方便临时覆盖）。
 *
 * @param {string} [file] .env 路径
 * @param {object} [env] 环境变量表
 * @returns {string[]} 实际补进去的变量名
 */
function loadDotEnv(file = DOTENV_PATH, env = process.env) {
  const loaded = [];
  try {
    if (!existsSync(file)) return loaded;
    for (const rawLine of readFileSync(file, "utf8").split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq <= 0) continue;
      const name = line.slice(0, eq).trim();
      let value = line.slice(eq + 1).trim();
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      if (!name || !value || env[name]) continue;
      env[name] = value;
      loaded.push(name);
    }
  } catch {
    return loaded;
  }
  return loaded;
}

export const dotenvLoaded = loadDotEnv();

/** 按 api_base 猜该用哪个环境变量。 */
export function envNamesFor(apiBase) {
  const base = String(apiBase ?? "");
  for (const [host, names] of ENV_BY_HOST) {
    if (base.includes(host)) return [...names, ...ENV_FALLBACK];
  }
  return ENV_FALLBACK;
}

/** 从环境变量里找这个节点该用的密钥。 */
export function keyForNode(node, env = process.env) {
  for (const name of envNamesFor(node?.params?.api_base)) {
    const value = env[name];
    if (typeof value === "string" && value.trim()) return { key: value.trim(), source: name };
  }
  return { key: null, source: null };
}

/**
 * 只对 llm_chat 节点生效：把 --llm-model / --llm-api-base / --llm-api-key-env /
 * --llm-max-tokens / --llm-timeout-seconds 覆盖上去。
 *
 * 为什么要它：两个座位要能接不同厂家的模型（一个 DeepSeek、一个 OpenAI 之类），
 * 而蓝图是两边共用的，所以覆盖只能在司机进程里做——每个司机一份参数。
 *
 * Args:
 *   doc: 蓝图对象（会被克隆，原对象不动）。
 *   args: 命令行参数。
 *   env: 环境变量表（测试时可注入）。
 *
 * Returns:
 *   object: 覆盖后的蓝图。
 */
export function applyLlmOverrides(doc, args, env = process.env) {
  if (
    !args.llmModel &&
    !args.llmApiBase &&
    !args.llmApiKeyEnv &&
    !args.llmApiKey &&
    args.llmMaxTokens == null &&
    args.llmTimeoutSeconds == null
  ) {
    return doc;
  }
  let key = null;
  if (args.llmApiKey) {
    key = String(args.llmApiKey).trim() || null;
  } else if (args.llmApiKeyEnv) {
    const value = env[args.llmApiKeyEnv];
    if (typeof value !== "string" || !value.trim()) {
      throw new Error(`--llm-api-key-env ${args.llmApiKeyEnv} 指向的环境变量是空的（写进 driver/.env 或先 export）`);
    }
    key = value.trim();
  }
  const patched = structuredClone(doc);
  for (const node of patched.nodes ?? []) {
    if (node?.type !== "llm_chat") continue;
    node.params = { ...(node.params ?? {}) };
    if (args.llmModel) node.params.model = args.llmModel;
    if (args.llmApiBase) node.params.api_base = args.llmApiBase;
    if (args.llmMaxTokens != null) node.params.max_tokens = Number(args.llmMaxTokens);
    if (args.llmTimeoutSeconds != null) node.params.timeout_seconds = Number(args.llmTimeoutSeconds);
    if (key) node.params.api_key = key;
  }
  return patched;
}

/** 每座位可以从 .env 里读的配置项（命令行优先，.env 只当默认值）。 */
const LLM_ENV_FIELDS = {
  llmModel: "LLM_MODEL",
  llmApiBase: "LLM_API_BASE",
  llmApiKeyEnv: "LLM_API_KEY_ENV",
  llmApiKey: "LLM_API_KEY",
  llmMaxTokens: "LLM_MAX_TOKENS",
  llmTimeoutSeconds: "LLM_TIMEOUT_SECONDS",
};

/**
 * 从环境变量里取这个座位的 LLM 默认配置。
 *
 * 为什么要它：把密钥写进命令行会被 shell 历史记下来，而让用户每次开终端都 export
 * 一遍又太麻烦。约定 `SEAT0_LLM_MODEL` / `SEAT1_LLM_MODEL` 这种带座位前缀的名字，
 * 没写前缀的 `LLM_MODEL` 当两个座位共用的默认值；命令行参数仍然优先。
 *
 * Args:
 *   seat: 座位号（0/1）。
 *   env: 环境变量表（测试时可注入）。
 *
 * Returns:
 *   object: 只含实际填了的字段，可直接 Object.assign 到 args 上。
 */
export function envLlmOverrides(seat, env = process.env) {
  const out = {};
  const prefix = `SEAT${Number(seat)}_`;
  const pick = (name) => {
    const value = env[name];
    return typeof value === "string" && value.trim() ? value.trim() : "";
  };
  for (const [field, suffix] of Object.entries(LLM_ENV_FIELDS)) {
    const raw = pick(prefix + suffix) || pick(suffix);
    if (!raw) continue;
    if (field === "llmMaxTokens" || field === "llmTimeoutSeconds") {
      const num = Number(raw);
      if (Number.isFinite(num)) out[field] = num;
      continue;
    }
    out[field] = raw;
  }
  return out;
}

export function injectSecrets(blueprint, env = process.env) {
  const injected = [];
  const missing = [];
  const doc = structuredClone(blueprint ?? {});
  for (const node of doc.nodes ?? []) {
    if (node?.type !== "llm_chat") continue;
    if (typeof node.params?.api_key === "string" && node.params.api_key.trim()) continue;
    const { key, source } = keyForNode(node, env);
    if (!key) {
      missing.push(node.id ?? "llm_chat");
      continue;
    }
    node.params = { ...(node.params ?? {}), api_key: key };
    injected.push(`${node.id ?? "llm_chat"}←${source}`);
  }
  return { blueprint: doc, injected, missing };
}
