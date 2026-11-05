# -*- coding: utf-8 -*-
"""把牌组翻译成给模型看的"备赛资料"。

为什么需要它:
    牌组分享码是一串 51 字节的 base64，模型看不懂；而"赛前构思战术"必须知道
    **三个角色是谁、带什么技能、30 张牌大概是什么结构**。这个节点负责把
    ``{characters:[id], cards:[id]}` + 名字表（catalog）翻译成一段中文文本，
    让模型能据此写计划。

    它只做"翻译"，不做判断：谁的技能伤害高、留什么牌，那是提示词的事。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import BaseNode

#: 技能类型 → 中文。
_SKILL_KINDS = {
    "normalAttack": "普通攻击",
    "elementalSkill": "元素战技",
    "elementalBurst": "元素爆发",
    "passiveSkill": "被动",
    "triggeredSkill": "被动",
}

#: 行动牌类型 → 中文（catalog 里是 GCG_CARD_* 枚举）。
_CARD_KINDS = {
    "GCG_CARD_EVENT": "事件牌",
    "GCG_CARD_MODIFY": "装备牌",
    "GCG_CARD_ASSIST": "支援牌",
    "GCG_CARD_SUMMON": "召唤物",
    "GCG_CARD_STATE": "状态",
    "GCG_CARD_OTHER": "其他",
}

#: 元素/骰面 id → 中文（费用里 8 在骰面语义下是"万能"；编号见 enums.ts：3=火、4=雷）。
_DICE = {0: "任意", 1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草", 8: "万能", 9: "充能", 10: "秘传"}


def _index(catalog: Dict[str, Any], category: str) -> Dict[int, Dict[str, Any]]:
    """把 catalog 的某一类列表拍平成 id → 条目。"""
    out: Dict[int, Dict[str, Any]] = {}
    items = (catalog or {}).get(category)
    if isinstance(items, dict):
        for key, value in items.items():
            try:
                ident = int(key)
            except (TypeError, ValueError):
                continue
            out[ident] = value if isinstance(value, dict) else {"name": str(value)}
    elif isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                ident = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            out[ident] = item
    return out


def _normalize(deck: Dict[str, Any]) -> tuple:
    """把牌组拍平成 (角色 id 列表, 行动牌 id 列表(含重复))。"""
    characters: List[int] = []
    for item in (deck or {}).get("characters") or []:
        ident = item if isinstance(item, int) else (item or {}).get("id")
        if isinstance(ident, int):
            characters.append(ident)
    cards: List[int] = []
    for item in (deck or {}).get("cards") or []:
        if isinstance(item, int):
            cards.append(item)
            continue
        if not isinstance(item, dict):
            continue
        try:
            ident = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        count = int(item.get("count") or 1)
        cards.extend([ident] * max(0, count))
    return characters, cards


def _dice_text(cost: Any) -> str:
    """把 cost/dice 计数渲染成 "3（火 2 + 任意 1）"。"""
    if isinstance(cost, dict) and cost:
        parts = []
        for key, value in cost.items():
            try:
                element = _DICE.get(int(key), str(key))
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                parts.append(f"{element}{number}")
        total = sum(int(v) for v in cost.values() if str(v).isdigit())
        return f"{total}（{' + '.join(parts)}）" if parts else str(total)
    return str(cost) if cost not in (None, "") else "0"


class DeckBriefInput(BaseModel):
    """``deck_brief`` 的输入。"""

    model_config = ConfigDict(extra="forbid")

    deck: Dict[str, Any] = Field(default_factory=dict, description="牌组 {characters, cards, name?}（id 或 {id,count}）")
    catalog: Dict[str, Any] = Field(default_factory=dict, description="名字表：含 characters / cards / skills 三类")
    seat: Optional[int] = Field(None, description="座位号，仅用于标注")

    max_chars: int = Field(1800, ge=100, le=20000, description="文本上限（字符），超出截断；单人牌组最小约 120 字符")
    skill_detail: bool = Field(True, description="是否列出每个技能的伤害与费用（关掉只看名字，更短）")


class DeckBriefOutput(BaseModel):
    """``deck_brief`` 的输出。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="给模型看的牌组说明")
    characters: List[str] = Field(default_factory=list, description="角色名字，按牌组顺序")
    total_cards: int = Field(0, description="行动牌总张数")
    distinct_cards: int = Field(0, description="行动牌种类数")
    chars: int = Field(0, description="文本字符数")


