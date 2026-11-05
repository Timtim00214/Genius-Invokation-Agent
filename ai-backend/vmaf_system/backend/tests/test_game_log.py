# -*- coding: utf-8 -*-
"""``game_log`` 与 ``prompt_pack.recent`` 的单元测试。

缺陷背景（为什么单独测这两个）:
    - ``game_log`` 把桥接日志（``detail={req,res}``，键全是 proto-JSON 的
      ``$case`` / ``value``）翻成人话。这层翻译一旦错位，提示词里就会静默变成
      "对方 行动 #x"或整段消失——模型不会报错，只会开始猜。
    - ``recent`` 是给 ``prompt_pack`` 新加的槽位：它必须排在"往期判断"与
      "当前局面"之间，且超预算时优先被丢（宁可少一段回顾，也不能挤掉局面/菜单）。
"""

from __future__ import annotations

import pytest

from plugins.tools.game_log import GameLogInput, GameLogNode
from plugins.tools.prompt_pack import PromptPackInput, PromptPackNode

pytestmark = [pytest.mark.plugin, pytest.mark.asyncio]


def _state():
    """最小局面：双方各一个出战角色、一张手牌。"""
    return {
        "player": [
            {
                "activeCharacterId": -10,
                "character": [{"id": -10, "definitionId": 1411, "health": 10, "maxHealth": 10}],
                "handCard": [{"id": -30, "definitionId": 330008}],
            },
            {
                "activeCharacterId": -20,
                "character": [{"id": -20, "definitionId": 2304, "health": 5, "maxHealth": 5}],
                "handCard": [],
            },
        ]
    }


def _catalog():
    return {
        "characters": {"1411": "久岐忍", "2304": "铁甲熔火帝皇"},
        "cards": {"330008": "旧日鏖战"},
        "tokens": {},
        "skills": {"23041": "重钳碎击"},
    }


def _card_text():
    return {
        "descriptions": {
            "330008": "敌方出战角色失去1点充能。",
            "23041": "造成2点物理伤害。",
        }
    }


def _action_entry(seat, actions, index, kind="decision"):
    """构造一条 ``kind="decision"`` 的桥接日志（形状与页面 POST 的一致）。"""
    return {
        "time": 1000 + index,
        "seat": seat,
        "kind": kind,
        "text": "",
        "detail": {
            "req": {"request": {"$case": "action", "value": {"action": actions}}},
            "res": {"response": {"$case": "action", "value": {"chosenActionIndex": index}}},
        },
    }


def _act(case, value=None):
    return {"action": {"$case": case, "value": value or {}}}


async def test_renders_play_card_with_effect_and_side():
    """打出牌要带牌名与效果，且按座位号区分"我方/对方"。"""
    log = [
        _action_entry(0, [_act("playCard", {"cardDefinitionId": 330008})], 0),
        _action_entry(1, [_act("playCard", {"cardDefinitionId": 330008})], 0),
    ]
    out = await GameLogNode().execute(
        GameLogInput(log=log, seat=1, state=_state(), catalog=_catalog(), card_text=_card_text())
    )
    lines = out.text.splitlines()
    assert out.count == 2
    assert lines[0] == "- 对方 打出「旧日鏖战」：敌方出战角色失去1点充能"
    assert lines[1] == "- 我方 打出「旧日鏖战」：敌方出战角色失去1点充能"


async def test_renders_skill_switch_and_end_turn():
    """技能/切人/结束回合都要翻成人话，运行期 id 用局面翻译成名字。"""
    log = [
        _action_entry(0, [_act("useSkill", {"skillDefinitionId": 23041})], 0),
        _action_entry(0, [_act("switchActive", {"characterId": -20})], 0),
        _action_entry(0, [_act("declareEnd")], 0),
    ]
    out = await GameLogNode().execute(
        GameLogInput(log=log, seat=1, state=_state(), catalog=_catalog(), card_text=_card_text())
    )
    assert "对方 使用技能「重钳碎击」：造成2点物理伤害" in out.text
    assert "对方 切换到「铁甲熔火帝皇」" in out.text
    assert "对方 宣布结束回合" in out.text


async def test_renders_choose_active():
    """选出生效角色（chooseActive）也要进日志，否则开局像凭空完成。"""
    entry = {
        "time": 1,
        "seat": 0,
        "kind": "decision",
        "detail": {
            "req": {"request": {"$case": "chooseActive", "value": {}}},
            "res": {"response": {"$case": "chooseActive", "value": {"activeCharacterId": -20}}},
        },
    }
    out = await GameLogNode().execute(
        GameLogInput(log=[entry], seat=1, state=_state(), catalog=_catalog(), card_text=_card_text())
    )
    assert out.text == "- 对方 选择出战「铁甲熔火帝皇」"


async def test_skips_noise_entries():
    """系统播报、未知座位、认不出的动作都不该出现在提示词里。"""
    log = [
        {"time": 1, "seat": None, "kind": "decision", "detail": {}},
        {"time": 2, "seat": 0, "kind": "system", "text": "对局开始", "detail": None},
        _action_entry(0, [_act("playCard", {"cardDefinitionId": 330008})], 5, kind="info"),
        {"time": 4, "seat": 0, "kind": "decision", "detail": {"req": {"request": {"$case": "selectCard"}}}},
    ]
    out = await GameLogNode().execute(GameLogInput(log=log, seat=1, state=_state()))
    assert out.count == 0
    assert out.empty is True
    assert out.text == ""


async def test_limit_keeps_most_recent():
    """limit 只保留最近的若干条（不按时间倒序输出）。"""
    log = [
        _action_entry(0, [_act("playCard", {"cardDefinitionId": 330008})], 0)
        for _ in range(5)
    ]
    out = await GameLogNode().execute(
        GameLogInput(log=log, seat=1, state=_state(), catalog=_catalog(), card_text=_card_text(), limit=2)
    )
    assert out.count == 2
    assert out.text.count("\n") == 1


async def test_missing_catalog_degrades_gracefully():
    """没有名字表/效果表时只退化成编号，不能抛异常。"""
    log = [_action_entry(0, [_act("useSkill", {"skillDefinitionId": 23041})], 0)]
    out = await GameLogNode().execute(GameLogInput(log=log, seat=1, state=_state()))
    assert out.empty is True, "认不出技能名时宁可不写，也不要写空壳"


async def test_prompt_pack_recent_sits_between_history_and_state():
    """recent 段必须排在"往期判断"之后、"当前局面"之前。"""
    out = await PromptPackNode().execute(
        PromptPackInput(
            history="往期文字",
            recent="- 对方 打出「旧日鏖战」：敌方出战角色失去1点充能",
            state_text="当前局面文字",
            menu_text="[0] 重钳碎击",
            task="只回复编号",
        )
    )
    prompt = out.prompt
    assert prompt.index("### 往期判断") < prompt.index("### 最近发生的事") < prompt.index("### 当前局面")
    assert out.stats["sections"]["recent"] > 0


async def test_prompt_pack_drops_recent_before_state():
    """超预算时 recent 先于局面/菜单被丢（回顾可以让路，局面不能）。"""
    out = await PromptPackNode().execute(
        PromptPackInput(
            recent="回" * 400,
            state_text="局面" * 20,
            menu_text="[0] 重钳碎击",
            task="只回复编号",
            budget_chars=260,
        )
    )
    assert "recent" in out.stats["dropped"]
    assert "state_text" not in out.stats["dropped"]
    assert "### 可选动作" in out.prompt
