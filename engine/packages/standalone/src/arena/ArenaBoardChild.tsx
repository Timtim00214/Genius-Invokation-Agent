/**
 * AI 对战 / 本地对战弹出的第二个棋盘窗口。
 *
 * 和上游本地模拟的“先手弹窗”一致：一个窗口只负责显示一个座位。
 * 父窗口通过 postMessage 送 notify / rpc / timer。
 */
import { createClient } from "@gi-tcg/web-ui-core";
import "@gi-tcg/web-ui-core/style.css";
import { createSignal, onCleanup, onMount } from "solid-js";
import type { SeatTimer } from "./seatIo";
import "./arena.css";

export function ArenaBoardChild(props: { who: 0 | 1; human: boolean }) {
  const [uiIo, Board] = createClient(props.who, {
    disableAction: !props.human,
    onGiveUp: props.human
      ? () => {
          window.opener?.postMessage(
            { giTcg: "1.0", method: "giveUp" },
            "*",
          );
        }
      : undefined,
  });
  const [timer, setTimer] = createSignal<SeatTimer | null>(null);

  async function messageHandler(e: MessageEvent) {
    const { data } = e;
    if (typeof data !== "object" || data === null) return;
    if (data.giTcg !== "1.0") return;
    if (!("method" in data)) return;
    switch (data.method) {
      case "notify": {
        if (!Array.isArray(data.params)) return;
        uiIo.notify(...(data.params as [any]));
        break;
      }
      case "rpc": {
        if (!Array.isArray(data.params)) return;
        const result = await uiIo.rpc(...(data.params as [any]));
        (e.source as WindowProxy)?.postMessage(
          {
            giTcg: "1.0",
            method: "rpc",
            result: JSON.parse(JSON.stringify(result)),
            id: data.id,
          },
          "*",
        );
        break;
      }
      case "cancelRpc": {
        uiIo.cancelRpc();
        break;
      }
      case "timer": {
        setTimer(data.timer ?? null);
        break;
      }
    }
  }

  onMount(() => {
    window.addEventListener("message", messageHandler);
    window.opener?.postMessage({ giTcg: "1.0", method: "initialized" }, "*");
  });
  onCleanup(() => {
    window.removeEventListener("message", messageHandler);
  });

  return (
    <div class="arena-popup">
      <div class="arena-popup-head">
        <span class="arena-popup-title">
          {props.who === 0 ? "先手" : "后手"}（座位 {props.who}）·{" "}
          <b classList={{ ai: !props.human }}>{props.human ? "玩家" : "AI"}</b> 视角
        </span>
        <span class="arena-hint">
          {props.human ? "这个窗口是你的操作棋盘" : "AI 的棋盘窗口，实时显示它的对局画面"}
        </span>
        <span class="arena-spacer" />
        <span class="arena-hint">关闭此窗口会隐藏这一侧棋盘（PvP 中视为该方退出）</span>
      </div>
      <div class="arena-popup-board">
        <div class="standalone-board-shell">
          <Board spectatorMode={!props.human} timer={timer()} />
        </div>
      </div>
    </div>
  );
}
