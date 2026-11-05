# -*- coding: utf-8 -*-
"""
【对局】把一次行动阶段的候选动作渲染成模型能读懂的菜单，并给一个参考排序（action_rank）。

为什么需要它:
    真实对局里一次行动阶段的候选动辄 25~35 项（每个技能、每张手牌、调和、结束回合都在里面）。
    早期版本只把打分最高的前 ``top_k`` 项交给模型，结果模型看到的菜单被"值 0 分"的
    元素调和与一次性事件牌占满——真正能斩杀的技能、能切换的角色反而被筛掉了，这是
    "静默变笨"。现在默认**全部合法动作都列出来**（``top_k`` 只作为极端情况下的上限），
    排序只用来给一行"参考"，编号始终是原始编号。

渲染上要防三种"信息量为 0":
    - ``预览`` 是引擎内部结构（``removeEntity``/``moveEntity``），直接打印等于没说；
      这里按类型翻成人话（造成 2 点物理伤害 → 久岐忍 / 附着火 / 生成……）。
    - 元素调和在 aiDetail 里只带 ``removedCardId``，名字是"（弃置 未知）"；
      这里用手牌 + 名字表还原成"弃「旧日鏖战」→ 换 1 个火骰"。
      多条调和只差一张弃牌，名字不对就等于让模型盲选。
    - 费用 ``cost.dice`` 是"骰子类型 → 个数"的字典（0=任意、3=火、8=相同元素……），
      不是数组；优先用引擎给的 ``cost.text``，缺了再自己拼。

契约:
    输入 :class:`ActionRankInput` / 输出 :class:`ActionRankOutput`。
"""

from __future__ import annotations

import re

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = ["ActionRankInput", "ActionRankOutput", "ActionRankNode"]

#: 三种风格对应的权重：(伤害, 治疗, 护盾, 充能, 单点费用惩罚)
_STYLES: Dict[str, tuple] = {
    "aggressive": (1.0, 0.5, 0.6, 0.4, 0.08),
    "balanced": (1.0, 0.8, 0.8, 0.6, 0.10),
    "defensive": (0.8, 1.2, 1.2, 0.6, 0.10),
}

#: 视为"直接伤害"的预示类型。
_DAMAGE_TYPES = {"damage", "pierce", "伤害", "穿透"}
#: 视为"治疗"的预示类型。
_HEAL_TYPES = {"heal", "治疗"}
#: 视为"护盾"的预示类型。
_SHIELD_TYPES = {"shield", "护盾"}

