/**
 * 冒烟策略：把"合法但无脑"的决策拼出来，并做本地形状校验。
 *
 * 存在的理由：
 *   1. M0 阶段先用它跑通协议（不接模型就能打完整一局）；
 *   2. 一旦模型/司机出错，它就是兜底——桥接只校验形状，形状对了但语义错会被
 *      Game 判 IO 错误（GiTcgIoError = 可能判负），所以提交前必须自己先验。
 */

/** validity 标签（与 bridge.mjs 的 VALIDITY_LABELS 一致） */
export const VALIDITY_LABELS = {
  0: "合法",
  1: "条件不满足",
  2: "没有可选目标",
  3: "骰子不足",
  4: "充能不足",
  5: "被技能/状态禁用",
  6: "本局秘传牌已用过",
};

export const RPC_CASES = ["action", "rerollDice", "switchHands", "chooseActive", "selectCard"];

function rpcOf(request) {
  return request?.request?.$case ?? request?.$case ?? null;
}

function valueOf(request) {
  return request?.request?.value ?? request?.value ?? {};
}

/**
 * 兜底行动的价值粗排：能造成影响的动作永远排在"结束回合"前面。
 *
 * 和服务端 timeoutRpc 的默认策略刻意不同：那边一律先结束回合，实测会在
 * "还有 5–8 颗骰子没花"的情况下替 AI 弃牌弃骰（夜间那局 15 手有 5 手这样）。
 * 这里改成：只要有非结束回合的合法动作，就按"伤害 > 召唤/充能 > 出牌 > 调和 > 切人"
 * 挑一个最像样的交上去；只有真的只剩结束回合时才结束。
 */
const FALLBACK_KIND_SCORES = {
  useSkill: 60,
  playCard: 55,
  elementalTuning: 40,
  switchActive: 30,
};

function fallbackScore(action) {
  const kind = action?.action?.$case ?? "unknown";
  if (kind === "declareEnd") return -1e9;
  let score = FALLBACK_KIND_SCORES[kind] ?? 20;
  const preview = Array.isArray(action?.preview) ? action.preview : [];
  for (const p of preview) {
    const mutation = p?.mutation;
    if (!mutation) continue;
    const val = mutation.value ?? {};
    const amount = Number(val.value ?? val.amount ?? 0) || 0;
    switch (mutation.$case) {
      case "damage":
        score += 10 + 6 * amount;
        break;
      case "heal":
      case "shield":
        score += 4 + 3 * amount;
        break;
      case "summon":
      case "createSummon":
        score += 6;
        break;
      case "defeated":
        score += 8;
        break;
      case "modifyEntityVar":
        score += val.variable === "energy" ? 4 * Math.max(amount, 1) : 2;
        break;
      default:
        score += 1;
    }
  }
  const cost = action?.requiredCost?.total;
  if (Number.isFinite(cost)) score -= 0.5 * cost;
  return score;
}

/** 兜底：行动→按价值挑一个合法非结束动作（真没有才结束）；选人/挑牌→第一个候选；重投/换牌→空。 */
export function fallbackResponse(request) {
  const rpc = rpcOf(request);
  const value = valueOf(request);
  switch (rpc) {
    case "action": {
      const actions = value.action ?? [];
      const valid = actions
        .map((a, i) => ({ a, i }))
        .filter(({ a }) => (a?.validity ?? 0) === 0 && a?.action?.$case !== "declareEnd");
      let index;
      if (valid.length) {
        valid.sort((x, y) => fallbackScore(y.a) - fallbackScore(x.a) || x.i - y.i);
        index = valid[0].i;
      } else {
        index = actions.findIndex((a) => a.action?.$case === "declareEnd" && a.validity === 0);
        if (index < 0) index = actions.findIndex((a) => a.validity === 0);
      }
      if (index < 0) index = 0;
      const action = actions[index];
      return {
        response: {
          $case: "action",
          value: {
            chosenActionIndex: Math.max(index, 0),
            usedDice: action?.autoSelectedDice ?? [],
          },
        },
      };
    }
    case "chooseActive":
      return {
        response: {
          $case: "chooseActive",
          value: { activeCharacterId: value.candidateIds?.[0] ?? 0 },
        },
      };
    case "selectCard":
      return {
        response: {
          $case: "selectCard",
          value: { selectedDefinitionId: value.candidateDefinitionIds?.[0] ?? 0 },
        },
      };
    case "rerollDice":
      return { response: { $case: "rerollDice", value: { diceToReroll: [] } } };
    case "switchHands":
      return { response: { $case: "switchHands", value: { removedHandIds: [] } } };
    default:
      return null;
  }
}

/**
 * echo 策略：能打就打（优先合法技能），不能打就结束回合。
 * 目的只是让对局自己跑起来，验证协议；不代表任何"棋力"。
 * @param {any} detail 桥接给的 aiDetail（含 actions 的 index/name/validity）
 */
