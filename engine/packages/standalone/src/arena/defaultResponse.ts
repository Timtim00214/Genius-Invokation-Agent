/**
 * 超时/耗时间时的默认行动。
 *
 * 行动 -→ 优先「过一手」（把行动权交给对手，不消耗任何资源）；没有可用的过一手时
 * 才退化为「结束回合」。这样烧绳不会再替玩家把整回合的骰子作废。
 * 选人 → 第一个；重投/换牌 → 不换；挑选 → 第一个。
 */
import type { RpcRequest, RpcResponse } from "@gi-tcg/core";

type AnyRpc = RpcRequest & { request: { $case: string; value: any } };
type AnyResponse = RpcResponse & { response: { $case: string; value: any } };

export interface DefaultResponseOptions {
  /**
   * 有「过一手」时是否优先过一手。
   *
   * 人类座位开着（烧绳不该被替玩家弃掉整回合骰子）；AI 座位关掉——
   * 两边同时过一手会互踢皮球，AI 超时还是老老实实结束回合。
   */
  preferPass?: boolean;
}

export function defaultRpcResponse(
  req: RpcRequest,
  options: DefaultResponseOptions = {},
): RpcResponse {
  const { $case, value } = (req as AnyRpc).request;
  switch ($case) {
    case "action": {
      const actions: any[] = value.action ?? [];
      const preferPass = options.preferPass !== false;
      // 有「过一手」就过一手（只是把行动权让给对手），没有才结束回合
      let index = preferPass
        ? actions.findIndex(
            (a) => a.action?.$case === "passAction" && a.validity === 0,
          )
        : -1;
      if (index < 0)
        index = actions.findIndex(
          (a) => a.action?.$case === "declareEnd" && a.validity === 0,
        );
      if (index < 0) index = actions.findIndex((a) => a.validity === 0);
      if (index < 0) index = 0;
      return {
        response: {
          $case: "action",
          value: {
            chosenActionIndex: index,
            usedDice: actions[index]?.autoSelectedDice ?? [],
          },
        },
      } as RpcResponse;
    }
    case "chooseActive":
      return {
        response: {
          $case: "chooseActive",
          value: { activeCharacterId: value.candidateIds?.[0] ?? 0 },
        },
      } as RpcResponse;
    case "selectCard":
      return {
        response: {
          $case: "selectCard",
          value: { selectedDefinitionId: value.candidateDefinitionIds?.[0] ?? 0 },
        },
      } as RpcResponse;
    case "rerollDice":
      return {
        response: { $case: "rerollDice", value: { diceToReroll: [] } },
      } as RpcResponse;
    case "switchHands":
      return {
        response: { $case: "switchHands", value: { removedHandIds: [] } },
      } as RpcResponse;
    default:
      throw new Error(`未知的 rpc: ${$case}`);
  }
}

const CHAR = (id: unknown) => `角色 #${id}`;

/** 把一次 rpc 响应翻译成人类可读的日志。 */
export function describeDecision(req: RpcRequest, res: RpcResponse): string {
  const reqCase = (req as AnyRpc).request.$case;
  const resValue = (res as AnyResponse).response?.value ?? {};
  switch (reqCase) {
    case "action": {
      const actions: any[] = (req as AnyRpc).request.value.action ?? [];
      const index = resValue.chosenActionIndex ?? 0;
      const action = actions[index]?.action;
      switch (action?.$case) {
        case "useSkill":
          return `使用技能 #${action.value.skillDefinitionId}`;
        case "playCard":
          return `打出卡牌 #${action.value.cardDefinitionId}`;
        case "switchActive":
          return `切换到 ${CHAR(action.value.characterId)}`;
        case "elementalTuning":
          return `元素调和（弃 #${action.value.removedCardId}）`;
        case "declareEnd":
          return "结束回合";
        case "passAction":
          return "过一手（交出行动权）";
        default:
          return `行动 #${index}`;
      }
    }
    case "chooseActive":
      return `选择出战 ${CHAR(resValue.activeCharacterId)}`;
    case "rerollDice":
      return `重投 ${resValue.diceToReroll?.length ?? 0} 个骰子`;
    case "switchHands":
      return `替换 ${resValue.removedHandIds?.length ?? 0} 张手牌`;
    case "selectCard":
      return `挑选卡牌 #${resValue.selectedDefinitionId}`;
    default:
      return reqCase;
  }
}

export function sameRpcCase(req: RpcRequest, res: RpcResponse): boolean {
  return (
    (req as AnyRpc).request?.$case === (res as AnyResponse).response?.$case
  );
}
