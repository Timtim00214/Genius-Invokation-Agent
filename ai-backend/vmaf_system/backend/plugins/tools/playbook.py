# -*- coding: utf-8 -*-
"""
【对局】按当前可选的动作类别，拼出"打法要求"文本（playbook）。

为什么单独一个节点:
    提示词里的"策略"部分是要**反复改**的（哪张牌该打、骰子怎么花、什么时候收手），
    而"局面""菜单""编号"是机器生成的。把可编辑的策略文字集中在一个节点的参数里，
    就能在网页上直接改、改完下一手就生效（司机每回合重读蓝图），不用动任何代码。

    更重要的是：不同类别的动作需要**不同的**指导。一次行动里可能同时有
    "用技能 / 出牌 / 调和骰子 / 切换出战 / 结束回合"，混成一段话必然互相干扰；
    本节点按"当前菜单里真实出现的类别"只取对应的那几段。

留白原则:
    所有 ``*_rule`` 字段默认都是空字符串——**策略由使用者撰写**。
    这里只负责"选段 + 拼接 + 标注类别"，不预设立场。

契约:
    输入 :class:`PlaybookInput` / 输出 :class:`PlaybookOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = ["PlaybookInput", "PlaybookOutput", "PlaybookNode"]

#: 行动类请求里"动作类别"→ 段名。顺序即拼接顺序。
_ACTION_RULES: List[tuple] = [
    ("useSkill", "用技能"),
    ("playCard", "出牌"),
    ("elementalTuning", "调和骰子"),
    ("switchActive", "切换出战"),
    ("declareEnd", "结束回合"),
]

#: 其他请求类型 → 段名。
_REQUEST_RULES: Dict[str, str] = {
    "chooseActive": "选出战角色",
    "selectCard": "选牌",
    "rerollDice": "重投骰子",
    "switchHands": "换手牌",
}


def _rpc_kind(detail: Dict[str, Any], request: Dict[str, Any]) -> str:
    """推断请求类型。

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


class PlaybookInput(BaseModel):
    """打法要求节点的输入契约。

    每个 ``*_rule`` 都是一段自由文本（可以多行）。留空表示"这一段不提要求"。

    Attributes:
        detail (Dict[str, Any]): 结构化视图，连线接 ``arena_input.detail``（用来判断出现了哪些类别）。
        request (Dict[str, Any]): 原始请求，detail 缺失时兜底判断类型。
        general_rule (str): 通用要求（每手都会带上）。
        use_skill_rule (str): 用技能时的要求。
        play_card_rule (str): 打出手牌时的要求。
        elemental_tuning_rule (str): 元素调和（弃牌换骰）时的要求。
        switch_active_rule (str): 切换出战角色时的要求。
        declare_end_rule (str): 结束回合时的要求。
        choose_active_rule (str): 选出战角色（被击倒后重选）时的要求。
        select_card_rule (str): 从候选里选牌时的要求。
        reroll_dice_rule (str): 掷骰后重投时的要求。
        switch_hands_rule (str): 开局换手牌时的要求。
        footer (str): 拼在最后的固定提醒（默认只有格式要求）。
        only_present (bool): 是否只输出"当前菜单里确实出现"的类别（默认是）。
        max_chars (int): 拼接后的总长度上限，超出截断。
    """

    model_config = {"extra": "forbid"}

    detail: Dict[str, Any] = Field(default_factory=dict, description="结构化视图 aiDetail，用来判断出现了哪些动作类别")
    request: Dict[str, Any] = Field(default_factory=dict, description="原始请求，兜底判断类型")

    general_rule: str = Field("", description="通用要求：每一手都会带上，适合写整体目标（可多行）")
    use_skill_rule: str = Field("", description="使用技能（普通攻击 / 元素战技 / 元素爆发）时的打法要求，可多行书写")
    play_card_rule: str = Field("", description="打出手牌时的打法要求：什么时候该出牌、优先出哪类，可多行书写")
    elemental_tuning_rule: str = Field("", description="元素调和（弃 1 张牌换 1 个骰子）时的打法要求，可多行书写")
    switch_active_rule: str = Field("", description="切换出战角色时的打法要求：什么情况下值得花一个行动换人")
    declare_end_rule: str = Field("", description="结束回合时的打法要求：骰子用到什么程度就该收手，可多行书写")
    choose_active_rule: str = Field("", description="选出战角色（当前角色被击倒后）时的打法要求：优先留谁站场")
    select_card_rule: str = Field("", description="从候选卡牌里选一张时的打法要求：按什么标准取舍，可多行书写")
    reroll_dice_rule: str = Field("", description="掷骰后决定重投哪些骰子时的打法要求：哪些骰面该留、哪些该重投")
    switch_hands_rule: str = Field("", description="开局换掉哪些手牌时的打法要求：哪些牌值得留，可多行书写")

    footer: str = Field(
        "只回答一个方括号里的编号数字，不要解释。",
        description="拼在末尾的固定提醒（默认只约束回答格式）",
    )
    only_present: bool = Field(True, description="只输出当前菜单里出现的类别")
    max_chars: int = Field(2000, ge=50, le=20000, description="拼接结果的最大字符数")


