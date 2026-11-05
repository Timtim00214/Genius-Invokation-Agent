# -*- coding: utf-8 -*-
"""``action_rank`` 与 ``playbook`` 的单元测试。

缺陷背景（为什么单独测这两个节点）:
    - ``action_rank`` 会把候选从三十多项砍到几项，一旦分数算错，模型就再也看不到
      真正该用的技能——这是"静默变笨"，必须用真实 aiDetail 形状锁住。
    - ``playbook`` 的每一段都是使用者手写的提示词，拼接逻辑错（漏段、串段、把
      不相关的类别也塞进去）会让调试时完全找不到原因。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from plugins.tools.action_rank import ActionRankInput, ActionRankNode
from plugins.tools.playbook import PlaybookInput, PlaybookNode

pytestmark = [pytest.mark.plugin, pytest.mark.asyncio]

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _detail(actions, rpc="action", **extra):
    """构造一个最小 aiDetail。"""
    detail = {"rpc": rpc, "actions": actions}
    detail.update(extra)
    return detail


def _action(index, kind, name, *, validity=0, damage=None, cost=2, preview_target="对方出战"):
    """构造一项 action（带预示伤害时自动生成 preview）。"""
    item = {
        "index": index,
        "kind": kind,
        "name": name,
        "validity": validity,
        "validityLabel": "合法" if validity == 0 else "骰子不足",
        "cost": {"total": cost, "dice": {"3": cost}, "text": f"{cost}火"},
    }
    if damage is not None:
        item["preview"] = [
            {"type": "damage", "targetId": -5, "target": preview_target, "amount": damage, "element": "火", "defeated": False}
        ]
    return item


def _state(opp_hp):
    """构造一份只关心"对方出战角色血量"的局面。"""
    return {
        "phase": 3,
        "currentTurn": 0,
        "roundNumber": 2,
        "player": [
            {"activeCharacterId": -1, "character": [{"id": -1, "health": 10, "maxHealth": 10, "defeated": False}]},
            {"activeCharacterId": -9, "character": [{"id": -9, "health": opp_hp, "maxHealth": 10, "defeated": False}]},
        ],
    }


async def test_rank_keeps_damage_first_and_always_end_turn():
    """高分伤害在前，结束回合始终保留在末尾。"""
    detail = _detail([
        _action(0, "elementalTuning", "元素调和（弃 未知牌）", damage=None, cost=1),
        _action(1, "useSkill", "普通攻击", damage=2, cost=1),
        _action(2, "useSkill", "元素战技", damage=5, cost=3),
        _action(3, "declareEnd", "结束回合", damage=None, cost=0),
    ])
    out = await ActionRankNode().execute(ActionRankInput(detail=detail, seat=0, top_k=3, state=_state(9)))
    assert out.kind == "action"
    assert out.indexes[-1] == 3, "结束回合必须保留"
    assert 2 in out.indexes, "5 点伤害的技能应该被保留"
    assert out.empty is False
    # 分数与编号一一对应，且按编号升序输出（便于模型对着菜单读）
    assert out.indexes == sorted(out.indexes)


async def test_rank_marks_lethal_skill_and_keeps_it():
    """能一击打死对方出战角色的技能要打上★并被优先保留。"""
    detail = _detail([
        _action(0, "useSkill", "普通攻击", damage=1, cost=1),
        _action(1, "useSkill", "斩杀", damage=4, cost=3),
        _action(2, "playCard", "换骰子", damage=None, cost=1),
        _action(3, "declareEnd", "结束回合", cost=0),
    ])
    out = await ActionRankNode().execute(ActionRankInput(detail=detail, seat=0, top_k=2, state=_state(4)))
    assert 1 in out.indexes
    assert "可直接击杀" in out.text


async def test_rank_truncates_to_top_k_plus_end_turn():
    """top_k 是硬上限（结束回合占用其中一格）。"""
    actions = [_action(i, "useSkill", f"技能{i}", damage=i % 3, cost=1) for i in range(10)]
    actions.append(_action(10, "declareEnd", "结束回合", cost=0))
    out = await ActionRankNode().execute(ActionRankInput(detail=_detail(actions), seat=0, top_k=4))
    assert out.shown == 4
    assert len(out.indexes) == 4
    assert 10 in out.indexes


async def test_rank_skips_invalid_actions():
    """合法性子段非 0 的候选不参与排序。"""
    detail = _detail([
        _action(0, "useSkill", "没骰子", damage=9, validity=3),
        _action(1, "useSkill", "能打", damage=2),
    ])
    out = await ActionRankNode().execute(ActionRankInput(detail=detail, seat=0, top_k=5))
    assert out.indexes == [1]


async def test_rank_passthrough_for_choose_active():
    """选出战角色不排序，编号即候选下标。"""
    detail = {"rpc": "chooseActive", "candidates": [{"id": -1, "name": "宵宫", "health": 3, "maxHealth": 10}]}
    out = await ActionRankNode().execute(ActionRankInput(detail=detail, top_k=5))
    assert out.kind == "chooseActive"
    assert out.indexes == [0]
    assert "宵宫" in out.text


async def test_rank_real_fixture_keeps_playable_options():
    """真实录制数据：排序后仍有可选项，且结束回合在里面。"""
    payload = json.loads((FIXTURES / "arena_turn_action.json").read_text(encoding="utf-8"))["payload"]
    out = await ActionRankNode().execute(
        ActionRankInput(detail=payload["aiDetail"], request=payload["request"], state=payload["state"], seat=1, top_k=6)
    )
    assert out.kind == "action"
    assert out.shown == 6
    assert out.empty is False
    assert any("[0]" in line or "[1]" in line for line in out.text.splitlines())


# ---------------------------------------------------------------------------
# playbook
# ---------------------------------------------------------------------------
async def test_playbook_only_emits_present_categories():
    """当前菜单里没有"出牌"时，就不该把"出牌"那段塞进提示词。"""
    detail = _detail([_action(0, "useSkill", "普通攻击", damage=1), _action(1, "declareEnd", "结束回合", cost=0)])
    out = await PlaybookNode().execute(
        PlaybookInput(
            detail=detail,
            general_rule="先算伤害。",
            use_skill_rule="能打死就打。",
            play_card_rule="这张牌不该出现。",
            declare_end_rule="骰子用完才结束。",
        )
    )
    assert "先算伤害。" in out.text
    assert "能打死就打。" in out.text
    assert "骰子用完才结束。" in out.text
    assert "这张牌不该出现。" not in out.text
    assert out.sections == ["通用", "用技能", "结束回合"]


async def test_playbook_all_present_when_only_present_off():
    """关掉 only_present 后，所有写了内容的类别都会出现（用于调试提示词）。"""
    detail = _detail([_action(0, "useSkill", "普通攻击", damage=1)])
    out = await PlaybookNode().execute(
        PlaybookInput(detail=detail, use_skill_rule="A", play_card_rule="B", only_present=False)
    )
    assert "A" in out.text and "B" in out.text


async def test_playbook_rpc_kinds():
    """chooseActive / rerollDice 各走各的段。"""
    out = await PlaybookNode().execute(
        PlaybookInput(detail={"rpc": "chooseActive", "candidates": []}, choose_active_rule="留能扛的")
    )
    assert out.sections == ["选出战角色"] and "留能扛的" in out.text

    out = await PlaybookNode().execute(
        PlaybookInput(detail={"rpc": "rerollDice", "dice": [1]}, reroll_dice_rule="重投渣骰")
    )
    assert out.sections == ["重投骰子"] and "重投渣骰" in out.text


async def test_playbook_empty_rules_keep_only_footer():
    """一句策略都没写时，只留格式提醒，不产生空的"打法要求"主体。"""
    out = await PlaybookNode().execute(PlaybookInput(detail=_detail([_action(0, "useSkill", "x", damage=1)])))
    assert out.sections == []
    assert out.text == PlaybookInput().footer
    assert "打法要求" not in out.text


async def test_playbook_truncates():
    """超长时截断并标注。"""
    out = await PlaybookNode().execute(
        PlaybookInput(detail=_detail([_action(0, "useSkill", "x", damage=1)]), use_skill_rule="很" * 500, max_chars=50)
    )
    assert out.chars <= 60
    assert "已截断" in out.text
