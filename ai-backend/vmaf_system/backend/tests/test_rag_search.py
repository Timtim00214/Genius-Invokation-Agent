# -*- coding: utf-8 -*-
"""
本地检索节点（``tools/rag_search.py``）测试。

覆盖：中英文切词、分片与重叠、BM25 排序、top_k / 分数下限、
空查询与空文档的降级行为、context 拼装、确定性排序、
以及与 ``llm_chat`` 的契约对齐（context -> system_prompt 可直连）。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.plugin

DOCS = [
    "VMAF 是一个可视化智能体框架。它把工作流画成图，节点之间通过连线传递数据。",
    "循环节点需要成对使用：loop_start 与 loop_end 之间的节点会被重复执行。",
    "模型参数档案节点可以把 api_base、api_key 和模型名沉淀下来，连线到对话节点即可复用。",
]


@pytest.fixture(scope="module")
def rag_module(scanner):
    """取回扫描器已加载的插件模块（与生产运行的是同一份）。"""
    import sys

    return sys.modules["vmaf.plugins.tools.rag_search"]


async def _run(scanner, **params):
    """实例化并执行检索节点。"""
    node = scanner.instantiate("rag_search")
    model = scanner.get_node_class("rag_search").input_model
    return await node.execute(model(**params))


# ======================================================================
# 切词
# ======================================================================
def test_tokenize_english_lowercases_words(rag_module):
    """英文按单词切分并统一小写。"""
    mod = rag_module
    assert mod.tokenize("Loop_START 与 BM25") == ["loop_start", "bm25", "与"]


def test_tokenize_cjk_uses_bigrams(rag_module):
    """中文按字符二元组切分，无需分词词典。"""
    mod = rag_module
    tokens = mod.tokenize("可视化")
    assert "可视" in tokens and "视化" in tokens


def test_tokenize_empty(rag_module):
    mod = rag_module
    assert mod.tokenize("") == []


# ======================================================================
# 分片
# ======================================================================
def test_split_chunks_respects_size_and_overlap(rag_module):
    """分片长度不超过 chunk_size，且相邻片段有重叠。"""
    mod = rag_module
    text = "字" * 100
    chunks = mod.split_chunks([text], chunk_size=40, overlap=10)
    assert len(chunks) >= 3
    assert all(len(c["text"]) <= 40 for c in chunks)
    # 重叠：第二片起点 = 40 - 10 = 30
    assert chunks[0]["text"][-10:] == chunks[1]["text"][:10]


def test_split_chunks_skips_empty_documents(rag_module):
    mod = rag_module
    chunks = mod.split_chunks(["", "   ", "有内容"], chunk_size=100, overlap=0)
    assert len(chunks) == 1
    assert chunks[0]["source_index"] == 2


# ======================================================================
# 检索行为
# ======================================================================
@pytest.mark.asyncio
async def test_retrieves_most_relevant_document_first(scanner):
    """与查询最相关的文档应排在第一位。"""
    out = await _run(scanner, query="loop_start 循环", documents=DOCS, top_k=3)
    assert out.matched >= 1
    assert out.results[0].source_index == 1
    assert "loop_start" in out.results[0].text


@pytest.mark.asyncio
async def test_top_k_limits_results(scanner):
    out = await _run(scanner, query="节点", documents=DOCS, top_k=1)
    assert out.matched == 1


@pytest.mark.asyncio
async def test_zero_score_chunks_filtered(scanner):
    """完全不相关的片段不应返回（避免把噪音塞给模型）。"""
    out = await _run(scanner, query="量子纠缠", documents=DOCS, top_k=3)
    assert out.matched == 0
    assert out.context == ""


@pytest.mark.asyncio
async def test_min_score_filters_weak_hits(scanner):
    """抬高分数下限后只保留强命中。"""
    out = await _run(
        scanner, query="可视化智能体框架", documents=DOCS, top_k=3, min_score=5.0
    )
    assert all(item.score >= 5.0 for item in out.results)


@pytest.mark.asyncio
async def test_empty_query_returns_first_chunks_in_order(scanner):
    """查询为空时按文档顺序返回前 K 段（用于"把资料整段喂给模型"）。"""
    out = await _run(scanner, query="", documents=DOCS, top_k=2)
    assert out.matched == 2
    assert [r.source_index for r in out.results] == [0, 1]


@pytest.mark.asyncio
async def test_empty_documents(scanner):
    out = await _run(scanner, query="任意", documents=[])
    assert out.matched == 0
    assert out.corpus_chunks == 0
    assert out.context == ""


# ======================================================================
# 输出：context 与统计
# ======================================================================
@pytest.mark.asyncio
async def test_context_contains_header_and_top_chunk(scanner):
    """context 应包含说明文字与命中片段，可直接作为 system_prompt。"""
    out = await _run(
        scanner,
        query="模型参数档案 api_key",
        documents=DOCS,
        top_k=1,
        context_header="资料如下：",
    )
    assert out.context.startswith("资料如下：")
    assert "模型参数档案" in out.context
    assert "片段1" in out.context


@pytest.mark.asyncio
async def test_query_terms_reported(scanner):
    """输出实际词元，便于排查"为什么没命中"。"""
    out = await _run(scanner, query="Loop 循环", documents=DOCS)
    assert "loop" in out.query_terms
    assert "循环" in out.query_terms


@pytest.mark.asyncio
async def test_results_are_deterministic(scanner):
    """同一输入多次执行结果完全一致（排序确定性）。"""
    first = await _run(scanner, query="节点 连线", documents=DOCS, top_k=3)
    second = await _run(scanner, query="节点 连线", documents=DOCS, top_k=3)
    assert [r.model_dump() for r in first.results] == [
        r.model_dump() for r in second.results
    ]


# ======================================================================
# 与对话节点的契约对齐
# ======================================================================
def test_contract_aligns_with_chat_system_prompt(scanner, parser_module, blueprint_module):
    """context(str) 应能直连 llm_chat.system_prompt（Optional[str]）。"""
    registry = {t: r.node_class for t, r in scanner.get_registry().items()}
    parser = parser_module.BlueprintParser(registry)
    bp = blueprint_module.Blueprint(
        nodes=[
            blueprint_module.BlueprintNode(
                id="rag", type="rag_search", params={"query": "x", "documents": ["x"]}
            ),
            blueprint_module.BlueprintNode(
                id="chat",
                type="llm_chat",
                params={"messages": [{"role": "user", "content": "hi"}]},
            ),
        ],
        edges=[
            blueprint_module.BlueprintEdge(
                id="e1",
                source="rag",
                source_handle="context",
                target="chat",
                target_handle="system_prompt",
            )
        ],
    )
    plan = parser.parse(bp)
    assert plan.valid, [str(i) for i in plan.errors]
