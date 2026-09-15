"""S3 候选机会点生成 —— docs/04-core-methodology.md §3。

方法:穷举 → 过滤 → 排序。
MVP 的语义匹配用「能力目录关键词 × 节点文本」的确定性打分代替向量检索,
因为 docs/07 §4 明确「条目超过 500 条再引入 Embedding」。该函数是后续替换点。
"""

from __future__ import annotations

from ..knowledge import CapabilityClass, KnowledgeBase, Scenario
from ..models import BusinessProfile, IntegrationMode, Opportunity, ProcessNode


def select_integration_mode(node: ProcessNode, profile: BusinessProfile) -> IntegrationMode:
    """按节点的系统依赖决定集成方式。

    - 不依赖系统 → 独立部署
    - 全部系统有 API 且数据可导出 → API 集成
    - 系统无 API 但数据可导出 → RPA
    - 数据不可导出 → 嵌入式改造
    """
    if not node.systems:
        return IntegrationMode.STANDALONE
    system_map = {s.name: s for s in profile.systems}
    systems = [system_map[name] for name in node.systems]
    if all(s.has_api and s.data_exportable for s in systems):
        return IntegrationMode.API
    if all(s.data_exportable for s in systems):
        return IntegrationMode.RPA
    return IntegrationMode.EMBEDDED


#: 各字段在关键词匹配中的权重。名称权重最高,触发条件最低
#: (触发条件里常出现「上架」这类跨领域词,是误匹配的主要来源)。
FIELD_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("name", 2.0),
    ("domain", 1.0),
    ("pain_point", 1.0),
    ("outputs", 1.0),
    ("inputs", 0.5),
    ("trigger", 0.5),
)

#: 低于该分数视为「不适用」,不作为候选机会点
MIN_RELEVANCE = 1.0


def _node_fields(node: ProcessNode) -> dict[str, str]:
    return {
        "name": node.name,
        "domain": node.domain,
        "pain_point": node.pain_point,
        "outputs": " ".join(node.outputs),
        "inputs": " ".join(node.inputs),
        "trigger": node.trigger,
    }


def _node_text(node: ProcessNode) -> str:
    return " ".join(_node_fields(node).values())


def relevance_score(
    node: ProcessNode, capability: CapabilityClass, scenarios: list[Scenario]
) -> float:
    """同一关键词只按命中字段中的最高权重计一次,避免多字段重复计数放大噪声。"""
    fields = _node_fields(node)
    score = 0.0
    for keyword in capability.node_keywords:
        best = 0.0
        for field_name, weight in FIELD_WEIGHTS:
            if keyword in fields[field_name] and weight > best:
                best = weight
        score += best
    for scenario in scenarios:
        if scenario.capability_class == capability.name:
            score += 2.0
    return score


def _pick_capability(node_text: str, capability: CapabilityClass) -> str:
    """从典型能力里挑一个与节点文本字面重叠最多的,作为机会点名称。"""
    best = capability.typical[0] if capability.typical else capability.name
    best_score = -1
    for item in capability.typical:
        score = sum(1 for ch in set(item) if ch in node_text)
        if score > best_score:
            best, best_score = item, score
    return best


def generate_candidates(
    profile: BusinessProfile, kb: KnowledgeBase
) -> tuple[list[Opportunity], list[str]]:
    """返回 (候选机会点, 未匹配到任何能力的节点名)。"""
    from .scoring import automation_coefficient

    opportunities: list[Opportunity] = []
    unmatched: list[str] = []

    for node in profile.nodes:
        text = _node_text(node)
        node_hits = 0
        for capability in kb.capabilities:
            scenarios = kb.match_scenarios(
                profile.industry,
                node.name,
                domain=node.domain,
                capability_class=capability.name,
                limit=3,
            )
            relevance = relevance_score(node, capability, scenarios)
            if relevance < MIN_RELEVANCE:
                continue
            node_hits += 1
            coef, coef_detail = automation_coefficient(node, capability)
            opportunities.append(
                Opportunity(
                    id=f"OPP-{node.name}-{capability.name}",
                    node=node.name,
                    domain=node.domain,
                    capability_class=capability.name,
                    capability=_pick_capability(text, capability),
                    automation_coef=coef,
                    coef_detail={**coef_detail, "relevance": relevance},
                    data_requirement=list(node.data_required),
                    integration_mode=select_integration_mode(node, profile),
                    rationale=(
                        f"节点「{node.name}」的文本与能力「{capability.name}」的典型场景"
                        f"命中 {relevance:g} 分;"
                        + (f"引用知识库案例 {', '.join(s.id for s in scenarios)}。" if scenarios else "暂无同行业案例可引用。")
                    ),
                    matched_scenarios=[s.id for s in scenarios],
                )
            )
        if node_hits == 0:
            unmatched.append(node.name)

    opportunities.sort(key=lambda o: (-o.coef_detail["relevance"], o.id))
    return opportunities, unmatched
