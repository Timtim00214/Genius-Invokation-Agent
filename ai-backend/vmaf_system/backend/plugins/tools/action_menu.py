# -*- coding: utf-8 -*-
"""
【对局】把候选动作渲染成"编号菜单"（action_menu）。

模型最容易犯的错不是不会打牌，而是**回答格式不对**（多说一句话、给了个不存在的编号）。
本节点把游戏侧的结构化候选（``aiDetail``）压成一份"每个选项带编号"的纯文本菜单，
并同时输出**合法编号集合**，供下游 ``choice_parse`` 做白名单校验。

为什么用"编号"而不是让模型直接输出 JSON 动作：
    - token 少一个数量级，延迟更低（每个决策只有几十秒预算）；
    - 编号是离散小整数，模型选错的概率远低于拼错整段 JSON；
    - 编号 → 动作的映射留在 Python 侧（``rpc_build``），模型永远读不到内部结构。

覆盖的五种请求类型:
    action       出牌 / 用技能 / 元素调和 / 结束回合
    chooseActive 选择出战角色
    selectCard   从候选里选一张牌
    rerollDice   重投骰子（回答要重投哪些骰子）
    switchHands  换手牌（回答要弃掉哪些）

契约:
    输入 :class:`ActionMenuInput` / 输出 :class:`ActionMenuOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = ["ActionMenuInput", "ActionMenuOutput", "ActionMenuNode"]

#: 中文或英文元素名，用于把骰面数字翻译成人话（编号见 enums.ts：3=火、4=雷）。
_ELEMENT_NAMES: Dict[int, str] = {
    0: "任意",
    1: "冰",
    2: "水",
    3: "火",
    4: "雷",
    5: "风",
    6: "岩",
    7: "草",
    8: "万能",
    9: "充能",
    10: "秘传",
}


class ActionMenuInput(BaseModel):
    """编号菜单的输入契约。

    Attributes:
        detail (Dict[str, Any]): 游戏侧结构化视图，连线接 ``arena_input.detail``。
        request (Dict[str, Any]): 原始请求，仅用于在缺少 detail 时兜底判断类型。
        max_items (int): 最多列出多少个候选，超出部分省略（防止提示词被撑爆）。
        include_invalid (bool): 是否也列出当前不合法的候选（默认只列合法的，
            避免模型挑一个"看起来能用但合法性为 0"的选项）。
        show_preview (bool): 是否展示预示伤害／效果（对决策很有帮助，默认开）。
    """

    model_config = {"extra": "forbid"}

    detail: Dict[str, Any] = Field(
        default_factory=dict, description="结构化视图 aiDetail，连线接 arena_input.detail"
    )
    request: Dict[str, Any] = Field(
        default_factory=dict, description="原始请求，仅在 detail 缺失时用于判断类型"
    )
    max_items: int = Field(40, ge=1, le=200, description="最多列出的候选数量")
    include_invalid: bool = Field(
        False,
        description="是否列出合法性不为 0 的候选；默认只列合法项，减少模型踩坑",
    )
    show_preview: bool = Field(True, description="是否展示预示伤害／效果")


class ActionMenuOutput(BaseModel):
    """编号菜单的输出契约。

    Attributes:
        text (str): 给模型看的纯文本菜单（含编号、费用、说明）。
        hint (str): 一行回答要求，直接拼进提示词即可。
        kind (str): 请求类型（action / chooseActive / selectCard / rerollDice / switchHands）。
        valid_indexes (List[int]): 合法编号集合，连线接 ``choice_parse.valid_indexes``。
        total (int): 候选总数（未过滤前）。
        shown (int): 实际展示的候选数。
        empty (bool): 没有任何可展示的候选（此时应走兜底）。
    """

    model_config = {"extra": "forbid"}

    text: str = Field(..., description="给模型看的纯文本菜单")
    hint: str = Field("", description="一行回答要求")
    kind: str = Field("", description="请求类型")
    valid_indexes: List[int] = Field(default_factory=list, description="合法编号集合")
    total: int = Field(0, description="候选总数（过滤前）")
    shown: int = Field(0, description="实际展示数量")
    empty: bool = Field(False, description="是否没有任何可展示候选")


def _rpc_kind(detail: Dict[str, Any], request: Dict[str, Any]) -> str:
    """推断当前请求类型。

    Args:
        detail (Dict[str, Any]): 结构化视图。
        request (Dict[str, Any]): 原始请求。

    Returns:
        str: 请求类型标识。
    """
    kind = str(detail.get("rpc") or "")
    if kind:
        return kind
    inner = request.get("request") if isinstance(request, dict) else None
    if isinstance(inner, dict):
        return str(inner.get("$case") or "")
    return str(request.get("$case") or "") if isinstance(request, dict) else ""


def _cost_text(cost: Any) -> str:
    """把费用结构渲染成简短文本。

    Args:
        cost (Any): 形如 ``{"dice": [...], "total": 3, "text": "3 火"}`` 的费用结构。

    Returns:
        str: 例如 ``"费用 3（火火万能）"``；无费用时返回空字符串。
    """
    if not isinstance(cost, dict):
        return ""
    total = cost.get("total")
    text = str(cost.get("text") or "")
    dice = cost.get("dice")
    detail = ""
    if isinstance(dice, list) and dice:
        names = []
        for d in dice:
            if isinstance(d, int):
                names.append(_ELEMENT_NAMES.get(d, str(d)))
            elif isinstance(d, dict):
                names.append(_ELEMENT_NAMES.get(int(d.get("diceType") or 0), str(d)))
        if names:
            detail = "".join(names)
    if total and detail:
        return f"费用 {total}（{detail}）"
    if total:
        return f"费用 {total}"
    if detail:
        return f"费用（{detail}）"
    return f"费用 {text}" if text else ""


def _preview_text(preview: Any) -> str:
    """渲染预示效果（伤害/治疗等）。

    Args:
        preview (Any): ``[{type, target, amount, element}]`` 列表。

    Returns:
        str: 例如 ``"预示：伤害 2（火）→ 对方出战"``；无预示时返回空字符串。
    """
    if not isinstance(preview, list) or not preview:
        return ""
    parts = []
    for item in preview:
        if not isinstance(item, dict):
            continue
        amount = item.get("amount")
        element = item.get("element")
        target = item.get("target")
        kind = item.get("type")
        seg = f"{kind}"
        if amount is not None:
            seg += f" {amount}"
        if element:
            seg += f"（{_ELEMENT_NAMES.get(int(element), element) if isinstance(element, int) else element}）"
        if target:
            seg += f" → {target}"
        parts.append(seg)
    return "预示：" + "；".join(parts) if parts else ""


class ActionMenuNode(BaseNode):
    """把 aiDetail 的候选压成编号菜单。"""

    node_type = "action_menu"
    description = "把候选动作渲染成带编号的菜单文本，并给出合法编号集合"
    input_model = ActionMenuInput
    output_model = ActionMenuOutput

    async def execute(
        self,
        inputs: ActionMenuInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> ActionMenuOutput:
        """按请求类型渲染菜单。

        Args:
            inputs (ActionMenuInput): 结构化视图与显示参数。
            context (Optional[Dict[str, Any]]): 未使用。

        Returns:
            ActionMenuOutput: 菜单文本、回答要求与合法编号集合。
        """
        detail = inputs.detail if isinstance(inputs.detail, dict) else {}
        kind = _rpc_kind(detail, inputs.request)

        if kind == "action":
            return self._action_menu(detail, inputs)
        if kind == "chooseActive":
            return self._candidate_menu(
                detail,
                inputs,
                kind=kind,
                items=list(detail.get("candidates") or []),
                label_of=lambda c: f"{c.get('name') or c.get('id')}（生命 {c.get('health', '?')}/{c.get('maxHealth', '?')}）",
                id_key="id",
                hint="只回答一个编号：要派上场的角色。",
            )
        if kind == "selectCard":
            return self._candidate_menu(
                detail,
                inputs,
                kind=kind,
                items=list(detail.get("candidates") or []),
                label_of=lambda c: str(c.get("name") or c.get("definitionId")),
                id_key="definitionId",
                hint="只回答一个编号：要选择的卡牌。",
            )
        return self._special_menu(detail, kind)

    # ------------------------------------------------------------------
    # 各类菜单
    # ------------------------------------------------------------------
    def _action_menu(self, detail: Dict[str, Any], inputs: ActionMenuInput) -> ActionMenuOutput:
        """渲染行动菜单（出牌 / 技能 / 调和 / 结束回合）。

        Args:
            detail (Dict[str, Any]): 结构化视图。
            inputs (ActionMenuInput): 显示参数。

        Returns:
            ActionMenuOutput: 菜单与合法编号。
        """
        actions = [a for a in (detail.get("actions") or []) if isinstance(a, dict)]
        lines: List[str] = []
        valid: List[int] = []
        shown = 0
        for item in actions:
            index = item.get("index")
            validity = int(item.get("validity") or 0)
            if not isinstance(index, int):
                continue
            if validity != 0 and not inputs.include_invalid:
                continue
            if shown >= inputs.max_items:
                break
            shown += 1
            if validity == 0:
                valid.append(index)
            bits = [f"[{index}] {item.get('name') or item.get('kind')}"]
            if item.get("kind"):
                bits.append(f"类型={item.get('kind')}")
            cost = _cost_text(item.get("cost"))
            if cost:
                bits.append(cost)
            if inputs.show_preview:
                preview = _preview_text(item.get("preview"))
                if preview:
                    bits.append(preview)
            if validity != 0:
                bits.append(f"× 不可用：{item.get('validityLabel') or validity}")
            lines.append("  " + " ｜ ".join(bits))

        head = [f"【可选行动】共 {len(actions)} 项，列出 {shown} 项"]
        active = detail.get("activeCharacter")
        if isinstance(active, dict) and active.get("name"):
            head.append(f"当前出战：{active.get('name')}")
        if detail.get("declaredEnd"):
            head.append("（你已宣布结束回合）")
        if detail.get("disabledBy"):
            names = "、".join(str(d.get("name") or d.get("id")) for d in detail["disabledBy"] if isinstance(d, dict))
            if names:
                head.append(f"被禁用：{names}")
        if detail.get("note"):
            head.append(str(detail["note"]))

        if not lines:
            lines.append("  （没有可用选项）")
        text = "\n".join(head) + "\n" + "\n".join(lines)
        return ActionMenuOutput(
            text=text,
            hint="只回答一个方括号里的编号数字（例如 3）；不要解释，不要写别的字。",
            kind="action",
            valid_indexes=valid,
            total=len(actions),
            shown=shown,
            empty=not valid,
        )

    def _candidate_menu(
        self,
        detail: Dict[str, Any],
        inputs: ActionMenuInput,
        *,
        kind: str,
        items: List[Dict[str, Any]],
        label_of,
        id_key: str,
        hint: str,
    ) -> ActionMenuOutput:
        """渲染"从候选里挑一个"类菜单（选出战角色 / 选牌）。

        Args:
            detail (Dict[str, Any]): 结构化视图（用于补充说明）。
            inputs (ActionMenuInput): 显示参数。
            kind (str): 请求类型。
            items (List[Dict[str, Any]]): 候选列表。
            label_of (Callable[[Dict[str, Any]], str]): 候选转文本。
            id_key (str): 候选的主键字段名。
            hint (str): 回答要求。

        Returns:
            ActionMenuOutput: 菜单与合法编号（这里等于候选下标，因为候选没有自带 index）。
        """
        lines: List[str] = []
        valid: List[int] = []
        shown = 0
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if shown >= inputs.max_items:
                break
            shown += 1
            valid.append(position)
            lines.append(f"  [{position}] {label_of(item)}")
        if detail.get("note"):
            lines.append(f"  （{detail['note']}）")
        if not lines:
            lines.append("  （没有候选）")
        text = f"【可选{kind}】共 {len(items)} 项\n" + "\n".join(lines)
        return ActionMenuOutput(
            text=text,
            hint=hint,
            kind=kind,
            valid_indexes=valid,
            total=len(items),
            shown=shown,
            empty=not valid,
        )

    def _special_menu(self, detail: Dict[str, Any], kind: str) -> ActionMenuOutput:
        """渲染骰子重投 / 换手牌这两类"清单式"菜单。

        这两类请求的答案不是"挑一个编号"，而是"挑一组对象"，
        因此合法编号集合留空，由 ``rpc_build`` 走默认（空集合）兜底；
        菜单文本仍说明清楚规则，供模型自行决定是否要重投/换牌。

        Args:
            detail (Dict[str, Any]): 结构化视图。
            kind (str): 请求类型。

        Returns:
            ActionMenuOutput: 说明文本（无编号）。
        """
        if kind == "rerollDice":
            dice = detail.get("dice") or []
            labels = detail.get("diceLabels") or []
            shown = "、".join(str(x) for x in labels) if labels else "、".join(str(x) for x in dice)
            attempt = detail.get("attempt")
            text = (
                f"【掷骰阶段】当前骰子：{shown or '（无）'}\n"
                f"  可以重投其中若干枚（重投后不可撤销）。"
                + (f"当前是第 {int(attempt) + 1} 次重投机会。" if isinstance(attempt, int) else "")
                + ("\n  " + str(detail["note"]) if detail.get("note") else "")
            )
            return ActionMenuOutput(
                text=text,
                hint="若不需要重投，回答空集合即可；需要重投时按骰面类型挑选。",
                kind=kind,
                valid_indexes=[],
                total=len(dice),
                shown=len(dice),
                empty=not dice,
            )

        if kind == "switchHands":
            hand = [c for c in (detail.get("hand") or []) if isinstance(c, dict)]
            lines = [f"  [{i}] {c.get('name') or c.get('definitionId')}" for i, c in enumerate(hand)]
            text = f"【换牌阶段】手牌 {len(hand)} 张\n" + "\n".join(lines or ["  （无手牌）"])
            return ActionMenuOutput(
                text=text,
                hint="若不需要换牌，回答空集合即可；需要换牌时按序号挑选要弃掉的牌。",
                kind=kind,
                valid_indexes=[],
                total=len(hand),
                shown=len(hand),
                empty=not hand,
            )

        return ActionMenuOutput(
            text=f"【未知请求类型：{kind or '空'}】\n  请走兜底逻辑。",
            hint="",
            kind=kind,
            valid_indexes=[],
            total=0,
            shown=0,
            empty=True,
        )
