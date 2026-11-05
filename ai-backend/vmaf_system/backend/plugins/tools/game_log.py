# -*- coding: utf-8 -*-
"""
【对局】把桥接服务里的对局日志渲染成"最近发生了什么"（game_log）。

为什么需要它:
    局面（``state_brief``）只描述"现在长什么样"，不描述"是怎么变成这样的"。
    对手打出一张事件牌（交给我吧！、仙跳墙）之后，场上往往什么都不留；
    模型只能看到血量/骰子变了，于是开始猜"刚才那张牌是干嘛的"——推理被浪费在
    还原规则上，而不是棋上。

    游戏页面本身会把双方每一步都写进 ``POST /api/arena/log``
    （``seat`` 是谁、``kind="decision"``、``detail={req,res}``）；
    本节点把 ``detail.req`` 里的原始动作 + ``catalog``/``card_text``
    翻译成"谁做了什么、那张牌有什么效果"的人类语言。

输入约定:
    ``log`` 来自 ``shared.game_log``（司机每手取一次 ``GET /api/arena/log``）。
    每条形如 ``{time, seat, text, kind, detail}``；只处理 ``kind="decision"``
    且 ``seat`` 是 0/1 的条目（其余是系统播报，噪音）。

契约:
    输入 :class:`GameLogInput` / 输出 :class:`GameLogOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = ["GameLogInput", "GameLogOutput", "GameLogNode"]

#: 效果文本里属于"构筑说明"的行，对局中没有意义，直接丢掉。
_META_HINTS = ("牌组中包含", "整局游戏只能打出", "一定在你的起始手牌中", "最多装备1个")

#: 请求类型里值得写进"最近发生的事"的几种。
_VERB = {
    "useSkill": "使用技能",
    "playCard": "打出",
    "switchActive": "切换到",
    "elementalTuning": "元素调和",
    "declareEnd": "结束回合",
}


def _lookup(mapping: Any, key: Any) -> Optional[str]:
    """在 ``{id: 名字}`` 里按字符串或整数键取值，取不到返回 None。"""
    if not isinstance(mapping, dict) or key is None:
        return None
    for k in (str(key), key):
        value = mapping.get(k)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _effect(card_text: Dict[str, Any], definition_id: Any, limit: int) -> str:
    """取一张牌/技能的效果文本，去掉构筑说明并截断。"""
    descriptions = (card_text or {}).get("descriptions")
    raw = _lookup(descriptions, definition_id)
    if not raw:
        return ""
    lines = []
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("（") or text.startswith("("):
            continue
        if any(hint in text for hint in _META_HINTS):
            continue
        lines.append(text)
    text = "；".join(lines).strip()
    if not text:
        return ""
    # 子技能前缀（"[23042] 名称" 这类）对决策没用，去掉。
    if text.startswith("[") and "]" in text:
        text = text.split("]", 1)[-1].strip()
    if len(text) > limit:
        cut = max(text.rfind("。", 0, limit), text.rfind("；", 0, limit))
        text = text[: cut + 1] if cut > limit // 2 else text[:limit] + "…"
    return text.rstrip("。；")


def _runtime_names(state: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """把运行期 id（负数）映射到 ``{definition_id, category}``。

    日志里的动作只带运行期 id（``characterId`` / ``removedCardId``），
    而名字表按 definitionId 建键，所以需要先经过现状这份"翻译表"。
    """
    names: Dict[int, Dict[str, Any]] = {}
    players = state.get("player") if isinstance(state, dict) else None
    if isinstance(players, dict):
        players = [players.get("me"), players.get("opponent")]
    if not isinstance(players, list):
        return names
    for side in players:
        if not isinstance(side, dict):
            continue
        for category, key in (
            ("characters", "character"),
            ("cards", "handCard"),
            ("cards", "pileCard"),
            ("supports", "support"),
            ("tokens", "summon"),
            ("tokens", "combatStatus"),
        ):
            items = side.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                rid, did = item.get("id"), item.get("definitionId")
                if isinstance(rid, int) and isinstance(did, int) and did > 0:
                    names.setdefault(rid, {"definition_id": did, "category": category})
        for item in side.get("character") or []:
            if not isinstance(item, dict):
                continue
            for entity in item.get("entity") or []:
                if not isinstance(entity, dict):
                    continue
                rid, did = entity.get("id"), entity.get("definitionId")
                if isinstance(rid, int) and isinstance(did, int) and did > 0:
                    names.setdefault(rid, {"definition_id": did, "category": "tokens"})
    return names


def _name_of(
    catalog: Dict[str, Any],
    runtime: Dict[int, Dict[str, Any]],
    ident: Any,
) -> str:
    """运行期 id → 名字；查不到就退化成 ``#id``。"""
    info = runtime.get(ident) if isinstance(ident, int) else None
    if not info:
        return f"#{ident}"
    category = "cards" if info["category"] == "cards" else info["category"]
    category = {"supports": "supports", "tokens": "tokens", "characters": "characters"}.get(
        category, category
    )
    name = _lookup((catalog or {}).get(category), info["definition_id"])
    return name or f"#{info['definition_id']}"


