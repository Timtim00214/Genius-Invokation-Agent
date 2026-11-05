# -*- coding: utf-8 -*-
"""
对局决策节点（``arena_input`` / ``action_menu`` / ``state_brief`` /
``prompt_pack`` / ``choice_parse`` / ``rpc_build``）行为测试。

这些节点把"游戏推来的结构化事实"翻译成"模型能读的一小段文本"，
再把"模型回的一个编号"翻译回"桥接能接受的完整响应"。
测试重点因此有三块：

    1. **翻译准确性**：菜单里出现的编号必须与 aiDetail 一致，不能凭空造编号；
    2. **预算与裁剪**：提示词不能超预算，候选菜单不能被裁掉；
    3. **兜底安全性**：任何一步失败都必须落到"游戏自己也认可的默认动作"，
       绝不能让非法响应被发出去（那会被判 IO 错误、直接输掉对局）。
"""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.plugin

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ======================================================================
# 公共样例数据
# ======================================================================
def _detail_action() -> dict:
    """构造一份"行动阶段"的 aiDetail 样例。

    Returns:
        dict: 含两个合法动作、一个非法动作与一个结束回合动作。
    """
    return {
        "type": "request",
        "rpc": "action",
        "round": 3,
        "phase": "action",
        "currentTurn": 1,
        "declaredEnd": False,
        "activeCharacter": {"id": 101, "definitionId": 1101, "name": "宵宫"},
        "disabledBy": [],
        "note": "",
        "actions": [
            {
                "index": 0,
                "kind": "useSkill",
                "name": "琉璃百华",
                "skillId": 15101,
                "validity": 0,
                "validityLabel": "",
                "cost": {"dice": [4, 8, 8], "total": 3, "text": "3 火"},
                "autoSelectedDice": [4, 8, 8],
                "preview": [{"type": "伤害", "target": "对方出战", "amount": 2, "element": 4}],
            },
            {
                "index": 1,
                "kind": "playCard",
                "name": "绝云锅巴",
                "validity": 0,
                "cost": {"dice": [0, 0], "total": 2, "text": "2 任意"},
                "autoSelectedDice": [0, 0],
                "preview": [],
            },
            {
                "index": 2,
                "kind": "useSkill",
                "name": "元素爆发",
                "validity": 4,
                "validityLabel": "充能不足",
                "cost": {"dice": [4, 4, 4], "total": 3, "text": "3 火"},
                "autoSelectedDice": [4, 4, 4],
                "preview": [],
            },
            {
                "index": 3,
                "kind": "declareEnd",
                "name": "结束回合",
                "validity": 0,
                "cost": {"dice": [], "total": 0, "text": ""},
                "autoSelectedDice": [],
                "preview": [],
            },
        ],
    }


def _state_sample() -> dict:
    """构造一份真实形状的 ExposedGameState（顶层 player 数组，下标即座位）。

    Returns:
        dict: 我方（座位 0）含完整角色/手牌/骰子，对方（座位 1）的手牌只有数量。
    """
    return {
        "phase": 3,
        "currentTurn": 0,
        "roundNumber": 3,
        "player": [
            {
                "activeCharacterId": -101,
                "character": [
                    {
                        "id": -101,
                        "definitionId": 1101,
                        "health": 8,
                        "maxHealth": 10,
                        "energy": 1,
                        "maxEnergy": 3,
                        "defeated": False,
                        "aura": 4,
                        "tags": {},
                        "entity": [
                            {"id": -901, "type": "equipment", "definitionId": 123041, "variableName": "", "variableValue": 0}
                        ],
                    },
                    {
                        "id": -102,
                        "definitionId": 1201,
                        "health": 0,
                        "maxHealth": 10,
                        "energy": 0,
                        "maxEnergy": 3,
                        "defeated": True,
                        "aura": 0,
                        "tags": {},
                        "entity": [
                            {"id": -902, "type": "status", "definitionId": 123042, "variableName": "shield", "variableValue": 2}
                        ],
                    },
                    {
                        "id": -103,
                        "definitionId": 1301,
                        "health": 10,
                        "maxHealth": 10,
                        "energy": 0,
                        "maxEnergy": 2,
                        "defeated": False,
                        "aura": 0,
                        "tags": {},
                        "entity": [],
                    },
                ],
                "handCard": [
                    {"id": -201, "type": "handCard", "definitionId": 330001, "variableName": "", "variableValue": 0}
                ],
                "dice": [4, 8, 1],
                "pileCard": [{"id": -1000 - i, "type": "pileCard", "definitionId": 330001} for i in range(27)],
                "combatStatus": [],
                "support": [{"id": -301, "type": "support", "definitionId": 312001}],
                "summon": [],
                "initiativeSkill": [],
                "declaredEnd": False,
                "legendUsed": False,
                "status": 0,
            },
            {
                "activeCharacterId": -501,
                "character": [
                    {"id": -501, "definitionId": 2101, "health": 6, "maxHealth": 10, "energy": 0, "maxEnergy": 2, "defeated": False, "aura": 0, "tags": {}, "entity": []}
                ],
                # 对方的 definitionId 一律为 0：内容隐藏，只有张数有意义
                "handCard": [{"id": -600 - i, "type": "handCard", "definitionId": 0} for i in range(5)],
                "dice": [4, 4, 8, 8],
                "pileCard": [],
                "combatStatus": [],
                "support": [],
                "summon": [],
                "initiativeSkill": [],
                "declaredEnd": True,
                "legendUsed": False,
                "status": 0,
            },
        ],
    }


