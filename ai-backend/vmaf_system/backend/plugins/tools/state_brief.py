"""state_brief：把一份 ExposedGameState 压成一段人/模型都读得懂的简短局面描述。

为什么需要它：``state`` 是 protobuf 的 JSON，字段多达几百个（每张牌的
``descriptionDictionary``、``definitionCost``、``attachment``……），整份塞给模型
既费 token 又全是噪声。模型真正需要的是：这是第几回合、轮到谁、双方出战角色
的血量/能量、我方手牌和骰子、对方还剩几张手牌，以及场上那些支援/召唤物/状态
**到底在干什么**（只有名字等于没说：模型会去猜"烤蘑菇披萨是不是回复 3 血"）。

契约要点：
- ``seat`` 决定谁是我方（``state.player[seat]``）；不传则按 player[0] 当"玩家 1"。
- ``catalog`` 是 ``GET /api/arena/catalog`` 的内容，用来把 ``definitionId`` 换成中文名
  （state 里只有 id，没有名字）。catalog 缺失时退化成 ``角色#1411`` 这种可读 id。
- ``card_text`` 是 ``GET /api/arena/card-text`` 的裁剪结果（definitionId → 效果原文），
  用来给场上实体补一句"它干什么"；缺失时只显示名字，不影响渲染。
- ``detail``（aiDetail）里有 ``diceLabels`` 与出战角色的中文名，优先用它，避免骰面翻译错。
- 兼容两种输入：真实引擎的 ``{phase, currentTurn, player: [p0, p1]}``，以及
  ``{me, opponent}`` 这种已经分好边的简化形状（方便手写测试）。
"""

import re

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import BaseNode


_PHASES = {0: "准备手牌", 1: "选出战", 2: "掷骰", 3: "行动", 4: "结算"}