export function echoResponse(request, detail) {
  const rpc = rpcOf(request);
  if (rpc !== "action") return fallbackResponse(request);
  const actions = detail?.actions ?? [];
  const value = valueOf(request);
  const raw = value.action ?? [];
  // 优先选一个合法的、非 declareEnd 的行动；没有就结束回合
  const playable = actions.filter((a) => a.validity === 0 && a.kind !== "declareEnd");
  if (playable.length) {
    const pick = playable[Math.floor(Math.random() * playable.length)];
    return { response: { $case: "action", value: { chosenActionIndex: pick.index } } };
  }
  const end = actions.find((a) => a.validity === 0 && a.kind === "declareEnd");
  if (end) return { response: { $case: "action", value: { chosenActionIndex: end.index } } };
  const firstValid = actions.find((a) => a.validity === 0);
  if (firstValid) {
    return { response: { $case: "action", value: { chosenActionIndex: firstValid.index } } };
  }
  // 一个合法行动都没有（理论上不会发生，规则允许认输）；退化为第 0 个，让服务端 422 告诉我们原因
  return { response: { $case: "action", value: { chosenActionIndex: 0 } } };
}

/**
 * 本地形状校验——镜像 bridge.mjs:validateResponse 的检查，但更严格：
 * 提交前就拦住，避免"形状对语义错"被 Game 判 IO 错误。
 * @returns {null | string} null 表示通过
 */
export function checkShape(request, response) {
  const rpc = rpcOf(request);
  if (!rpc) return "request 缺失";
  if (!response?.response) return "response 缺失（必须是 {response:{$case,value}}）";
  const res = response.response;
  if (res.$case !== rpc) return `响应类型不匹配：请求 ${rpc}，响应 ${res.$case}`;
  const value = res.value ?? {};
  const reqValue = valueOf(request);
  switch (rpc) {
    case "action": {
      const actions = reqValue.action ?? [];
      const index = value.chosenActionIndex;
      if (!Number.isInteger(index)) return `chosenActionIndex 不是整数：${JSON.stringify(index)}`;
      if (index < 0 || index >= actions.length) return `行动下标越界：${index}（共 ${actions.length} 个）`;
      const validity = actions[index]?.validity;
      if (validity !== 0) {
        return `第 ${index} 个行动不可用：${VALIDITY_LABELS[validity] ?? validity}`;
      }
      return null;
    }
    case "chooseActive": {
      const candidates = reqValue.candidateIds ?? [];
      if (!candidates.includes(value.activeCharacterId)) {
        return `出战角色 ${value.activeCharacterId} 不在候选 ${JSON.stringify(candidates)} 里`;
      }
      return null;
    }
    case "selectCard": {
      const candidates = reqValue.candidateDefinitionIds ?? [];
      if (!candidates.includes(value.selectedDefinitionId)) {
        return `卡牌 ${value.selectedDefinitionId} 不在候选 ${JSON.stringify(candidates)} 里`;
      }
      return null;
    }
    case "rerollDice": {
      if (!Array.isArray(value.diceToReroll)) return "diceToReroll 必须是数组";
      return null;
    }
    case "switchHands": {
      if (!Array.isArray(value.removedHandIds)) return "removedHandIds 必须是数组";
      return null;
    }
    default:
      return `未知 rpc: ${rpc}`;
  }
}

/**
 * 把 422 的反馈变成"下一次该怎么办"的约束，供重试使用。
 * @returns {{availableIndexes: number[]|null, reason: string}} 
 */
export function feedbackOf(json) {
  const availableIndexes = Array.isArray(json?.availableActions)
    ? json.availableActions.filter((a) => (a.validity ?? 0) === 0).map((a) => a.index)
    : null;
  return {
    availableIndexes,
    reason: String(json?.reason ?? json?.error ?? "invalid response"),
  };
}

/** 把一次请求渲染成人类可读的小抄（调试/日志用，也是后面喂模型的原型）。 */
export function describeRequest(request, detail) {
  const rpc = rpcOf(request);
  if (rpc === "action" && Array.isArray(detail?.actions)) {
    const lines = detail.actions.map((a) => {
      const mark = a.validity === 0 ? " " : "x";
      const cost = a.cost?.text ? ` 费用:${a.cost.text}` : "";
      const pv = Array.isArray(a.preview) && a.preview.length
        ? ` 预示:${a.preview.map((p) => `${p.type}${p.amount ?? ""}`).join(",")}`
        : "";
      return `[${mark}]${a.index} ${a.name ?? a.kind}${cost}${pv}`;
    });
    return lines.join("\n");
  }
  if (rpc === "chooseActive") {
    return (detail?.candidates ?? []).map((c) => `- ${c.id} ${c.name ?? ""} ${c.health ?? "?"}/${c.maxHealth ?? "?"}`).join("\n");
  }
  if (rpc === "selectCard") {
    return (detail?.candidates ?? []).map((c) => `- ${c.definitionId} ${c.name ?? ""}`).join("\n");
  }
  return detail?.note ?? rpc ?? "?";
}

/** 决策 → 存储用的紧凑描述 */
export function describeDecision(request, response) {
  const rpc = rpcOf(request);
  const value = response?.response?.value ?? {};
  if (rpc === "action") return { kind: "action", index: value.chosenActionIndex };
  if (rpc === "chooseActive") return { kind: "chooseActive", id: value.activeCharacterId };
  if (rpc === "selectCard") return { kind: "selectCard", definitionId: value.selectedDefinitionId };
  if (rpc === "rerollDice") return { kind: "rerollDice", dice: value.diceToReroll ?? [] };
  if (rpc === "switchHands") return { kind: "switchHands", count: (value.removedHandIds ?? []).length };
  return { kind: rpc ?? "unknown" };
}