def _name_table() -> dict:
    """构造司机传下来的紧凑名字表（id 键是字符串，与真实 JSON 一致）。"""
    return {
        "characters": {"1101": "宵宫", "1201": "行秋", "1301": "重云", "2101": "莫娜"},
        "cards": {"330001": "交给你了", "312001": "璃月港口"},
        "tokens": {"123041": "祭礼弓", "123042": "护盾"},
        "skills": {},
    }


def _request(request_case: str, value: dict | None = None) -> dict:
    """构造一份 RpcRequest 样例。

    Args:
        request_case (str): 请求类型。
        value (dict | None): 请求内容。

    Returns:
        dict: RpcRequest 形状的字典。
    """
    return {"request": {"$case": request_case, "value": value or {}}}


# ======================================================================
# arena_input
# ======================================================================
@pytest.mark.asyncio
async def test_arena_input_reads_shared(registry):
    """应从 shared 中摊平出请求、局面与派生字段。"""
    node = registry["arena_input"]()
    output = await node.execute(
        node.input_model(),
        {
            "shared": {
                "request": _request("action"),
                "detail": _detail_action(),
                "state": _state_sample(),
                "meta": {
                    "matchId": "local",
                    "seat": 1,
                    "rpcKind": "action",
                    "round": 3,
                    "deadline": 1792925150000,
                    "remainingMs": 41000,
                    "attempt": 1,
                },
                "retry": {"reason": "编号越界"},
                "ctx": {"summary": "#1 r1 action"},
            }
        },
    )

    assert output.rpc_kind == "action"
    assert output.round == 3
    assert output.remaining_ms == 41000
    assert output.attempt == 1
    assert output.retry["reason"] == "编号越界"
    assert output.ctx["summary"] == "#1 r1 action"
    assert "第2次尝试" in output.summary
    assert "上一轮被驳回" in output.summary


@pytest.mark.asyncio
async def test_arena_input_rejects_missing_context(registry):
    """shared 里既没有 request 也没有 detail 时必须报错，而不是猜。"""
    node = registry["arena_input"]()
    with pytest.raises(ValueError):
        await node.execute(node.input_model(), {"shared": {}})


@pytest.mark.asyncio
async def test_arena_input_accepts_ai_detail_alias(registry):
    """司机若把结构化视图放在 aiDetail 键下也应能读到。"""
    node = registry["arena_input"]()
    output = await node.execute(
        node.input_model(),
        {"shared": {"request": _request("action"), "aiDetail": _detail_action()}},
    )
    assert output.detail["rpc"] == "action"
    assert output.rpc_kind == "action"


