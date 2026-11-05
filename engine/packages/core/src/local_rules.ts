// Copyright (C) 2026 Piovium Labs
//
// [LOCAL PATCH] 本地增强：运行时可插拔的数值覆盖钩子。
// 这是“数值工坊”的基础设施。未调用 setLocalRulesHooks 时，所有钩子为 null，
// 结算路径与原版完全一致（多一次 null 判断）。
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as
// published by the Free Software Foundation, either version 3 of the
// License, or (at your option) any later version.

import type { DamageType } from "@gi-tcg/typings";
import type { AnyState } from "./base/state";

/** 一次 `increaseDamage(N)` 调用的上下文。caller 通常是武器/圣遗物/状态实体。 */
export interface LocalDamageIncreaseInfo {
  readonly caller: AnyState;
  readonly value: number;
}

/** 一次技能基础伤害 `:damage(type, N)` 的上下文。 */
export interface LocalBaseDamageInfo {
  readonly caller: AnyState;
  readonly skillId: number;
  readonly type: DamageType;
  readonly value: number;
}

export interface LocalRulesHooks {
  /** 返回值替换本次伤害加成；返回 value 表示不修改。 */
  readonly increaseDamage?: (info: LocalDamageIncreaseInfo) => number;
  /** 返回值替换本次技能基础伤害；返回 value 表示不修改。 */
  readonly baseDamage?: (info: LocalBaseDamageInfo) => number;
}

let hooks: LocalRulesHooks | null = null;

export function setLocalRulesHooks(next: LocalRulesHooks | null): void {
  hooks = next;
}

export function getLocalRulesHooks(): LocalRulesHooks | null {
  return hooks;
}
