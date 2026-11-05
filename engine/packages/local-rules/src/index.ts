/**
 * 数值工坊的运行时层。
 *
 * 设计原则：**不启用预设时，对局与原版逐字节一致**。
 * - `applyPresetToData` 在预设为空时直接返回原始 GameData（同一对象）。
 * - `installPreset` 在预设为空时把核心钩子设为 null。
 * - 只有用户明确选择了带改动的预设，才会改写数据/伤害/费用。
 */
import {
  playSkillOfCard,
  setLocalRulesHooks,
  type CharacterDefinition,
  type EntityDefinition,
  type GameData,
  type InitiativeSkillDefinition,
  type LocalRulesHooks,
  type SkillDefinition,
} from "@gi-tcg/core";
import { DiceType, type ReadonlyDiceRequirement } from "@gi-tcg/typings";

export * from "./catalog";

/** 骰子消耗：键是 DiceType 数字，值是数量。
 * 0=任意元素(Void) / 1..7=冰水雷火风岩草 / 8=相同元素(Aligned) / 9=充能 / 10=秘传 */
export type DiceRecord = Record<string, number>;

export interface CharacterOverride {
  /** 生命值上限。会同时把初始生命设为该值。 */
  hp?: number;
  /** 最大充能。 */
  energy?: number;
}

export interface CardOverride {
  /** 打出费用（总骰数）。保留原费用的骰子类型，只改数量。 */
  cost?: number;
  /** 精确骰子消耗；设置后优先于 cost。 */
  dice?: DiceRecord;
  /** 该卡牌所有 `increaseDamage(N)` 的 N 统一替换为该值。 */
  damageBonus?: number;
}

export interface SkillOverride {
  /** 该技能所有基础伤害统一替换为该值。 */
  damage?: number;
  /** 技能消耗的总骰数；保留原骰子类型。 */
  cost?: number;
  /** 精确骰子消耗；设置后优先于 cost。 */
  dice?: DiceRecord;
}

export interface RulesPreset {
  id: string;
  name: string;
  note?: string;
  /** 内置预设（原版）不可编辑/删除 */
  builtin?: boolean;
  characters?: Record<string, CharacterOverride>;
  cards?: Record<string, CardOverride>;
  skills?: Record<string, SkillOverride>;
}

export const VANILLA_PRESET: RulesPreset = {
  id: "vanilla",
  name: "原版",
  note: "不修改任何数值",
  builtin: true,
};

export function clonePreset(preset: RulesPreset): RulesPreset {
  return JSON.parse(JSON.stringify(preset)) as RulesPreset;
}

// ---------- 骰子工具 ----------

/** 去掉 0/负数/非法键，并按骰子类型排序，便于比较和保存。 */
export function normalizeDiceRecord(dice: DiceRecord | null | undefined): DiceRecord {
  const out: DiceRecord = {};
  for (const [key, value] of Object.entries(dice ?? {})) {
    const type = Number(key);
    if (!Number.isInteger(type) || type < 0 || type > 10) continue;
    const count = Math.max(0, Math.round(Number(value)));
    if (count > 0) out[key] = count;
  }
  const sorted: DiceRecord = {};
  for (const key of Object.keys(out).sort((a, b) => Number(a) - Number(b))) {
    sorted[key] = out[key];
  }
  return sorted;
}

export function diceRecordToMap(dice: DiceRecord | null | undefined): Map<DiceType, number> {
  const map = new Map<DiceType, number>();
  for (const [key, value] of Object.entries(normalizeDiceRecord(dice))) {
    map.set(Number(key) as DiceType, value);
  }
  return map;
}

export function diceMapToRecord(
  map: ReadonlyMap<DiceType, number> | null | undefined,
): DiceRecord {
  const out: DiceRecord = {};
  for (const [key, value] of map ?? []) {
    if (value > 0) out[String(key)] = value;
  }
  return normalizeDiceRecord(out);
}

export function diceTotal(dice: DiceRecord | null | undefined): number {
  return Object.values(normalizeDiceRecord(dice)).reduce((a, b) => a + b, 0);
}

export function sameDiceRecord(
  a: DiceRecord | null | undefined,
  b: DiceRecord | null | undefined,
): boolean {
  return JSON.stringify(normalizeDiceRecord(a)) === JSON.stringify(normalizeDiceRecord(b));
}