# ======================================================================
# action_menu
# ======================================================================
@pytest.mark.asyncio
async def test_action_menu_hides_invalid_by_default(registry):
    """默认只列合法项，非法项不进编号白名单。"""
    node = registry["action_menu"]()
    output = await node.execute(
        node.input_model(detail=_detail_action(), request=_request("action"))
    )
    assert output.kind == "action"
    assert output.valid_indexes == [0, 1, 3]
    assert output.shown == 3
    assert output.total == 4
    assert "[2]" not in output.text
    assert "琉璃百华" in output.text
    assert "费用 3" in output.text
    assert "预示" in output.text
    assert output.empty is False


@pytest.mark.asyncio
async def test_action_menu_can_include_invalid(registry):
    """打开 include_invalid 后应列出非法项及其原因，但不放进白名单。"""
    node = registry["action_menu"]()
    output = await node.execute(
        node.input_model(detail=_detail_action(), request=_request("action"), include_invalid=True)
    )
    assert "[2]" in output.text
    assert "充能不足" in output.text
    assert 2 not in output.valid_indexes
    assert output.shown == 4


@pytest.mark.asyncio
async def test_action_menu_choose_active(registry):
    """选人菜单的编号等于候选下标，并带上血量。"""
    node = registry["action_menu"]()
    detail = {
        "rpc": "chooseActive",
        "candidates": [
            {"id": 101, "name": "宵宫", "health": 8, "maxHealth": 10},
            {"id": 102, "name": "行秋", "health": 3, "maxHealth": 10},
        ],
    }
    output = await node.execute(node.input_model(detail=detail))
    assert output.kind == "chooseActive"
    assert output.valid_indexes == [0, 1]
    assert "行秋" in output.text and "3/10" in output.text
    assert "只回答一个编号" in output.hint


@pytest.mark.asyncio
async def test_action_menu_empty_when_nothing_usable(registry):
    """没有合法候选时应标记 empty，供下游走兜底。"""
    node = registry["action_menu"]()
    detail = {"rpc": "action", "actions": [{"index": 0, "kind": "useSkill", "validity": 3}]}
    output = await node.execute(node.input_model(detail=detail))
    assert output.empty is True
    assert output.valid_indexes == []


# ======================================================================
# state_brief
# ======================================================================
@pytest.mark.asyncio
async def test_state_brief_renders_both_sides(registry):
    """格局文本应含双方角色/血量/骰子，且不泄露对方手牌内容。"""
    node = registry["state_brief"]()
    output = await node.execute(
        node.input_model(state=_state_sample(), seat=0, catalog=_name_table())
    )
    assert "回合 3" in output.text
    assert "我方（座位 0） 出战：宵宫" in output.text
    assert "对方（座位 1） 出战：莫娜" in output.text
    assert "宵宫 8/10 能量1/3" in output.text
    # aura/dice 编号来自 enums.ts：1冰 2水 3火 4雷 5风 6岩 7草（这里 aura=4、骰面 4 → 雷）
    assert "★出战" in output.text and "附着雷" in output.text and "祭礼弓" in output.text
    assert "行秋 0/10" in output.text and "✝阵亡" in output.text and "护盾2" in output.text
    assert "手牌 1 张：交给你了" in output.text
    assert "骰子 3 个：雷、万能、冰" in output.text
    assert "支援：璃月港口" in output.text
    assert "牌堆 27 张" in output.text
    assert "手握" not in output.text
    assert "手牌 5 张（内容不可见）" in output.text
    assert "骰子 4 个（骰面不可见）" in output.text
    assert "牌#0" not in output.text
    assert output.truncated is False


@pytest.mark.asyncio
async def test_state_brief_real_fixture(registry):
    """用真实录制的一回合做回归：名字能翻译，对方手牌不泄露。"""
    payload = json.loads((FIXTURES / "arena_turn_action.json").read_text(encoding="utf-8"))["payload"]
    state = payload["state"]
    ids = [char["definitionId"] for char in state["player"][1]["character"]]
    catalog = {"characters": dict(zip(ids, ["铁甲熔火帝皇", "托马", "赛诺"]))}

    node = registry["state_brief"]()
    output = await node.execute(node.input_model(state=state, seat=1, catalog=catalog, max_chars=1200))

    assert "我方（座位 1） 出战：铁甲熔火帝皇" in output.text
    assert "托马" in output.text and "赛诺" in output.text
    assert "对方（座位 0）" in output.text
    assert "手牌（内容不可见）" in output.text or "张（内容不可见）" in output.text
    assert output.chars <= 1200


