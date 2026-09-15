"""S4 适用性过滤与合规红线 —— docs/04-core-methodology.md §3 step 2、§5.5。

规则是确定性的、可审计的。命中红线的机会点一票否决,单独输出。
"""

from __future__ import annotations

from ..knowledge import KnowledgeBase
from ..models import (
    BusinessProfile,
    Opportunity,
    OpportunityStatus,
    ProcessNode,
)

#: 数据可得性低于该值时判为「数据不可得」
DATA_UNAVAILABLE_FLOOR = 0.3

#: 流程标准化度低于该值时降级为「需先标准化」
STANDARDIZATION_FLOOR = 0.5


def _node_map(profile: BusinessProfile) -> dict[str, ProcessNode]:
    return {node.name: node for node in profile.nodes}


def apply_rules(
    profile: BusinessProfile, opportunities: list[Opportunity], kb: KnowledgeBase
) -> list[Opportunity]:
    """就地更新机会点状态并返回全部机会点(含被剔除的,便于输出红线清单)。"""
    nodes = _node_map(profile)
    data_map = {d.name: d for d in profile.data_sources}
    results: list[Opportunity] = []

    for opp in opportunities:
        node = nodes[opp.node]
        text = f"{node.name} {node.domain} {node.pain_point} {' '.join(node.outputs)}"

        # 1) 合规红线(最高优先级,一票否决)
        has_pii = any(
            data_map[name].has_pii
            for name in node.data_required
            if name in data_map
        )
        hits = kb.compliance_hits(profile.industry, text, has_pii)
        redlines = [rule for rule in hits if rule.is_redline]
        if redlines:
            rule = redlines[0]
            results.append(
                opp.model_copy(
                    update={
                        "status": OpportunityStatus.REDLINE,
                        "status_reason": f"[{rule.id}] {rule.reason};整改前提:{rule.remedy}",
                    }
                )
            )
            continue

        # 2) 数据不可得或不可导出 → 剔除
        blocked_data = [
            name
            for name in node.data_required
            if data_map[name].availability < DATA_UNAVAILABLE_FLOOR
            or not data_map[name].exportable
        ]
        if blocked_data:
            results.append(
                opp.model_copy(
                    update={
                        "status": OpportunityStatus.INFEASIBLE,
                        "status_reason": f"数据不可得或不可导出:{', '.join(blocked_data)}",
                    }
                )
            )
            continue

        # 3) 流程未标准化 → 降级为「需先标准化」
        if node.standardized < STANDARDIZATION_FLOOR:
            results.append(
                opp.model_copy(
                    update={
                        "status": OpportunityStatus.DOWNGRADED,
                        "status_reason": (
                            f"流程标准化度 {node.standardized:g} 低于阈值 "
                            f"{STANDARDIZATION_FLOOR:g},需先完成流程标准化"
                        ),
                    }
                )
            )
            continue

        # 4) 命中 guard 级合规要求 → 保留但记录约束
        guards = [rule for rule in hits if not rule.is_redline]
        reason = opp.status_reason
        if guards:
            reason = ";".join(f"[{r.id}] {r.remedy}" for r in guards)
        results.append(
            opp.model_copy(
                update={"status": OpportunityStatus.CANDIDATE, "status_reason": reason}
            )
        )

    return results


def partition(
    opportunities: list[Opportunity],
) -> dict[OpportunityStatus, list[Opportunity]]:
    buckets: dict[OpportunityStatus, list[Opportunity]] = {
        status: [] for status in OpportunityStatus
    }
    for opp in opportunities:
        buckets[opp.status].append(opp)
    return buckets