class DeckBriefNode(BaseNode):
    """牌组 → 中文备赛资料。"""

    node_type = "deck_brief"
    description = "把牌组（角色 + 30 张行动牌）翻译成中文说明，供模型构思战术"
    version = "0.1.0"
    input_model = DeckBriefInput
    output_model = DeckBriefOutput

    async def execute(self, inputs: DeckBriefInput, context: Optional[Dict[str, Any]] = None) -> DeckBriefOutput:
        """执行翻译。"""
        characters, cards = _normalize(inputs.deck)
        char_index = _index(inputs.catalog, "characters")
        card_index = _index(inputs.catalog, "cards")
        token_index = _index(inputs.catalog, "tokens")

        lines: List[str] = []
        name = (inputs.deck or {}).get("name")
        seat = f"座位 {inputs.seat} " if inputs.seat is not None else ""
        lines.append(f"{seat}牌组：{name or '（未命名）'}（角色 {len(characters)}、行动牌 {len(cards)} 张）")

        lines.append("角色：")
        names: List[str] = []
        for ident in characters:
            info = char_index.get(ident) or token_index.get(ident) or {}
            char_name = info.get("name") or f"角色#{ident}"
            names.append(char_name)
            tags = "、".join(str(t) for t in (info.get("tags") or []) if t)
            hp = info.get("hp")
            energy = info.get("energy")
            stat = []
            if hp:
                stat.append(f"生命{hp}")
            if energy:
                stat.append(f"充能{energy}")
            suffix = f"（{'，'.join(stat + ([tags] if tags else []))}）" if stat or tags else ""
            lines.append(f"- {char_name}{suffix}")
            if not inputs.skill_detail:
                continue
            for skill in (info.get("skills") or [])[:4]:
                if not isinstance(skill, dict):
                    continue
                kind = _SKILL_KINDS.get(str(skill.get("type")), str(skill.get("type") or "技能"))
                damage = skill.get("damage") or []
                dmg = f"，伤害 {'/'.join(str(d) for d in damage)}" if damage else ""
                cost = skill.get("costDice") or skill.get("cost")
                lines.append(f"    · {skill.get('name') or '技能'}（{kind}{dmg}，费用 {_dice_text(cost)}）")

        if cards:
            buckets: Dict[str, List[str]] = {}
            cost_hist: Dict[int, int] = {}
            for ident in cards:
                info = card_index.get(ident) or token_index.get(ident) or {}
                kind = _CARD_KINDS.get(str(info.get("type")), str(info.get("type") or "行动牌"))
                buckets.setdefault(kind, []).append(info.get("name") or f"牌#{ident}")
                cost = info.get("cost")
                if isinstance(cost, int):
                    cost_hist[cost] = cost_hist.get(cost, 0) + 1
            lines.append(f"行动牌（{len(cards)} 张，{sum(len(v) for v in buckets.values())} 个条目）：")
            for kind, items in buckets.items():
                counts: Dict[str, int] = {}
                for item in items:
                    counts[item] = counts.get(item, 0) + 1
                shown = "、".join(f"{n}×{c}" if c > 1 else n for n, c in counts.items())
                lines.append(f"- {kind}（{len(items)}）：{shown}")
            if cost_hist:
                hist = " / ".join(f"{k}费{v}" for k, v in sorted(cost_hist.items()))
                lines.append(f"费用分布：{hist}")

        text = "\n".join(lines)
        truncated = False
        if len(text) > inputs.max_chars:
            mark = "…（已截断）"
            text = text[: max(0, inputs.max_chars - len(mark))] + mark
            truncated = True
        return DeckBriefOutput(
            text=text,
            characters=names,
            total_cards=len(cards),
            distinct_cards=len({*cards}),
            chars=len(text) + (6 if truncated else 0),
        )