@pytest.mark.asyncio
async def test_state_brief_hides_raw_unless_asked(registry):
    """原始 state 片段默认不回传（否则每次运行多背十几 KB），需要调试时才打开。"""
    node = registry["state_brief"]()
    quiet = await node.execute(node.input_model(state=_state_sample(), seat=0))
    assert quiet.me == {} and quiet.opponent == {}

    loud = await node.execute(node.input_model(state=_state_sample(), seat=0, include_raw=True))
    assert loud.me.get("activeCharacterId") == -101
    assert loud.opponent.get("activeCharacterId") == -501


@pytest.mark.asyncio
async def test_state_brief_truncates(registry):
    """超出 max_chars 时应截断并标记。"""
    node = registry["state_brief"]()
    output = await node.execute(node.input_model(state=_state_sample(), max_chars=100))
    assert output.truncated is True
    assert output.chars <= 120
    assert output.text.endswith("…（已截断）")


# ======================================================================
# prompt_pack
# ======================================================================
@pytest.mark.asyncio
async def test_prompt_pack_orders_sections(registry):
    """各段应按固定顺序拼进正文，system 单独成消息。"""
    node = registry["prompt_pack"]()
    output = await node.execute(
        node.input_model(
            system="你是七圣召唤选手。",
            memory="上一手：用了技能",
            state_text="我方：宵宫 8/10",
            menu_text="[0] 琉璃百华",
            task="只回答编号。",
            extra="备注：别抖机灵",
        )
    )
    assert [m.role for m in output.messages] == ["system", "user"]
    body = output.messages[-1].content
    assert body.index("之前发生的事") < body.index("当前局面") < body.index("可选动作") < body.index("你的任务")
    assert output.stats["dropped"] == []


@pytest.mark.asyncio
async def test_prompt_pack_drops_low_priority_first(registry):
    """超预算时先丢 extra，再丢 memory，候选菜单必须保留。"""
    node = registry["prompt_pack"]()
    output = await node.execute(
        node.input_model(
            memory="记" * 400,
            state_text="局" * 400,
            menu_text="[0] 技能",
            task="只回答编号。",
            extra="附" * 400,
            budget_chars=600,
        )
    )
    assert "附" * 10 not in output.messages[-1].content
    assert "[0] 技能" in output.messages[-1].content
    assert "extra" in output.stats["dropped"]
    assert "menu_text" not in output.stats["dropped"]


@pytest.mark.asyncio
async def test_prompt_pack_skips_empty_system(registry):
    """没有系统提示词时不应产生空 system 消息。"""
    node = registry["prompt_pack"]()
    output = await node.execute(node.input_model(system="   ", menu_text="[0] 略"))
    assert [m.role for m in output.messages] == ["user"]


# ======================================================================
# choice_parse
# ======================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3", 3),
        ("我选 1。", 1),
        ("[0]", 0),
        ('{"index": 2}', 2),
        ('好的：{"action": "1"}', 1),
        ("  2  ", 2),
    ],
)
async def test_choice_parse_accepts_common_shapes(registry, text, expected):
    """常见回答形态都应解析成同一个编号。"""
    node = registry["choice_parse"]()
    output = await node.execute(node.input_model(text=text, valid_indexes=[0, 1, 2, 3]))
    assert output.ok is True
    assert output.index == expected


@pytest.mark.asyncio
async def test_choice_parse_rejects_out_of_range(registry):
    """编号不在白名单内时必须报失败，并说清可选范围。"""
    node = registry["choice_parse"]()
    output = await node.execute(node.input_model(text="我要选 9 号", valid_indexes=[0, 1]))
    assert output.ok is False
    assert output.index is None
    assert "0" in output.reason and "1" in output.reason


@pytest.mark.asyncio
async def test_choice_parse_without_number(registry):
    """完全没有数字时应给出"只回复数字"的反馈。"""
    node = registry["choice_parse"]()
    output = await node.execute(node.input_model(text="我觉得应该进攻。", valid_indexes=[0]))
    assert output.ok is False
    assert "数字" in output.reason


