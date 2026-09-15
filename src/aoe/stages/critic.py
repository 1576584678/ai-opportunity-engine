"""S8 自检 —— docs/06-ai-pipeline.md §5 Critic 自检清单。

清单的 10 条被实现为确定性校验。这是本引擎相对「直接问大模型」最有价值的一环:
结构约束、来源约束、数字约束都可以被机器判定,不需要另一个模型来「感觉一下」。

打回时必须给出具体的修改指令(docs/06 §5 末句)。
"""

from __future__ import annotations

import re

from ..knowledge import KnowledgeBase
from ..models import (
    BusinessProfile,
    Critique,
    CritiqueCheck,
    Plan,
    ProcessNode,
    Source,
)
from .composer import NumberRegistry

QUOTED_PATTERN = re.compile(r"「([^」]+)」")


def _all_text(plan: Plan) -> str:
    parts = [
        plan.before_process,
        plan.after_process,
        plan.data_plan,
        plan.agent_design,
        plan.integration,
        plan.compliance_notes,
        *plan.risks,
    ]
    for milestone in plan.milestones:
        parts.append(milestone.name)
    for metric in plan.acceptance_metrics:
        parts.extend([metric.name, metric.target, metric.how_to_measure])
    for assumption in plan.assumptions:
        parts.append(assumption.name)
    return "\n".join(parts)


def check_1_bound_to_node(plan: Plan, node: ProcessNode) -> CritiqueCheck:
    name = (node.name or "").strip()
    text = "\n".join(
        [
            plan.before_process,
            plan.after_process,
            plan.data_plan,
            plan.agent_design,
            plan.integration,
        ]
    )
    ok = bool(name) and name in text
    return CritiqueCheck(
        no=1,
        name="绑定到具体的流程节点",
        passed=ok,
        detail=f"方案绑定节点「{node.name}」" if ok else "方案未绑定到具体流程节点",
    )


def check_2_before_after(plan: Plan) -> CritiqueCheck:
    before, after = plan.before_process.strip(), plan.after_process.strip()
    ok = len(before) > 20 and len(after) > 20 and before != after
    return CritiqueCheck(
        no=2,
        name="说明改造前后的流程差异",
        passed=ok,
        detail="已分别描述改造前现状与改造后流程" if ok else "改造前后流程描述缺失或完全相同",
    )


def check_3_data_plan(plan: Plan, node: ProcessNode) -> CritiqueCheck:
    if node.data_required:
        ok = all(name in plan.data_plan for name in node.data_required)
        detail = (
            "数据要求与可得性已逐项列出"
            if ok
            else "数据方案未覆盖全部数据要求:" + ", ".join(
                n for n in node.data_required if n not in plan.data_plan
            )
        )
    else:
        ok = "未声明" in plan.data_plan or "确认" in plan.data_plan
        detail = (
            "节点未声明数据要求,方案已标注需向客户追问"
            if ok
            else "节点未声明数据要求,方案未标注该缺口"
        )
    return CritiqueCheck(no=3, name="列出数据要求及可得性", passed=ok, detail=detail)


def check_4_integration(plan: Plan) -> CritiqueCheck:
    labels = ("API 集成", "RPA 自动化", "嵌入式改造", "独立部署")
    # 模型常写成「RPA自动化」这类无空格形式,比较前统一去掉空白再匹配。
    normalized = re.sub(r"\s+", "", plan.integration)
    ok = any(re.sub(r"\s+", "", label) in normalized for label in labels)
    return CritiqueCheck(
        no=4,
        name="给出集成方式",
        passed=ok,
        detail="已给出集成方式" if ok else "缺少集成方式(API / RPA / 嵌入 / 独立)",
    )


def check_5_metrics(plan: Plan) -> CritiqueCheck:
    if not plan.acceptance_metrics:
        return CritiqueCheck(
            no=5, name="验收指标可测量且有口径", passed=False, detail="没有任何验收指标"
        )
    bad = [
        metric.name
        for metric in plan.acceptance_metrics
        if not (metric.name and metric.target and metric.how_to_measure)
    ]
    return CritiqueCheck(
        no=5,
        name="验收指标可测量且有口径",
        passed=not bad,
        detail="全部验收指标均可测量且有明确口径" if not bad else f"以下指标缺口径:{bad}",
    )


