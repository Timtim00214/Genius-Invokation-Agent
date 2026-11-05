import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";
import getData from "@gi-tcg/data";
import {
  CURRENT_VERSION,
  Game,
  type RpcRequest,
  type RpcResponse,
  type Version,
} from "@gi-tcg/core";
import { createClient } from "@gi-tcg/web-ui-core";
import "@gi-tcg/web-ui-core/style.css";
import { DEFAULT_ASSETS_MANAGER } from "@gi-tcg/assets-manager";
import {
  applyPresetToData,
  countPresetChanges,
  installPreset,
  type RulesPreset,
} from "@gi-tcg/local-rules";
import { activePreset } from "../workshop/storage";
import { createAgentSeatIo, createHumanSeatIo, type SeatTimer } from "./seatIo";
import { describeDecision } from "./defaultResponse";
import { decisionEvent } from "./aiEvents";
import {
  agentStreamUrl,
  getPrepare,
  getStatus,
  pushAiEvents,
  pushConfig,
  pushLog,
  resetMatch,
  type ArenaPrepare,
  type ArenaStatus,
} from "./bridge";
import "./arena.css";

export type ArenaMode = "pvp" | "pve" | "eve";

export interface ArenaDeckData {
  characters: number[];
  cards: number[];
  name?: string;
}

export interface ArenaConfig {
  mode: ArenaMode;
  humanSeat: 0 | 1;
  deck0: string;
  deck1: string;
  deck0Data?: ArenaDeckData;
  deck1Data?: ArenaDeckData;
  version: Version;
  preset: RulesPreset | null;
  actionSeconds: number;
  initSeconds: number;
  matchMinutes: number;
  stall: [boolean, boolean];
  /** 立刻让过（不等待，直接默认响应），每座位一个 */
  instant?: [boolean, boolean];
  /** 开局是否自动打开"对方棋盘"弹窗；默认 EvE 关、其余开 */
  openPopup?: boolean;
  /**
   * 回合上限。官方规则是 15；这里允许调大，方便 AI 对局打出胜负而不是到点判平。
   * 值为 `undefined` 时沿用核心默认值（15，与原版完全一致）。
   */
  maxRounds?: number;
}

const INIT_DECK0 =
  "FZDByRUNGRCB0WoNFlGgWpEPE0AB9TAPFGCB9kgWGIERCoEQDLFADcQQDPFgacYWDJAA";
const INIT_DECK1 =
  "FdHxNj8TAWDQxFkMFkAhyWIYCYDA45wOCUDQ5J0PGEAh9IIPGWDh9p4YC6FgirYRCxAA";

const MODE_LABEL: Record<ArenaMode, string> = {
  pvp: "真人 vs 真人",
  pve: "真人 vs AI",
  eve: "AI vs AI",
};

