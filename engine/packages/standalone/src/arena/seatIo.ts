/**
 * 把 web-ui-core 的棋盘客户端、本地桥接服务包装成一个 PlayerIO。
 *
 * - 人类座位：用棋盘 UI，外面套一层计时器；
 * - AI 座位：把 rpc 转发给外部 agent，棋盘只用来“看”（disableAction）。
 */
import type { CancellablePlayerIO, RpcRequest, RpcResponse } from "@gi-tcg/core";
import type { WebUiPlayerIO } from "@gi-tcg/web-ui-core";
import { defaultRpcResponse, sameRpcCase } from "./defaultResponse";
import { flushAiEvents, pushState, requestDecision } from "./bridge";
import { buildSnapshot, mutationEvents, requestEvent } from "./aiEvents";

export interface SeatTimer {
  current: number;
  total: number;
}

export interface SeatIoOptions {
  who: 0 | 1;
  matchId: string;
  /** 棋盘的 IO（用于渲染状态） */
  clientIo: WebUiPlayerIO;
  /** 计时总秒数 */
  totalSeconds: (req: RpcRequest) => number;
  /** 是否“烧绳”：为 true 时不提交行动，等计时归零后由默认响应接管（默认会先“过一手”，不结束回合） */
  stall: () => boolean;
  /** 立刻用默认响应：不等待，直接交出这一手（人类座位优先“过一手”，不结束回合） */
  instant?: () => boolean;
  /** 把对局配置透给桥接，让外部 agent 能知道限时/开关（目前只用来说明显式信息） */
  config?: () => unknown;
  onTimer: (timer: SeatTimer | null) => void;
  onLog: (text: string, detail?: unknown) => void;
  onDecision?: (req: RpcRequest, res: RpcResponse, state: unknown) => void;
  /** 结构化 AI 事件（变化、快照、请求、拒绝等） */
  onAiEvents?: (events: any[]) => void;
  /** 每次收到通知（用于弹窗重开后补发最新画面） */
  onNotify?: (notification: unknown) => void;
  /** PvE 里隐藏 AI 的手牌/骰子，避免人类玩家偷看 */
  hidePrivateInfo?: boolean;
  /** 人类座位：每次有待答请求时回调，允许外部（页面上的按钮）直接作答（用于“过一手”） */
  onPendingRequest?: (req: RpcRequest, respond: (res: RpcResponse) => void) => void;
  /** 待答请求结束（无论谁作答）时回调，用来清理页面按钮 */
  onPendingSettled?: () => void;
}

const TIMEOUT = Symbol("timeout");

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function tickTimer(total: number, started: number, onTimer: (t: SeatTimer | null) => void): () => void {
  const update = () => {
    const left = Math.max(0, total - Math.floor((Date.now() - started) / 1000));
    onTimer({ current: left, total });
  };
  update();
  const handle = setInterval(update, 250);
  return () => {
    clearInterval(handle);
    onTimer(null);
  };
}

function sanitizeNotification(n: any, who: 0 | 1): any {
  if (!n?.state) return n;
  const state = structuredClone(n.state);
  for (const seat of [who]) {
    state.player[seat].handCard = [];
    state.player[seat].dice = [];
    state.player[seat].pileCard = [];
  }
  return { ...n, state };
}

export function createHumanSeatIo(
  opts: SeatIoOptions,
): CancellablePlayerIO {
  let lastState: unknown = null;
  return {
    notify: (n) => {
      lastState = (n as any)?.state ?? lastState;
      opts.clientIo.notify(n);
      opts.onNotify?.(n);
      pushState(opts.who, (n as any)?.state, opts.matchId);
    },
    rpc: async (req: RpcRequest): Promise<RpcResponse> => {
      const total = Math.max(1, opts.totalSeconds(req));
      const started = Date.now();
      const stop = tickTimer(total, started, opts.onTimer);
      try {
        if (opts.instant?.()) {
          opts.onLog("立刻用默认响应：优先「过一手」交出行动权，不结束回合", { rpc: req.request?.$case });
          return defaultRpcResponse(req);
        }
        if (opts.stall()) {
          opts.onLog(`烧绳：等待 ${total} 秒后由默认响应接管（优先「过一手」）`, { rpc: req.request?.$case });
          await sleep(total * 1000);
          return defaultRpcResponse(req);
        }
        let manualAnswer: RpcResponse | null = null;
        const manual = new Promise<RpcResponse | null>((resolve) => {
          opts.onPendingRequest?.(req, (res) => {
            if (manualAnswer) return;
            manualAnswer = res;
            resolve(res);
          });
        });
        const result = await Promise.race([
          opts.clientIo.rpc(req),
          sleep(total * 1000).then(() => TIMEOUT),
          manual,
        ]);
        if (manualAnswer !== null && result === manualAnswer) {
          // 页面上点了“过一手”之类的快捷作答：取消棋盘那边的等待，直接提交
          opts.clientIo.cancelRpc?.();
          opts.onDecision?.(req, manualAnswer, lastState);
          return manualAnswer;
        }
        if (result === TIMEOUT) {
          opts.clientIo.cancelRpc?.();
          opts.onLog(`超时（${total} 秒）→ 默认响应：优先「过一手」，不结束本回合`, { rpc: req.request?.$case });
          return defaultRpcResponse(req);
        }
        opts.onDecision?.(req, result as RpcResponse, lastState);
        return result as RpcResponse;
      } finally {
        stop();
        opts.onPendingSettled?.();
      }
    },
    cancelRpc: () => opts.clientIo.cancelRpc?.(),
  };
}