# 骰子/元素编号（实测 diceLabels 确认）：1冰 2水 3火 4雷 5风 6岩 7草，8 = 万能
_DICE_ELEMENT = {1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草"}
_DICE_SPECIAL = {0: "任意", 8: "万能", 9: "充能", 10: "秘传"}

_VAR_LABELS = {
    "shield": "护盾",
    "count": "计数",
    "usage": "次数",
    "damage": "伤害",
    "hp": "生命",
    "cost": "费用",
}

#: 效果原文里属于"构筑说明"的行：对局面判断没用，删掉省字数。
_META_LINE_HINTS = ("牌组中包含", "整局游戏只能打出", "此牌一定在你的起始手牌中", "最多装备1个")


def _short_effect(card_text: Any, ident: Any, limit: int = 76) -> str:
    """从 card-text 里取一条效果原文，压成一行短句。

    Args:
        card_text (Any): 效果表 ``{"descriptions": {id: 原文}}``（也接受裸 ``{id: 原文}``）。
        ident (Any): 实体的 definitionId（正整数）。
        limit (int): 最多保留多少字符。

    Returns:
        str: 一行效果文本；没有对应条目时返回空串。
    """
    if not isinstance(card_text, dict) or not isinstance(ident, int):
        return ""
    descriptions = card_text.get("descriptions")
    if not isinstance(descriptions, dict):
        descriptions = card_text
    raw = descriptions.get(str(ident))
    if raw is None:
        raw = descriptions.get(ident)
    if not isinstance(raw, str) or not raw.strip():
        return ""
    lines: List[str] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith(("（", "(")):
            continue
        if any(hint in text for hint in _META_LINE_HINTS):
            continue
        text = re.sub(r"^\[[^\]]*\]\s*", "", text)
        if text:
            lines.append(text)
    merged = ""
    for text in lines:
        text = re.sub(r"\(\d+\*\w+\)", "", text).strip()  # 去掉引擎的 "(2*Aligned)" 这类费用标签
        if not text:
            continue
        if merged and not merged.endswith(("。", "；", "！", "？", "，", "：", "、", "…")):
            merged += "；"
        merged += text
    joined = re.sub(r"\s+", " ", merged).strip()
    if len(joined) <= limit:
        return joined
    cut = joined[:limit]
    for sep in ("。", "；", "，"):
        pos = cut.rfind(sep)
        if pos >= int(limit * 0.6):
            cut = cut[:pos]
            break
    return cut.rstrip("；，。 ") + "…"


def _dice_label(value: Any) -> str:
    """把单个 DiceType 换成中文。"""
    if isinstance(value, str):
        return value
    try:
        num = int(value)
    except (TypeError, ValueError):
        return str(value)
    if num in _DICE_ELEMENT:
        return _DICE_ELEMENT[num]
    if num in _DICE_SPECIAL:
        return _DICE_SPECIAL[num]
    return f"骰{num}"


def _build_index(catalog: Any) -> Dict[str, Dict[int, str]]:
    """把 catalog 的几份列表拍平成 {类别: {id: 名字}}。"""
    index: Dict[str, Dict[int, str]] = {"characters": {}, "cards": {}, "tokens": {}, "skills": {}}
    if not isinstance(catalog, dict):
        return index
    for category in ("characters", "cards", "tokens", "skills"):
        items = catalog.get(category)
        # 两种形状都吃：完整 catalog 是 [{id, name, ...}]，司机传的紧凑名字表是 {id: name}
        if isinstance(items, dict):
            for ident, name in items.items():
                try:
                    key = int(ident)
                except (TypeError, ValueError):
                    continue
                if isinstance(name, str) and name:
                    index[category][key] = name
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ident = item.get("id")
            name = item.get("name")
            if isinstance(ident, int) and isinstance(name, str) and name:
                index[category][ident] = name
    return index


def _lookup(index: Dict[str, Dict[int, str]], ident: Any, categories=("characters", "cards", "tokens", "skills")) -> Optional[str]:
    """按 id 在若干类别里找名字。"""
    if not isinstance(ident, int):
        return None
    for category in categories:
        name = index[category].get(ident)
        if name:
            return name
    return None


def _name_of(index, ident: Any, fallback_prefix: str) -> str:
    """找不到名字就退化成 ``前缀#id``，保证模型至少知道"有这么一个东西"。"""
    name = _lookup(index, ident)
    if name:
        return name
    if ident is None:
        return f"{fallback_prefix}#?"
    return f"{fallback_prefix}#{ident}"


def _entity_label(index, entity: Any, card_text: Any = None) -> str:
    """一条 entity（装备/状态/召唤物）→ ``名字 护盾2（效果一句话）`` 这样的文本。"""
    if not isinstance(entity, dict):
        return str(entity)
    label = _name_of(index, entity.get("definitionId"), "实体")
    var_name = entity.get("variableName")
    var_value = entity.get("variableValue")
    if isinstance(var_name, str) and var_name and var_value not in (None, 0):
        label = f"{label} {_VAR_LABELS.get(var_name, var_name)}{var_value}"
    effect = _short_effect(card_text, entity.get("definitionId"))
    if effect:
        label = f"{label}（{effect}）"
    return label


def _entity_list(index, items: Any, card_text: Any = None, limit: int = 6) -> str:
    """把一组 entity 渲染成 ``A、B、C``；超过 limit 就省略。"""
    if not isinstance(items, list) or not items:
        return ""
    labels = [_entity_label(index, item, card_text) for item in items[:limit]]
    if len(items) > limit:
        labels.append(f"等 {len(items)} 个")
    return "、".join(labels)


def _character_line(index, char: Dict[str, Any], active_id: Any, card_text: Any = None) -> str:
    """单个角色一行：名字 血量 能量 [★出战] [✝阵亡] [附着元素]。

    装备/状态不塞进这里的括号——它们的效果原文自己带括号，嵌套两层会读不出来，
    改由 :func:`_character_gear` 单独出一行。
    """
    name = _name_of(index, char.get("definitionId"), "角色")
    health = char.get("health")
    max_health = char.get("maxHealth")
    energy = char.get("energy")
    max_energy = char.get("maxEnergy")

    parts = [f"{name} {health}/{max_health}"]
    if max_energy:
        parts.append(f"能量{energy}/{max_energy}")

    marks = []
    if char.get("id") == active_id:
        marks.append("★出战")
    if char.get("defeated"):
        marks.append("✝阵亡")
    aura = char.get("aura")
    if isinstance(aura, int) and aura in _DICE_ELEMENT:
        marks.append(f"附着{_DICE_ELEMENT[aura]}")
    if marks:
        parts.append("（" + "，".join(marks) + "）")
    return " ".join(parts[:2]) + (parts[2] if len(parts) > 2 else "")


def _character_gear(index, char: Dict[str, Any], card_text: Any = None) -> str:
    """角色身上的装备/状态，一条独立行（带效果原文）。"""
    return _entity_list(index, char.get("entity"), card_text, limit=4)


def _dice_text(values: Any, labels: Any, mine: bool) -> str:
    """骰子渲染：我方给明细，对方只给数量。"""
    if not mine:
        count = len(values) if isinstance(values, list) else 0
        return f"骰子 {count} 个（骰面不可见）" if count else "骰子 0 个"
    if isinstance(labels, list) and labels and all(isinstance(x, str) for x in labels):
        return f"骰子 {len(labels)} 个：" + "、".join(labels)
    if isinstance(values, list) and values:
        return f"骰子 {len(values)} 个：" + "、".join(_dice_label(v) for v in values)
    return "骰子 0 个"


def _split_sides(state: Any, seat: Optional[int]):
    """拆成 (我方, 对方, 我方座位号)。兼容 player 数组与 me/opponent 两种形状。"""
    if not isinstance(state, dict):
        return {}, {}, seat
    if isinstance(state.get("player"), list) and len(state["player"]) >= 2:
        my_seat = seat if seat in (0, 1) else 0
        return state["player"][my_seat] or {}, state["player"][1 - my_seat] or {}, my_seat
    if isinstance(state.get("me"), dict) or isinstance(state.get("opponent"), dict):
        return state.get("me") or {}, state.get("opponent") or {}, seat
    return state, {}, seat


def _render_side(index, side: Dict[str, Any], label: str, *, mine: bool, detail: Dict[str, Any], include_hand: bool, card_text: Any = None) -> List[str]:
    """渲染一侧：出战角色 + 全部角色 + 手牌 + 骰子 + 支援/召唤/牌堆。"""
    lines: List[str] = []
    characters = side.get("character") or side.get("characters") or []
    active_id = side.get("activeCharacterId")
    if active_id is None and isinstance(side.get("activeCharacter"), dict):
        active_id = side["activeCharacter"].get("id")

    active_name = None
    if mine and isinstance(detail.get("activeCharacter"), dict):
        active_name = detail["activeCharacter"].get("name")
    if not active_name:
        for char in characters:
            if isinstance(char, dict) and char.get("id") == active_id:
                active_name = _name_of(index, char.get("definitionId"), "角色")
                break

    lines.append(f"{label} 出战：{active_name or '未知'}")
    for char in characters:
        if isinstance(char, dict):
            lines.append("  " + _character_line(index, char, active_id, card_text))
            gear = _character_gear(index, char, card_text)
            if gear:
                lines.append(f"    装备/状态：{gear}")

    if mine:
        if include_hand:
            hand = side.get("handCard") or side.get("hand") or []
            if isinstance(hand, list) and hand:
                names = []
                for card in hand[:8]:
                    if isinstance(card, dict):
                        name = _name_of(index, card.get("definitionId"), "牌")
                        cost = card.get("cost")
                        label = f"{name}({cost})" if isinstance(cost, int) else name
                        effect = _short_effect(card_text, card.get("definitionId"), limit=44)
                        if effect:
                            label += f"〔{effect}〕"
                        names.append(label)
                    elif isinstance(card, str):
                        names.append(card)
                more = "…" if len(hand) > 8 else ""
                lines.append(f"  手牌 {len(hand)} 张：{'、'.join(names)}{more}")
            else:
                lines.append("  手牌 0 张")
        else:
            count = len(side.get("handCard") or side.get("hand") or [])
            lines.append(f"  手牌 {count} 张（已隐藏）")
        lines.append("  " + _dice_text(side.get("dice"), detail.get("diceLabels"), True))
    else:
        hand_count = side.get("handCount")
        if not isinstance(hand_count, int):
            hand_count = len(side.get("handCard") or side.get("hand") or [])
        lines.append(f"  手牌 {hand_count} 张（内容不可见）")
        lines.append("  " + _dice_text(side.get("dice"), None, False))

    supports = _entity_list(index, side.get("support"), card_text)
    summons = _entity_list(index, side.get("summon"), card_text)
    statuses = _entity_list(index, side.get("combatStatus"), card_text)
    extras = []
    if supports:
        extras.append(f"支援：{supports}")
    if summons:
        extras.append(f"召唤物：{summons}")
    if statuses:
        extras.append(f"战斗状态：{statuses}")
    pile = side.get("pileCard")
    if isinstance(pile, list):
        extras.append(f"牌堆 {len(pile)} 张")
    if extras:
        lines.append("  " + "｜".join(extras))
    if side.get("declaredEnd"):
        lines.append("  已宣布结束回合")
    return lines


class StateBriefInput(BaseModel):
    """state_brief 的入参。"""

    model_config = ConfigDict(extra="forbid")

    state: Dict[str, Any] = Field(
        default_factory=dict,
        description="ExposedGameState（走 shared.state 推进来），也可以直接给 {me, opponent} 这种简化形状。",
    )
    detail: Dict[str, Any] = Field(
        default_factory=dict,
        description="aiDetail。用来取 diceLabels 与出战角色中文名，留空也能靠着 state+catalog 渲染。",
    )
    seat: Optional[int] = Field(
        None,
        description="我方座位号（0 或 1）。决定谁是我方；不填则把 player[0] 当我方。",
    )
    catalog: Dict[str, Any] = Field(
        default_factory=dict,
        description="catalog（GET /api/arena/catalog）的 id→名字表，用于把 definitionId 换成中文名。",
    )
    card_text: Dict[str, Any] = Field(
        default_factory=dict,
        description="卡牌效果文本（GET /api/arena/card-text 的裁剪结果，definitionId → 原文），给场上实体补一句效果。",
    )
    include_hand: bool = Field(True, description="是否列出我方手牌的具体名称与费用。")
    include_opponent: bool = Field(True, description="是否渲染对方一侧（只含公开信息）。")
    include_raw: bool = Field(
        False,
        description="是否把原始 state 片段放进 me/opponent 出参（调试用；开着会让每次响应多十几 KB）。",
    )
    max_chars: int = Field(
        1500,
        ge=100,
        le=20000,
        description="输出文本的最大字符数，超出部分截断并加省略号。",
    )


class StateBriefOutput(BaseModel):
    """state_brief 的出参。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="压缩后的局面描述，可直接拼进提示词。")
    chars: int = Field(..., description="text 的字符数。")
    truncated: bool = Field(..., description="是否因为 max_chars 被截断。")
    me: Dict[str, Any] = Field(default_factory=dict, description="我方原始 state 片段（调试用）。")
    opponent: Dict[str, Any] = Field(default_factory=dict, description="对方原始 state 片段（调试用）。")


class StateBriefNode(BaseNode):
    """把局面压成简短中文描述。"""

    node_type = "state_brief"
    description = "把 ExposedGameState 压成一段简短中文局面描述（血量/能量/手牌/骰子/场上实体及其效果，对方只给公开信息）"
    version = "0.4.0"
    input_model = StateBriefInput
    output_model = StateBriefOutput
    control_flow = None

    async def execute(
        self,
        inputs: StateBriefInput,
        context: Dict[str, Any] | None = None,
    ) -> StateBriefOutput:
        index = _build_index(inputs.catalog)
        detail = inputs.detail if isinstance(inputs.detail, dict) else {}
        state = inputs.state if isinstance(inputs.state, dict) else {}
        me, opponent, my_seat = _split_sides(state, inputs.seat)

        lines: List[str] = []
        round_no = state.get("roundNumber", me.get("roundNumber"))
        phase_no = state.get("phase", me.get("phase"))
        turn = state.get("currentTurn", me.get("currentTurn"))
        phrase = _PHASES.get(phase_no, f"阶段{phase_no}") if isinstance(phase_no, int) else "阶段?"
        lines.append(f"回合 {round_no if round_no is not None else '?'} · 阶段 {phrase} · 当前行动方：玩家 {turn if turn is not None else '?'}")

        mine_label = f"我方（座位 {my_seat}）" if my_seat is not None else "我方"
        lines.extend(_render_side(index, me, mine_label, mine=True, detail=detail, include_hand=inputs.include_hand, card_text=inputs.card_text))
        if inputs.include_opponent:
            opponent_label = f"对方（座位 {1 - my_seat}）" if my_seat in (0, 1) else "对方"
            lines.extend(_render_side(index, opponent, opponent_label, mine=False, detail={}, include_hand=False, card_text=inputs.card_text))

        text = "\n".join(line for line in lines if line.strip())
        truncated = len(text) > inputs.max_chars
        if truncated:
            text = text[: inputs.max_chars] + "…（已截断）"

        return StateBriefOutput(
            text=text,
            chars=len(text),
            truncated=truncated,
            me=me if inputs.include_raw and isinstance(me, dict) else {},
            opponent=opponent if inputs.include_raw and isinstance(opponent, dict) else {},
        )


__all__ = ["StateBriefNode", "StateBriefInput", "StateBriefOutput"]
