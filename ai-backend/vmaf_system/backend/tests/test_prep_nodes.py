# -*- coding: utf-8 -*-
"""``shared_get`` / ``deck_brief`` / ``plan_guard`` 的单元测试。

缺陷背景:
    - ``shared_get`` 是"司机 → 图"的唯一通用入口，路径写错时如果不报错也不
      返回 None，整张图会拿空数据继续跑，最后表现为"模型胡言乱语"，极难定位。
    - ``deck_brief`` 一旦漏掉技能或把费用渲染成乱码，模型写的战术就是错的，
      而且没有任何报错。
    - ``plan_guard`` 关系到"计划 ≤500 字并每手重复注入"，多一个字都持续花 token。
"""

from __future__ import annotations

import pytest

from plugins.tools.deck_brief import DeckBriefInput, DeckBriefNode
from plugins.tools.plan_guard import PlanGuardInput, PlanGuardNode
from plugins.tools.shared_get import SharedGetInput, SharedGetNode

pytestmark = [pytest.mark.plugin, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# shared_get
# ---------------------------------------------------------------------------
async def test_shared_get_reads_nested_path():
    """点号路径可以下钻到 dict 里面（ctx.plan 就是这么取的）。"""
    ctx = {"shared": {"ctx": {"plan": "前两回合抢节奏"}, "seat": 1}}
    out = await SharedGetNode().execute(SharedGetInput(key="ctx.plan"), ctx)
    assert out.value == "前两回合抢节奏"
    assert out.found is True
    assert out.kind == "str"


async def test_shared_get_missing_returns_default():
    """取不到时安静返回 default（不打断整张图）。"""
    out = await SharedGetNode().execute(SharedGetInput(key="nope", default=""), {"shared": {}})
    assert out.value == ""
    assert out.found is False
    assert out.kind == "absent"


async def test_shared_get_required_raises():
    """required=True 时取不到要报错，并列出当前的顶层键，方便定位。"""
    with pytest.raises(ValueError) as exc:
        await SharedGetNode().execute(SharedGetInput(key="deck", required=True), {"shared": {"seat": 1}})
    assert "deck" in str(exc.value) and "seat" in str(exc.value)


async def test_shared_get_list_index():
    """纯数字段当列表下标。"""
    out = await SharedGetNode().execute(SharedGetInput(key="deck.characters.1"), {"shared": {"deck": {"characters": [11, 22, 33]}}})
    assert out.value == 22


# ---------------------------------------------------------------------------
# deck_brief
# ---------------------------------------------------------------------------
CATALOG = {
    "characters": [
        {
            "id": 1411,
            "name": "久岐忍",
            "tags": ["雷", "单手剑", "稻妻"],
            "hp": 10,
            "energy": 2,
            "skills": [
                {"id": 111, "name": "忍流·飞刃", "type": "normalAttack", "damage": [2], "costDice": {"3": 1, "0": 2}},
                {"id": 112, "name": "越祓雷草之轮", "type": "elementalSkill", "damage": [3], "costDice": {"3": 3}},
            ],
        },
        {"id": 2102, "name": "莫娜", "tags": ["水"], "hp": 10, "energy": 3, "skills": []},
        {"id": 1510, "name": "珊瑚宫心海", "tags": ["水"], "hp": 11, "energy": 2, "skills": []},
    ],
    "cards": [
        {"id": 330008, "name": "交给你了", "type": "GCG_CARD_EVENT", "cost": 1},
        {"id": 313002, "name": "璃月港口", "type": "GCG_CARD_ASSIST", "cost": 2},
        {"id": 214041, "name": "祭礼剑", "type": "GCG_CARD_MODIFY", "cost": 3},
    ],
    "tokens": [],
}


async def test_deck_brief_renders_characters_and_cards():
    """角色带生命/充能/技能，行动牌按类型分组并计数。"""
    deck = {
        "name": "测试牌组",
        "characters": [1411, 2102, 1510],
        "cards": [330008, 330008, 313002, 214041],
    }
    out = await DeckBriefNode().execute(DeckBriefInput(deck=deck, catalog=CATALOG, seat=1))
    assert out.characters == ["久岐忍", "莫娜", "珊瑚宫心海"]
    assert out.total_cards == 4
    assert out.distinct_cards == 3
    assert "座位 1 牌组：测试牌组" in out.text
    assert "久岐忍" in out.text and "生命10" in out.text
    assert "忍流·飞刃" in out.text and "普通攻击" in out.text and "伤害 2" in out.text
    assert "事件牌（2）：交给你了×2" in out.text
    assert "支援牌（1）：璃月港口" in out.text
    assert "装备牌（1）：祭礼剑" in out.text


async def test_deck_brief_counts_dict_cards():
    """cards 允许写成 {id,count} 形式（桥接接口就是这个形状）。"""
    deck = {"characters": [1411], "cards": [{"id": 330008, "count": 2}, {"id": 313002, "count": 1}]}
    out = await DeckBriefNode().execute(DeckBriefInput(deck=deck, catalog=CATALOG))
    assert out.total_cards == 3
    assert "交给你了×2" in out.text


async def test_deck_brief_truncates_and_skips_skills():
    """skill_detail=False 时更短；超上限截断并标注。"""
    deck = {"characters": [1411, 2102, 1510], "cards": [330008] * 4}
    short = await DeckBriefNode().execute(DeckBriefInput(deck=deck, catalog=CATALOG, skill_detail=False))
    long = await DeckBriefNode().execute(DeckBriefInput(deck=deck, catalog=CATALOG))
    assert len(short.text) < len(long.text)
    out = await DeckBriefNode().execute(DeckBriefInput(deck=deck, catalog=CATALOG, max_chars=120))
    assert "已截断" in out.text
    assert out.chars <= 130


async def test_deck_brief_unknown_ids_fall_back_to_raw():
    """名字表里没有的 id 不能崩，退化成 角色#id / 牌#id。"""
    out = await DeckBriefNode().execute(DeckBriefInput(deck={"characters": [9999], "cards": [8888]}, catalog={}))
    assert "角色#9999" in out.text
    assert "牌#8888" in out.text


# ---------------------------------------------------------------------------
# plan_guard
# ---------------------------------------------------------------------------
async def test_plan_guard_strips_preamble_and_quotes():
    """去掉"好的，以下是……"和包裹引号。"""
    raw = "好的，以下是我的战术：\n“先用手牌铺场，第三回合爆发。”"
    out = await PlanGuardNode().execute(PlanGuardInput(text=raw))
    assert out.plan.startswith("先用手牌铺场")
    assert "好的" not in out.plan and "以下是" not in out.plan
    assert "preamble" in out.dropped and "quotes" in out.dropped


async def test_plan_guard_truncates_at_sentence():
    """超长时切在句末，不切在半句。"""
    raw = "第一句。" * 100
    out = await PlanGuardNode().execute(PlanGuardInput(text=raw, max_chars=50))
    assert out.truncated is True
    assert out.chars <= 50
    assert out.plan.endswith("。")


async def test_plan_guard_keeps_short_plan_untouched():
    """短计划原样保留（只归一化空白）。"""
    out = await PlanGuardNode().execute(PlanGuardInput(text="抢节奏，先铺场再爆发。\n\n\n留 2 费过牌。"))
    assert out.plan == "抢节奏，先铺场再爆发。\n\n留 2 费过牌。"
    assert out.truncated is False
