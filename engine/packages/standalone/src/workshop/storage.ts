/**
 * 数值预设的本地存储（localStorage）。
 *
 * 预设只影响“明确选择并启用”的对局；默认 activeId 是内置的“原版”。
 */
import { createSignal } from "solid-js";
import {
  VANILLA_PRESET,
  clonePreset,
  type CharacterOverride,
  type CardOverride,
  type RulesPreset,
  type SkillOverride,
} from "@gi-tcg/local-rules";

const STORAGE_KEY = "gi-tcg-local-presets-v1";

export interface PresetStore {
  presets: RulesPreset[];
  activeId: string;
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function sanitizeOverride<T extends CharacterOverride | CardOverride | SkillOverride>(
  raw: unknown,
): T | null {
  if (!raw || typeof raw !== "object") return null;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (isFiniteNumber(v)) {
      out[k] = v;
    } else if (k === "dice" && v && typeof v === "object" && !Array.isArray(v)) {
      const dice: Record<string, number> = {};
      for (const [dk, dv] of Object.entries(v as Record<string, unknown>)) {
        if (/^\d+$/.test(dk) && isFiniteNumber(dv) && dv > 0) {
          dice[dk] = Math.round(dv);
        }
      }
      // 保留空对象：它表示“费用改为 0”
      out.dice = dice;
    }
  }
  return Object.keys(out).length ? (out as unknown as T) : null;
}

export function sanitizePreset(raw: unknown): RulesPreset | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "string" || !r.id) return null;
  if (typeof r.name !== "string" || !r.name) return null;
  const preset: RulesPreset = {
    id: r.id,
    name: r.name,
    builtin: r.builtin === true,
  };
  if (typeof r.note === "string") preset.note = r.note;
  for (const key of ["characters", "cards", "skills"] as const) {
    const group = r[key];
    if (!group || typeof group !== "object") continue;
    const out: Record<string, CharacterOverride | CardOverride | SkillOverride> = {};
    for (const [id, value] of Object.entries(group as Record<string, unknown>)) {
      if (!/^\d+$/.test(id)) continue;
      const sanitized = sanitizeOverride(value);
      if (sanitized) out[id] = sanitized;
    }
    if (Object.keys(out).length) {
      (preset as unknown as Record<string, unknown>)[key] = out;
    }
  }
  return preset;
}

function defaultStore(): PresetStore {
  return { presets: [VANILLA_PRESET], activeId: VANILLA_PRESET.id };
}

function loadStore(): PresetStore {
  if (typeof localStorage === "undefined") return defaultStore();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultStore();
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return defaultStore();
    const obj = parsed as Record<string, unknown>;
    const presets = Array.isArray(obj.presets)
      ? obj.presets.map(sanitizePreset).filter((p): p is RulesPreset => !!p)
      : [];
    if (!presets.some((p) => p.id === VANILLA_PRESET.id)) {
      presets.unshift(clonePreset(VANILLA_PRESET));
    }
    const activeId =
      typeof obj.activeId === "string" && presets.some((p) => p.id === obj.activeId)
        ? obj.activeId
        : VANILLA_PRESET.id;
    return { presets, activeId };
  } catch {
    return defaultStore();
  }
}

const [store, setStoreInner] = createSignal<PresetStore>(loadStore());

function persist(next: PresetStore): void {
  setStoreInner(next);
  if (typeof localStorage !== "undefined") {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* 容量满了就算了，内存里仍然生效 */
    }
  }
}

export { store };

export function activePreset(): RulesPreset {
  const s = store();
  return s.presets.find((p) => p.id === s.activeId) ?? VANILLA_PRESET;
}

export function findPreset(id: string): RulesPreset | undefined {
  return store().presets.find((p) => p.id === id);
}

export function selectPreset(id: string): void {
  const s = store();
  if (!s.presets.some((p) => p.id === id)) return;
  persist({ ...s, activeId: id });
}

export function newPresetId(): string {
  return `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export function uniquePresetName(base: string): string {
  const names = new Set(store().presets.map((p) => p.name));
  if (!names.has(base)) return base;
  for (let i = 2; ; i++) {
    const name = `${base} ${i}`;
    if (!names.has(name)) return name;
  }
}

/** 保存（新增或覆盖）并设为当前预设。 */
export function savePreset(preset: RulesPreset): RulesPreset {
  const s = store();
  const next = clonePreset(preset);
  next.builtin = false;
  const index = s.presets.findIndex((p) => p.id === next.id);
  const presets = [...s.presets];
  if (index >= 0 && !presets[index].builtin) presets[index] = next;
  else presets.push(next);
  persist({ presets, activeId: next.id });
  return next;
}

export function deletePreset(id: string): void {
  const s = store();
  const target = s.presets.find((p) => p.id === id);
  if (!target || target.builtin) return;
  const presets = s.presets.filter((p) => p.id !== id);
  persist({
    presets,
    activeId: s.activeId === id ? VANILLA_PRESET.id : s.activeId,
  });
}

export function renamePreset(id: string, name: string): void {
  const s = store();
  const presets = s.presets.map((p) =>
    p.id === id && !p.builtin ? { ...p, name } : p,
  );
  persist({ ...s, presets });
}

export function exportPresetsJson(): string {
  const s = store();
  return JSON.stringify(
    {
      format: "gi-tcg-local-presets",
      version: 1,
      exportedAt: new Date().toISOString(),
      presets: s.presets.filter((p) => !p.builtin),
    },
    null,
    2,
  );
}

export function importPresetsJson(text: string): { imported: number; error?: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    return { imported: 0, error: "不是合法的 JSON 文件" };
  }
  const obj = parsed as Record<string, unknown>;
  if (obj?.format !== "gi-tcg-local-presets" || !Array.isArray(obj.presets)) {
    return { imported: 0, error: "不是本工具的预设文件（缺少 format 标记）" };
  }
  const incoming = obj.presets
    .map(sanitizePreset)
    .filter((p): p is RulesPreset => !!p && !p.builtin);
  if (!incoming.length) return { imported: 0, error: "文件里没有可导入的预设" };
  const s = store();
  const byId = new Map(s.presets.map((p) => [p.id, p]));
  let imported = 0;
  for (const p of incoming) {
    // id 冲突时换新 id，避免覆盖本地已有预设
    const id = byId.has(p.id) ? newPresetId() : p.id;
    const preset = { ...clonePreset(p), id, name: uniquePresetName(p.name) };
    byId.set(id, preset);
    imported++;
  }
  persist({ presets: [...byId.values()], activeId: s.activeId });
  return { imported };
}
