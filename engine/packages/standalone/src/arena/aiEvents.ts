/**
 * 把引擎的 notification / mutation 翻译成“给 AI 看的结构化数据”。
 *
 * 设计目标：
 * - 事件里的 id 都尽量附上中文名；
 * - 每次变化立刻产生一条事件；
 * - 每回合结束（stepRound，召唤物结算完之后）产生一份完整 snapshot；
 * - 不替 agent 做上下文管理，只保证数据完整、结构稳定。
 */
import {
  CARD_BY_ID,
  CHARACTER_BY_ID,
  SKILL_BY_ID,
  TOKEN_BY_ID,
} from "@gi-tcg/local-rules";
import {
  CARD_TAG_ABYSS,
  CARD_TAG_CONDUCTIVE,
  CHARACTER_TAG_BARRIER,
  CHARACTER_TAG_BOND_OF_LIFE,
  CHARACTER_TAG_DISABLE_SKILL,
  CHARACTER_TAG_NIGHTSOULS_BLESSING,
  CHARACTER_TAG_SHIELD,
} from "@gi-tcg/core";

export type AiSeat = 0 | 1;

const PHASE_LABELS = [
  "准备手牌",
  "选择出战角色",
  "掷骰",
  "行动",
  "结束（召唤物结算）",
  "对局结束",
];
const DAMAGE_LABELS: Record<number, string> = {
  0: "物理",
  1: "冰",
  2: "水",
  3: "火",
  4: "雷",
  5: "风",
  6: "岩",
  7: "草",
  8: "穿透",
  9: "治疗",
};
const AURA_LABELS: Record<number, string> = {
  0: "无",
  1: "冰",
  2: "水",
  3: "火",
  4: "雷",
  7: "草",
  113: "冰+草",
};
const REACTION_LABELS: Record<number, string> = {
  101: "融化",
  102: "蒸发",
  103: "超载",
  104: "超导",
  105: "感电",
  106: "冻结",
  107: "冰扩散",
  108: "水扩散",
  109: "火扩散",
  110: "雷扩散",
  111: "冰结晶",
  112: "水结晶",
  113: "火结晶",
  114: "雷结晶",
  115: "燃烧",
  116: "绽放",
  117: "激化",
  3001: "月感电",
  3002: "月绽放",
  3003: "月结晶",
};
const SKILL_TYPE_LABELS: Record<number, string> = {
  0: "触发",
  1: "角色被动",
  2: "普通攻击",
  3: "元素战技",
  4: "元素爆发",
  5: "特技",
  6: "装备触发",
};
const VALIDITY_LABELS: Record<number, string> = {
  0: "合法",
  1: "条件不满足",
  2: "没有可选目标",
  3: "骰子不足",
  4: "充能不足",
  5: "被技能/状态禁用",
  6: "本局秘传牌已用过",
};
const COST_LABELS: Record<number, string> = {
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
const DICE_LABELS: Record<number, string> = {
  0: "未知",
  1: "冰",
  2: "水",
  3: "火",
  4: "雷",
  5: "风",
  6: "岩",
  7: "草",
  8: "万能",
};
const AREA_LABELS: Record<number, string> = {
  1: "角色区",
  2: "出战状态区",
  3: "召唤物区",
  4: "支援区",
  5: "手牌",
  6: "牌堆",
};
const ENTITY_TYPE_LABELS: Record<number, string> = {
  1: "手牌",
  2: "状态",
  3: "出战状态",
  4: "召唤物",
  5: "支援",
  6: "装备",
};
const MOVE_REASON_LABELS: Record<number, string> = {
  1: "切换",
  2: "抽牌",
  3: "放回牌堆",
  4: "夺取",
  5: "交换",
  6: "装备",
  7: "生成支援",
  8: "卸下装备",
  9: "移除支援",
};
const REMOVE_REASON_LABELS: Record<number, string> = {
  1: "事件牌结算",
  2: "元素调和",
  3: "溢出",
  4: "弃置",
  5: "事件牌无效果",
  6: "抽到事件牌",
  7: "装备被覆盖",
  8: "支援被覆盖",
  9: "支援目标",
};
const FLAG_LABELS: Record<number, string> = {
  1: "declaredEnd",
  2: "legendUsed",
};
const PLAYER_STATUS_LABELS: Record<number, string> = {
  0: "未指定",
  1: "等待行动",
  2: "选择出战",
  3: "重投骰子",
  4: "替换手牌",
  5: "挑选卡牌",
};

export function phaseName(phase: number): string {
  return PHASE_LABELS[phase] ?? `阶段#${phase}`;
}

function elemLabel(type: number): string {
  return DAMAGE_LABELS[type] ?? `#${type}`;
}

/** 骰子“面”的中文（0 表示未知/隐藏）。 */
export function diceValueLabel(type: number): string {
  return DICE_LABELS[type] ?? `#${type}`;
}

export function costText(dice: Record<string, number>): string {
  const entries = Object.entries(dice).sort((a, b) => Number(a[0]) - Number(b[0]));
  if (!entries.length) return "0";
  return entries
    .map(([type, count]) => `${count}${COST_LABELS[Number(type)] ?? `#${type}`}`)
    .join("+");
}

function costFromPb(reqs: any[] | undefined): {
  dice: Record<string, number>;
  total: number;
  text: string;
} {
  const dice: Record<string, number> = {};
  for (const r of reqs ?? []) {
    if (r?.count > 0) dice[String(r.type)] = r.count;
  }
  const total = Object.values(dice).reduce((a, b) => a + b, 0);
  return { dice, total, text: costText(dice) };
}

function nameOf(defId: number | undefined): string {
  if (!defId) return "未知";
  return (
    CHARACTER_BY_ID.get(defId)?.name ??
    CARD_BY_ID.get(defId)?.name ??
    TOKEN_BY_ID.get(defId)?.name ??
    SKILL_BY_ID.get(defId)?.name ??
    `#${defId}`
  );
}

function skillName(id: number, callerId?: number): string {
  return (
    SKILL_BY_ID.get(id)?.name ||
    (callerId ? `${nameOf(callerId)}的技能` : `技能#${id}`)
  );
}

function entityTypeLabel(type: number | undefined): string {
  return type ? ENTITY_TYPE_LABELS[type] ?? `#${type}` : "未知";
}

function areaLabel(area: number | undefined): string {
  return area ? AREA_LABELS[area] ?? `#${area}` : "未知";
}

function characterTags(tags: number): Record<string, boolean | number> {
  return {
    raw: tags,
    shield: !!(tags & CHARACTER_TAG_SHIELD),
    barrier: !!(tags & CHARACTER_TAG_BARRIER),
    disableSkill: !!(tags & CHARACTER_TAG_DISABLE_SKILL),
    nightsoulsBlessing: !!(tags & CHARACTER_TAG_NIGHTSOULS_BLESSING),
    bondOfLife: !!(tags & CHARACTER_TAG_BOND_OF_LIFE),
  };
}

function entityTags(tags: number): Record<string, boolean | number> {
  return {
    raw: tags,
    abyss: !!(tags & CARD_TAG_ABYSS),
    conductive: !!(tags & CARD_TAG_CONDUCTIVE),
  };
}

interface Lookup {
  byId: Map<number, any>;
  seatOf: Map<number, 0 | 1>;
  kindOf: Map<number, string>;
}

function buildLookup(state: any): Lookup {
  const byId = new Map<number, any>();
  const seatOf = new Map<number, 0 | 1>();
  const kindOf = new Map<number, string>();
  for (const [seat, player] of (state?.player ?? []).entries()) {
    const add = (entity: any, kind: string) => {
      if (!entity || typeof entity.id !== "number") return;
      byId.set(entity.id, entity);
      seatOf.set(entity.id, seat as 0 | 1);
      kindOf.set(entity.id, kind);
    };
    for (const ch of player.character ?? []) add(ch, "character");
    for (const e of player.combatStatus ?? []) add(e, "combatStatus");
    for (const e of player.support ?? []) add(e, "support");
    for (const e of player.summon ?? []) add(e, "summon");
    for (const c of player.handCard ?? []) add(c, "hand");
    for (const c of player.pileCard ?? []) add(c, "pile");
  }
  return { byId, seatOf, kindOf };
}

export function entityView(e: any): any {
  if (!e) return null;
  return {
    id: e.id,
    definitionId: e.definitionId,
    name: nameOf(e.definitionId),
    type: entityTypeLabel(e.type),
    variableName: e.variableName ?? null,
    variableValue: e.variableValue ?? null,
    hintText: e.hintText ?? null,
    equipment: e.equipment ?? null,
    tags: entityTags(e.tags ?? 0),
    cost: e.definitionCost ? costFromPb(e.definitionCost) : undefined,
    attachments: (e.attachment ?? []).map((att: any) => ({
      id: att.id,
      definitionId: att.definitionId,
      name: nameOf(att.definitionId),
    })),
  };
}

function characterView(ch: any): any {
  const entities = (ch.entity ?? []).map(entityView);
  return {
    id: ch.id,
    definitionId: ch.definitionId,
    name: nameOf(ch.definitionId),
    health: ch.health,
    maxHealth: ch.maxHealth,
    energy: ch.energy,
    maxEnergy: ch.maxEnergy,
    aura: AURA_LABELS[ch.aura] ?? `#${ch.aura}`,
    defeated: !!ch.defeated,
    tags: characterTags(ch.tags ?? 0),
    equipment: entities.filter((e: any) => e.equipment !== null && e.equipment !== undefined),
    statuses: entities.filter((e: any) => e.equipment === null || e.equipment === undefined),
    entities,
  };
}

function playerView(player: any, seat: AiSeat, isMe: boolean): any {
  const characters = (player.character ?? []).map(characterView);
  const active = characters.find((c: any) => c.id === player.activeCharacterId);
  return {
    seat,
    activeCharacterId: player.activeCharacterId,
    activeCharacterName: active?.name ?? nameOf(active?.definitionId),
    characters,
    handCount: (player.handCard ?? []).length,
    hand: isMe ? (player.handCard ?? []).map(entityView) : undefined,
    diceCount: (player.dice ?? []).length,
    dice: isMe ? player.dice ?? [] : undefined,
    diceLabels: isMe ? (player.dice ?? []).map((d: number) => diceValueLabel(d)) : undefined,
    pileCount: (player.pileCard ?? []).length,
    supports: (player.support ?? []).map(entityView),
    summons: (player.summon ?? []).map(entityView),
    combatStatuses: (player.combatStatus ?? []).map(entityView),
    declaredEnd: !!player.declaredEnd,
    legendUsed: !!player.legendUsed,
    initiativeSkills: isMe
      ? (player.initiativeSkill ?? []).map((s: any) => {
          const cost = costFromPb(s.definitionCost);
          return {
            id: s.definitionId,
            name: skillName(s.definitionId),
            cost: cost.dice,
            costText: cost.text,
          };
        })
      : undefined,
  };
}

/** 一回合一次的完整局面（双方可见信息 + 自己的手牌/骰子）。 */
export function buildSnapshot(
  state: any,
  seat: AiSeat,
  reason: "start" | "round_end" | "game_end" | "request",
): any {
  return {
    type: "snapshot",
    reason,
    at: Date.now(),
    round: state.roundNumber,
    phase: phaseName(state.phase),
    currentTurn: state.currentTurn,
    winner: state.winner ?? null,
    me: playerView(state.player[seat], seat, true),
    opponent: playerView(state.player[1 - seat], (1 - seat) as AiSeat, false),
  };
}

function entityAt(lookup: Lookup, id: number | undefined): any {
  return id ? lookup.byId.get(id) : null;
}

function entitySummary(lookup: Lookup, id: number | undefined): any {
  const entity = entityAt(lookup, id);
  return {
    id,
    definitionId: entity?.definitionId,
    name: nameOf(entity?.definitionId),
    seat: id ? lookup.seatOf.get(id) ?? null : null,
    kind: id ? lookup.kindOf.get(id) ?? null : null,
  };
}

function actionTargets(lookup: Lookup, targets: any[] | undefined): any[] {
  return (targets ?? []).map((t: any) => ({
    id: t.id,
    name: nameOf(entityAt(lookup, t.id)?.definitionId),
  }));
}

export function actionView(a: any, index: number, lookup: Lookup): any {
  const kind = a?.action?.$case ?? "unknown";
  const value = a?.action?.value ?? {};
  let name = kind;
  let detail: any = {};
  switch (kind) {
    case "useSkill": {
      name = skillName(value.skillDefinitionId);
      detail = {
        skillId: value.skillDefinitionId,
        skillName: name,
        targets: actionTargets(lookup, a.targets?.length ? a.targets : value.targetIds),
      };
      break;
    }
    case "playCard": {
      name = nameOf(value.cardDefinitionId);
      detail = {
        cardId: value.cardId,
        cardDefinitionId: value.cardDefinitionId,
        cardName: name,
        targets: actionTargets(lookup, value.targetIds),
        willBeEffectless: !!value.willBeEffectless,
      };
      break;
    }
    case "switchActive": {
      name = `切换到 ${nameOf(value.characterDefinitionId)}`;
      detail = {
        characterId: value.characterId,
        characterName: nameOf(value.characterDefinitionId),
      };
      break;
    }
    case "elementalTuning": {
      name = `元素调和（弃置 ${nameOf(entityAt(lookup, value.cardId)?.definitionId)}）`;
      detail = {
        removedCardId: value.removedCardId,
        targetDice: value.targetDice,
        targetDiceLabel: diceValueLabel(value.targetDice),
      };
      break;
    }
    case "declareEnd":
      name = "结束回合";
      break;
    case "passAction":
      name = "过一手（交出行动权）";
      break;
  }
  return {
    index,
    kind,
    name,
    ...detail,
    validity: a?.validity ?? -1,
    validityLabel: VALIDITY_LABELS[a?.validity] ?? `#${a?.validity}`,
    cost: a?.requiredCost ? costFromPb(a.requiredCost) : undefined,
    autoSelectedDice: a?.autoSelectedDice ?? [],
    autoSelectedDiceLabels: (a?.autoSelectedDice ?? []).map((d: number) => diceValueLabel(d)),
    preview: (a?.preview ?? []).map(previewSummary).filter(Boolean),
  };
}

function previewSummary(p: any): any {
  const m = p?.mutation;
  if (!m) return null;
  switch (m.$case) {
    case "damage":
      return {
        type: "damage",
        targetId: m.value.targetId,
        target: nameOf(m.value.targetDefinitionId),
        amount: m.value.value,
        element: elemLabel(m.value.damageType),
        reaction: REACTION_LABELS[m.value.reactionType] ?? null,
        defeated: !!m.value.causeDefeated,
      };
    case "heal":
      return {
        type: "heal",
        targetId: m.value.targetId,
        target: nameOf(m.value.targetDefinitionId),
        amount: m.value.value,
      };
    case "createEntity":
      return { type: "createEntity", entity: nameOf(m.value.entity?.definitionId) };
    case "modifyEntityVar":
      return {
        type: "modifyEntityVar",
        entityId: m.value.entityId,
        entity: nameOf(m.value.entityDefinitionId),
        variable: m.value.variableName,
        value: m.value.value,
      };
    default:
      return { type: m.$case };
  }
}

export interface AiNotification {
  mutation?: any[];
  state?: any;
}

/** 把一次 notify 里的所有 mutation 翻译成事件。 */
export function mutationEvents(n: AiNotification, seat: AiSeat): any[] {
  const state = n.state;
  if (!state) return [];
  const lookup = buildLookup(state);
  const events: any[] = [];
  for (const em of n.mutation ?? []) {
    const m = em?.mutation;
    if (!m) continue;
    const v = m.value ?? {};
    switch (m.$case) {
      case "damage": {
        const source = entityAt(lookup, v.sourceId);
        const target = entityAt(lookup, v.targetId);
        const isHeal = (v.healKind ?? 0) !== 0;
        events.push({
          type: isHeal ? "heal" : "damage",
          at: Date.now(),
          seat: lookup.seatOf.get(v.sourceId) ?? null,
          source: entitySummary(lookup, v.sourceId),
          target: {
            ...entitySummary(lookup, v.targetId),
            healthBefore: v.oldHealth,
            healthAfter: v.newHealth,
          },
          amount: v.value,
          element: elemLabel(v.damageType),
          reaction: REACTION_LABELS[v.reactionType] ?? null,
          reactionId: v.reactionType,
          defeated: !!v.causeDefeated,
          targetAuraBefore: AURA_LABELS[v.oldAura] ?? null,
          targetAuraAfter: AURA_LABELS[v.newAura] ?? null,
          healKind: v.healKind,
          sourceName: nameOf(source?.definitionId),
          targetName: nameOf(target?.definitionId),
        });
        break;
      }
      case "applyAura":
        events.push({
          type: "aura",
          at: Date.now(),
          target: entitySummary(lookup, v.targetId),
          element: DAMAGE_LABELS[v.elementType] ?? null,
          reaction: REACTION_LABELS[v.reactionType] ?? null,
          oldAura: AURA_LABELS[v.oldAura] ?? null,
          newAura: AURA_LABELS[v.newAura] ?? null,
        });
        break;
      case "modifyEntityVar": {
        const entity = entityAt(lookup, v.entityId);
        events.push({
          type: "variable",
          at: Date.now(),
          entity: {
            id: v.entityId,
            definitionId: v.entityDefinitionId,
            name: nameOf(v.entityDefinitionId),
            seat: entity ? lookup.seatOf.get(v.entityId) ?? null : null,
          },
          variable: v.variableName,
          oldValue: v.oldValue,
          value: v.variableValue,
          direction: v.direction,
        });
        break;
      }
      case "skillUsed": {
        const caller = entityAt(lookup, v.callerId);
        events.push({
          type: "skill",
          at: Date.now(),
          seat: v.who,
          skill: {
            id: v.skillDefinitionId,
            name: skillName(v.skillDefinitionId, v.callerDefinitionId),
            type: SKILL_TYPE_LABELS[v.skillType] ?? v.skillType,
            triggeredOn: v.triggeredOn ?? null,
          },
          source: {
            id: v.callerId,
            definitionId: v.callerDefinitionId,
            name: nameOf(v.callerDefinitionId),
            seat: v.who,
            kind: caller ? lookup.kindOf.get(v.callerId) ?? null : null,
          },
        });
        break;
      }
      case "resetDice": {
        const isMine = v.who === seat;
        events.push({
          type: "dice",
          at: Date.now(),
          seat: v.who,
          reason: ["未指定", "掷骰", "消耗", "元素调和", "生成", "转化", "吸收"][
            v.reason ?? 0
          ],
          dice: isMine ? v.dice : undefined,
          diceLabels: isMine ? (v.dice ?? []).map((d: number) => diceValueLabel(d)) : undefined,
          diceCount: (v.dice ?? []).length,
          hidden: !isMine,
          conversionTargetHint: v.conversionTargetHint ?? null,
        });
        break;
      }
      case "rerollDone":
        events.push({ type: "reroll", at: Date.now(), seat: v.who, count: v.count });
        break;
      case "switchHandsDone":
        events.push({ type: "switch_hands", at: Date.now(), seat: v.who, count: v.count });
        break;
      case "chooseActiveDone":
        events.push({
          type: "choose_active",
          at: Date.now(),
          seat: v.who,
          character: {
            id: v.characterId,
            definitionId: v.characterDefinitionId,
            name: nameOf(v.characterDefinitionId),
          },
        });
        break;
      case "selectCardDone":
        events.push({
          type: "select_card",
          at: Date.now(),
          seat: v.who,
          card: { definitionId: v.selectedDefinitionId, name: nameOf(v.selectedDefinitionId) },
        });
        break;
      case "createEntity":
        events.push({
          type: "create_entity",
          at: Date.now(),
          seat: v.who,
          where: areaLabel(v.where),
          entity: entityView(v.entity),
          masterCharacterId: v.masterCharacterId ?? null,
        });
        break;
      case "removeEntity":
        events.push({
          type: "remove_entity",
          at: Date.now(),
          seat: v.who,
          where: areaLabel(v.where),
          entity: entityView(v.entity),
          reason: REMOVE_REASON_LABELS[v.reason] ?? v.reason,
        });
        break;
      case "moveEntity":
        events.push({
          type: "move_entity",
          at: Date.now(),
          fromSeat: v.fromWho,
          fromWhere: v.fromWhere,
          toSeat: v.toWho,
          toWhere: v.toWhere,
          entity: entityView(v.entity),
          reason: MOVE_REASON_LABELS[v.reason] ?? v.reason,
        });
        break;
      case "createAttachment":
        events.push({
          type: "create_attachment",
          at: Date.now(),
          seat: v.who,
          where: v.where,
          attachment: {
            id: v.attachment?.id,
            definitionId: v.attachment?.definitionId,
            name: nameOf(v.attachment?.definitionId),
          },
          masterCardId: v.masterCardId,
        });
        break;
      case "transformDefinition":
        events.push({
          type: "transform",
          at: Date.now(),
          entityId: v.entityId,
          newDefinitionId: v.newEntityDefinitionId,
          newName: nameOf(v.newEntityDefinitionId),
        });
        break;
      case "resetVariables":
        events.push({
          type: "reset_variables",
          at: Date.now(),
          entity: {
            id: v.entityId,
            definitionId: v.entityDefinitionId,
            name: nameOf(v.entityDefinitionId),
          },
        });
        break;
      case "switchActive":
        events.push({
          type: "switch_active",
          at: Date.now(),
          seat: v.who,
          character: {
            id: v.characterId,
            definitionId: v.characterDefinitionId,
            name: nameOf(v.characterDefinitionId),
          },
          viaSkillDefinitionId: v.viaSkillDefinitionId ?? null,
          fromAction: v.fromAction,
        });
        break;
      case "swapCharacterPosition":
        events.push({
          type: "swap_characters",
          at: Date.now(),
          seat: v.who,
          characters: [nameOf(v.character0DefinitionId), nameOf(v.character1DefinitionId)],
        });
        break;
      case "setPlayerFlag":
        events.push({
          type: "player_flag",
          at: Date.now(),
          seat: v.who,
          flag: FLAG_LABELS[v.flagName] ?? v.flagName,
          value: v.flagValue,
        });
        break;
      case "playerStatusChange":
        events.push({
          type: "player_status",
          at: Date.now(),
          seat: v.who,
          status: PLAYER_STATUS_LABELS[v.status] ?? v.status,
        });
        break;
      case "changePhase":
        events.push({
          type: "phase",
          at: Date.now(),
          phase: phaseName(v.newPhase),
          phaseId: v.newPhase,
          hasChange: !!v.hasChange,
        });
        break;
      case "stepRound":
        events.push({ type: "round", at: Date.now(), round: state.roundNumber });
        break;
      case "switchTurn":
        events.push({ type: "turn", at: Date.now(), currentTurn: state.currentTurn });
        break;
      case "setWinner":
        events.push({ type: "winner", at: Date.now(), winner: v.winner ?? null });
        break;
      case "handleEvent":
        events.push({
          type: "trigger",
          at: Date.now(),
          event: v.eventName,
          closed: !!v.isClose,
        });
        break;
      default:
        break;
    }
  }
  return events;
}

/** 一次 rpc 请求的完整结构化描述（发给 AI 的“现在轮到你做什么”）。 */
export function requestEvent(
  req: any,
  state: any,
  seat: AiSeat,
  deadline: number,
  attempt: number,
): any {
  const rpc = req.request?.$case;
  const value = req.request?.value ?? {};
  const lookup = buildLookup(state);
  const base: any = {
    type: "request",
    at: Date.now(),
    seat,
    rpc,
    round: state?.roundNumber,
    phase: phaseName(state?.phase),
    currentTurn: state?.currentTurn,
    deadline,
    attempt,
  };
  switch (rpc) {
    case "action": {
      const actions = (value.action ?? []).map((a: any, i: number) => actionView(a, i, lookup));
      const activeChar = state?.player?.[seat]?.character?.find(
        (c: any) => c.id === state.player[seat].activeCharacterId,
      );
      const disabledBy = (activeChar?.entity ?? [])
        .filter((e: any) => {
          const tags = TOKEN_BY_ID.get(e.definitionId)?.tags ?? [];
          return tags.includes("disableSkill");
        })
        .map((e: any) => ({
          id: e.id,
          definitionId: e.definitionId,
          name: nameOf(e.definitionId),
        }));
      return {
        ...base,
        actions,
        validActions: actions.filter((a: any) => a.validity === 0),
        dice: state?.player?.[seat]?.dice ?? [],
        diceLabels: (state?.player?.[seat]?.dice ?? []).map((d: number) => diceValueLabel(d)),
        declaredEnd: !!state?.player?.[seat]?.declaredEnd,
        activeCharacter: {
          id: activeChar?.id,
          definitionId: activeChar?.definitionId,
          name: nameOf(activeChar?.definitionId),
          tags: characterTags(activeChar?.tags ?? 0),
        },
        disabledBy,
        note: "选择 actions 里的 index；usedDice 可以省略，服务端会自动用该行动的 autoSelectedDice。",
      };
    }
    case "rerollDice":
      return {
        ...base,
        dice: state?.player?.[seat]?.dice ?? [],
        diceLabels: (state?.player?.[seat]?.dice ?? []).map((d: number) => diceValueLabel(d)),
        note:
          attempt > 0
            ? "这是额外获得的重投机会；返回要重投的骰子类型。"
            : "返回要重投的骰子类型；空数组表示不重投。若卡牌提供额外重投，重投非空骰子后会再次请求（attempt+1）；返回空数组会放弃剩余重投。",
      };
    case "switchHands":
      return {
        ...base,
        hand: (state?.player?.[seat]?.handCard ?? []).map(entityView),
        note: "返回要移回牌堆的手牌 id（removedHandIds）；空数组表示不换。",
      };
    case "chooseActive":
      return {
        ...base,
        candidates: (value.candidateIds ?? []).map((id: number) => ({
          id,
          name: nameOf(entityAt(lookup, id)?.definitionId),
          health: entityAt(lookup, id)?.health,
          maxHealth: entityAt(lookup, id)?.maxHealth,
        })),
      };
    case "selectCard":
      return {
        ...base,
        candidates: (value.candidateDefinitionIds ?? []).map((id: number) => ({
          definitionId: id,
          name: nameOf(id),
        })),
      };
    default:
      return base;
  }
}

/** 某次选择的公开结果（双方都能看到），用于日志。 */
export function decisionEvent(req: any, res: any, seat: AiSeat, state: any): any {
  const rpc = req.request?.$case;
  const value = res.response?.value ?? {};
  const lookup = buildLookup(state);
  const base = { type: "decision", at: Date.now(), seat, rpc };
  switch (rpc) {
    case "action": {
      const actions = req.request?.value?.action ?? [];
      const index = value.chosenActionIndex ?? 0;
      return {
        ...base,
        action: actionView(actions[index], index, lookup),
      };
    }
    case "chooseActive": {
      const ch = entityAt(lookup, value.activeCharacterId);
      return {
        ...base,
        character: {
          id: value.activeCharacterId,
          definitionId: ch?.definitionId,
          name: nameOf(ch?.definitionId),
        },
      };
    }
    case "selectCard":
      return {
        ...base,
        card: {
          definitionId: value.selectedDefinitionId,
          name: nameOf(value.selectedDefinitionId),
        },
      };
    case "rerollDice":
      return {
        ...base,
        dice: value.diceToReroll ?? [],
        diceLabels: (value.diceToReroll ?? []).map((d: number) => diceValueLabel(d)),
      };
    case "switchHands":
      return { ...base, removedCount: (value.removedHandIds ?? []).length };
    default:
      return base;
  }
}

export { nameOf, costFromPb, skillName, buildLookup, entitySummary };
