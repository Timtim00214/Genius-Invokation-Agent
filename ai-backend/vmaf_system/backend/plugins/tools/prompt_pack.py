# -*- coding: utf-8 -*-
"""
【提示词】把若干段文本装进预算内的一个提示词（prompt_pack）。

每个决策的上下文都是"局面 + 候选 + 历史摘要 + 任务说明"的拼接，
而不同对局阶段这几段的长度差得很远：回合初的菜单很长，残局的手牌很少；
上一手的复盘可能很长，但有时根本不需要。

本节点做三件事:
    1. **按固定顺序拼装**（历史 → 局面 → 候选 → 任务 → 附加），
       让模型每次都看到稳定的结构，不必猜"这段是什么"。
    2. **守住字符预算**：超预算时按"附加 → 历史 → 局面"的顺序丢弃，
       **候选菜单与任务说明永不丢弃**（它们是能不能答对的前提）。
    3. **输出统计**：每段原始长度、是否被丢/被截、总字符数，
       方便在 VMAF 界面上直接看出"提示词花在哪了"。

输出直接是可连 ``llm_chat.messages`` 的 ``List[Message]``，所以本节点在图上
通常只有一个下游：对话节点。

契约:
    输入 :class:`PromptPackInput` / 输出 :class:`PromptPackOutput`。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode, Message

__all__ = ["PromptPackInput", "PromptPackOutput", "PromptPackNode"]

#: 超预算时允许被丢弃的段落，按"先丢这个"的顺序排列。
_DROP_ORDER: tuple = (
    "extra",
    "memory",
    "recent",
    "deck_text",
    "history",
    "state_text",
    "plan",
    "advice",
    "task",
)

#: 段落标题（拼进提示词，帮助模型定位）。
_TITLES: Dict[str, str] = {
    "memory": "### 之前发生的事",
    "deck_text": "### 我的牌组",
    "plan": "### 赛前计划",
    "history": "### 往期判断（自己估过的胜率与意图）",
    "recent": "### 最近发生的事",
    "state_text": "### 当前局面",
    "advice": "### 打法要求",
    "menu_text": "### 可选动作",
    "task": "### 你的任务",
    "extra": "### 补充信息",
}

#: 绝不允许丢弃的段落（丢了模型就无法作答或违反使用者的打法）。
_PROTECTED: frozenset = frozenset({"menu_text", "advice", "plan"})


class PromptPackInput(BaseModel):
    """提示词拼装的输入契约。

    Attributes:
        system (str): 系统提示词（单独作为 system 消息，不占正文预算）。
        memory (str): 历史摘要（上一手、更早的决策），最容易被丢弃。
        state_text (str): 当前局面文本，通常连 ``state_brief.text``。
        advice (str): 打法要求（分类别的可编辑提示词），通常连 ``playbook.text``；
            **永不丢弃**，且紧挨着候选菜单出现。
        menu_text (str): 候选菜单文本，通常连 ``action_menu.text``（**永不丢弃**）。
        task (str): 任务说明与回答格式要求，通常由上游给出或手写。
        extra (str): 其它补充信息（如重试反馈），优先级最低。
        budget_chars (int): 正文（不含 system）允许的字符上限。
        role (Literal["user", "system"]): 正文放进哪种角色的消息。
        include_system (bool): 是否把 ``system`` 作为独立 system 消息输出。
    """

    model_config = {"extra": "forbid"}

    system: str = Field("", description="系统提示词（独立 system 消息，不占正文预算）")
    memory: str = Field("", description="历史摘要，超预算时最先被丢弃")
    deck_text: str = Field("", description="牌组清单，连线接 deck_brief.text")
    plan: str = Field("", description="赛前计划（每手都带，永不丢弃），连线接 plan_guard.plan")
    history: str = Field("", description="往期胜率与意图，连线接 winrate_table.text")
    recent: str = Field("", description="最近发生了什么（双方动作），连线接 game_log.text")
    state_text: str = Field("", description="当前局面文本，连线接 state_brief.text")
    advice: str = Field("", description="打法要求，连线接 playbook.text（永不丢弃）")
    menu_text: str = Field("", description="候选菜单文本，连线接 action_menu.text（永不丢弃）")
    task: str = Field("", description="任务说明与回答格式要求")
    extra: str = Field("", description="补充信息（如重试反馈），优先级最低")
    budget_chars: int = Field(6000, ge=200, le=100000, description="正文允许的最大字符数")
    role: Literal["user", "system"] = Field("user", description="正文放进哪种角色的消息")
    include_system: bool = Field(True, description="是否输出独立的 system 消息")


class PromptPackOutput(BaseModel):
    """提示词拼装的输出契约。

    Attributes:
        messages (List[Message]): 可直接连 ``llm_chat.messages`` 的消息列表。
        prompt (str): 正文（不含 system），便于调试或写入日志。
        stats (Dict[str, Any]): 统计信息：每段原始长度、丢弃/截断情况、总字符数。
    """

    model_config = {"extra": "forbid"}

    messages: List[Message] = Field(..., description="给对话节点的消息列表")
    prompt: str = Field("", description="正文文本（不含 system）")
    stats: Dict[str, Any] = Field(default_factory=dict, description="拼装统计")


class PromptPackNode(BaseNode):
    """按预算拼装提示词，输出消息列表。"""

    node_type = "prompt_pack"
    description = "把历史摘要/局面/候选菜单/任务说明拼成预算内的提示词，并给出统计"
    input_model = PromptPackInput
    output_model = PromptPackOutput

    async def execute(
        self,
        inputs: PromptPackInput,
        context: Optional[Dict[str, Any]] = None,
    ) -> PromptPackOutput:
        """按预算拼装正文并包装成消息列表。

        Args:
            inputs (PromptPackInput): 各段文本与预算。
            context (Optional[Dict[str, Any]]): 未使用。

        Returns:
            PromptPackOutput: 消息列表、正文与统计。
        """
        sections: List[tuple] = [
            ("memory", inputs.memory),
            ("deck_text", inputs.deck_text),
            ("plan", inputs.plan),
            ("history", inputs.history),
            ("recent", inputs.recent),
            ("state_text", inputs.state_text),
            ("advice", inputs.advice),
            ("menu_text", inputs.menu_text),
            ("task", inputs.task),
            ("extra", inputs.extra),
        ]
        originals = {key: len(text or "") for key, text in sections}
        texts = {key: (text or "").strip() for key, text in sections}
        dropped: List[str] = []
        truncated: List[str] = []

        def total() -> int:
            """当前正文总长度（含标题与换行）。"""
            return sum(len(_TITLES[k]) + 1 + len(texts[k]) + 2 for k in texts if texts[k])

        # 第 1 轮：整段丢弃（标题一起丢），候选菜单受保护。
        for key in _DROP_ORDER:
            if total() <= inputs.budget_chars:
                break
            if key in _PROTECTED or not texts[key]:
                continue
            texts[key] = ""
            dropped.append(key)

        # 第 2 轮：仍超预算就截断剩下的可截段落（任务说明最多牺牲一半）。
        for key in _DROP_ORDER:
            if total() <= inputs.budget_chars:
                break
            if key in _PROTECTED or not texts[key]:
                continue
            overflow = total() - inputs.budget_chars
            if key == "task":
                floor = max(20, len(texts[key]) // 2)
                new_len = max(floor, len(texts[key]) - overflow)
            else:
                new_len = max(0, len(texts[key]) - overflow)
            if new_len < len(texts[key]):
                texts[key] = texts[key][:new_len] + "…（已截断）"
                truncated.append(key)

        blocks: List[str] = []
        for key, _ in sections:
            if texts[key]:
                blocks.append(f"{_TITLES[key]}\n{texts[key]}")
        prompt = "\n\n".join(blocks)

        messages: List[Message] = []
        if inputs.include_system and inputs.system.strip():
            messages.append(Message(role="system", content=inputs.system.strip()))
        messages.append(Message(role=inputs.role, content=prompt))

        stats: Dict[str, Any] = {
            "budget": inputs.budget_chars,
            "chars": len(prompt),
            "sections": originals,
            "dropped": dropped,
            "truncated": truncated,
            "kept": [k for k in originals if texts.get(k)],
        }
        return PromptPackOutput(messages=messages, prompt=prompt, stats=stats)
