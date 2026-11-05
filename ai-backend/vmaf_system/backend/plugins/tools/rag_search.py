# -*- coding: utf-8 -*-
"""
【增强】本地检索（RAG Search）。

把一批文档按"与查询的相关度"排序，产出 Top-K 片段与拼好的上下文，
供下游 ``llm_chat`` 作为 system_prompt（或消息内容）使用，形成
"先检索、再回答"的最小 RAG 链路。

设计红线:
    - **零外部依赖、无网络**：不引入向量库/嵌入模型，纯本地词法检索
      （BM25 简化实现），保证单机确定性与可复现；
    - **纯函数**：不缓存、不写盘，同一输入必然得到同一输出；
    - **确定性排序**：分数相同按 (文档序号, 片段序号) 升序打破并列，
      避免"同一问题两次检索结果顺序不同"；
    - **中文可用**：CJK 以**字符二元组**切词（无需分词词典），
      英文/数字按单词切分。

检索质量边界（务必知情）:
    词法检索擅长"关键词命中"，不擅长同义改写与语义近似。
    需要语义检索时应另接嵌入模型（未来的独立节点），不要把本节点
    当作向量检索使用。

契约:
    输入 :class:`RagSearchInput` / 输出 :class:`RagSearchOutput`。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..base import BaseNode

__all__ = ["RetrievedChunk", "RagSearchInput", "RagSearchOutput", "RagSearchNode"]

#: 英文/数字词元（小写后匹配）。
_WORD_PATTERN = re.compile(r"[a-z0-9_]+")

#: CJK 连续片段（用于生成字符二元组）。
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")

#: BM25 参数：词频饱和系数与长度归一化强度（业界常用默认值）。
_BM25_K1 = 1.5
_BM25_B = 0.75


def tokenize(text: str) -> List[str]:
    """把文本切为检索词元（英文按词、中文按字符二元组）。

    Args:
        text (str): 原始文本。

    Returns:
        List[str]: 词元列表；空白文本返回空列表。
    """
    if not text:
        return []

    lowered = text.lower()
    tokens: List[str] = _WORD_PATTERN.findall(lowered)

    for run in _CJK_PATTERN.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
            continue
        # 二元组：能覆盖"检索""索引"这类双字词，且无需分词词典。
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))

    return tokens


def split_chunks(
    documents: List[str], chunk_size: int, overlap: int
) -> List[Dict[str, Any]]:
    """把文档切分为带重叠的片段（先按段落，再按长度兜底切分）。

    Args:
        documents (List[str]): 文档正文列表。
        chunk_size (int): 单片段目标字符数（至少 20）。
        overlap (int): 相邻片段重叠字符数（0 <= overlap < chunk_size）。

    Returns:
        List[Dict[str, Any]]: 每项含 ``text`` / ``source_index`` / ``chunk_index``。
    """
    size = max(20, int(chunk_size))
    step = size - max(0, min(int(overlap), size - 1))
    chunks: List[Dict[str, Any]] = []

    for source_index, document in enumerate(documents):
        text = (document or "").strip()
        if not text:
            continue
        chunk_index = 0
        start = 0
        length = len(text)
        while start < length:
            piece = text[start : start + size].strip()
            if piece:
                chunks.append(
                    {
                        "text": piece,
                        "source_index": source_index,
                        "chunk_index": chunk_index,
                    }
                )
                chunk_index += 1
            if start + size >= length:
                break
            start += step

    return chunks


def bm25_scores(
    query_tokens: List[str], chunk_tokens: List[List[str]]
) -> List[float]:
    """计算 BM25 相关度分数。

    Args:
        query_tokens (List[str]): 查询词元。
        chunk_tokens (List[List[str]]): 每个片段的词元列表。

    Returns:
        List[float]: 与 ``chunk_tokens`` 等长的分数列表。
    """
    if not query_tokens or not chunk_tokens:
        return [0.0] * len(chunk_tokens)

    total = len(chunk_tokens)
    lengths = [len(tokens) for tokens in chunk_tokens]
    average = sum(lengths) / total if total else 0.0

    # 文档频率：每个词元出现在多少个片段中。
    doc_freq: Dict[str, int] = {}
    for tokens in chunk_tokens:
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1

    scores: List[float] = []
    for tokens, length in zip(chunk_tokens, lengths):
        counts: Dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1

        score = 0.0
        for token in set(query_tokens):
            frequency = counts.get(token, 0)
            if frequency == 0:
                continue
            df = doc_freq.get(token, 0)
            # 平滑 IDF：避免出现负分。
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            norm = 1 - _BM25_B + _BM25_B * (length / average if average else 1.0)
            score += idf * (frequency * (_BM25_K1 + 1)) / (
                frequency + _BM25_K1 * norm
            )
        scores.append(score)

    return scores


class RetrievedChunk(BaseModel):
    """一条命中片段。

    Attributes:
        text (str): 片段正文。
        source_index (int): 来自第几个文档（从 0 开始，对应 ``documents`` 下标）。
        chunk_index (int): 该片段在文档内的序号（从 0 开始）。
        score (float): 相关度分数（BM25，越大越相关）。
    """

    model_config = {"extra": "forbid"}

    text: str = Field(..., description="命中的片段正文")
    source_index: int = Field(..., ge=0, description="来源文档下标（从 0 开始）")
    chunk_index: int = Field(..., ge=0, description="片段在文档内的序号（从 0 开始）")
    score: float = Field(..., ge=0, description="相关度分数（BM25，越大越相关）")


class RagSearchInput(BaseModel):
    """检索节点的输入契约。

    Attributes:
        query (str): 检索问题/关键词。
        documents (List[str]): 待检索的文档正文列表（可来自连线）。
        top_k (int): 返回片段数。
        min_score (float): 分数下限，低于该值的片段被丢弃。
        chunk_size (int): 切分片段的字符数。
        overlap (int): 相邻片段的重叠字符数。
        context_header (str): 拼装 context 时的开头说明（可为空）。
    """

    model_config = {"extra": "forbid"}

    query: str = Field(
        "",
        description="检索问题或关键词；留空时按文档顺序返回前 top_k 个片段",
    )
    documents: List[str] = Field(
        default_factory=list,
        description="待检索的文档正文列表，每项是一篇文档的完整文本（可由上游连线注入）",
    )
    top_k: int = Field(
        3, ge=1, le=50, description="返回最相关的片段数量，默认 3"
    )
    min_score: float = Field(
        0.0,
        ge=0.0,
        description="相关度下限，低于该分数的片段会被过滤；0 表示不过滤",
    )
    chunk_size: int = Field(
        400, ge=20, le=4000, description="切分片段的目标字符数，默认 400"
    )
    overlap: int = Field(
        60,
        ge=0,
        le=2000,
        description="相邻片段的重叠字符数，避免答案恰好被切断；必须小于 chunk_size",
    )
    context_header: str = Field(
        "以下是检索到的资料片段，请依据这些内容回答用户问题：",
        description="拼装 context 时放在最前面的说明文字；留空则不加",
    )


class RagSearchOutput(BaseModel):
    """检索节点的输出契约。

    Attributes:
        results (List[RetrievedChunk]): 命中的片段（按相关度降序）。
        context (str): 把命中片段拼好的上下文文本，可直接接到对话节点。
        matched (int): 命中片段数量。
        corpus_chunks (int): 参与检索的片段总数。
        query_terms (List[str]): 实际使用的词元（便于排查"为什么没命中"）。
    """

    model_config = {"extra": "forbid"}

    results: List[RetrievedChunk] = Field(
        default_factory=list, description="命中的片段，按相关度降序"
    )
    context: str = Field(
        "",
        description="拼好的上下文文本，可直接连到 llm_chat 的 system_prompt",
    )
    matched: int = Field(0, ge=0, description="命中片段数量")
    corpus_chunks: int = Field(0, ge=0, description="参与检索的片段总数")
    query_terms: List[str] = Field(
        default_factory=list, description="本次实际使用的检索词元"
    )


class RagSearchNode(BaseNode):
    """本地检索节点：从文档中挑出与查询最相关的片段。

    node_type: ``rag_search``
    """

    node_type: str = "rag_search"
    description: str = (
        "【增强】本地检索：按关键词从文档中挑出最相关的片段并拼成上下文，"
        "接到对话节点的 system_prompt 即可实现先检索再回答"
    )
    version: str = "0.1.0"
    input_model: type = RagSearchInput
    output_model: type = RagSearchOutput

    async def execute(
        self, inputs: RagSearchInput, context: Optional[Dict[str, Any]] = None
    ) -> RagSearchOutput:
        """执行一次本地检索。

        Args:
            inputs (RagSearchInput): 已校验的输入。
            context (Optional[Dict[str, Any]]): 运行时上下文（本节点未使用）。

        Returns:
            RagSearchOutput: 命中片段、拼好的上下文与统计信息。
        """
        chunks = split_chunks(inputs.documents, inputs.chunk_size, inputs.overlap)
        query_tokens = tokenize(inputs.query)
        chunk_tokens = [tokenize(chunk["text"]) for chunk in chunks]
        scores = bm25_scores(query_tokens, chunk_tokens)

        ranked = sorted(
            range(len(chunks)),
            key=lambda i: (-scores[i], chunks[i]["source_index"], chunks[i]["chunk_index"]),
        )

        picked: List[RetrievedChunk] = []
        for index in ranked:
            if len(picked) >= inputs.top_k:
                break
            score = scores[index]
            if score < inputs.min_score:
                continue
            # 查询为空时按文档顺序取前 K 段（分数恒为 0，视为"全部可接受"）。
            if query_tokens and score <= 0.0:
                continue
            picked.append(
                RetrievedChunk(
                    text=chunks[index]["text"],
                    source_index=chunks[index]["source_index"],
                    chunk_index=chunks[index]["chunk_index"],
                    score=round(score, 6),
                )
            )

        context_text = self._build_context(inputs.context_header, picked)

        return RagSearchOutput(
            results=picked,
            context=context_text,
            matched=len(picked),
            corpus_chunks=len(chunks),
            query_terms=query_tokens,
        )

    @staticmethod
    def _build_context(header: str, picked: List[RetrievedChunk]) -> str:
        """把命中片段拼成一段可读文本。

        Args:
            header (str): 开头说明文字（可为空）。
            picked (List[RetrievedChunk]): 已排序的命中片段。

        Returns:
            str: 拼接结果；无命中时返回空字符串（下游可据此判断"没检索到"）。
        """
        if not picked:
            return ""

        blocks = [
            f"[片段{i + 1} · 文档{item.source_index} · 相关度 {item.score:.2f}]\n{item.text}"
            for i, item in enumerate(picked)
        ]
        body = "\n\n".join(blocks)
        return f"{header}\n\n{body}" if header.strip() else body