@pytest.mark.asyncio
async def test_choice_parse_uses_default_index(registry):
    """给了 default_index 时失败结果应带上它，方便下游直接兜底。"""
    node = registry["choice_parse"]()
    output = await node.execute(
        node.input_model(text="随便", valid_indexes=[0, 7], default_index=7)
    )
    assert output.ok is False
    assert output.index == 7


# ======================================================================
# rpc_build
# ======================================================================
@pytest.mark.asyncio
async def test_rpc_build_action_uses_index_and_auto_dice(registry):
    """合法编号应原样采用，骰子用动作自带的 autoSelectedDice。"""
    node = registry["rpc_build"]()
    output = await node.execute(
        node.input_model(request=_request("action"), detail=_detail_action(), index=0)
    )
    assert output.ok is True
    assert output.used_fallback is False
    assert output.response == {
        "response": {"$case": "action", "value": {"chosenActionIndex": 0, "usedDice": [4, 8, 8]}}
    }


@pytest.mark.asyncio
async def test_rpc_build_action_falls_back_to_best_legal_action(registry):
    """编号非法时应兜底到"最有价值的合法动作"，而不是直接替 AI 结束回合。"""
    node = registry["rpc_build"]()
    output = await node.execute(
        node.input_model(request=_request("action"), detail=_detail_action(), index=2)
    )
    assert output.ok is False
    assert output.used_fallback is True
    # 0 号是带 2 点伤害的技能，价值高于"出牌"和"结束回合"
    assert output.response["response"]["value"]["chosenActionIndex"] == 0
    assert "结束回合" not in output.note


@pytest.mark.asyncio
async def test_rpc_build_action_ends_turn_only_when_nothing_else(registry):
    """只有"结束回合"合法时才允许结束回合（否则等于替 AI 弃骰）。"""
    node = registry["rpc_build"]()
    detail = {
        "rpc": "action",
        "actions": [
            {
                "index": 0,
                "kind": "useSkill",
                "name": "元素爆发",
                "validity": 4,
                "autoSelectedDice": [4, 4, 4],
            },
            {"index": 1, "kind": "declareEnd", "name": "结束回合", "validity": 0},
        ],
    }
    output = await node.execute(node.input_model(request=_request("action"), detail=detail, index=9))
    assert output.response["response"]["value"]["chosenActionIndex"] == 1
    assert "结束回合" in output.note


@pytest.mark.asyncio
async def test_rpc_build_action_respects_explicit_dice(registry):
    """显式给 used_dice 时以它为准（留给未来的"控骰"能力）。"""
    node = registry["rpc_build"]()
    output = await node.execute(
        node.input_model(request=_request("action"), detail=_detail_action(), index=1, used_dice=[7, 7])
    )
    assert output.response["response"]["value"]["usedDice"] == [7, 7]


@pytest.mark.asyncio
async def test_rpc_build_choose_active(registry):
    """选人响应应带上候选的 id。"""
    node = registry["rpc_build"]()
    detail = {"rpc": "chooseActive", "candidates": [{"id": 101}, {"id": 102}]}
    output = await node.execute(node.input_model(request=_request("chooseActive"), detail=detail, index=1))
    assert output.ok is True
    assert output.response["response"]["value"] == {"activeCharacterId": 102}


@pytest.mark.asyncio
async def test_rpc_build_select_card(registry):
    """选牌响应应带上 definitionId。"""
    node = registry["rpc_build"]()
    detail = {"rpc": "selectCard", "candidates": [{"definitionId": 330001}]}
    output = await node.execute(node.input_model(request=_request("selectCard"), detail=detail, index=0))
    assert output.response["response"]["value"] == {"selectedDefinitionId": 330001}


@pytest.mark.asyncio
async def test_rpc_build_reroll_and_switch_default_empty(registry):
    """重投/换牌在没有指令时给空集合（等于"不重投、不换牌"）。"""
    node = registry["rpc_build"]()
    reroll = await node.execute(node.input_model(request=_request("rerollDice"), detail={"rpc": "rerollDice"}))
    switch = await node.execute(node.input_model(request=_request("switchHands"), detail={"rpc": "switchHands"}))
    assert reroll.response["response"]["value"] == {"diceToReroll": []}
    assert switch.response["response"]["value"] == {"removedHandIds": []}