/**
 * 计算某个卡牌/技能最终的骰子消耗：
 * - `dice` 精确覆盖优先；
 * - `cost` 只改总数，尽量保留原骰子类型；
 * - 都没有则用默认值。
 */
export function resolveCostDice(
  defaultDice: DiceRecord | null | undefined,
  override: { cost?: number; dice?: DiceRecord } | null | undefined,
): DiceRecord {
  const base = normalizeDiceRecord(defaultDice);
  if (!override) return base;
  if (override.dice !== undefined) return normalizeDiceRecord(override.dice);
  if (typeof override.cost === "number") {
    return diceMapToRecord(
      setCostTotal(diceRecordToMap(base), Math.max(0, Math.round(override.cost))),
    );
  }
  return base;
}

// ---------- 预设是否为空 / 改动计数 ----------

function entryHasChange(entry: object): boolean {
  for (const [key, value] of Object.entries(entry)) {
    if (typeof value === "number" && Number.isFinite(value)) return true;
    if (key === "dice" && value && typeof value === "object") {
      // 空对象也算改动：它表示把费用改成 0
      return true;
    }
  }
  return false;
}

/** 预设是否没有任何实际改动。 */
export function isEmptyPreset(preset: RulesPreset | null | undefined): boolean {
  if (!preset) return true;
  const hasValues = (obj: Record<string, object> | undefined) =>
    !!obj && Object.values(obj).some((v) => entryHasChange(v));
  if (hasValues(preset.characters)) return false;
  if (hasValues(preset.cards)) return false;
  if (hasValues(preset.skills)) return false;
  return true;
}

/** 统计改动条数，用于界面上显示“已改 N 项”。骰子按每个类型算一项。 */
export function countPresetChanges(preset: RulesPreset | null | undefined): number {
  if (!preset) return 0;
  let n = 0;
  for (const group of [preset.characters, preset.cards, preset.skills]) {
    for (const entry of Object.values(group ?? {})) {
      for (const [key, value] of Object.entries(entry)) {
        if (typeof value === "number" && Number.isFinite(value)) n++;
        else if (key === "dice" && value && typeof value === "object") {
          n += Math.max(1, Object.keys(value).length);
        }
      }
    }
  }
  return n;
}

// ---------- 数据覆盖 ----------

function costSize(req: ReadonlyDiceRequirement): number {
  let total = 0;
  for (const count of req.values()) total += count;
  return total;
}

function diceCostSize(req: ReadonlyDiceRequirement): number {
  let total = 0;
  for (const [dice, count] of req) {
    if (dice !== DiceType.Energy && dice !== DiceType.Legend) total += count;
  }
  return total;
}

/**
 * 把总费用改成 `total`，尽量保留原来的骰子类型：
 * 优先调整「相同元素」项，否则调整最后一项；其余项不变。
 */
export function setCostTotal(
  req: ReadonlyDiceRequirement,
  total: number,
): Map<DiceType, number> {
  const entries = [...req.entries()];
  if (entries.length === 0) {
    return new Map([[DiceType.Aligned, Math.max(0, total)]]);
  }
  const current = costSize(req);
  if (current === total) return new Map(entries);
  let index = entries.findIndex(([dice]) => dice === DiceType.Aligned);
  if (index < 0) index = entries.length - 1;
  const others = current - entries[index][1];
  const next = new Map(entries);
  next.set(entries[index][0], Math.max(0, total - others));
  return next;
}

function patchCharacter(
  ch: CharacterDefinition,
  override: CharacterOverride,
): CharacterDefinition {
  const varConfigs = { ...ch.varConfigs };
  if (typeof override.hp === "number") {
    const hp = Math.max(1, Math.round(override.hp));
    if (varConfigs.maxHealth) {
      varConfigs.maxHealth = { ...varConfigs.maxHealth, initialValue: hp };
    }
    if (varConfigs.health) {
      varConfigs.health = { ...varConfigs.health, initialValue: hp };
    }
  }
  if (typeof override.energy === "number") {
    const energy = Math.max(0, Math.round(override.energy));
    if (varConfigs.maxEnergy) {
      varConfigs.maxEnergy = { ...varConfigs.maxEnergy, initialValue: energy };
    }
  }
  return { ...ch, varConfigs };
}