function seatName(who: 0 | 1): string {
  return who === 0 ? "先手" : "后手";
}
function fmt(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// ======================================================================
// 入口：配置页 / 对局页
// ======================================================================
export function Arena(props: { onBack: () => void }) {
  const [config, setConfig] = createSignal<ArenaConfig | null>(null);
  return (
    <Show
      when={config()}
      fallback={
        <ArenaSetup onStart={(c) => setConfig(c)} onBack={props.onBack} />
      }
    >
      {(cfg) => (
        <ArenaMatch
          config={cfg()}
          onExit={() => setConfig(null)}
          onHome={props.onBack}
        />
      )}
    </Show>
  );
}

// ======================================================================
// 配置页
// ======================================================================
function ArenaSetup(props: {
  onStart: (config: ArenaConfig) => void;
  onBack: () => void;
}) {
  const [mode, setMode] = createSignal<ArenaMode>("pve");
  const [humanSeat, setHumanSeat] = createSignal<0 | 1>(0);
  const [deck0, setDeck0] = createSignal(INIT_DECK0);
  const [deck1, setDeck1] = createSignal(INIT_DECK1);
  // 思考型模型一手实测 20–70s，旧默认 45s 会把一半回合逼进超时兜底。
  // 现在默认 300s、上限 600s（桥接自身也是 600s 封顶），等于"够用就别限时"；
  // 真超时也不会再替你结束回合（driver/lib/policy.mjs 与 rpc_build 的兜底都改了）。
  const [actionSeconds, setActionSeconds] = createSignal(300);
  const [initSeconds, setInitSeconds] = createSignal(120);
  const [matchMinutes, setMatchMinutes] = createSignal(0);
  const [maxRounds, setMaxRounds] = createSignal(15);
  const [stall0, setStall0] = createSignal(false);
  const [stall1, setStall1] = createSignal(false);
  const [instant0, setInstant0] = createSignal(false);
  let autoTouched = false;
  const [instant1, setInstant1] = createSignal(false);
  const [autoPopup, setAutoPopup] = createSignal(true);
  // EvE 时默认不弹"对方棋盘"（两个 AI 座位都在主窗口看；要看另一侧随时点按钮）
  createEffect(() => {
    if (mode() === "eve") setAutoPopup(false);
    else if (!autoTouched) setAutoPopup(true);
  });
  const [status, setStatus] = createSignal<ArenaStatus | null>(null);
  const [prepare, setPrepare] = createSignal<ArenaPrepare | null>(null);
  const [error, setError] = createSignal("");

  const preset = createMemo(() => activePreset());

  onMount(() => {
    let alive = true;
    const refresh = async () => {
      const s = await getStatus();
      if (alive) setStatus(s);
      const p = await getPrepare();
      if (alive) setPrepare(p);
    };
    void refresh();
    const handle = setInterval(refresh, 2000);
    onCleanup(() => {
      alive = false;
      clearInterval(handle);
    });
  });

  function isHuman(who: 0 | 1): boolean {
    return mode() === "pvp" || (mode() === "pve" && who === humanSeat());
  }

  function agentDeck(seat: 0 | 1): any | null {
    return prepare()?.decks?.[seat] ?? null;
  }

  function effectiveDeckData(seat: 0 | 1): ArenaDeckData | undefined {
    if (mode() === "pvp" || isHuman(seat)) return undefined;
    const deck = agentDeck(seat);
    if (!deck) return undefined;
    return {
      characters: deck.characters,
      cards: deck.cards,
      name: deck.name,
    };
  }

  let autoStarted = false;
  createEffect(() => {
    if (autoStarted || mode() === "pvp") return;
    if (!prepare()?.startRequested) return;
    const aiSeats = ([0, 1] as const).filter((w) => !isHuman(w));
    if (!aiSeats.every((w) => !!effectiveDeckData(w))) return;
    autoStarted = true;
    start();
  });

  function start() {
    setError("");
    const deck0Data = effectiveDeckData(0);
    const deck1Data = effectiveDeckData(1);
    try {
      // 提前校验牌组码；agent 已选牌组的座位直接使用其数组
      if (!deck0Data) DEFAULT_ASSETS_MANAGER.decode(deck0());
      if (!deck1Data) DEFAULT_ASSETS_MANAGER.decode(deck1());
    } catch (e) {
      setError(`牌组码无法解析：${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    props.onStart({
      mode: mode(),
      humanSeat: humanSeat(),
      deck0: deck0(),
      deck1: deck1(),
      deck0Data,
      deck1Data,
      version: CURRENT_VERSION,
      preset: preset(),
      actionSeconds: Math.min(600, Math.max(5, actionSeconds())),
      initSeconds: Math.min(600, Math.max(5, initSeconds())),
      matchMinutes: Math.max(0, matchMinutes()),
      stall: [stall0(), stall1()],
      instant: [instant0(), instant1()],
      openPopup: autoPopup(),
      maxRounds: Math.min(100, Math.max(5, maxRounds())),
    });
  }

  async function copyAgentInfo() {
    const text = [
      `座位 0（先手） SSE: ${agentStreamUrl(0)}`,
      `座位 1（后手） SSE: ${agentStreamUrl(1)}`,
      `状态: ${location.origin}/api/arena/state?seat=0`,
      `响应: POST ${location.origin}/api/arena/agent/respond  {"id":..., "response":...}`,
    ].join("\n");
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* ignore */
    }
  }

  return (
    <div class="arena arena-setup">
      <header class="arena-header">
        <div>
          <h1>AI 对战场</h1>
          <div class="arena-sub">
            和本地模拟一样：主窗口一个棋盘，另一个棋盘在弹窗；AI 的棋盘窗口始终可见，人类可以围观。外部 agent 通过本地 HTTP/SSE 接入。
          </div>
        </div>
        <div class="arena-header-actions">
          <button class="arena-btn" onClick={props.onBack}>返回首页</button>
          <button class="arena-btn arena-primary" onClick={start}>开始对局</button>
        </div>
      </header>

      <section class="arena-panel">
        <h2>对局模式</h2>
        <div class="arena-modes">
          <For each={["pvp", "pve", "eve"] as ArenaMode[]}>
            {(m) => (
              <button
                class="arena-mode"
                classList={{ active: mode() === m }}
                onClick={() => setMode(m)}
              >
                <b>{MODE_LABEL[m]}</b>
                <span>
                  {m === "pvp"
                    ? "AI 关闭，两边都用棋盘操作"
                    : m === "pve"
                      ? "一个真人座位 + 一个 AI 座位"
                      : "两边都是 AI，适合演示和评测"}
                </span>
              </button>
            )}
          </For>
        </div>
        <Show when={mode() === "pve"}>
          <div class="arena-row">
            <label>真人坐哪边</label>
            <div class="arena-seg">
              <button classList={{ active: humanSeat() === 0 }} onClick={() => setHumanSeat(0)}>
                先手（座位 0）
              </button>
              <button classList={{ active: humanSeat() === 1 }} onClick={() => setHumanSeat(1)}>
                后手（座位 1）
              </button>
            </div>
          </div>
        </Show>
        <div class="arena-row arena-role-preview">
          <span classList={{ ai: !isHuman(0) }}>座位 0（先手）：{isHuman(0) ? "玩家" : "AI"}</span>
          <span classList={{ ai: !isHuman(1) }}>座位 1（后手）：{isHuman(1) ? "玩家" : "AI"}</span>
        </div>
      </section>

      <section class="arena-panel">
        <h2>牌组</h2>
        <div class="arena-field">
          <label>先手牌组码</label>
          <input class="arena-input" value={deck0()} onInput={(e) => setDeck0(e.currentTarget.value)} />
        </div>
        <div class="arena-field">
          <label>后手牌组码</label>
          <input class="arena-input" value={deck1()} onInput={(e) => setDeck1(e.currentTarget.value)} />
        </div>
        <div class="arena-hint">
          数值预设：<b>{preset().name}</b>
          {preset().builtin ? "" : `（${countPresetChanges(preset())} 项改动）`}
          。想改数值请先回首页进「数值工坊」；这里会使用当前启用的预设。
        </div>
        <div class="arena-agent-decks">
          <For each={[0, 1] as const}>
            {(w) => (
              <div class="arena-agent-deck" classList={{ ai: !isHuman(w) }}>
                <b>座位 {w}（{seatName(w)}）</b>
                <Show
                  when={isHuman(w)}
                  fallback={
                    <Show
                      when={agentDeck(w)}
                      fallback={
                        <span class="arena-hint">
                          等待 agent 选牌组（GET /api/arena/decks → POST /api/arena/deck）
                        </span>
                      }
                    >
                      {(deck) => (
                        <span>
                          AI 已选：<b>{deck().name}</b> · {deck().characters.length} 角色 /{" "}
                          {deck().cards.length} 张牌
                          {deck().note ? <i class="arena-deck-note">「{deck().note}」</i> : null}
                        </span>
                      )}
                    </Show>
                  }
                >
                  <span class="arena-hint">玩家：使用上方牌组码</span>
                </Show>
              </div>
            )}
          </For>
          <Show when={prepare()?.startRequested}>
            <div class="arena-hint">
              agent 已请求开局；AI 座位牌组就绪后会自动开始。也可以直接点右上角「开始对局」。
            </div>
          </Show>
        </div>
      </section>

      <section class="arena-panel">
        <h2>计时与耗时间</h2>
        <div class="arena-grid3">
          <div class="arena-field">
            <label>每次行动限时（秒）</label>
            <input
              class="arena-input"
              type="number"
              min="5"
              max="600"
              value={actionSeconds()}
              onInput={(e) => setActionSeconds(Number(e.currentTarget.value))}
            />
          </div>
          <div class="arena-field">
            <label>开局/选人/重投限时（秒）</label>
            <input
              class="arena-input"
              type="number"
              min="5"
              max="600"
              value={initSeconds()}
              onInput={(e) => setInitSeconds(Number(e.currentTarget.value))}
            />
          </div>
          <div class="arena-field">
            <label>整局上限（分钟，0=不限）</label>
            <input
              class="arena-input"
              type="number"
              min="0"
              max="120"
              value={matchMinutes()}
              onInput={(e) => setMatchMinutes(Number(e.currentTarget.value))}
            />
          </div>
          <div class="arena-field">
            <label>回合上限</label>
            <input
              class="arena-input"
              type="number"
              min="5"
              max="100"
              value={maxRounds()}
              onInput={(e) => setMaxRounds(Number(e.currentTarget.value))}
            />
          </div>
        </div>
        <div class="arena-row">
          <label>座位 0 烧绳</label>
          <input type="checkbox" checked={stall0()} onChange={(e) => setStall0(e.currentTarget.checked)} />
          <label>座位 1 烧绳</label>
          <input type="checkbox" checked={stall1()} onChange={(e) => setStall1(e.currentTarget.checked)} />
          <span class="arena-hint">
            烧绳＝这一手不提交，等计时真的归零，再由引擎用默认响应（出招回合＝结束本回合，剩余骰子作废）。
            七圣召唤的规则里没有"跳过这一次行动、还留在本回合继续打"这种选项：不行动就只有结束回合；
            想留着回合继续打，只能出牌、用技能、切人或元素调和。
          </span>
        </div>
        <div class="arena-row">
          <label>座位 0 让过</label>
          <input type="checkbox" checked={instant0()} onChange={(e) => setInstant0(e.currentTarget.checked)} />
          <label>座位 1 让过</label>
          <input type="checkbox" checked={instant1()} onChange={(e) => setInstant1(e.currentTarget.checked)} />
          <label>开局自动打开对方棋盘</label>
          <input
            type="checkbox"
            checked={autoPopup()}
            onChange={(e) => {
              autoTouched = true;
              setAutoPopup(e.currentTarget.checked);
            }}
          />
          <span class="arena-hint">
            EvE 建议关掉自动弹窗，只保留主窗口；需要看两边时随时点「打开对方棋盘」。
          </span>
        </div>
        <div class="arena-hint">
          整局上限到达后会按双方剩余生命比例判定；0 表示不限制。LLM 思考慢可以把行动限时调大。
          回合上限默认 15（官方规则）；调大后对局不会因"到最大轮数"而判平，AI 对局建议 30。
        </div>
      </section>

      <section class="arena-panel">
        <h2>Agent 接入</h2>
        <div class="arena-agent-grid">
          <div>
            <div class="arena-agent-title">座位 0（先手）</div>
            <code>GET {agentStreamUrl(0)}</code>
            <div class="arena-agent-state">
              {status()?.agents[0] ? `已连接 ${status()?.agents[0]} 个 agent` : "等待 agent 连接…"}
            </div>
          </div>
          <div>
            <div class="arena-agent-title">座位 1（后手）</div>
            <code>GET {agentStreamUrl(1)}</code>
            <div class="arena-agent-state">
              {status()?.agents[1] ? `已连接 ${status()?.agents[1]} 个 agent` : "等待 agent 连接…"}
            </div>
          </div>
        </div>
        <div class="arena-row">
          <button class="arena-btn" onClick={copyAgentInfo}>复制接口信息</button>
          <span class="arena-hint">
            接口说明见 local/arena/API.md；示例 agent 见 local/arena/example-agent.mjs。
          </span>
        </div>
      </section>

      <Show when={error()}>
        <div class="arena-error">{error()}</div>
      </Show>
    </div>
  );
}

// ======================================================================
// 对局页（和本地模拟一致：主窗口一个棋盘 + 弹窗另一个棋盘）
// ======================================================================
interface LogEntry {
  time: number;
  who: 0 | 1 | null;
  text: string;
}

interface PopupChannel {
  notify: (n: unknown) => void;
  rpc: (req: RpcRequest) => Promise<RpcResponse>;
  cancelRpc: () => void;
  sendTimer: (t: SeatTimer | null) => void;
  close: () => void;
  window: Window;
}

function waitForPopupInit(win: Window): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error("棋盘弹窗加载超时"));
    }, 15000);
    const handler = (e: MessageEvent) => {
      if (e.source !== win) return;
      const data = e.data;
      if (typeof data !== "object" || data === null) return;
      if (data.giTcg !== "1.0" || data.method !== "initialized") return;
      cleanup();
      resolve();
    };
    const cleanup = () => {
      clearTimeout(timeout);
      window.removeEventListener("message", handler);
    };
    window.addEventListener("message", handler);
  });
}

function createPopupChannel(win: Window, onGiveUp: () => void): PopupChannel {
  const pending = new Map<string, (res: RpcResponse) => void>();
  const handler = (e: MessageEvent) => {
    if (e.source !== win) return;
    const data = e.data;
    if (typeof data !== "object" || data === null) return;
    if (data.giTcg !== "1.0") return;
    if (data.method === "giveUp") {
      onGiveUp();
      return;
    }
    if (data.method !== "rpc" || !data.id) return;
    const resolve = pending.get(data.id);
    if (resolve) {
      pending.delete(data.id);
      resolve(data.result as RpcResponse);
    }
  };
  window.addEventListener("message", handler);

  const post = (payload: unknown) => {
    try {
      win.postMessage(payload, "*");
    } catch {
      /* window may be closed */
    }
  };

  return {
    notify: (n) => post({ giTcg: "1.0", method: "notify", params: [n] }),
    rpc: (req) =>
      new Promise<RpcResponse>((resolve) => {
        const id = Math.random().toString(36).slice(2);
        pending.set(id, resolve);
        post({ giTcg: "1.0", method: "rpc", params: [req], id });
      }),
    cancelRpc: () => post({ giTcg: "1.0", method: "cancelRpc" }),
    sendTimer: (timer) => post({ giTcg: "1.0", method: "timer", timer }),
    close: () => {
      window.removeEventListener("message", handler);
      if (!win.closed) win.close();
    },
    window: win,
  };
}

function ArenaMatch(props: {
  config: ArenaConfig;
  onExit: () => void;
  onHome: () => void;
}) {
  const cfg = props.config;
  const matchId = "local";
  const mainSeat: 0 | 1 = cfg.mode === "pve" ? cfg.humanSeat : 1;
  const popupSeat: 0 | 1 = (1 - mainSeat) as 0 | 1;

  function isHuman(who: 0 | 1): boolean {
    return cfg.mode === "pvp" || (cfg.mode === "pve" && who === cfg.humanSeat);
  }

  let game: Game | null = null;
  const [mainIo, MainBoard] = createClient(mainSeat, {
    disableAction: !isHuman(mainSeat),
    onGiveUp: isHuman(mainSeat) ? () => game?.giveUp(mainSeat) : undefined,
  });

  const [timer0, setTimer0] = createSignal<SeatTimer | null>(null);
  const [timer1, setTimer1] = createSignal<SeatTimer | null>(null);
  const [stall0, setStall0] = createSignal(cfg.stall[0]);
  const [stall1, setStall1] = createSignal(cfg.stall[1]);
  const [instant0, setInstant0] = createSignal(cfg.instant?.[0] ?? false);
  const [instant1, setInstant1] = createSignal(cfg.instant?.[1] ?? false);
  // 待答的「过一手」快捷按钮：人类座位有请求时出现，点一下直接把行动权交给对手
  const [passPrompt, setPassPrompt] = createSignal<{
    seat: 0 | 1;
    index: number;
    respond: (res: RpcResponse) => void;
  } | null>(null);
  function passActionIndex(req: RpcRequest): number {
    if (req.request?.$case !== "action") return -1;
    const actions = (req.request.value as any)?.action ?? [];
    return actions.findIndex(
      (a: any) => a?.action?.$case === "passAction" && (a?.validity ?? -1) === 0,
    );
  }
  const [logs, setLogs] = createSignal<LogEntry[]>([]);
  const [elapsed, setElapsed] = createSignal(0);
  const [round, setRound] = createSignal(0);
  const [turn, setTurn] = createSignal<0 | 1>(0);
  const [winner, setWinner] = createSignal<string | null>(null);
  const [error, setError] = createSignal("");
  const [status, setStatus] = createSignal<ArenaStatus | null>(null);
  const [started, setStarted] = createSignal(false);
  const [popupChannel, setPopupChannel] = createSignal<PopupChannel | null>(null);
  const [popupState, setPopupState] = createSignal<
    "idle" | "opening" | "open" | "closed" | "error"
  >("idle");

  const lastNotify: [unknown, unknown] = [null, null];
  let finished = false;
  let logRef!: HTMLDivElement;
  let popupWatch: number | undefined;

  const timerOf = (who: 0 | 1) => (who === 0 ? timer0 : timer1);
  const setTimerOf = (who: 0 | 1, t: SeatTimer | null) =>
    who === 0 ? setTimer0(t) : setTimer1(t);
  const stallOf = (who: 0 | 1) => (who === 0 ? stall0 : stall1);
  const setStallOf = (who: 0 | 1, v: boolean) =>
    who === 0 ? setStall0(v) : setStall1(v);
  const instantOf = (who: 0 | 1) => (who === 0 ? instant0 : instant1);
  // 对局配置快照：既随每次决策发给桥接，也在开局时先推一次，让 agent 提前知情
  const configSnapshot = () => ({
    mode: cfg.mode,
    humanSeat: cfg.humanSeat,
    actionSeconds: cfg.actionSeconds,
    initSeconds: cfg.initSeconds,
    matchMinutes: cfg.matchMinutes,
    maxRounds: cfg.maxRounds ?? 15,
    stall: [stall0(), stall1()],
    instant: [instant0(), instant1()],
  });

  function addLog(who: 0 | 1 | null, text: string, kind = "action", detail?: unknown) {
    setLogs((prev) => [...prev.slice(-199), { time: Date.now(), who, text }]);
    pushLog({ text, seat: who, kind, detail }, matchId);
    queueMicrotask(() => {
      if (logRef) logRef.scrollTop = logRef.scrollHeight;
    });
  }

  function totalSeconds(req: RpcRequest): number {
    const c = req.request?.$case;
    return c === "action" || c === "selectCard" ? cfg.actionSeconds : cfg.initSeconds;
  }

  function seatIoOptions(who: 0 | 1, clientIo: unknown) {
    return {
      who,
      matchId,
      clientIo: clientIo as any,
      totalSeconds,
      stall: () => stallOf(who)(),
      instant: () => instantOf(who)(),
      config: configSnapshot,
      onTimer: (t: SeatTimer | null) => {
        setTimerOf(who, t);
        if (who === popupSeat) popupChannel()?.sendTimer(t);
      },
      onLog: (text: string, detail?: unknown) => addLog(who, text, "info", detail),
      onDecision: (req: RpcRequest, res: RpcResponse, state: unknown) => {
        addLog(who, describeDecision(req, res), "decision", { req, res });
        // 决策是公开信息：双方 agent 都能看到对手出了什么
        pushAiEvents(matchId, null, [decisionEvent(req, res, who, state)]);
      },
      onAiEvents: (events: any[]) => {
        pushAiEvents(matchId, who, events);
      },
      onNotify: (n: unknown) => {
        lastNotify[who] = n;
      },
      onPendingRequest: (req: RpcRequest, respond: (res: RpcResponse) => void) => {
        const index = passActionIndex(req);
        setPassPrompt(index >= 0 ? { seat: who, index, respond } : null);
      },
      onPendingSettled: () => setPassPrompt(null),
    };
  }

  async function openPopup(): Promise<PopupChannel> {
    const existing = popupChannel();
    if (existing && !existing.window.closed) return existing;
    const url = `${location.origin}${location.pathname}${location.search}#arena-board-${popupSeat}-${
      isHuman(popupSeat) ? "human" : "ai"
    }`;
    const win = window.open(
      url,
      "gi-tcg-arena-board",
      "popup=yes,height=980,width=1500",
    );
    if (!win) {
      setPopupState("error");
      throw new Error(
        "浏览器阻止了棋盘弹窗。请允许本站使用弹出式窗口，然后点“打开对方棋盘”。",
      );
    }
    setPopupState("opening");
    await waitForPopupInit(win);
    const channel = createPopupChannel(win, () => game?.giveUp(popupSeat));
    setPopupChannel(channel);
    setPopupState("open");
    if (lastNotify[popupSeat]) channel.notify(lastNotify[popupSeat]);
    if (popupWatch !== undefined) clearInterval(popupWatch);
    popupWatch = setInterval(() => {
      if (!win.closed) return;
      clearInterval(popupWatch);
      popupWatch = undefined;
      setPopupState("closed");
      if (isHuman(popupSeat)) {
        addLog(popupSeat, "玩家关闭了棋盘窗口，判负", "system");
        game?.giveUp(popupSeat);
      } else {
        addLog(null, "棋盘窗口被关闭；对局继续，可点“打开对方棋盘”重新显示", "system");
      }
    }, 700);
    return channel;
  }

  async function startMatch() {
    try {
      await resetMatch(matchId);
      pushConfig(configSnapshot(), matchId);
      // 弹窗只是"另一个视角"的显示器；EvE 默认不开，避免误以为两个棋盘进度不同步
      const mustPopup = isHuman(popupSeat);
      const channel =
        mustPopup || (cfg.openPopup ?? true) ? await openPopup() : null;
      if (!channel) {
        setPopupState("closed");
      }
      const deck0 = cfg.deck0Data
        ? { characters: cfg.deck0Data.characters, cards: cfg.deck0Data.cards }
        : DEFAULT_ASSETS_MANAGER.decode(cfg.deck0);
      const deck1 = cfg.deck1Data
        ? { characters: cfg.deck1Data.characters, cards: cfg.deck1Data.cards }
        : DEFAULT_ASSETS_MANAGER.decode(cfg.deck1);
      const data = applyPresetToData(getData(cfg.version), cfg.preset);
      installPreset(cfg.preset);
      const state = Game.createInitialState({
        decks: [deck0, deck1],
        data,
        versionBehavior: cfg.version,
        // 回合上限默认 15；对战场允许调大，避免 AI 对局总是"到最大轮数判平局"
        ...(cfg.maxRounds ? { maxRoundsCount: cfg.maxRounds } : {}),
      });
      const g = new Game(state);
      game = g;
      (window as any).theGame = g;
      g.onPause = async () => {};
      g.onIoError = (e: any) => {
        addLog(
          e?.who === 0 || e?.who === 1 ? e.who : null,
          `座位 ${e?.who} 提交了非法行动，判负（${e?.message ?? e}）`,
          "error",
        );
      };

      const mainOptions = seatIoOptions(mainSeat, mainIo);
      g.players[mainSeat].io = isHuman(mainSeat)
        ? createHumanSeatIo(mainOptions)
        : createAgentSeatIo({
            ...mainOptions,
            hidePrivateInfo: cfg.mode === "pve",
          });

      const silentIo = {
        notify: () => {},
        rpc: async () => ({ response: {} }) as any,
        cancelRpc: () => {},
      };
      const popupOptions = seatIoOptions(popupSeat, channel ?? silentIo);
      g.players[popupSeat].io = isHuman(popupSeat)
        ? createHumanSeatIo(popupOptions)
        : createAgentSeatIo({
            ...popupOptions,
            hidePrivateInfo: cfg.mode === "pve",
          });

      setStarted(true);
      addLog(
        null,
        `对局开始：${MODE_LABEL[cfg.mode]}；主窗口=${seatName(mainSeat)}，弹窗=${seatName(popupSeat)}；数值预设“${cfg.preset?.name ?? "原版"}”；回合上限=${cfg.maxRounds ?? 15}`,
        "system",
      );
      if (cfg.mode !== "pvp") {
        addLog(
          null,
          `AI 座位：${[mainSeat, popupSeat]
            .filter((w) => !isHuman(w))
            .map((w) => seatName(w))
            .join("、")}；等待外部 agent 接入`,
          "system",
        );
      }

      const result = await g.start();
      if (finished) return;
      finished = true;
      const text =
        result === null
          ? "平局（达到最大轮数）"
          : `${seatName(result)}（${isHuman(result) ? "玩家" : "AI"}）获胜`;
      setWinner(text);
      addLog(null, `对局结束：${text}`, "system");
    } catch (e) {
      if (finished) return;
      finished = true;
      const message = e instanceof Error ? e.message : String(e);
      setError(message);
      addLog(null, `对局出错：${message}`, "error");
    }
  }

  function finishByTime() {
    if (!game || finished) return;
    finished = true;
    const ratio = (who: 0 | 1) => {
      const chars = game!.state.players[who].characters;
      const total = chars.reduce((a, c) => a + Math.max(0, c.variables.health), 0);
      const max = chars.reduce((a, c) => a + Math.max(0, c.variables.maxHealth), 0);
      return max > 0 ? total / max : 0;
    };
    const r0 = ratio(0);
    const r1 = ratio(1);
    const text =
      Math.abs(r0 - r1) < 1e-9
        ? "时间到：双方生命比例相同，平局"
        : `时间到：${seatName(r0 > r1 ? 0 : 1)}剩余生命比例更高，判胜`;
    setWinner(text);
    addLog(null, text, "system");
    game.terminate();
  }

  function stopMatch() {
    if (!game || finished) return;
    finished = true;
    game.terminate();
    setWinner("已手动停止");
    addLog(null, "已手动停止对局", "system");
  }

  async function reopenPopup() {
    try {
      const channel = await openPopup();
      if (lastNotify[popupSeat]) channel.notify(lastNotify[popupSeat]);
      if (timerOf(popupSeat)()) channel.sendTimer(timerOf(popupSeat)());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function resyncPopup() {
    const ch = popupChannel();
    if (!ch || !lastNotify[popupSeat]) {
      addLog(null, "还没有可同步的棋盘：先点「打开对方棋盘」", "system");
      return;
    }
    ch.notify(lastNotify[popupSeat]);
    if (timerOf(popupSeat)()) ch.sendTimer(timerOf(popupSeat)());
    addLog(null, "已把最新局面重新推给弹窗棋盘（弹窗卡住时用它）", "system");
  }

  onMount(() => {
    void startMatch();
    const startedAt = Date.now();
    const clock = setInterval(() => {
      const sec = Math.floor((Date.now() - startedAt) / 1000);
      setElapsed(sec);
      if (game) {
        setRound(game.state.roundNumber);
        setTurn(game.state.currentTurn as 0 | 1);
      }
      if (cfg.matchMinutes > 0 && sec >= cfg.matchMinutes * 60) {
        finishByTime();
      }
    }, 1000);
    let alive = true;
    const statusTimer = setInterval(async () => {
      const s = await getStatus(matchId);
      if (alive) setStatus(s);
    }, 2000);
    onCleanup(() => {
      alive = false;
      clearInterval(clock);
      clearInterval(statusTimer);
      if (popupWatch !== undefined) clearInterval(popupWatch);
      finished = true;
      game?.terminate();
      popupChannel()?.close();
      installPreset(null);
    });
  });

  const popupLabel = () => {
    switch (popupState()) {
      case "open":
        return "已打开";
      case "opening":
        return "打开中…";
      case "closed":
        return "已关闭";
      case "error":
        return "打开失败";
      default:
        return "未打开";
    }
  };

  return (
    <div class="arena arena-match">
      <header class="arena-header arena-match-header">
        <div>
          <h1>
            AI 对战场 · {MODE_LABEL[cfg.mode]}
            <span class="arena-live">● LIVE</span>
          </h1>
          <div class="arena-sub">
            主窗口：{seatName(mainSeat)}（{isHuman(mainSeat) ? "玩家" : "AI"}）· 弹窗：
            {seatName(popupSeat)}（{isHuman(popupSeat) ? "玩家" : "AI"}）· 第 {round()} 轮 ·
            当前 {seatName(turn())}方 · 已用时 {fmt(elapsed())}
            {cfg.matchMinutes > 0 ? ` / ${cfg.matchMinutes}:00` : "（不限时）"}
            <span class="arena-popup-state"> · 棋盘窗口：{popupLabel()}</span>
            <Show when={status()}>
              {(s) => (
                <span class="arena-agent-live">
                  {" "}· agent：座位0 {s().agents[0]} / 座位1 {s().agents[1]}
                </span>
              )}
            </Show>
          </div>
        </div>
        <div class="arena-header-actions">
          <button class="arena-btn" onClick={reopenPopup}>打开对方棋盘</button>
          <button class="arena-btn" onClick={resyncPopup}>重新同步棋盘</button>
          <button class="arena-btn arena-danger" onClick={stopMatch}>停止</button>
          <button class="arena-btn" onClick={props.onExit}>返回配置</button>
          <button class="arena-btn" onClick={props.onHome}>返回首页</button>
        </div>
      </header>

      <div class="arena-controls">
        <label class="arena-stall">
          <input
            type="checkbox"
            checked={stall0()}
            onChange={(e) => setStall0(e.currentTarget.checked)}
          />
          座位 0 烧绳
        </label>
        <label class="arena-stall">
          <input
            type="checkbox"
            checked={stall1()}
            onChange={(e) => setStall1(e.currentTarget.checked)}
          />
          座位 1 烧绳
        </label>
        <label class="arena-stall">
          <input
            type="checkbox"
            checked={instant0()}
            onChange={(e) => setInstant0(e.currentTarget.checked)}
          />
          座位 0 立刻用默认响应
        </label>
        <label class="arena-stall">
          <input
            type="checkbox"
            checked={instant1()}
            onChange={(e) => setInstant1(e.currentTarget.checked)}
          />
          座位 1 立刻用默认响应
        </label>
        <Show when={passPrompt()}>
          {(p) => (
            <button
              class="arena-btn arena-primary"
              onClick={() => {
                const cur = p();
                setPassPrompt(null);
                cur.respond({
                  response: {
                    $case: "action",
                    value: { chosenActionIndex: cur.index, usedDice: [] },
                  },
                } as RpcResponse);
              }}
            >
              过一手（交出行动权）
            </button>
          )}
        </Show>
        <span class="arena-hint">
          烧绳＝不提交、等计时归零后由引擎用默认响应；立刻＝马上用默认响应，不等计时。
          默认响应现在会优先「过一手」（把行动权交给对手，什么都不消耗），过不了才结束本回合，
          所以烧绳不会再替你把整回合的骰子作废。轮到你时也可以直接点「过一手」。
        </span>
      </div>

      <div class="arena-main">
        <div class="arena-board-head">
          <span class="arena-board-title">
            {seatName(mainSeat)}（座位 {mainSeat}）·{" "}
            <b classList={{ ai: !isHuman(mainSeat) }}>
              {isHuman(mainSeat) ? "玩家" : "AI"}
            </b>
          </span>
          <Show when={timerOf(mainSeat)()}>
            {(t) => (
              <span class="arena-timer" classList={{ urgent: t().current <= 5 }}>
                {t().current}s / {t().total}s
              </span>
            )}
          </Show>
          <Show when={!isHuman(mainSeat)}>
            <span class="arena-chip">观战视角</span>
          </Show>
          <span class="arena-spacer" />
          <span class="arena-hint">
            另一个棋盘在弹窗里；弹窗被拦截时点右上角「打开对方棋盘」。
          </span>
        </div>
        <div class="standalone-board-shell arena-main-board">
          <MainBoard
            spectatorMode={!isHuman(mainSeat)}
            timer={timerOf(mainSeat)()}
          />
        </div>
        <Show when={!isHuman(mainSeat) && cfg.mode === "pve"}>
          <div class="arena-board-note">
            PvE 模式：AI 的手牌和骰子已隐藏，只看它的操作和场面。
          </div>
        </Show>
      </div>

      <div class="arena-log-wrap">
        <div class="arena-log-head">
          <b>实时操作日志</b>
          <span class="arena-hint">AI 的每一次选择都会记在这里；观众不用打开控制台。</span>
        </div>
        <div class="arena-log" ref={logRef!}>
          <For each={logs()}>
            {(entry) => (
              <div class="arena-log-line" classList={{ [`log-${entry.who}`]: entry.who !== null }}>
                <span class="arena-log-time">
                  {fmt(Math.floor((entry.time - (logs()[0]?.time ?? entry.time)) / 1000))}
                </span>
                <span class="arena-log-who">
                  {entry.who === null
                    ? "系统"
                    : `${seatName(entry.who)}${isHuman(entry.who) ? "·玩家" : "·AI"}`}
                </span>
                <span>{entry.text}</span>
              </div>
            )}
          </For>
          <Show when={logs().length === 0}>
            <div class="arena-hint">等待对局开始…</div>
          </Show>
        </div>
      </div>

      <Show when={error()}>
        <div class="arena-error">{error()}</div>
      </Show>

      <Show when={winner()}>
        {(w) => (
          <div class="arena-overlay">
            <div class="arena-result">
              <h2>{w()}</h2>
              <div class="arena-hint">{started() ? "本局已结束。" : "对局尚未开始。"}</div>
              <div class="arena-row">
                <button class="arena-btn arena-primary" onClick={props.onExit}>再来一局</button>
                <button class="arena-btn" onClick={props.onHome}>返回首页</button>
              </div>
            </div>
          </div>
        )}
      </Show>
    </div>
  );
}