class PlaybookOutput(BaseModel):
    """打法要求节点的输出契约。

    Attributes:
        text (str): 可直接拼进提示词的"打法要求"文本；没有可写内容时为空串。
        sections (List[str]): 实际输出的段名（调试用）。
        chars (int): 文本长度。
        kind (str): 请求类型。
    """

    model_config = {"extra": "forbid"}

    text: str = Field("", description="拼好的打法要求文本")
    sections: List[str] = Field(default_factory=list, description="实际输出的段名")
    chars: int = Field(0, description="文本长度")
    kind: str = Field("", description="请求类型")


class PlaybookNode(BaseNode):
    """按当前动作类别拼出可编辑的"打法要求"。"""

    node_type = "playbook"
    description = "把分类别的打法提示词（出牌/技能/调和/结束回合…）按当前菜单拼成一段文本"
    input_model = PlaybookInput
    output_model = PlaybookOutput

    async def execute(
        self,
        inputs: PlaybookInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> PlaybookOutput:
        """拼接文本。

        Args:
            inputs (PlaybookInput): 各类别提示词与开关。
            context (Optional[Dict[str, Any]]): 未使用。

        Returns:
            PlaybookOutput: 拼好的文本与段名。
        """
        detail = inputs.detail if isinstance(inputs.detail, dict) else {}
        kind = _rpc_kind(detail, inputs.request)
        picked: List[tuple] = []

        general = (inputs.general_rule or "").strip()
        if general:
            picked.append(("通用", general))

        if kind == "action":
            present = {
                str(a.get("kind"))
                for a in (detail.get("actions") or [])
                if isinstance(a, dict) and int(a.get("validity") or 0) == 0
            }
            for action_kind, label in _ACTION_RULES:
                if inputs.only_present and present and action_kind not in present:
                    continue
                rule = (self._rule_of(inputs, action_kind) or "").strip()
                if rule:
                    picked.append((label, rule))
        else:
            label = _REQUEST_RULES.get(kind)
            if label:
                rule = (self._rule_of(inputs, kind) or "").strip()
                if rule:
                    picked.append((label, rule))

        footer = (inputs.footer or "").strip()
        lines: List[str] = []
        for label, rule in picked:
            lines.append(f"- {label}：{rule}" if "\n" not in rule else f"- {label}：\n{rule}")
        text = "【打法要求】\n" + "\n".join(lines) if lines else ""
        if text and footer:
            text += "\n" + footer
        elif not text and footer:
            text = footer
        if len(text) > inputs.max_chars:
            text = text[: inputs.max_chars].rstrip() + "…（已截断）"
        return PlaybookOutput(
            text=text,
            sections=[label for label, _ in picked],
            chars=len(text),
            kind=kind,
        )

    @staticmethod
    def _rule_of(inputs: PlaybookInput, key: str) -> str:
        """按动作类别取出对应的提示词字段。

        Args:
            inputs (PlaybookInput): 输入契约。
            key (str): 类别标识（如 ``useSkill`` / ``chooseActive``）。

        Returns:
            str: 对应字段的内容；未知类别返回空串。
        """
        mapping = {
            "useSkill": inputs.use_skill_rule,
            "playCard": inputs.play_card_rule,
            "elementalTuning": inputs.elemental_tuning_rule,
            "switchActive": inputs.switch_active_rule,
            "declareEnd": inputs.declare_end_rule,
            "chooseActive": inputs.choose_active_rule,
            "selectCard": inputs.select_card_rule,
            "rerollDice": inputs.reroll_dice_rule,
            "switchHands": inputs.switch_hands_rule,
        }
        return mapping.get(key, "")