export function createAgentSeatIo(opts: SeatIoOptions): CancellablePlayerIO {
  let lastState: unknown = null;
  let started = false;
  let lastRound = 0;
  let rerollAttempts = 0;
  return {
    notify: (n) => {
      const state = (n as any)?.state ?? lastState;
      lastState = state;
      const view = opts.hidePrivateInfo
        ? sanitizeNotification(n, opts.who)
        : n;
      opts.clientIo.notify(view);
      opts.onNotify?.(view);
      pushState(opts.who, (n as any)?.state, opts.matchId);

      if (state) {
        const events = mutationEvents(
          { mutation: (n as any)?.mutation, state: (n as any)?.state },
          opts.who,
        );
        // 等初始手牌发下来再发第一份完整快照，避免开局快照是空手牌
        if (!started && (state.player?.[opts.who]?.handCard?.length ?? 0) > 0) {
          started = true;
          events.unshift(buildSnapshot(state, opts.who, "start"));
        }
        const hasRound = events.some((e) => e.type === "round");
        const hasWinner = events.some((e) => e.type === "winner");
        if (
          hasRound ||
          events.some((e) => e.type === "phase" && e.phase === "掷骰")
        ) {
          rerollAttempts = 0;
        }
        if (hasRound || (lastRound !== 0 && state.roundNumber !== lastRound)) {
          events.push(buildSnapshot(state, opts.who, "round_end"));
        }
        if (hasWinner) events.push(buildSnapshot(state, opts.who, "game_end"));
        lastRound = state.roundNumber;
        if (events.length) opts.onAiEvents?.(events);
      }
    },
    rpc: async (req: RpcRequest): Promise<RpcResponse> => {
      const total = Math.max(1, opts.totalSeconds(req));
      const started = Date.now();
      const stop = tickTimer(total, started, opts.onTimer);
      try {
        if (opts.instant?.()) {
          opts.onLog("让过：立刻用默认响应（AI 不做「过一手」，这里会结束本回合）", { rpc: req.request?.$case });
          return defaultRpcResponse(req, { preferPass: false });
        }
        if (opts.stall()) {
          opts.onLog(`耗时间：等待 ${total} 秒后由默认响应接管（AI 会结束本回合）`, { rpc: req.request?.$case });
          await sleep(total * 1000);
          return defaultRpcResponse(req, { preferPass: false });
        }
        opts.onLog(`等待 agent 决策（${req.request?.$case}）`, { rpc: req.request?.$case });
        const attempt = req.request?.$case === "rerollDice" ? rerollAttempts++ : 0;
        const requestAiEvent = requestEvent(
          req,
          lastState,
          opts.who,
          Date.now() + total * 1000,
          attempt,
        );
        await flushAiEvents();
        const result = await requestDecision({
          matchId: opts.matchId,
          seat: opts.who,
          request: req,
          state: lastState,
          timeoutMs: total * 1000 + 1500,
          aiDetail: requestAiEvent,
          config: opts.config?.(),
        });
        if (!result.response || result.timeout) {
          opts.onLog(`agent 未响应（${total} 秒），自动过`, { rpc: req.request?.$case });
          return defaultRpcResponse(req, { preferPass: false });
        }
        if (!sameRpcCase(req, result.response)) {
          opts.onLog(`agent 响应类型不匹配，已忽略并自动过`, { rpc: req.request?.$case });
          return defaultRpcResponse(req, { preferPass: false });
        }
        opts.onDecision?.(req, result.response, lastState);
        return result.response;
      } catch (e) {
        opts.onLog(`agent 桥接失败：${e instanceof Error ? e.message : String(e)}`, { rpc: req.request?.$case });
        return defaultRpcResponse(req, { preferPass: false });
      } finally {
        stop();
      }
    },
    cancelRpc: () => {},
  };
}
