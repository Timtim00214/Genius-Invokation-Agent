import { For, Show, createMemo, createSignal } from "solid-js";
import { diceTotal, normalizeDiceRecord, type DiceRecord } from "@gi-tcg/local-rules";

/** 费用里可用的骰子类型（注意 8 在费用里是“相同元素”，不是万能骰）。 */
export const COST_DICE_ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10] as const;

export const COST_DICE_LABELS: Record<number, string> = {
  0: "任意元素",
  1: "冰",
  2: "水",
  3: "火",
  4: "雷",
  5: "风",
  6: "岩",
  7: "草",
  8: "相同元素",
  9: "充能",
  10: "秘传",
};

export function diceText(dice: DiceRecord | null | undefined): string {
  const entries = Object.entries(normalizeDiceRecord(dice));
  if (!entries.length) return "0";
  return entries
    .sort((a, b) => Number(a[0]) - Number(b[0]))
    .map(([type, count]) => `${count} ${COST_DICE_LABELS[Number(type)] ?? `#${type}`}`)
    .join(" + ");
}

export function DiceChips(props: { dice: DiceRecord | null | undefined; emptyText?: string }) {
  const entries = createMemo(() =>
    Object.entries(normalizeDiceRecord(props.dice)).sort(
      (a, b) => Number(a[0]) - Number(b[0]),
    ),
  );
  return (
    <span class="ws-dice-chips">
      <Show
        when={entries().length > 0}
        fallback={<span class="ws-dice-chip d-empty">{props.emptyText ?? "0"}</span>}
      >
        <For each={entries()}>
          {([type, count]) => (
            <span class={`ws-dice-chip d-${type}`}>
              {count}
              <i>{COST_DICE_LABELS[Number(type)] ?? `#${type}`}</i>
            </span>
          )}
        </For>
      </Show>
    </span>
  );
}

export interface CostEditorProps {
  title: string;
  defaultDice: DiceRecord;
  value: DiceRecord;
  onApply: (dice: DiceRecord) => void;
  onReset: () => void;
  onClose: () => void;
}

export function CostEditor(props: CostEditorProps) {
  const [values, setValues] = createSignal<Record<string, number>>({
    ...props.value,
  });
  const total = createMemo(() => diceTotal(values()));

  function setValue(type: number, raw: string) {
    const n = Math.max(0, Math.min(20, Math.round(Number(raw) || 0)));
    setValues((prev) => ({ ...prev, [String(type)]: n }));
  }

  return (
    <div class="ws-modal-mask" onClick={props.onClose}>
      <div class="ws-modal" onClick={(e) => e.stopPropagation()}>
        <h3>{props.title}</h3>
        <div class="ws-modal-sub">
          默认消耗：<DiceChips dice={props.defaultDice} />
        </div>
        <div class="ws-dice-grid">
          <For each={COST_DICE_ORDER}>
            {(type) => (
              <label class={`ws-dice-row d-${type}`}>
                <span class="ws-dice-name">{COST_DICE_LABELS[type]}</span>
                <input
                  class="ws-input"
                  type="number"
                  min="0"
                  max="20"
                  value={values()[String(type)] ?? 0}
                  onInput={(e) => setValue(type, e.currentTarget.value)}
                />
              </label>
            )}
          </For>
        </div>
        <div class="ws-modal-note">
          0 = 任意元素（Void）；1–7 = 具体元素；8 = 相同元素（Aligned，跟着出战角色元素走）；
          9 = 充能；10 = 秘传。填 0 表示不需要该类骰子。
        </div>
        <div class="ws-modal-footer">
          <span class="ws-modal-total">总计 {total()} 骰</span>
          <span class="ws-spacer" />
          <button
            class="ws-btn ws-warn"
            onClick={() => {
              props.onReset();
              props.onClose();
            }}
          >
            恢复默认
          </button>
          <button class="ws-btn" onClick={props.onClose}>
            取消
          </button>
          <button
            class="ws-btn ws-primary"
            onClick={() => {
              props.onApply(normalizeDiceRecord(values()));
              props.onClose();
            }}
          >
            确定
          </button>
        </div>
      </div>
    </div>
  );
}
