# -*- coding: utf-8 -*-
"""``winrate_table`` / ``review_parse`` 的单元测试。

缺陷背景:
    - ``review_parse`` 是"模型自由文本 → 结构化意图 + 胜率"的唯一关卡。
      模型经常写成 ``胜率：60%``、``胜算: 0.6``、``意图：…`` 夹在解释里，
      解析一松就会把"0.6 张牌"读成胜率，把整局的历史估计污染掉；
      解析一严又会每回合都丢掉复盘，观测台上什么都看不到。
    - ``winrate_table`` 把这个历史重新喂回提示词。它必须容忍
      ``0.45`` / ``"45%"`` / ``45`` 三种写法，并且只保留最近几条，
      否则提示词会随回合数无限膨胀（第 15 回合多出上千字）。
"""

from __future__ import annotations

import pytest

from plugins.tools.review_parse import ReviewParseInput, ReviewParseNode
from plugins.tools.winrate_table import WinrateTableInput, WinrateTableNode

pytestmark = [pytest.mark.plugin, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# review_parse
# ---------------------------------------------------------------------------
async def test_review_parse_two_lines():
    """标准两行写法：意图 + 小数胜率。"""
    text = "意图：这回合先手铺场，留一张充能牌下回合开大。\n胜率：0.62"
    out = await ReviewParseNode().execute(ReviewParseInput(text=text), {"shared": {}})
    assert out.ok is True
    assert out.winrate == pytest.approx(0.62)
    assert "铺场" in out.intent
    assert "胜率" not in out.intent


async def test_review_parse_percent_and_labels():
    """``胜率：60%`` 这类百分比要读成 0.6，且标签可以带空格/全角冒号。"""
    text = "复盘\n意图  ：  对手双充能，我先手打消耗。\n胜率 ： 60%"
    out = await ReviewParseNode().execute(ReviewParseInput(text=text), {"shared": {}})
    assert out.winrate == pytest.approx(0.6)
    assert "消耗" in out.intent


async def test_review_parse_intent_without_label():
    """没有 ``意图：`` 标签时，把非胜率的行整段当意图（模型偶尔直接写正文）。"""
    text = "先稳住前排，攒够三充能再爆发。\n胜率: 0.5"
    out = await ReviewParseNode().execute(ReviewParseInput(text=text), {"shared": {}})
    assert out.ok is True
    assert out.winrate == pytest.approx(0.5)
    assert "稳住前排" in out.intent


async def test_review_parse_truncates_intent():
    """意图超过上限要截断（用户要求 ≤200 字）。"""
    text = "意图：" + "打" * 500 + "\n胜率：0.4"
    out = await ReviewParseNode().execute(
        ReviewParseInput(text=text, max_intent_chars=50), {"shared": {}}
    )
    assert out.ok is True
    assert len(out.intent) <= 51
    assert out.intent.endswith("…")


async def test_review_parse_missing_winrate_is_not_ok():
    """只有意图、没给胜率：意图照用，但 ok=False 并给出说明。"""
    out = await ReviewParseNode().execute(
        ReviewParseInput(text="意图：铺场抢节奏。"), {"shared": {}}
    )
    assert out.winrate is None
    assert out.ok is False
    assert "胜率" in out.note


async def test_review_parse_uses_default_winrate():
    """给了 default_winrate 就沿用上一次的估计，避免历史断档。"""
    out = await ReviewParseNode().execute(
        ReviewParseInput(text="意图：稳住。", default_winrate=0.55), {"shared": {}}
    )
    assert out.winrate == pytest.approx(0.55)


async def test_review_parse_strips_markdown():
    """模型爱加 ``**`` / 列表符号，解析后不该残留在意图里。"""
    text = "**意图**：- 先手压制\n- 留意对方的冰茧\n**胜率**：0.7"
    out = await ReviewParseNode().execute(ReviewParseInput(text=text), {"shared": {}})
    assert out.winrate == pytest.approx(0.7)
    assert "*" not in out.intent
    assert "冰茧" in out.intent


# ---------------------------------------------------------------------------
# winrate_table
# ---------------------------------------------------------------------------
def _hist(*pairs):
    return [{"round": r, "winrate": w, "intent": f"R{r} 的想法"} for r, w in pairs]


async def test_winrate_table_empty():
    """空历史要给出可读的提示，而不是空白。"""
    out = await WinrateTableNode().execute(WinrateTableInput(history=[]), {"shared": {}})
    assert out.count == 0
    assert "第一次" in out.text


async def test_winrate_table_renders_percent_and_trend():
    """百分比渲染 + 连续下降时给出趋势说明。"""
    out = await WinrateTableNode().execute(
        WinrateTableInput(history=_hist((1, 0.6), (2, 0.5), (3, 0.35))), {"shared": {}}
    )
    assert out.count == 3
    assert out.latest == pytest.approx(0.35)
    assert "60%" in out.text and "35%" in out.text
    assert "下降" in out.trend


async def test_winrate_table_accepts_string_inputs():
    """历史里的 ``"45%"`` / ``"0.45"`` / round 字符串都要能读。"""
    hist = [{"round": "1", "winrate": "45%"}, {"round": "2", "winrate": "0.5"}]
    out = await WinrateTableNode().execute(WinrateTableInput(history=hist), {"shared": {}})
    assert out.first == pytest.approx(0.45)
    assert out.latest == pytest.approx(0.5)


async def test_winrate_table_keeps_only_recent():
    """只保留最近 keep 条，避免提示词随回合数膨胀。"""
    out = await WinrateTableNode().execute(
        WinrateTableInput(history=_hist(*[(r, 0.5) for r in range(1, 21)]), keep=3),
        {"shared": {}},
    )
    assert out.count == 3
    assert "18" in out.text and "20" in out.text


async def test_winrate_table_hides_intent_when_asked():
    """``with_intent=False`` 时不带意图，只给数字。"""
    out = await WinrateTableNode().execute(
        WinrateTableInput(history=_hist((1, 0.5)), with_intent=False), {"shared": {}}
    )
    assert "想法" not in out.text


async def test_winrate_table_rejects_unknown_field():
    """契约：未知字段必须被拒（``extra="forbid"``）。"""
    with pytest.raises(Exception):
        WinrateTableInput(history=[], nope=1)