@pytest.mark.asyncio
async def test_rpc_build_unknown_kind_is_safe(registry):
    """未知请求类型应返回空响应并标记兜底，绝不猜一个形状出去。"""
    node = registry["rpc_build"]()
    output = await node.execute(node.input_model(request=_request("whatIsThis"), detail={}, index=0))
    assert output.ok is False
    assert output.response == {}


@pytest.mark.asyncio
async def test_rpc_build_rejects_index_without_fallback(registry):
    """关闭兜底时非法编号应直接返回空响应，交由司机处理。"""
    node = registry["rpc_build"]()
    output = await node.execute(
        node.input_model(request=_request("action"), detail=_detail_action(), index=2, allow_fallback=False)
    )
    assert output.ok is False
    assert output.response == {}


# ======================================================================
# 端到端：在真实引擎里串起来
# ======================================================================
@pytest.mark.asyncio
async def test_graph_chain_builds_action_response(engine, make_blueprint):
    """arena_input → action_menu / rpc_build 串起来后应产出完整响应。"""
    bp = make_blueprint(
        nodes=[
            {"id": "in", "type": "arena_input", "params": {}},
            {"id": "menu", "type": "action_menu", "params": {}},
            {"id": "build", "type": "rpc_build", "params": {"index": 1}},
        ],
        edges=[
            {"id": "e1", "source": "in", "source_handle": "detail", "target": "menu", "target_handle": "detail"},
            {"id": "e2", "source": "in", "source_handle": "request", "target": "menu", "target_handle": "request"},
            {"id": "e3", "source": "in", "source_handle": "request", "target": "build", "target_handle": "request"},
            {"id": "e4", "source": "in", "source_handle": "detail", "target": "build", "target_handle": "detail"},
        ],
    )
    report = await engine.run(
        bp,
        shared={"request": _request("action"), "detail": _detail_action(), "meta": {"rpcKind": "action"}},
    )
    assert report.ok, report.errors
    build_result = next(r for r in report.results if r.node_id == "build")
    assert build_result.output.response["response"]["$case"] == "action"
    assert build_result.output.response["response"]["value"]["chosenActionIndex"] == 1


@pytest.mark.asyncio
async def test_graph_chain_packs_prompt(engine, make_blueprint):
    """arena_input → state_brief → prompt_pack 应产出可直接喂给模型的 messages。"""
    bp = make_blueprint(
        nodes=[
            {"id": "in", "type": "arena_input", "params": {}},
            {"id": "brief", "type": "state_brief", "params": {}},
            {"id": "pack", "type": "prompt_pack", "params": {"task": "只回答编号。"}},
        ],
        edges=[
            {"id": "e1", "source": "in", "source_handle": "state", "target": "brief", "target_handle": "state"},
            {"id": "e2", "source": "brief", "source_handle": "text", "target": "pack", "target_handle": "state_text"},
        ],
    )
    report = await engine.run(
        bp, shared={"request": _request("action"), "detail": {}, "state": _state_sample()}
    )
    assert report.ok, report.errors
    pack_result = next(r for r in report.results if r.node_id == "pack")
    assert pack_result.output.messages[0].role == "user"
    assert "当前局面" in pack_result.output.messages[0].content


@pytest.mark.asyncio
async def test_action_menu_missing_detail_does_not_crash(registry):
    """detail 为空时应给空菜单而不是抛异常（让兜底逻辑接管）。"""
    node = registry["action_menu"]()
    output = await node.execute(node.input_model(detail={}, request=_request("action")))
    assert output.empty is True
    assert output.text


def test_arena_node_contracts_are_strict(registry):
    """每个新节点的输入契约都必须拒绝未知字段（params 走 extra=forbid）。"""
    for node_type in ("arena_input", "action_menu", "state_brief", "prompt_pack", "choice_parse", "choice_map", "rpc_build"):
        model = registry[node_type].input_model
        with pytest.raises(ValidationError):
            model(not_a_field=1)


