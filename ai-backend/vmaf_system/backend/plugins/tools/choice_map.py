"""choice_map：把"菜单位置编号"翻译成引擎真正要的东西（骰面值 / 手牌 id）。

为什么需要它：``choice_parse`` 只能给编号（模型看到的是菜单里的 ``[0]``、``[1]``），
而 ``rerollDice`` 要的是**骰面值的多重集**（引擎按值逐个 ``indexOf`` 移除再随机补等量），
``switchHands`` 要的是**手牌 id 列表**。中间这一段"位置 → 真实值"必须有个地方做，
而且必须是纯映射、不含任何策略，这样连线才看得懂、也好单测。

约定（与 ``action_rank`` 的多选菜单严格对应）：

- ``rerollDice``：编号是 ``aiDetail.dice`` 的下标；``len(dice)`` 这一项表示"不重投"；越界编号直接忽略。
- ``switchHands``：编号是 ``aiDetail.hand`` 的下标；``len(hand)`` 这一项表示"不换牌"；越界编号直接忽略。
- 其他请求类型：不产生 ``used_dice``（返回 None），让 ``rpc_build`` 用自己的兜底逻辑，
  免得把单选请求误伤成"指定骰子"。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import BaseNode

__all__ = ["ChoiceMapInput", "ChoiceMapOutput", "ChoiceMapNode"]


def _rpc_kind(request: Dict[str, Any], detail: Dict[str, Any]) -> str:
    """推断请求类型（顺序与 ``action_rank`` / ``rpc_build`` 保持一致）。"""
    if isinstance(detail, dict):
        kind = str(detail.get("rpc") or "")
        if kind:
            return kind
    inner = request.get("request") if isinstance(request, dict) else None
    if isinstance(inner, dict) and inner.get("$case"):
        return str(inner["$case"])
    if isinstance(request, dict) and request.get("$case"):
        return str(request["$case"])
    return ""


def _int_list(value: Any) -> List[int]:
    """把任意值收敛成整数列表（跳过 bool 与非数字）。"""
    if not isinstance(value, list):
        return []
    return [int(v) for v in value if isinstance(v, (int, float)) and not isinstance(v, bool)]


class ChoiceMapInput(BaseModel):
    """位置映射的输入契约。

    Attributes:
        numbers (List[int]): 模型选中的菜单位置，连线接 ``choice_parse.numbers``。
        detail (Dict[str, Any]): 结构化视图，连线接 ``arena_input.detail``。
        request (Dict[str, Any]): 原始请求，连线接 ``arena_input.request``（判断请求类型用）。
        max_picks (int): 一次最多接受几个位置，防止模型把整张菜单抄回来。
    """

    model_config = ConfigDict(extra="forbid")

    numbers: List[int] = Field(default_factory=list, description="菜单位置编号")
    detail: Dict[str, Any] = Field(default_factory=dict, description="结构化视图 aiDetail")
    request: Dict[str, Any] = Field(default_factory=dict, description="原始 RpcRequest")
    max_picks: int = Field(8, ge=1, le=16, description="一次最多接受几个位置")


class ChoiceMapOutput(BaseModel):
    """位置映射的输出契约。

    Attributes:
        used_dice (Optional[List[int]]): 重投的骰面值 / 换掉的手牌 id；非多选请求为 None。
        kind (str): 请求类型。
        count (int): 实际翻译出几个值。
        note (str): 中文说明，写进日志便于复盘。
        dropped (List[int]): 被忽略的编号（越界或超出上限）。
    """

    model_config = ConfigDict(extra="forbid")

    used_dice: Optional[List[int]] = Field(
        None, description="重投的骰面值 / 换掉的手牌 id；非多选请求为 None"
    )
    kind: str = Field("", description="请求类型")
    count: int = Field(0, description="实际翻译出几个值")
    note: str = Field("", description="中文说明")
    dropped: List[int] = Field(default_factory=list, description="被忽略的编号")


class ChoiceMapNode(BaseNode):
    """把菜单位置翻译成骰面值 / 手牌 id（纯映射，不含策略）。"""

    node_type = "choice_map"
    description = "把重投/换牌的菜单位置编号翻译成骰面值或手牌 id，接 rpc_build.used_dice"
    version = "0.1.0"
    input_model = ChoiceMapInput
    output_model = ChoiceMapOutput
    control_flow = None

    async def execute(
        self,
        inputs: ChoiceMapInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> ChoiceMapOutput:
        """按请求类型翻译位置编号。

        Args:
            inputs (ChoiceMapInput): 位置编号、结构化视图与原始请求。
            context (Optional[Dict[str, Any]]): 未使用。

        Returns:
            ChoiceMapOutput: 翻译结果；非多选请求的 ``used_dice`` 为 None。
        """
        detail = inputs.detail if isinstance(inputs.detail, dict) else {}
        request = inputs.request if isinstance(inputs.request, dict) else {}
        kind = _rpc_kind(request, detail)

        if kind == "rerollDice":
            dice = _int_list(detail.get("dice"))
            values, dropped = self._pick(inputs.numbers, len(dice), lambda pos: dice[pos], inputs.max_picks)
            return ChoiceMapOutput(
                used_dice=values,
                kind=kind,
                count=len(values),
                note=("不重投" if not values else f"重投 {len(values)} 枚骰子：{values}"),
                dropped=dropped,
            )

        if kind == "switchHands":
            hand = [c for c in (detail.get("hand") or []) if isinstance(c, dict)]
            values, dropped = self._pick(
                inputs.numbers,
                len(hand),
                lambda pos: hand[pos].get("id"),
                inputs.max_picks,
            )
            ids = [int(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
            return ChoiceMapOutput(
                used_dice=ids,
                kind=kind,
                count=len(ids),
                note=("不换牌" if not ids else f"换掉 {len(ids)} 张手牌：{ids}"),
                dropped=dropped,
            )

        return ChoiceMapOutput(
            used_dice=None,
            kind=kind,
            count=0,
            note=f"{kind or '空'} 不是多选请求，未指定 used_dice",
            dropped=[],
        )

    @staticmethod
    def _pick(numbers: List[int], size: int, getter, limit: int) -> tuple:
        """按位置取值：跳过"不操作"哨兵（等于 size）、越界与重复位置。

        Args:
            numbers (List[int]): 模型给的位置编号。
            size (int): 候选数量（骰子颗数 / 手牌张数）。
            getter (Callable[[int], Any]): 位置 → 真实值。
            limit (int): 最多取几个。

        Returns:
            tuple: ``(取值列表, 被忽略的编号列表)``。
        """
        picked: List[Any] = []
        dropped: List[int] = []
        seen = set()
        for raw in numbers or []:
            try:
                pos = int(raw)
            except (TypeError, ValueError):
                continue
            if pos < 0 or pos >= size or pos in seen:
                dropped.append(pos)
                continue
            seen.add(pos)
            if len(picked) >= limit:
                dropped.append(pos)
                continue
            value = getter(pos)
            if value is None:
                dropped.append(pos)
                continue
            picked.append(value)
        return picked, dropped