def check_6_sources(plan: Plan) -> CritiqueCheck:
    if not plan.assumptions:
        return CritiqueCheck(
            no=6, name="数字带来源标签", passed=False, detail="未列出任何量级假设"
        )
    bad = [
        a.name
        for a in plan.assumptions
        if a.source not in (Source.USER, Source.BENCHMARK, Source.ASSUMED)
        or not str(a.value).strip()
    ]
    return CritiqueCheck(
        no=6,
        name="数字带来源标签",
        passed=not bad,
        detail=(
            f"全部 {len(plan.assumptions)} 项量级假设均带来源标签"
            if not bad
            else f"以下假设缺来源标签:{bad}"
        ),
    )


def check_7_risks(plan: Plan) -> CritiqueCheck:
    ok = bool(plan.risks) and bool(plan.compliance_notes.strip())
    return CritiqueCheck(
        no=7,
        name="列出风险与合规注意事项",
        passed=ok,
        detail="已列出风险与合规注意事项" if ok else "风险清单或合规注意事项缺失",
    )


def check_8_no_fabricated_numbers(plan: Plan, numbers: NumberRegistry) -> CritiqueCheck:
    """docs/06 §6 数字隔离:方案中出现的每个数字都必须由系统注入。"""
    text = _all_text(plan)
    unregistered = numbers.unregistered(text)
    unique = sorted(set(unregistered))
    return CritiqueCheck(
        no=8,
        name="未出现未经确认的量级数字",
        passed=not unique,
        detail=(
            "方案内全部数字均来自系统注入的计算结果或客户输入"
            if not unique
            else f"发现未经登记的数字:{unique}(疑似编造,必须改为系统注入)"
        ),
    )


def check_9_profile_consistency(
    plan: Plan, profile: BusinessProfile, kb: KnowledgeBase
) -> CritiqueCheck:
    """方案中引用的实体必须真实存在于业务画像或知识库中。"""
    known = (
        {node.name for node in profile.nodes}
        | {node.domain for node in profile.nodes}
        | {s.name for s in profile.systems}
        | {d.name for d in profile.data_sources}
        | {r.name for r in profile.roles}
        | {k.name for k in profile.kpis}
        | {c.name for c in kb.capabilities}
        | {rule.id for rule in kb.compliance_rules}
    )
    quoted = set(QUOTED_PATTERN.findall(_all_text(plan)))
    unknown = sorted(q for q in quoted if q not in known)
    return CritiqueCheck(
        no=9,
        name="内容与业务画像一致",
        passed=not unknown,
        detail=(
            "方案引用的实体全部可在业务画像或知识库中追溯"
            if not unknown
            else f"引用画像中不存在的实体:{unknown}"
        ),
    )


def check_10_poc_scope(plan: Plan, score_build_days: float) -> CritiqueCheck:
    poc_mentioned = any("PoC" in milestone.name for milestone in plan.milestones)
    if score_build_days <= 20:
        return CritiqueCheck(
            no=10,
            name="可用更小的 PoC 先验证",
            passed=True,
            detail=f"建设规模 {score_build_days:g} 人日,可直接以 M1/M2 作为验证范围",
        )
    return CritiqueCheck(
        no=10,
        name="可用更小的 PoC 先验证",
        passed=poc_mentioned,
        detail=(
            "建设规模偏大,方案已包含独立 PoC 阶段"
            if poc_mentioned
            else "建设规模偏大但方案未拆分出 PoC 阶段,必须要求拆分"
        ),
    )


def critique_plan(
    plan: Plan,
    numbers: NumberRegistry,
    node: ProcessNode,
    profile: BusinessProfile,
    kb: KnowledgeBase,
    build_days: float,
) -> Critique:
    checks = [
        check_1_bound_to_node(plan, node),
        check_2_before_after(plan),
        check_3_data_plan(plan, node),
        check_4_integration(plan),
        check_5_metrics(plan),
        check_6_sources(plan),
        check_7_risks(plan),
        check_8_no_fabricated_numbers(plan, numbers),
        check_9_profile_consistency(plan, profile, kb),
        check_10_poc_scope(plan, build_days),
    ]
    failed = [check for check in checks if not check.passed]
    instructions = [f"[第{c.no}条 {c.name}] {c.detail}" for c in failed]
    return Critique(
        plan_id=plan.plan_id,
        passed=not failed,
        checks=checks,
        rework_instructions=instructions,
    )