def _info_line(entry: Dict[str, Any], seat: int) -> str:
    """把一条 ``kind="info"`` 的引擎日志翻成一句话（对方动作主要靠它）。

    引擎日志本来就是人话（"打出「莲花酥」""使用技能「风鹰剑」"），这里只补上是谁，
    已经自带"我方/对方"的就原样返回，免得读起来像复读。

    Args:
        entry (Dict[str, Any]): 桥接日志里的一项（``kind="info"``）。
        seat (int): 我方座位号。

    Returns:
        str: 渲染好的句子；空文本返回空串。
    """
    text = str(entry.get("text") or "").strip()
    if not text:
        return ""
    who = entry.get("seat")
    if who not in (0, 1):
        return text
    if text.startswith(("我方", "对方", "敌方", "你", "对手")):
        return text
    return f"{'我方' if who == seat else '对方'} {text}"


def _decision_line(
    entry: Dict[str, Any],
    seat: int,
    catalog: Dict[str, Any],
    card_text: Dict[str, Any],
    runtime: Dict[int, Dict[str, Any]],
    effect_limit: int,
) -> str:
    """把一条 ``kind="decision"`` 的日志翻成一句话；翻不出来返回空串。"""
    detail = entry.get("detail") if isinstance(entry.get("detail"), dict) else {}
    req = detail.get("req") if isinstance(detail.get("req"), dict) else {}
    res = detail.get("res") if isinstance(detail.get("res"), dict) else {}
    case = ((req.get("request") or {}).get("$case")) if isinstance(req.get("request"), dict) else None
    who = "我方" if entry.get("seat") == seat else "对方"

    if case == "action":
        actions = ((req.get("request") or {}).get("value") or {}).get("action") or []
        value = ((res.get("response") or {}).get("value")) if isinstance(res.get("response"), dict) else {}
        index = value.get("chosenActionIndex") if isinstance(value, dict) else None
        if not isinstance(index, int) or not (0 <= index < len(actions)):
            return ""
        action = ((actions[index] or {}).get("action")) if isinstance(actions[index], dict) else {}
        kind = action.get("$case")
        body = action.get("value") if isinstance(action.get("value"), dict) else {}
        if kind == "useSkill":
            definition_id = body.get("skillDefinitionId")
            name = _lookup((catalog or {}).get("skills"), definition_id)
            if not name:
                return ""
            effect = _effect(card_text, definition_id, effect_limit)
            return f"{who} 使用技能「{name}」：{effect}" if effect else f"{who} 使用技能「{name}」"
        if kind == "playCard":
            definition_id = body.get("cardDefinitionId")
            name = _lookup((catalog or {}).get("cards"), definition_id) or f"#{definition_id}"
            effect = _effect(card_text, definition_id, effect_limit)
            return f"{who} 打出「{name}」：{effect}" if effect else f"{who} 打出「{name}」"
        if kind == "switchActive":
            return f"{who} 切换到「{_name_of(catalog, runtime, body.get('characterId'))}」"
        if kind == "elementalTuning":
            return f"{who} 元素调和（弃一张手牌换骰）"
        if kind == "declareEnd":
            return f"{who} 宣布结束回合"
        return ""
    if case == "chooseActive":
        value = ((res.get("response") or {}).get("value")) if isinstance(res.get("response"), dict) else {}
        ident = value.get("activeCharacterId") if isinstance(value, dict) else None
        if ident is None:
            return ""
        return f"{who} 选择出战「{_name_of(catalog, runtime, ident)}」"
    if case == "rerollDice":
        return f"{who} 重投了骰子"
    if case == "switchHands":
        return f"{who} 替换了起始手牌"
    return ""