#: 元素编号（与引擎 DamageType 一致）：1 冰 / 2 水 / 3 火 / 4 雷 / 5 风 / 6 岩 / 7 草。
_ELEMENT_NAMES = {1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草"}

#: 费用里的骰子类型：0 任意 / 8 相同元素 / 9 充能 / 10 秘传，其余同上。
_COST_NAMES = {0: "任意", 1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草", 8: "相同元素", 9: "充能", 10: "秘传"}

#: 骰面编号的说法，与网页 DICE_LABELS 保持一致（0 未知 / 8 万能）。
_DICE_NAMES = {0: "未知", 1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草", 8: "万能"}

#: 实体变量名的中文说法（preview.modifyEntityVar.variable）。
_VAR_NAMES = {"health": "生命", "energy": "充能", "shield": "护盾", "count": "计数", "usage": "次数", "cost": "费用"}

#: 效果原文里属于"构筑说明"的行：对决策没用，删掉省字数。
_META_LINE_HINTS = ("牌组中包含", "整局游戏只能打出", "此牌一定在你的起始手牌中", "最多装备1个")


def _short_effect(card_text: Any, ident: Any, limit: int = 72) -> str:
    """从 card-text 里取一条效果原文，压成一行短句（去构筑说明、去子技能前缀、按标点截断）。

    Args:
        card_text (Any): 司机传进来的效果表，形如 ``{"descriptions": {id: 原文}}``。
        ident (Any): definitionId / skillId（正整数）。
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
        if merged and not merged.endswith(("。", "；", "！", "？", "，", "…")):
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


def _rpc_kind(detail: Dict[str, Any], request: Dict[str, Any]) -> str:
    """推断当前请求类型（与 action_menu 保持一致的判定顺序）。

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


def _opponent_active_hp(state: Dict[str, Any], seat: Optional[int]) -> Optional[int]:
    """从局面里读出"对方出战角色的当前生命"，用于判断能不能一击打死。

    Args:
        state (Dict[str, Any]): ExposedGameState JSON（``player[0] / player[1]``）。
        seat (Optional[int]): 我方座位号；为 ``None`` 时不判断。

    Returns:
        Optional[int]: 对方出战角色生命；读不到返回 ``None``。
    """
    if not isinstance(state, dict) or seat is None:
        return None
    players = state.get("player")
    if not isinstance(players, list) or len(players) < 2:
        return None
    try:
        other = players[1 - int(seat)]
    except (IndexError, TypeError, ValueError):
        return None
    if not isinstance(other, dict):
        return None
    active_id = other.get("activeCharacterId")
    characters = [c for c in (other.get("character") or []) if isinstance(c, dict)]
    for ch in characters:
        if ch.get("defeated"):
            continue
        if active_id is None or ch.get("id") == active_id:
            health = ch.get("health")
            return int(health) if isinstance(health, (int, float)) else None
    return None


def _char_name(catalog: Dict[str, Any], definition_id: Any) -> str:
    """从裁剪后的名字表里取角色名（查不到就返回空串，不强行编一个）。"""
    if not isinstance(catalog, dict):
        return ""
    raw = catalog.get("characters")
    if not isinstance(raw, dict):
        return ""
    name = raw.get(str(definition_id))
    if name is None:
        name = raw.get(definition_id)
    return str(name) if name else ""


def _active_summary(player: Dict[str, Any], catalog: Dict[str, Any]) -> str:
    """把"出战角色"渲染成 ``名字 4/5`` 这样的一小段。"""
    characters = [c for c in (player.get("character") or []) if isinstance(c, dict)]
    if not characters:
        return ""
    active_id = player.get("activeCharacterId")
    picked = None
    for ch in characters:
        if ch.get("defeated"):
            continue
        if active_id is None or ch.get("id") == active_id:
            picked = ch
            break
    if picked is None:
        picked = next((c for c in characters if not c.get("defeated")), None)
    if picked is None:
        return ""
    name = _char_name(catalog, picked.get("definitionId"))
    health = picked.get("health")
    max_health = picked.get("maxHealth")
    label = f"{name} " if name else ""
    if isinstance(health, (int, float)) and isinstance(max_health, (int, float)):
        return f"{label}{int(health)}/{int(max_health)}"
    if isinstance(health, (int, float)):
        return f"{label}{int(health)}"
    return label.strip()


def _active_health(player: Dict[str, Any]) -> str:
    """只取出战角色的 ``当前/上限`` 生命，不带名字（名字已在上一行"当前出战："里）。"""
    characters = [c for c in (player.get("character") or []) if isinstance(c, dict)]
    if not characters:
        return ""
    active_id = player.get("activeCharacterId")
    picked = None
    for ch in characters:
        if ch.get("defeated"):
            continue
        if active_id is None or ch.get("id") == active_id:
            picked = ch
            break
    if picked is None:
        picked = next((c for c in characters if not c.get("defeated")), None)
    if picked is None:
        return ""
    health = picked.get("health")
    max_health = picked.get("maxHealth")
    if isinstance(health, (int, float)) and isinstance(max_health, (int, float)):
        return f"{int(health)}/{int(max_health)}"
    if isinstance(health, (int, float)):
        return str(int(health))
    return ""


def _resource_line(state: Dict[str, Any], seat: Optional[int], catalog: Dict[str, Any]) -> str:
    """渲染一行"本回合资源账"：骰子明细、双方出战血量、结束回合会浪费几颗。

    为什么要它：模型只看到一串动作和费用，很容易在骰子还剩 5～8 颗时宣布结束回合，
    把整个回合的资源扔掉。把"剩多少颗、结束就作废"摆在菜单最上面，比在系统提示词里
    反复叮嘱更管用（实测那些叮嘱经常被当成客套话）。

    Args:
        state (Dict[str, Any]): ExposedGameState JSON。
        seat (Optional[int]): 我方座位号。
        catalog (Dict[str, Any]): 裁剪后的名字表。

    Returns:
        str: 一行资源说明；读不到局面时返回空串。
    """
    if not isinstance(state, dict) or seat is None:
        return ""
    players = state.get("player")
    if not isinstance(players, list) or len(players) < 2:
        return ""
    try:
        me = players[int(seat)]
        other = players[1 - int(seat)]
    except (IndexError, TypeError, ValueError):
        return ""
    if not isinstance(me, dict) or not isinstance(other, dict):
        return ""

    dice = [d for d in (me.get("dice") or []) if isinstance(d, int) and not isinstance(d, bool)]
    parts: List[str] = []
    if dice:
        counts: Dict[int, int] = {}
        for face in dice:
            counts[face] = counts.get(face, 0) + 1
        summary = "、".join(f"{_dice_name(face)}×{count}" for face, count in sorted(counts.items()))
        parts.append(f"骰子 {len(dice)} 颗（{summary}）")
    mine = _active_health(me)
    if mine:
        parts.append(f"我方 {mine}")
    hand = [c for c in (me.get("hand") or []) if isinstance(c, dict)]
    if hand:
        parts.append(f"手牌 {len(hand)} 张")
    theirs = _active_summary(other, catalog)
    if theirs:
        parts.append(f"对方出战 {theirs}")
    if other.get("declaredEnd"):
        parts.append("对方已宣布结束回合，你可以把骰子用光再结束")
    elif dice:
        parts.append(f"现在结束回合，这 {len(dice)} 颗骰子会作废")
    return "资源：" + "；".join(parts) + "。" if parts else ""


def _scores_of(action: Dict[str, Any]) -> Dict[str, float]:
    """把一项行动的各项数值拆出来。

    Args:
        action (Dict[str, Any]): ``aiDetail.actions`` 里的一项。

    Returns:
        Dict[str, float]: ``{"damage","heal","shield","energy","cost"}``。
    """
    out = {"damage": 0.0, "heal": 0.0, "shield": 0.0, "energy": 0.0, "cost": 0.0}
    preview = action.get("preview")
    if isinstance(preview, list):
        for item in preview:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "").lower()
            amount = item.get("amount")
            try:
                value = float(amount) if amount is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            if kind in _DAMAGE_TYPES:
                out["damage"] += value
            elif kind in _HEAL_TYPES:
                out["heal"] += value
            elif kind in _SHIELD_TYPES:
                out["shield"] += value
            elif kind == "modifyentityvar" and str(item.get("variable") or "") == "energy":
                # 只有引擎给了具体变化量时才算"充能"，否则 +1/-1 无法区分
                if isinstance(item.get("value"), (int, float)):
                    out["energy"] += float(item["value"])
    cost = action.get("cost")
    if isinstance(cost, dict):
        total = cost.get("total")
        if isinstance(total, (int, float)):
            out["cost"] = float(total)
    return out


def _element_label(value: Any) -> str:
    """把元素/骰面编号翻成中文（已经是中文的字符串原样返回）。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return _ELEMENT_NAMES.get(value) or _COST_NAMES.get(value, "")
    return ""


def _dice_name(value: int) -> str:
    """骰面值 → 中文说法（0 未知 / 8 万能），不认识的原样显示。"""
    return _DICE_NAMES.get(int(value), f"#{value}")


def _cost_text(cost: Any) -> str:
    """渲染费用：优先用引擎给的 ``text``，缺了再把 ``dice`` 计数字典拼成人话。

    Args:
        cost (Any): 费用结构（``{dice,total,text}``）。

    Returns:
        str: 例如 ``"2任意+1火"``；免费返回空串。
    """
    if not isinstance(cost, dict):
        return ""
    total = cost.get("total")
    if isinstance(total, (int, float)) and total <= 0:
        return ""
    text = str(cost.get("text") or "").strip()
    if text and text not in {"0", "0元素"}:
        return text
    dice = cost.get("dice")
    parts: List[str] = []
    if isinstance(dice, dict):
        for key, count in dice.items():
            try:
                kind, num = int(key), int(count)
            except (TypeError, ValueError):
                continue
            if num <= 0:
                continue
            name = _COST_NAMES.get(kind, f"骰{kind}")
            parts.append(f"{name}×{num}" if num > 1 else name)
    elif isinstance(dice, list):
        for item in dice:
            if isinstance(item, dict):
                kind = int(item.get("diceType") or 0)
            elif isinstance(item, int):
                kind = item
            else:
                continue
            parts.append(_COST_NAMES.get(kind, f"骰{kind}"))
    if parts:
        return "＋".join(parts)
    if isinstance(total, (int, float)):
        return f"{int(total)} 费"
    return ""


def _effects_text(action: Dict[str, Any]) -> str:
    """把 ``preview`` 翻成一两句人话；翻译不了的内部类型直接丢掉（宁缺勿噪）。

    Args:
        action (Dict[str, Any]): ``aiDetail.actions`` 里的一项。

    Returns:
        str: 例如 ``"造成 2 点物理伤害 → 久岐忍"``；没有可读预示返回空串。
    """
    preview = action.get("preview")
    if not isinstance(preview, list):
        return ""
    segs: List[str] = []
    for item in preview:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        amount = item.get("amount")
        if kind == "damage":
            seg = "造成"
            seg += f" {amount} 点" if isinstance(amount, (int, float)) else " "
            seg += _element_label(item.get("element"))
            seg += "伤害"
            target = item.get("target")
            if isinstance(target, str) and target:
                seg += f" → {target}"
            reaction = item.get("reaction")
            if isinstance(reaction, str) and reaction:
                seg += f"（触发{reaction}）"
            if item.get("defeated"):
                seg += "（击杀）"
            segs.append(seg)
        elif kind == "heal":
            seg = "治疗"
            seg += f" {amount} 点" if isinstance(amount, (int, float)) else ""
            target = item.get("target")
            if isinstance(target, str) and target:
                seg += f" → {target}"
            segs.append(seg)
        elif kind in {"applyAura", "aura"}:
            element = _element_label(item.get("element"))
            segs.append(f"附着{element}" if element else "附着元素")
        elif kind == "createEntity":
            name = item.get("entity")
            segs.append(f"生成{name}" if isinstance(name, str) and name else "生成实体")
        elif kind == "modifyEntityVar":
            label = _VAR_NAMES.get(str(item.get("variable") or ""))
            value = item.get("value")
            if label and isinstance(value, (int, float)):
                segs.append(f"{label}{'+' if value >= 0 else ''}{int(value)}")
    return "；".join(segs)


def _hand_names(state: Dict[str, Any], seat: Optional[int], catalog: Dict[str, Any]) -> Dict[int, str]:
    """建一张 ``手牌实例 id → 牌名`` 的表，用来把"元素调和弃置了哪张牌"说清楚。

    Args:
        state (Dict[str, Any]): ExposedGameState。
        seat (Optional[int]): 我方座位号。
        catalog (Dict[str, Any]): 裁剪后的名字表 ``{类别: {id: 名字}}``。

    Returns:
        Dict[int, str]: 手牌实例 id 到牌名的映射（名字缺失时给 ``手牌#definitionId``）。
    """
    cards: Dict[int, str] = {}
    if isinstance(catalog, dict):
        raw = catalog.get("cards")
        if isinstance(raw, dict):
            for ident, name in raw.items():
                try:
                    cards[int(ident)] = str(name)
                except (TypeError, ValueError):
                    continue
    out: Dict[int, str] = {}
    if not isinstance(state, dict) or seat is None:
        return out
    players = state.get("player")
    try:
        player = players[int(seat)]
    except (TypeError, IndexError, KeyError):
        return out
    if not isinstance(player, dict):
        return out
    for card in player.get("handCard") or []:
        if not isinstance(card, dict) or not isinstance(card.get("id"), int):
            continue
        definition = card.get("definitionId")
        name = cards.get(definition) if isinstance(definition, int) else None
        out[int(card["id"])] = name or f"手牌#{definition}"
    return out


def _action_label(action: Dict[str, Any], hand: Dict[int, str]) -> str:
    """给一项动作起一个"人话名字"（元素调和/切换角色这种引擎名字信息量为 0）。

    Args:
        action (Dict[str, Any]): ``aiDetail.actions`` 里的一项。
        hand (Dict[int, str]): 手牌 id → 牌名。

    Returns:
        str: 动作名。
    """
    kind = str(action.get("kind") or "")
    name = str(action.get("name") or "").strip() or kind
    if kind == "elementalTuning":
        bits: List[str] = []
        removed = action.get("removedCardId")
        if isinstance(removed, int):
            bits.append(f"弃「{hand.get(removed, f'手牌#{removed}')}」")
        target = str(action.get("targetDiceLabel") or "").strip() or _element_label(action.get("targetDice"))
        if target:
            bits.append(f"换 1 个{target}骰")
        return "元素调和：" + " → ".join(bits) if bits else "元素调和"
    if kind == "declareEnd":
        return "结束回合（本回合剩余骰子作废）"
    if kind == "playCard":
        return name + ("（打出后无效果）" if action.get("willBeEffectless") else "")
    return name


class ActionRankInput(BaseModel):
    """重排序节点的输入契约。

    Attributes:
        detail (Dict[str, Any]): 结构化视图，连线接 ``arena_input.detail``。
        request (Dict[str, Any]): 原始请求，detail 缺失时用于判断类型。
        state (Dict[str, Any]): 局面，连线接 ``arena_input.state``；用来判断"能否击杀"、还原调和弃牌。
        catalog (Dict[str, Any]): 裁剪后的名字表，连线接 ``arena_input.catalog``。
        seat (Optional[int]): 我方座位，连线接 ``arena_input.seat``。
        top_k (int): 最多保留几项（含"结束回合"）；默认 40，等价于"全部列出"。
        style (str): 打分风格（aggressive / balanced / defensive）。
        keep_end_turn (bool): 是否总是保留"结束回合"这一项。
    """

    model_config = {"extra": "forbid"}

    detail: Dict[str, Any] = Field(default_factory=dict, description="结构化视图 aiDetail")
    request: Dict[str, Any] = Field(default_factory=dict, description="原始请求，用于兜底判断类型")
    state: Dict[str, Any] = Field(default_factory=dict, description="局面 JSON，用于判断能否击杀")
    catalog: Dict[str, Any] = Field(default_factory=dict, description="裁剪后的名字表，用于翻译手牌名")
    card_text: Dict[str, Any] = Field(
        default_factory=dict,
        description="裁剪后的卡牌效果文本（definitionId/skillId → 原文），用于在菜单里写清楚这张牌/技能干什么",
    )
    seat: Optional[int] = Field(None, description="我方座位号")
    top_k: int = Field(40, ge=1, le=40, description="最多保留几项（默认全部）")
    style: str = Field("aggressive", description="打分风格：aggressive / balanced / defensive")
    keep_end_turn: bool = Field(True, description="是否总是保留结束回合选项")


class ActionRankOutput(BaseModel):
    """重排序节点的输出契约。

    Attributes:
        text (str): 菜单文本（编号为原始编号）。
        hint (str): 一行回答要求（给人看的短提示）。
        task (str): 给模型的回答格式要求，随请求类型变化（单选 / 多选），连线接 ``prompt_pack.task``。
        indexes (List[int]): 列出的原始编号，连线接 ``choice_parse.valid_indexes``。
        scores (List[float]): 与 ``indexes`` 一一对应的分数（调试用）。
        total (int): 合法候选总数。
        shown (int): 实际列出数量。
        empty (bool): 没有任何可列出的候选。
        kind (str): 请求类型。
    """

    model_config = {"extra": "forbid"}

    text: str = Field(..., description="菜单文本")
    hint: str = Field("", description="一行回答要求")
    task: str = Field("", description="给模型的回答格式要求（单选/多选）")
    indexes: List[int] = Field(default_factory=list, description="列出的原始编号")
    scores: List[float] = Field(default_factory=list, description="对应分数（调试用）")
    total: int = Field(0, description="合法候选总数")
    shown: int = Field(0, description="实际列出数量")
    empty: bool = Field(False, description="是否没有可列出候选")
    kind: str = Field("", description="请求类型")


class ActionRankNode(BaseNode):
    """渲染合法动作菜单，并给一个"参考排序"。"""

    node_type = "action_rank"
    description = "把合法动作渲染成读得懂的菜单（全部列出），并给一行参考排序"
    input_model = ActionRankInput
    output_model = ActionRankOutput

    async def execute(
        self,
        inputs: ActionRankInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> ActionRankOutput:
        """渲染菜单。

        Args:
            inputs (ActionRankInput): 候选、局面与排序参数。
            context (Optional[Dict[str, Any]]): 未使用。

        Returns:
            ActionRankOutput: 菜单与列出的编号。
        """
        detail = inputs.detail if isinstance(inputs.detail, dict) else {}
        kind = _rpc_kind(detail, inputs.request)
        if kind != "action":
            return self._passthrough(detail, kind, inputs)
        return self._rank_actions(detail, inputs)

    # ------------------------------------------------------------------
    def _rank_actions(self, detail: Dict[str, Any], inputs: ActionRankInput) -> ActionRankOutput:
        """给合法动作打分、按原始编号渲染成菜单。

        Args:
            detail (Dict[str, Any]): 结构化视图。
            inputs (ActionRankInput): 排序参数。

        Returns:
            ActionRankOutput: 渲染结果。
        """
        actions = [a for a in (detail.get("actions") or []) if isinstance(a, dict)]
        weights = _STYLES.get(str(inputs.style), _STYLES["aggressive"])
        w_damage, w_heal, w_shield, w_energy, w_cost = weights
        target_hp = _opponent_active_hp(inputs.state, inputs.seat)
        hand = _hand_names(inputs.state, inputs.seat, inputs.catalog)

        scored: List[tuple] = []
        end_turn: Optional[tuple] = None
        for item in actions:
            index = item.get("index")
            if not isinstance(index, int):
                continue
            if int(item.get("validity") or 0) != 0:
                continue
            if str(item.get("kind") or "") == "passAction":
                # 「过一手」是给人类玩家的便利动作；AI 菜单里不提供，避免双方互相让过把对局卡死
                continue
            nums = _scores_of(item)
            score = (
                nums["damage"] * w_damage
                + nums["heal"] * w_heal
                + nums["shield"] * w_shield
                + nums["energy"] * w_energy
                - nums["cost"] * w_cost
            )
            text = str(item.get("name") or item.get("kind") or "")
            is_end = str(item.get("kind") or "") in {"declareEnd", "endTurn", "declare_end"} or "结束回合" in text
            kill = target_hp is not None and nums["damage"] >= target_hp > 0
            if kill:
                score += 50.0
            entry = (score, index, item, nums, kill)
            if is_end:
                end_turn = entry
            else:
                scored.append(entry)

        scored.sort(key=lambda e: (-e[0], e[1]))
        limit = max(1, inputs.top_k - (1 if (inputs.keep_end_turn and end_turn) else 0))
        kept = scored[:limit]
        if inputs.keep_end_turn and end_turn:
            kept = kept + [end_turn]
        kept.sort(key=lambda e: e[1])
        total = len(scored) + (1 if end_turn else 0)

        lines: List[str] = []
        indexes: List[int] = []
        scores: List[float] = []
        for score, index, item, nums, kill in kept:
            indexes.append(index)
            scores.append(round(score, 2))
            bits = [f"[{index}] {_action_label(item, hand)}"]
            cost = _cost_text(item.get("cost"))
            if cost:
                bits.append(cost)
            item_kind = str(item.get("kind") or "")
            if item_kind == "playCard":
                effect = _short_effect(inputs.card_text, item.get("cardDefinitionId"))
                if effect:
                    bits.append(f"牌面：{effect}")
            elif item_kind == "useSkill":
                effect = _short_effect(inputs.card_text, item.get("skillId"), limit=56)
                # 只写了"造成 N 点伤害。"的说明跟 preview 重复，纯属占字数
                trivial = "造成" in effect and "如果" not in effect and "然后" not in effect and len(effect) <= 24
                if effect and not trivial:
                    bits.append(f"说明：{effect}")
            effects = _effects_text(item)
            if effects:
                bits.append(effects)
            if kill:
                bits.append(f"★ 可直接击杀对方出战角色（对方剩余生命 {target_hp}）")
            lines.append("  " + " ｜ ".join(bits))

        if shown_all := len(kept) >= total:
            head = [f"共 {total} 项合法动作（全部列出；编号就是原始编号，直接用编号回答）"]
        else:
            head = [f"共 {total} 项合法动作，这里列出价值最高的 {len(kept)} 项（编号就是原始编号，直接用编号回答）"]
        active = detail.get("activeCharacter")
        if isinstance(active, dict) and active.get("name"):
            head.append(f"当前出战：{active.get('name')}")
        resources = _resource_line(inputs.state, inputs.seat, inputs.catalog)
        if resources:
            head.append(resources)
        if detail.get("disabledBy"):
            names = "、".join(str(d.get("name") or d.get("id")) for d in detail["disabledBy"] if isinstance(d, dict))
            if names:
                head.append(f"本回合被禁用：{names}")
        if not lines:
            lines.append("  （没有可用的合法动作）")

        # 参考排序只作提示：全部动作都已列出，模型可以自由选任何一个合法编号
        ranked = [e for e in sorted(scored, key=lambda e: (-e[0], e[1])) if e[0] > 0][:3]
        if ranked and shown_all:
            tags = "、".join(f"[{e[1]}]" for e in ranked)
            lines.append(f"【参考】按伤害/治疗/护盾/费用粗排，优先看 {tags}（也可以选任意其他编号）")

        return ActionRankOutput(
            text="\n".join(head) + "\n" + "\n".join(lines),
            hint="只回答一个方括号里的编号数字；不要解释。",
            task="只回答一个方括号里的编号数字；不要解释。",
            indexes=indexes,
            scores=scores,
            total=total,
            shown=len(kept),
            empty=not kept,
            kind="action",
        )

    def _passthrough(self, detail: Dict[str, Any], kind: str, inputs: ActionRankInput) -> ActionRankOutput:
        """非行动类请求原样列成菜单（不排序，因为候选通常只有 1~5 个）。

        Args:
            detail (Dict[str, Any]): 结构化视图。
            kind (str): 请求类型。
            inputs (ActionRankInput): 参数。

        Returns:
            ActionRankOutput: 候选菜单。
        """
        if kind in {"chooseActive", "selectCard"}:
            items = [c for c in (detail.get("candidates") or []) if isinstance(c, dict)]
            lines = []
            for position, item in enumerate(items):
                label = item.get("name") or item.get("definitionId") or item.get("id")
                if item.get("health") is not None:
                    label = f"{label}（生命 {item.get('health')}/{item.get('maxHealth')}）"
                if kind == "selectCard":
                    effect = _short_effect(inputs.card_text, item.get("definitionId"), limit=60)
                    if effect:
                        label = f"{label} ｜ {effect}"
                lines.append(f"  [{position}] {label}")
            return ActionRankOutput(
                text=f"【可选{kind}】共 {len(items)} 项\n" + "\n".join(lines or ["  （无候选）"]),
                hint="只回答一个方括号里的编号数字。",
                task="只回答一个方括号里的编号数字；不要解释。",
                indexes=list(range(len(items))),
                scores=[],
                total=len(items),
                shown=len(items),
                empty=not items,
                kind=kind,
            )
        if kind in {"rerollDice", "switchHands"}:
            return self._pick_many(detail, kind, inputs)
        return ActionRankOutput(
            text=f"【未知请求类型：{kind or '空'}】",
            hint="",
            task="",
            indexes=[],
            scores=[],
            total=0,
            shown=0,
            empty=True,
            kind=kind,
        )

    def _pick_many(self, detail: Dict[str, Any], kind: str, inputs: ActionRankInput) -> ActionRankOutput:
        """把骰子/手牌逐项编号列出来（多选），让模型能真的表达"重投第 1、3 颗"或"换掉第 2 张"。

        编号是本菜单里的位置（不是骰面值、也不是手牌 id）——最后一项是"什么都不做"，
        ``choice_map`` 会把位置翻回骰面值/手牌 id，越界的编号一律丢弃。

        Args:
            detail (Dict[str, Any]): 结构化视图（rerollDice 带 ``dice``/``diceLabels``，switchHands 带 ``hand``）。
            kind (str): ``rerollDice`` 或 ``switchHands``。
            inputs (ActionRankInput): 参数（取 ``card_text`` 渲染手牌效果）。

        Returns:
            ActionRankOutput: 逐项菜单；``indexes`` 是可选位置，末位是"不操作"。
        """
        if kind == "rerollDice":
            raw = detail.get("dice")
            dice = [int(d) for d in raw if isinstance(d, (int, float)) and not isinstance(d, bool)] if isinstance(raw, list) else []
            raw_labels = detail.get("diceLabels")
            labels = raw_labels if isinstance(raw_labels, list) else []
            counts: Dict[int, int] = {}
            for value in dice:
                counts[value] = counts.get(value, 0) + 1
            summary = "、".join(f"{_dice_name(v)}×{n}" for v, n in sorted(counts.items())) or "无"
            head = f"【重投骰子】现在有 {len(dice)} 颗骰子：{summary}"
            lines = ["重投 = 把这几颗扔回骰池再随机补等量，骰面随机、不消耗行动，也不影响本回合剩余骰子数。"]
            for position, value in enumerate(dice):
                label = labels[position] if position < len(labels) and isinstance(labels[position], str) else _dice_name(value)
                lines.append(f"  [{position}] {label}（骰面值 {value}）")
            lines.append(f"  [{len(dice)}] 不重投（保留现在这些骰面）")
            total = len(dice) + 1
            task = (
                "只回答要重投的方括号编号，多个用逗号分开（例如 [0]、[3]）；"
                f"一颗都不重投就回答 [{len(dice)}]。不要解释。"
            )
            hint = "回答若干个方括号编号，逗号分隔。"
        else:
            raw = detail.get("hand")
            hand = [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
            head = f"【换手牌】现在有 {len(hand)} 张手牌"
            lines = ["换牌 = 把这些手牌移回牌堆，然后重抽等量张；换掉的牌这局还能再摸到，但节奏会慢一拍。"]
            for position, item in enumerate(hand):
                label = str(item.get("name") or item.get("definitionId") or item.get("id") or "?")
                effect = _short_effect(inputs.card_text, item.get("definitionId"), limit=60)
                suffix = f" ｜ 牌面：{effect}" if effect else ""
                lines.append(f"  [{position}] {label}{suffix}")
            lines.append(f"  [{len(hand)}] 不换（保留现在的手牌）")
            total = len(hand) + 1
            task = (
                "只回答要移回牌堆的方括号编号，多个用逗号分开（例如 [0]、[2]）；"
                f"一张都不换就回答 [{len(hand)}]。不要解释。"
            )
            hint = "回答若干个方括号编号，逗号分隔。"
        return ActionRankOutput(
            text=head + "\n" + "\n".join(lines),
            hint=hint,
            task=task,
            indexes=list(range(total)),
            scores=[],
            total=total,
            shown=total,
            empty=False,
            kind=kind,
        )
