# -*- coding: utf-8 -*-
"""
【对局入口】读取 shared 里的对局上下文（arena_input）。

七圣召唤的司机进程每回合会把一份 ``shared`` 传给 ``POST /run``，其中包含：

    - ``request``：本回合要回答的 protobuf RpcRequest（``{"request": {"$case": ..., "value": ...}}``）
    - ``detail``：游戏侧生成的结构化视图（中文名、费用、合法性、预示伤害）
    - ``state``：当前局面（己方完整、对方仅公开）
    - ``meta``：本题元数据（对局 id、座位、回合、deadline、剩余毫秒、第几次尝试）
    - ``retry``：上一次提交被驳回时的原因与可选项
    - ``ctx``：司机维护的跨回合摘要（历史决策、上一手）

本节点把这些散装数据一次性取出来并补上几个常用派生字段，让后面每个节点
都能各取所需，而不必人人关心 ``shared`` 的原始形状。

设计要点:
    - 只用 ``context["shared"]``（引擎注入的只读快照），不读环境变量、不落盘。
    - 缺 ``request`` 时直接报错：那说明司机没把 shared 传对，后面的决策全是猜的，
      越早失败越好。

契约:
    输入 :class:`InputSchema`（无参数）/ 输出 :class:`ArenaInputOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode, InputSchema

__all__ = ["ArenaInputOutput", "ArenaInputNode"]


class ArenaInputOutput(BaseModel):
    """对局上下文的强类型快照。

    Attributes:
        request (Dict[str, Any]): 本回合的 RpcRequest（原始形状）。
        detail (Dict[str, Any]): 游戏侧的结构化视图（aiDetail）。
        state (Dict[str, Any]): 当前局面。
        meta (Dict[str, Any]): 元数据（matchId / seat / requestId / rpcKind / round / deadline / remainingMs / attempt）。
        retry (Dict[str, Any]): 上一轮被驳回的反馈；为空字典表示首次尝试。
        ctx (Dict[str, Any]): 跨回合摘要（summary / stats / lastDecision）。
        rpc_kind (str): 本回合要回答的请求类型（action / chooseActive / selectCard / rerollDice / switchHands）。
        round (Optional[int]): 第几回合（可能未知）。
        deadline_ms (Optional[int]): 服务端给出的截止时间戳（毫秒）；未知为空。
        remaining_ms (Optional[int]): 距离截止还剩多少毫秒；未知为空。
        attempt (int): 第几次尝试（0 表示首次）。
        summary (str): 一行速览，便于写进提示词或日志。
        card_text (Dict[str, Any]): 卡牌/装备/支援/召唤物的效果原文（definitionId → 文本），司机按需裁剪后传入。
    """

    model_config = {"extra": "forbid"}

    request: Dict[str, Any] = Field(default_factory=dict, description="本回合的 RpcRequest")
    detail: Dict[str, Any] = Field(default_factory=dict, description="结构化视图 aiDetail")
    state: Dict[str, Any] = Field(default_factory=dict, description="当前局面")
    meta: Dict[str, Any] = Field(default_factory=dict, description="本题元数据")
    retry: Dict[str, Any] = Field(default_factory=dict, description="上一次被驳回的反馈")
    ctx: Dict[str, Any] = Field(default_factory=dict, description="跨回合摘要")
    rpc_kind: str = Field("", description="请求类型：action / chooseActive / selectCard / rerollDice / switchHands")
    round: Optional[int] = Field(None, description="第几回合")
    deadline_ms: Optional[int] = Field(None, description="截止时间戳（毫秒）")
    remaining_ms: Optional[int] = Field(None, description="剩余预算毫秒数")
    attempt: int = Field(0, description="第几次尝试，0 表示首次")
    summary: str = Field("", description="一行速览文本")
    seat: Optional[int] = Field(None, description="我方座位号（元数据里的 seat 优先，其次看请求事件）")
    catalog: Dict[str, Any] = Field(default_factory=dict, description="catalog：definitionId → 中文名，用于把场面翻译成人话")
    plan: str = Field("", description="赛前计划（驱动在开局前算好，每回合都带进来）")
    deck: Dict[str, Any] = Field(default_factory=dict, description="我方牌组（characters / cards），未提供时为空")
    winrates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="往期每回合末的胜率估计：[{round, winrate, intent}]，供 winrate_table 渲染、供模型参照",
    )
    intent: str = Field("", description="上一回合结束时留下的意图（≤200 字）")
    config: Dict[str, Any] = Field(default_factory=dict, description="对局配置（限时/耗时间/让过），由页面推给桥接")
    card_text: Dict[str, Any] = Field(
        default_factory=dict,
        description="卡牌/装备/支援/召唤物的效果文本：definitionId → 原文（司机从 /api/arena/card-text 裁剪而来）",
    )
    game_log: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="桥接对局日志（双方动作）：司机每手从 /api/arena/log 取最近的条目",
    )


def _rpc_kind(request: Dict[str, Any]) -> str:
    """从 RpcRequest 中取出请求类型。

    Args:
        request (Dict[str, Any]): RpcRequest，形如 ``{"request": {"$case": "action", "value": {...}}}``。

    Returns:
        str: 请求类型；无法识别时返回空字符串。
    """
    inner = request.get("request") if isinstance(request, dict) else None
    if isinstance(inner, dict):
        return str(inner.get("$case") or "")
    if isinstance(request, dict):
        return str(request.get("$case") or "")
    return ""


class ArenaInputNode(BaseNode):
    """把司机传入的 shared 摊平成一份可连线的输出。"""

    node_type = "arena_input"
    description = "对局入口：从 shared 读取请求、局面、元数据并补齐派生字段"
    input_model = InputSchema
    output_model = ArenaInputOutput

    async def execute(
        self,
        inputs: BaseModel,
        context: Optional[Dict[str, Any]] = None,
    ) -> BaseModel:
        """读取并整理 shared 中的对局上下文。

        Args:
            inputs (BaseModel): 无参数（保留形参以符合统一签名）。
            context (Optional[Dict[str, Any]]): 引擎注入的只读上下文，``context["shared"]`` 即司机传来的数据。

        Returns:
            ArenaInputOutput: 整理后的上下文。

        Raises:
            ValueError: shared 中缺少 ``request`` 且也拿不到 ``detail`` 时抛出，
                因为此时任何决策都只能是瞎猜。
        """
        shared: Dict[str, Any] = {}
        if isinstance(context, dict):
            candidate = context.get("shared")
            if isinstance(candidate, dict):
                shared = candidate

        request = shared.get("request") or {}
        detail = shared.get("detail") or shared.get("aiDetail") or {}
        state = shared.get("state") or {}
        meta = shared.get("meta") or {}
        retry = shared.get("retry") or {}
        ctx = shared.get("ctx") or {}
        catalog = shared.get("catalog") or {}
        card_text = shared.get("cardText") or shared.get("card_text") or {}
        game_log = shared.get("gameLog") or shared.get("game_log") or []
        deck = shared.get("deck") or {}
        plan = shared.get("plan") or (ctx.get("plan") if isinstance(ctx, dict) else "") or ""
        winrates = shared.get("winrates") or []
        intent = str(shared.get("intent") or "")
        config = shared.get("config") or (meta.get("config") if isinstance(meta, dict) else {}) or {}

        # 复盘（meta.kind == "review"）没有 request/detail，只带 state：
        # 只要有 state 就允许继续，让同一张图既能做"决策"也能做"回合复盘"。
        if not request and not detail and not state:
            raise ValueError(
                "shared 里既没有 request/detail 也没有 state：司机没有把对局上下文传进来，"
                "请检查 arena-driver 的 shared 字段名（request / detail / state / meta）"
            )

        kind = str(meta.get("rpcKind") or "") or _rpc_kind(request)
        round_no = meta.get("round")
        if round_no is None and isinstance(detail, dict):
            round_no = detail.get("round")
        deadline = meta.get("deadline")
        remaining = meta.get("remainingMs")
        attempt = int(meta.get("attempt") or 0)
        seat = meta.get("seat")
        if seat is None and isinstance(detail, dict):
            seat = detail.get("seat")

        bits = [f"请求={kind or '未知'}"]
        if round_no is not None:
            bits.append(f"回合={round_no}")
        if remaining is not None:
            bits.append(f"剩余={int(remaining)}ms")
        if attempt:
            bits.append(f"第{attempt + 1}次尝试")
        if retry:
            bits.append("上一轮被驳回")
        summary = "，".join(bits)

        return ArenaInputOutput(
            request=dict(request) if isinstance(request, dict) else {},
            detail=dict(detail) if isinstance(detail, dict) else {},
            state=dict(state) if isinstance(state, dict) else {},
            meta=dict(meta) if isinstance(meta, dict) else {},
            retry=dict(retry) if isinstance(retry, dict) else {},
            ctx=dict(ctx) if isinstance(ctx, dict) else {},
            rpc_kind=kind,
            round=int(round_no) if isinstance(round_no, (int, float)) else None,
            deadline_ms=int(deadline) if isinstance(deadline, (int, float)) else None,
            remaining_ms=int(remaining) if isinstance(remaining, (int, float)) else None,
            attempt=attempt,
            summary=summary,
            seat=int(seat) if isinstance(seat, (int, float)) else None,
            catalog=dict(catalog) if isinstance(catalog, dict) else {},
            plan=str(plan or ""),
            deck=dict(deck) if isinstance(deck, dict) else {},
            winrates=[dict(item) for item in winrates if isinstance(item, dict)][:40]
            if isinstance(winrates, list)
            else [],
            intent=intent,
            config=dict(config) if isinstance(config, dict) else {},
            card_text=dict(card_text) if isinstance(card_text, dict) else {},
            game_log=[dict(item) for item in game_log if isinstance(item, dict)][-40:]
            if isinstance(game_log, list)
            else [],
        )
