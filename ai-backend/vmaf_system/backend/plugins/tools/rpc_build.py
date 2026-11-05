# -*- coding: utf-8 -*-
"""
【提交】把编号补成桥接能接受的完整 RpcResponse（rpc_build）。

模型只回一个编号，但游戏桥接要的是一份完整 protobuf 响应：

    {"response": {"$case": "action", "value": {"chosenActionIndex": 3, "usedDice": [...]}}}

本节点负责这最后一跳，并且**绝不把明显非法的东西发出去**：
编号不在合法集合里、候选缺失、类型对不上时，它会退回"游戏自己也会做"的默认动作
（`action` 已宣布结束 → 结束回合；否则第一个合法动作；选人/选牌取第一个候选；
重投/换牌给空集合）。宁可让这一手平淡，也不要因为格式错误被判 IO 错误。

用法提示:
    - 默认动作是**最后一道保险**，不是懒惰：司机已经做过预算检查，
      走到这里说明还有时间，只是模型没给出可用的答案。
    - 想让 AI 更保守/激进，改上游 ``action_menu`` 的过滤条件或提示词，
      而不是在这里加规则——本节点只做形状转换与合法性兜底。

契约:
    输入 :class:`RpcBuildInput` / 输出 :class:`RpcBuildOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = ["RpcBuildInput", "RpcBuildOutput", "RpcBuildNode"]

#: 视为"结束回合"的动作类型/名称关键字（用于兜底优先级）。
_END_TURN_KINDS: frozenset = frozenset({"declareEnd", "endTurn", "declare_end"})
_END_TURN_NAMES: tuple = ("结束回合", "宣布结束")

#: 兜底时的动作类型基础分：能造成影响的动作永远排在"结束回合"前面。
_FALLBACK_KIND_SCORES: Dict[str, float] = {
    "useSkill": 60.0,
    "playCard": 55.0,
    "elementalTuning": 40.0,
    "switchActive": 30.0,
}

#: 预示里视为"直接伤害 / 治疗 / 护盾"的类型（与 action_rank 的判定口径一致）。
_DAMAGE_TYPES: frozenset = frozenset({"damage", "pierce", "伤害", "穿透"})
_HEAL_TYPES: frozenset = frozenset({"heal", "治疗"})
_SHIELD_TYPES: frozenset = frozenset({"shield", "护盾"})


class RpcBuildInput(BaseModel):
    """响应构造的输入契约。

    Attributes:
        request (Dict[str, Any]): 本回合的 RpcRequest，连线接 ``arena_input.request``。
        detail (Dict[str, Any]): 结构化视图，连线接 ``arena_input.detail``（用于查候选与便宜骰子）。
        index (Optional[int]): 解析出的编号，连线接 ``choice_parse.index``。
        used_dice (Optional[List[int]]): 指定使用的骰子；留空则用该动作自带的 ``autoSelectedDice``。
        allow_fallback (bool): 编号非法时是否退回默认动作（默认 True）。
    """

    model_config = {"extra": "forbid"}

    request: Dict[str, Any] = Field(default_factory=dict, description="RpcRequest，连线接 arena_input.request")
    detail: Dict[str, Any] = Field(default_factory=dict, description="结构化视图 aiDetail")
    index: Optional[int] = Field(None, description="解析出的编号，连线接 choice_parse.index")
    used_dice: Optional[List[int]] = Field(
        None, description="指定使用的骰子；留空则用动作自带的 autoSelectedDice（推荐留空）"
    )
    allow_fallback: bool = Field(True, description="编号非法时是否退回默认动作")


class RpcBuildOutput(BaseModel):
    """响应构造的输出契约。

    Attributes:
        response (Dict[str, Any]): 完整 RpcResponse，司机会原样提交给桥接。
        kind (str): 请求类型。
        ok (bool): 是否使用了模型给出的编号（False 表示走了兜底）。
        used_fallback (bool): 是否走了默认动作。
        note (str): 中文说明，写进日志便于复盘。
    """

    model_config = {"extra": "forbid"}

    response: Dict[str, Any] = Field(..., description="完整 RpcResponse")
    kind: str = Field("", description="请求类型")
    ok: bool = Field(False, description="是否采用了模型给出的编号")
    used_fallback: bool = Field(False, description="是否退回默认动作")
    note: str = Field("", description="中文说明")


def _rpc_kind(request: Dict[str, Any], detail: Dict[str, Any]) -> str:
    """推断请求类型。

    Args:
        request (Dict[str, Any]): RpcRequest。
        detail (Dict[str, Any]): 结构化视图。

    Returns:
        str: 请求类型标识。
    """
    inner = request.get("request") if isinstance(request, dict) else None
    if isinstance(inner, dict) and inner.get("$case"):
        return str(inner["$case"])
    if isinstance(request, dict) and request.get("$case"):
        return str(request["$case"])
    return str(detail.get("rpc") or "") if isinstance(detail, dict) else ""


def _fallback_score(item: Dict[str, Any]) -> float:
    """给"兜底候选"打一个粗分：有伤害/召唤/充能/出牌的排在结束回合前面。

    与服务端 ``timeoutRpc`` 的默认策略（一律先结束回合）刻意不同：实测那样会在
    "还有 5–8 颗骰子没花"时替 AI 弃骰，这才是"提前宣布结束"的主因之一。

    Args:
        item (Dict[str, Any]): ``aiDetail.actions`` 里的一项。

    Returns:
        float: 分数；结束回合为 ``-1e9``，保证只在别无选择时被选中。
    """
    if _is_end_turn(item):
        return -1e9
    kind = str(item.get("kind") or "")
    score = _FALLBACK_KIND_SCORES.get(kind, 20.0)
    preview = item.get("preview")
    if isinstance(preview, list):
        for entry in preview:
            if not isinstance(entry, dict):
                continue
            etype = str(entry.get("type") or "").lower()
            try:
                amount = float(entry.get("amount") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            if etype in _DAMAGE_TYPES:
                score += 10.0 + 6.0 * amount
            elif etype in _HEAL_TYPES or etype in _SHIELD_TYPES:
                score += 4.0 + 3.0 * amount
            elif etype in {"summon", "createsummon", "召唤"}:
                score += 6.0
            elif etype == "modifyentityvar" and str(entry.get("variable") or "") == "energy":
                score += 4.0 * max(amount, 1.0)
            else:
                score += 1.0
    cost = item.get("cost")
    if isinstance(cost, dict):
        total = cost.get("total")
        if isinstance(total, (int, float)):
            score -= 0.5 * float(total)
    return score


def _is_end_turn(item: Dict[str, Any]) -> bool:
    """判断一个候选动作是否是"结束回合"。

    Args:
        item (Dict[str, Any]): 单个候选动作。

    Returns:
        bool: 是结束回合则为 True。
    """
    kind = str(item.get("kind") or "")
    name = str(item.get("name") or "")
    if kind in _END_TURN_KINDS:
        return True
    return any(word in name for word in _END_TURN_NAMES)


class RpcBuildNode(BaseNode):
    """把编号补成完整 RpcResponse，并做合法性兜底。"""

    node_type = "rpc_build"
    description = "把编号补成完整 RpcResponse（含默认动作兜底），保证形状一定能被桥接接受"
    input_model = RpcBuildInput
    output_model = RpcBuildOutput

    async def execute(
        self,
        inputs: RpcBuildInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> RpcBuildOutput:
        """按请求类型构造响应。

        Args:
            inputs (RpcBuildInput): 请求、结构化视图与编号。
            context (Optional[Dict[str, Any]]): 未使用。

        Returns:
            RpcBuildOutput: 完整响应与说明。
        """
        request = inputs.request if isinstance(inputs.request, dict) else {}
        detail = inputs.detail if isinstance(inputs.detail, dict) else {}
        kind = _rpc_kind(request, detail)

        if kind == "action":
            return self._action(request, detail, inputs)
        if kind == "chooseActive":
            return self._pick_one(
                kind, "activeCharacterId", detail.get("candidates"), inputs, request
            )
        if kind == "selectCard":
            return self._pick_one(
                kind, "selectedDefinitionId", detail.get("candidates"), inputs, request
            )
        if kind == "rerollDice":
            dice = list(inputs.used_dice or [])
            return RpcBuildOutput(
                response={"response": {"$case": kind, "value": {"diceToReroll": dice}}},
                kind=kind,
                ok=True,
                used_fallback=False,
                note="不重投" if not dice else f"重投 {len(dice)} 枚骰子",
            )
        if kind == "switchHands":
            removed = list(inputs.used_dice or [])
            return RpcBuildOutput(
                response={"response": {"$case": kind, "value": {"removedHandIds": removed}}},
                kind=kind,
                ok=True,
                used_fallback=False,
                note="不换牌" if not removed else f"换掉 {len(removed)} 张手牌",
            )

        return RpcBuildOutput(
            response={},
            kind=kind,
            ok=False,
            used_fallback=True,
            note=f"未知请求类型 {kind or '空'}，无法构造响应",
        )

    # ------------------------------------------------------------------
    # 各类构造
    # ------------------------------------------------------------------
    def _action(
        self, request: Dict[str, Any], detail: Dict[str, Any], inputs: RpcBuildInput
    ) -> RpcBuildOutput:
        """构造行动响应（出牌 / 技能 / 调和 / 结束回合）。

        Args:
            request (Dict[str, Any]): RpcRequest。
            detail (Dict[str, Any]): 结构化视图。
            inputs (RpcBuildInput): 输入参数。

        Returns:
            RpcBuildOutput: 行动响应。
        """
        actions = [a for a in (detail.get("actions") or []) if isinstance(a, dict)]
        by_index: Dict[int, Dict[str, Any]] = {}
        valid: List[Dict[str, Any]] = []
        for item in actions:
            idx = item.get("index")
            if not isinstance(idx, int):
                continue
            by_index[idx] = item
            if int(item.get("validity") or 0) == 0:
                valid.append(item)

        chosen: Optional[Dict[str, Any]] = None
        ok = False
        if inputs.index is not None and inputs.index in by_index:
            candidate = by_index[inputs.index]
            if int(candidate.get("validity") or 0) == 0:
                chosen = candidate
                ok = True

        note = ""
        if chosen is None:
            if not inputs.allow_fallback:
                return RpcBuildOutput(
                    response={},
                    kind="action",
                    ok=False,
                    used_fallback=False,
                    note=f"编号 {inputs.index} 不在合法集合内，且未允许兜底",
                )
            # 只在"真的没有别的合法动作"时才结束回合；否则按价值挑一个最像样的
            playable = [
            a
            for a in valid
            if not _is_end_turn(a) and str(a.get("kind") or "") != "passAction"
        ]
            if playable:
                chosen = min(
                    playable,
                    key=lambda a: (-_fallback_score(a), int(a.get("index") or 0)),
                )
            else:
                chosen = valid[0] if valid else None
            note = (
                f"编号 {inputs.index} 不可用，已兜底为「{chosen.get('name')}」"
                if chosen
                else "没有任何合法动作"
            )

        if chosen is None:
            # 连一个合法动作都没有：只能给 0 与空骰子，桥接会回 422，司机再兜底。
            return RpcBuildOutput(
                response={
                    "response": {
                        "$case": "action",
                        "value": {"chosenActionIndex": 0, "usedDice": []},
                    }
                },
                kind="action",
                ok=False,
                used_fallback=True,
                note="没有合法动作，发送 0 号与空骰子（预期会被 422 拒绝）",
            )

        dice = inputs.used_dice
        if dice is None:
            dice = list(chosen.get("autoSelectedDice") or [])
        value = {
            "chosenActionIndex": int(chosen.get("index")),
            "usedDice": [int(d) for d in dice],
        }
        return RpcBuildOutput(
            response={"response": {"$case": "action", "value": value}},
            kind="action",
            ok=ok,
            used_fallback=not ok,
            note=note or f"采用编号 {inputs.index}：{chosen.get('name')}",
        )

    def _pick_one(
        self,
        kind: str,
        field: str,
        candidates: Any,
        inputs: RpcBuildInput,
        request: Dict[str, Any],
    ) -> RpcBuildOutput:
        """构造"从候选里挑一个"类响应（选出战角色 / 选牌）。

        Args:
            kind (str): 请求类型。
            field (str): 响应值里的字段名（``activeCharacterId`` / ``selectedDefinitionId``）。
            candidates (Any): 候选列表。
            inputs (RpcBuildInput): 输入参数。
            request (Dict[str, Any]): RpcRequest（仅用于日志）。

        Returns:
            RpcBuildOutput: 响应。
        """
        items = [c for c in (candidates or []) if isinstance(c, dict)]
        key = "id" if field == "activeCharacterId" else "definitionId"
        picked: Optional[Dict[str, Any]] = None
        ok = False
        if inputs.index is not None and 0 <= inputs.index < len(items):
            picked = items[inputs.index]
            ok = True
        note = ""
        if picked is None:
            if not inputs.allow_fallback:
                return RpcBuildOutput(
                    response={},
                    kind=kind,
                    ok=False,
                    used_fallback=False,
                    note=f"编号 {inputs.index} 超出候选范围，且未允许兜底",
                )
            picked = items[0] if items else None
            note = f"编号 {inputs.index} 不可用，已兜底为第一个候选"

        if picked is None or picked.get(key) is None:
            return RpcBuildOutput(
                response={},
                kind=kind,
                ok=False,
                used_fallback=True,
                note=f"没有可用的候选（{kind}），无法构造响应",
            )
        return RpcBuildOutput(
            response={"response": {"$case": kind, "value": {field: picked[key]}}},
            kind=kind,
            ok=ok,
            used_fallback=not ok,
            note=note or f"采用候选 {inputs.index}：{picked.get('name') or picked.get(key)}",
        )
