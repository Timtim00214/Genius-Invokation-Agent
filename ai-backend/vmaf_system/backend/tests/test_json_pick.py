"""json_pick 的单元测试。

缺陷背景：引擎的类型预检不允许 ``Dict[str, Any]`` 直接连到 ``str``
（例如把 ``retry`` 反馈直接连进 ``prompt_pack.extra`` 会被判类型不兼容）。
``json_pick`` 是那个安全的"结构化 → 文本"降维点，所以它的取值规则必须明确：
点号路径、列表下标、取不到不报错、dict/list 序列化后可截断。
"""

import json

import pytest
from pydantic import ValidationError

from plugins.tools.json_pick import JsonPickInput, JsonPickNode, JsonPickOutput

pytestmark = pytest.mark.plugin


def run(inputs: JsonPickInput) -> JsonPickOutput:
    """同步跑一次节点（execute 是 async，但这里没有任何 IO）。"""
    import asyncio

    return asyncio.run(JsonPickNode().execute(inputs))


# ----------------------------------------------------------------------
# 取值
# ----------------------------------------------------------------------
def test_picks_nested_field():
    """点号路径应逐层下钻。"""
    out = run(JsonPickInput(data={"retry": {"reason": "充能不足"}}, key="retry.reason"))

    assert out.found is True
    assert out.text == "充能不足"
    assert out.type == "str"


def test_picks_list_index():
    """纯数字段当作列表下标。"""
    data = {"candidates": [{"name": "行秋"}, {"name": "宵宫"}]}

    out = run(JsonPickInput(data=data, key="candidates.1.name"))

    assert out.text == "宵宫"
    assert out.found is True


def test_picks_top_level_scalar():
    """单段路径也能取到标量。"""
    out = run(JsonPickInput(data={"seat": 1}, key="seat"))

    assert out.text == "1"
    assert out.type == "number"


# ----------------------------------------------------------------------
# 取不到
# ----------------------------------------------------------------------
def test_missing_path_returns_default_without_error():
    """取不到不该抛异常，否则整张蓝图会因为"少一段提示词"而中止。"""
    out = run(JsonPickInput(data={"a": 1}, key="b.c", default="（无反馈）"))

    assert out.found is False
    assert out.text == "（无反馈）"
    assert out.type == "absent"


def test_index_out_of_range_is_absent():
    """下标越界同样按"取不到"处理。"""
    out = run(JsonPickInput(data={"xs": [1, 2]}, key="xs.9"))

    assert out.found is False
    assert out.text == ""


def test_scalar_cannot_be_descended():
    """对着标量继续下钻按取不到处理。"""
    out = run(JsonPickInput(data={"a": "文本"}, key="a.b"))

    assert out.found is False


# ----------------------------------------------------------------------
# 序列化与截断
# ----------------------------------------------------------------------
def test_dict_value_is_serialized_as_json():
    """取到 dict 时输出 JSON 文本，便于直接塞进提示词。"""
    out = run(JsonPickInput(data={"cost": {"total": 3, "dice": ["火"]}}, key="cost"))

    assert out.type == "dict"
    assert json.loads(out.text) == {"total": 3, "dice": ["火"]}


def test_pretty_prints_when_asked():
    """pretty=True 时缩进两格。"""
    out = run(JsonPickInput(data={"cost": {"total": 3}}, key="cost", pretty=True))

    assert out.text == '{\n  "total": 3\n}'


def test_truncates_long_text():
    """超长文本按 max_chars 截断并加省略号。"""
    out = run(JsonPickInput(data={"note": "甲" * 100}, key="note", max_chars=20))

    assert len(out.text) <= 20 + len("…（已截断）")
    assert out.text.endswith("…（已截断）")


def test_empty_string_value_stays_empty():
    """空字符串是合法值，不该被当成"取不到"。"""
    out = run(JsonPickInput(data={"reason": ""}, key="reason", default="兜底"))

    assert out.found is True
    assert out.text == ""


# ----------------------------------------------------------------------
# 契约
# ----------------------------------------------------------------------
def test_empty_key_rejected():
    """key 为空没有意义，交给 Pydantic 拦。"""
    with pytest.raises(ValidationError):
        JsonPickInput(data={}, key="")


def test_unknown_field_rejected():
    """params 一律 extra="forbid"。"""
    with pytest.raises(ValidationError):
        JsonPickInput(data={}, key="a", nope=1)