# ======================================================================
# 重投骰子 / 换手牌：菜单 → 多选解析 → 位置映射 → 响应
# ======================================================================
_DICE_DETAIL = {"rpc": "rerollDice", "dice": [3, 1, 3, 8], "diceLabels": ["火", "冰", "火", "万能"]}


@pytest.mark.asyncio
async def test_game_log_renders_opponent_info_entries(registry):
    """对手的引擎事件（info）也要进"最近发生的事"，否则模型看不到对方出了什么。"""
    node = registry["game_log"]()
    log = [
        {"kind": "info", "seat": 0, "text": "打出「莲花酥」"},
        {"kind": "info", "seat": 1, "text": "使用技能「风鹰剑」"},
        {"kind": "system", "seat": None, "text": "第 3 回合开始"},
    ]
    out = await node.execute(node.input_model(log=log, seat=1))
    assert "对方 打出「莲花酥」" in out.text
    assert "我方 使用技能「风鹰剑」" in out.text
    assert "第 3 回合开始" not in out.text
    assert out.count == 2


def _economy_state(dice, *, declared_end=False, my_hp=4, opp_hp=9):
    """造一份最小局面：我方座位 1、双方各一个出战角色。"""
    return {
        "currentTurn": 1,
        "phase": 3,
        "roundNumber": 3,
        "player": [
            {
                "activeCharacterId": 201,
                "character": [
                    {"id": 201, "definitionId": 1404, "health": opp_hp, "maxHealth": 10, "defeated": False}
                ],
                "dice": [8] * 8,
                "declaredEnd": declared_end,
            },
            {
                "activeCharacterId": 101,
                "character": [
                    {"id": 101, "definitionId": 1101, "health": my_hp, "maxHealth": 5, "defeated": False}
                ],
                "dice": list(dice),
                "declaredEnd": False,
            },
        ],
    }


@pytest.mark.asyncio
async def test_action_rank_shows_dice_economy(registry):
    node = registry["action_rank"]()
    out = await node.execute(
        node.input_model(detail=_detail_action(), seat=1, state=_economy_state([3, 3, 8, 1, 7]))
    )
    assert "骰子 5 颗" in out.text
    assert "火×2" in out.text
    assert "我方 4/5" in out.text
    assert "对方出战 9/10" in out.text
    assert "这 5 颗骰子会作废" in out.text


@pytest.mark.asyncio
async def test_action_rank_urges_spending_when_opponent_ended(registry):
    node = registry["action_rank"]()
    out = await node.execute(
        node.input_model(
            detail=_detail_action(), seat=1, state=_economy_state([3, 3, 8], declared_end=True)
        )
    )
    assert "对方已宣布结束回合" in out.text
    assert "你可以把骰子用光再结束" in out.text
    assert "骰子会作废" not in out.text


@pytest.mark.asyncio
async def test_action_rank_reroll_lists_each_die(registry):
    """掷骰阶段要逐颗列出骰子，最后一项是"不重投"哨兵。"""
    node = registry["action_rank"]()
    output = await node.execute(node.input_model(detail=_DICE_DETAIL))
    assert output.kind == "rerollDice"
    assert output.indexes == [0, 1, 2, 3, 4]
    assert "[0]" in output.text and "[3]" in output.text and "[4]" in output.text
    assert "火" in output.text and "万能" in output.text
    assert "逗号" in output.task


@pytest.mark.asyncio
async def test_action_rank_switch_hands_lists_cards(registry):
    """换牌阶段要逐张列出手牌，最后一项是"不换"哨兵。"""
    node = registry["action_rank"]()
    detail = {
        "rpc": "switchHands",
        "hand": [{"id": 101, "name": "旧日鏖战"}, {"id": 102, "name": "匿叶龙"}],
    }
    output = await node.execute(node.input_model(detail=detail))
    assert output.kind == "switchHands"
    assert output.indexes == [0, 1, 2]
    assert "旧日鏖战" in output.text and "匿叶龙" in output.text


@pytest.mark.asyncio
async def test_choice_parse_collects_multiple_numbers(registry):
    """多选回复要解析出全部编号，并保持顺序去重。"""
    node = registry["choice_parse"]()
    output = await node.execute(
        node.input_model(text="重投 [0]、[3]", valid_indexes=[0, 1, 2, 3, 4], max_chars=500)
    )
    assert output.numbers == [0, 3]
    assert output.index == 0