function patchSkillCost(sk: SkillDefinition, dice: DiceRecord): SkillDefinition {
  if (!("initiativeSkillConfig" in sk) || !sk.initiativeSkillConfig) return sk;
  const requiredCost = diceRecordToMap(dice);
  const next: InitiativeSkillDefinition = {
    ...sk,
    initiativeSkillConfig: {
      ...sk.initiativeSkillConfig,
      requiredCost,
      computed$costSize: costSize(requiredCost),
      computed$diceCostSize: diceCostSize(requiredCost),
    },
  };
  return next;
}

function defaultSkillDice(sk: SkillDefinition): DiceRecord {
  return "initiativeSkillConfig" in sk && sk.initiativeSkillConfig
    ? diceMapToRecord(sk.initiativeSkillConfig.requiredCost)
    : {};
}

/**
 * 返回应用了预设的新 GameData。
 * 空预设直接返回原对象（不复制），保证原版行为。
 */
export function applyPresetToData(
  data: GameData,
  preset: RulesPreset | null | undefined,
): GameData {
  if (!preset || isEmptyPreset(preset)) return data;

  const skillCostOverrides = new Map<number, SkillOverride>();
  for (const [key, override] of Object.entries(preset.skills ?? {})) {
    if (
      override &&
      (override.dice !== undefined || typeof override.cost === "number")
    ) {
      skillCostOverrides.set(Number(key), override);
    }
  }

  let characters: Map<number, CharacterDefinition> | null = null;
  for (const [id, ch] of data.characters) {
    const charOverride = preset.characters?.[String(id)];
    let next = ch;
    let changed = false;

    if (
      charOverride &&
      (typeof charOverride.hp === "number" ||
        typeof charOverride.energy === "number")
    ) {
      next = patchCharacter(next, charOverride);
      changed = true;
    }

    if (skillCostOverrides.size > 0) {
      let skillsChanged = false;
      const skills = next.skills.map((sk) => {
        const override = skillCostOverrides.get(sk.id);
        if (!override) return sk;
        const base = defaultSkillDice(sk);
        const target = resolveCostDice(base, override);
        if (sameDiceRecord(base, target)) return sk;
        skillsChanged = true;
        return patchSkillCost(sk, target);
      });
      if (skillsChanged) {
        next = { ...next, skills };
        changed = true;
      }
    }

    if (changed) {
      characters ??= new Map(data.characters);
      characters.set(id, next);
    }
  }

  let entities: Map<number, EntityDefinition> | null = null;
  for (const [key, override] of Object.entries(preset.cards ?? {})) {
    if (!override) continue;
    if (override.dice === undefined && typeof override.cost !== "number") continue;
    const id = Number(key);
    const et = data.entities.get(id);
    if (!et) continue;
    const play = playSkillOfCard(et);
    if (!play) continue;
    const base = diceMapToRecord(play.initiativeSkillConfig.requiredCost);
    const target = resolveCostDice(base, override);
    if (sameDiceRecord(base, target)) continue;
    const patched = patchSkillCost(play, target);
    entities ??= new Map(data.entities);
    entities.set(id, {
      ...et,
      skills: et.skills.map((s) => (s.id === play.id ? patched : s)),
    });
  }

  return {
    ...data,
    characters: characters ?? data.characters,
    entities: entities ?? data.entities,
  };
}

/** 根据预设生成核心钩子；没有伤害类改动时返回 null。 */
export function buildHooks(preset: RulesPreset | null | undefined): LocalRulesHooks | null {
  if (!preset) return null;
  const cardBonus = new Map<number, number>();
  for (const [key, override] of Object.entries(preset.cards ?? {})) {
    if (typeof override.damageBonus === "number") {
      cardBonus.set(Number(key), override.damageBonus);
    }
  }
  const skillDamage = new Map<number, number>();
  for (const [key, override] of Object.entries(preset.skills ?? {})) {
    if (typeof override.damage === "number") {
      skillDamage.set(Number(key), override.damage);
    }
  }
  if (cardBonus.size === 0 && skillDamage.size === 0) return null;
  return {
    increaseDamage: ({ caller, value }) =>
      cardBonus.get(caller.definition.id) ?? value,
    baseDamage: ({ skillId, value }) => skillDamage.get(skillId) ?? value,
  };
}

/** 把预设装进核心钩子。传 null / 空预设即恢复原版。 */
export function installPreset(preset: RulesPreset | null | undefined): void {
  setLocalRulesHooks(buildHooks(preset));
}