class GameLogInput(BaseModel):
    """对局日志渲染的输入契约。

    Attributes:
        log (List[Dict[str, Any]]): 桥接日志（``GET /api/arena/log`` 的 ``logs``），
            连线接 ``arena_input.game_log``。
        seat (int): 自己坐哪边，用于标注"我方/对方"。
        state (Dict[str, Any]): 当前局面，用于把运行期 id 翻成名字。
        catalog (Dict[str, Any]): 名字表 ``{characters,cards,tokens,skills:{id:名字}}``。
        card_text (Dict[str, Any]): 效果文本 ``{descriptions:{id:文本}}``。
        limit (int): 最多渲染多少条（只取最近的）。
        effect_chars (int): 单张牌效果文本的最大字符数。
    """

    model_config = {"extra": "forbid"}

    log: List[Dict[str, Any]] = Field(
        default_factory=list, description="桥接对局日志，连线接 arena_input.game_log"
    )
    seat: int = Field(0, ge=0, le=1, description="我方座位")
    state: Dict[str, Any] = Field(default_factory=dict, description="当前局面")
    catalog: Dict[str, Any] = Field(default_factory=dict, description="名字表")
    card_text: Dict[str, Any] = Field(default_factory=dict, description="效果文本表")
    limit: int = Field(12, ge=1, le=80, description="最多渲染多少条最近的动作")
    effect_chars: int = Field(48, ge=8, le=200, description="单张牌效果文本的最大字符数")


class GameLogOutput(BaseModel):
    """对局日志渲染的输出契约。

    Attributes:
        text (str): 逐行文本，每行一件事（可直接塞进提示词）。
        count (int): 实际渲染了几条。
        empty (bool): 没有任何可渲染的决策日志。
    """

    model_config = {"extra": "forbid"}

    text: str = Field("", description="逐行文本")
    count: int = Field(0, ge=0, description="渲染条数")
    empty: bool = Field(False, description="是否为空")


class GameLogNode(BaseNode):
    """把对局日志渲染成"最近发生了什么"。"""

    node_type = "game_log"
    description = "把桥接对局日志（双方动作）渲染成带效果说明的逐行文本"

    input_model = GameLogInput
    output_model = GameLogOutput

    async def execute(
        self,
        inputs: GameLogInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> GameLogOutput:
        """渲染最近若干条决策日志。

        Args:
            inputs (GameLogInput): 日志、座位、局面与名字/效果表。
            context (Optional[Dict[str, Any]]): 未使用。

        Returns:
            GameLogOutput: 逐行文本与条数。
        """
        runtime = _runtime_names(inputs.state)
        lines: List[str] = []
        for entry in inputs.log or []:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            if kind == "info":
                line = _info_line(entry, inputs.seat)
            elif kind == "decision":
                if entry.get("seat") not in (0, 1):
                    continue
                line = _decision_line(
                    entry,
                    inputs.seat,
                    inputs.catalog,
                    inputs.card_text,
                    runtime,
                    inputs.effect_chars,
                )
            else:
                continue
            if line:
                lines.append(line)
        lines = lines[-inputs.limit :]
        return GameLogOutput(
            text="\n".join(f"- {line}" for line in lines),
            count=len(lines),
            empty=not lines,
        )