@pytest.mark.asyncio
async def test_choice_parse_accepts_json_number_list(registry):
    """模型回 JSON 数组时也要能读出来。"""
    node = registry["choice_parse"]()
    output = await node.execute(
        node.input_model(text='{"numbers": [1, 2]}', valid_indexes=[0, 1, 2, 3])
    )
    assert output.numbers == [1, 2]


@pytest.mark.asyncio
async def test_choice_map_translates_positions_to_dice(registry):
    """位置要翻成骰面值（多重集，重复值合法），"不重投"哨兵被丢掉。"""
    node = registry["choice_map"]()
    output = await node.execute(node.input_model(numbers=[0, 2, 4], detail=_DICE_DETAIL))
    assert output.used_dice == [3, 3]
    assert output.dropped == [4]


@pytest.mark.asyncio
async def test_choice_map_translates_positions_to_hand_ids(registry):
    """换牌的位置要翻成手牌 id。"""
    node = registry["choice_map"]()
    detail = {
        "rpc": "switchHands",
        "hand": [{"id": 101, "name": "旧日鏖战"}, {"id": 102, "name": "匿叶龙"}],
    }
    output = await node.execute(node.input_model(numbers=[1, 2], detail=detail))
    assert output.used_dice == [102]
    assert output.dropped == [2]


@pytest.mark.asyncio
async def test_choice_map_leaves_single_choice_alone(registry):
    """单选请求不能被误伤成"指定骰子"。"""
    node = registry["choice_map"]()
    output = await node.execute(
        node.input_model(numbers=[0], detail={"rpc": "action", "actions": []})
    )
    assert output.used_dice is None


@pytest.mark.asyncio
async def test_graph_reroll_chain_builds_dice_to_reroll(engine, make_blueprint, tmp_path):
    """用"模型回复文件"替换 llm.reply，验证 菜单→多选解析→位置映射→响应 整链真的能重投。"""
    reply = tmp_path / "reply.txt"
    reply.write_text("重投 [0]、[2]，第 4 颗留着。", encoding="utf-8")
    bp = make_blueprint(
        nodes=[
            {"id": "in", "type": "arena_input", "params": {}},
            {"id": "rank", "type": "action_rank", "params": {}},
            {"id": "reply", "type": "read_file", "params": {"path": str(reply), "encoding": "utf-8"}},
            {"id": "parse", "type": "choice_parse", "params": {"mode": "index", "max_chars": 500}},
            {"id": "map", "type": "choice_map", "params": {}},
            {"id": "build", "type": "rpc_build", "params": {}},
        ],
        edges=[
            {"id": "e1", "source": "in", "source_handle": "detail", "target": "rank", "target_handle": "detail"},
            {"id": "e2", "source": "reply", "source_handle": "content", "target": "parse", "target_handle": "text"},
            {"id": "e3", "source": "rank", "source_handle": "indexes", "target": "parse", "target_handle": "valid_indexes"},
            {"id": "e4", "source": "parse", "source_handle": "numbers", "target": "map", "target_handle": "numbers"},
            {"id": "e5", "source": "in", "source_handle": "detail", "target": "map", "target_handle": "detail"},
            {"id": "e6", "source": "in", "source_handle": "request", "target": "map", "target_handle": "request"},
            {"id": "e7", "source": "map", "source_handle": "used_dice", "target": "build", "target_handle": "used_dice"},
            {"id": "e8", "source": "in", "source_handle": "request", "target": "build", "target_handle": "request"},
            {"id": "e9", "source": "in", "source_handle": "detail", "target": "build", "target_handle": "detail"},
            {"id": "e10", "source": "parse", "source_handle": "index", "target": "build", "target_handle": "index"},
        ],
    )
    report = await engine.run(
        bp,
        shared={"request": _request("rerollDice"), "detail": _DICE_DETAIL, "meta": {"rpcKind": "rerollDice"}},
    )
    assert report.ok, report.errors
    build_result = next(r for r in report.results if r.node_id == "build")
    assert build_result.output.response["response"] == {
        "$case": "rerollDice",
        "value": {"diceToReroll": [3, 3]},
    }
