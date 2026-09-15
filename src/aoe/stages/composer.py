"""S7 方案组装 —— docs/06-ai-pipeline.md §3 Plan。

**数字隔离(docs/06 §6):** 所有数字必须由系统注入,模型不得生成数字。
本模块用一个显式的数字登记表(NumberRegistry)实现该约束:方案文本里的每一个数字
都必须经过 `reg()` 登记,否则 S8 的自检会判为「出现未经确认的数字」并打回。

MVP 用模板组装代替强模型撰写;换成 LLM 时,只需让它填充同一组占位符,
登记表与自检逻辑可以原样复用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..knowledge import KnowledgeBase
from ..models import (
    AcceptanceMetric,
    Assumption,
    BusinessProfile,
    CostBreakdown,
    Milestone,
    Opportunity,
    ProcessNode,
    Plan,
    Score,
)

NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: 集成方式的中文展示名,避免把 api/embedded 这类枚举值直接写进正文
INTEGRATION_LABELS = {
    "api": "API 集成",
    "rpa": "RPA 自动化",
    "embedded": "嵌入式改造",
    "standalone": "独立部署",
}
WEEKS_PER_MONTH = 4.33
WEEKS_PER_YEAR = 52


def _normalize_number(text: str) -> str:
    """归一化数字字面量,让 `01`、`1.0`、`1` 视为同一个数。"""
    text = text.replace(",", "")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text.isdigit():
        text = text.lstrip("0") or "0"
    return text or "0"


@dataclass
class NumberRegistry:
    """记录方案中允许出现的所有数字,供 S8 防幻觉自检比对。"""

    _allowed: set[str] = field(default_factory=set)

    def reg(self, value: float, fmt: str = "{:g}") -> str:
        text = fmt.format(value)
        self._allowed.add(_normalize_number(text))
        return text

    def reg_literal(self, text: str) -> str:
        self._allowed.add(_normalize_number(text))
        return text

    def reg_text(self, text: str) -> str:
        """登记一段文本中出现的全部数字。

        用于客户提供的原文(如 KPI 当前值与目标值):这些数字来自客户,属于合法来源,
        但因为绕过了 `reg()`,必须显式登记,否则会被 S8 判为未经确认的数字。
        """
        for token in NUMBER_PATTERN.findall(text):
            self._allowed.add(_normalize_number(token))
        return text

    def allowed(self) -> set[str]:
        return set(self._allowed)

    def unregistered(self, text: str) -> list[str]:
        found: list[str] = []
        for token in NUMBER_PATTERN.findall(text):
            if _normalize_number(token) not in self._allowed:
                found.append(token)
        return found


@dataclass
class ComposedPlan:
    plan: Plan
    numbers: NumberRegistry


def _money(value: float, numbers: NumberRegistry) -> str:
    return numbers.reg(value, "{:,.0f}")


def _unit_hours(node: ProcessNode, numbers: NumberRegistry) -> float | None:
    weekly_volume = node.volume_per_month / WEEKS_PER_MONTH
    if weekly_volume <= 0 or node.hours_per_week <= 0:
        return None
    return node.hours_per_week / weekly_volume


def milestones_for(build_days: float, numbers: NumberRegistry) -> list[Milestone]:
    plan_days = [
        ("数据接入与基线测量", 0.40),
        ("PoC 与效果评估", 0.35),
        ("上线与推广", 0.25),
    ]
    total = max(3, round(build_days))
    days_list = [
        max(2, round(total * ratio)) for _, ratio in plan_days[:-1]
    ]
    # 最后一段用余数,保证三段之和恰好等于建设人日
    days_list.append(max(1, total - sum(days_list)))

    milestones: list[Milestone] = []
    for ordinal, ((label, _), days) in enumerate(zip(plan_days, days_list), start=1):
        numbers.reg_literal(str(ordinal))
        milestones.append(
            Milestone(
                name=f"M{ordinal} {label}",
                duration_days=int(numbers.reg(days, "{:d}")),
            )
        )
    return milestones


def _acceptance_metrics(
    node: ProcessNode,
    profile: BusinessProfile,
    coef: float,
    numbers: NumberRegistry,
) -> list[AcceptanceMetric]:
    metrics: list[AcceptanceMetric] = []
    unit = _unit_hours(node, numbers)

    if unit is not None:
        target_unit = unit * (1 - coef)
        metrics.append(
            AcceptanceMetric(
                name=f"{node.name} 单件处理工时",
                target=(
                    f"从 {numbers.reg(unit, '{:.2f}')} 小时/件降至 "
                    f"{numbers.reg(target_unit, '{:.2f}')} 小时/件"
                ),
                how_to_measure=(
                    f"随机抽取处理完成的单据计时,样本量不少于 "
                    f"{numbers.reg(30, '{:d}')} 件,取中位数"
                ),
            )
        )
        annual_volume = node.volume_per_month * 12
        saved_hours = node.hours_per_week * WEEKS_PER_YEAR * coef
        metrics.append(
            AcceptanceMetric(
                name="年节省工时",
                target=f"{numbers.reg(saved_hours, '{:,.0f}')} 小时",
                how_to_measure=(
                    f"按年处理量 {numbers.reg(annual_volume, '{:,.0f}')} 件 × 单件节省工时核算,"
                    "每季度复核一次"
                ),
            )
        )

    metrics.append(
        AcceptanceMetric(
            name="AI 输出采纳率",
            target=f"不低于 {numbers.reg(coef * 100, '{:.0f}')}%",
            how_to_measure="统计被人工直接采纳或仅做轻微修改的输出条数占比,按月出表",
        )
    )

    for kpi in profile.kpis:
        if kpi.node == node.name:
            metrics.append(
                AcceptanceMetric(
                    name=f"KPI:{kpi.name}",
                    target=(
                        numbers.reg_text(f"由 {kpi.current} 改善至 {kpi.target}")
                        if kpi.target
                        else "待客户确认目标值"
                    ),
                    how_to_measure=f"按节点「{node.name}」的既有统计口径出数",
                )
            )
    return metrics


def _risks(
    node: ProcessNode, score: Score, coef: float, numbers: NumberRegistry
) -> list[str]:
    risks: list[str] = []
    if score.risk.hallucination_impact >= 4:
        risks.append(
            f"幻觉影响面为 {numbers.reg(node.hallucination_impact, '{:g}')} 级,"
            "输出错误后果较重,必须保留人工终审"
        )
    if score.feasibility.data_availability < 0.6:
        risks.append("数据可得性偏低,收益兑现依赖数据治理进度")
    if coef < 0.5:
        risks.append(
            f"可自动化系数仅 {numbers.reg(coef, '{:g}')},"
            "收益不确定性高,建议先做小范围 PoC"
        )
    if node.physical_action:
        risks.append("环节涉及线下实体作业,AI 只能覆盖其中信息处理部分")
    if node.requires_human_judgement:
        risks.append("环节需人工承担最终判断后果,AI 定位为辅助而非替代")
    for hit in score.risk.detail.get("compliance_hits", []):
        risks.append(f"合规约束[{hit['id']}]:{hit['reason']}")
    if not risks:
        risks.append("暂未识别到显著风险,按标准变更管理流程推进")
    return risks


def compose_plan(
    node: ProcessNode,
    profile: BusinessProfile,
    opportunity: Opportunity,
    score: Score,
    assumptions: list[Assumption],
    kb: KnowledgeBase,
    plan_id: str,
) -> ComposedPlan:
    numbers = NumberRegistry()
    coef = opportunity.automation_coef
    cost: CostBreakdown = score.cost
    build_days = float(cost.detail.get("build_days", 0))

    current_hours = numbers.reg(node.hours_per_week, "{:g}")
    monthly_volume = numbers.reg(node.volume_per_month, "{:,.0f}")
    coef_pct = numbers.reg(coef * 100, "{:.0f}")
    fte = numbers.reg(
        score.value.detail.get("node_fte", 0), "{:.2f}"
    )

    mode_label = INTEGRATION_LABELS[opportunity.integration_mode.value]
    before = (
        f"节点「{node.name}」归属业务域「{node.domain}」,"
        f"由{node.role or '未指定角色'}负责,触发条件为“{node.trigger or '按固定周期'}”。"
        f"输入为{('、'.join(node.inputs) or '人工收集的信息')},"
        f"输出为{('、'.join(node.outputs) or '人工整理的成果')}。"
        f"当前每周投入约 {current_hours} 工时(折合 {fte} 个 FTE),月处理量约 {monthly_volume} 件。"
        f"主要痛点:{node.pain_point or '未记录'}。"
    )

    after = (
        f"在节点「{node.name}」引入「{opportunity.capability_class}」能力"
        f"({opportunity.capability}),以{mode_label}方式接入"
        f"{('、'.join(node.systems) or '现有作业环境')}。"
        f"系统承担其中约 {coef_pct}% 的处理量,执行角色从“逐件处理”转为“抽检与例外处理”。"
        f"超出置信度的结果自动转人工,人工结论回写为训练与规则迭代的输入。"
    )

    if node.data_required:
        data_lines = []
        data_map = {d.name: d for d in profile.data_sources}
        for name in node.data_required:
            src = data_map[name]
            availability = numbers.reg(src.availability * 100, "{:.0f}")
            pii_note = ",含个人信息" if src.has_pii else ""
            data_lines.append(
                f"{name}(载体:{src.carrier or '未记录'},可得性 {availability}%{pii_note})"
            )
        data_plan = (
            "数据方案:所需数据源为 " + "、".join(data_lines) + "。"
            f"优先通过{mode_label}方式获取;数据可得性不足的来源"
            "纳入前置数据治理范围,未达标前不进入验收。"
        )
    else:
        data_plan = (
            "数据方案:该节点未声明结构化数据要求,须先补齐数据清单并向客户确认,"
            "在数据要求明确前不得进入开发。"
        )

    agent_design = (
        f"Agent 设计:采用「{opportunity.capability_class}」单一职责链路,"
        f"模型档位 {kb.capability(opportunity.capability_class).model_tier}。"
        "链路为 输入校验 → 模型处理 → 结构化结果校验 → 置信度分流 → 人工复核(低置信度)。"
        "所有中间结果落库,支持重跑与对比。"
    )
    if opportunity.matched_scenarios:
        # docs/06 §6 证据引用:案例引用必须指向知识库真实条目 id
        agent_design += numbers.reg_text(
            " 参考知识库案例:" + "、".join(opportunity.matched_scenarios) + "。"
        )
    else:
        agent_design += " 该行业暂无可用案例,本条方案不含同行案例引用。"

    integration = (
        f"集成方式:{mode_label}。"
        f"建设人日 {numbers.reg(build_days, '{:g}')} 人日,"
        f"首年总成本约 {_money(cost.total, numbers)} 元"
        f"(建设 {_money(cost.build, numbers)} 元、运行 {_money(cost.run, numbers)} 元、"
        f"维护 {_money(cost.maintain, numbers)} 元)。"
        f"其中运行成本按年调用 {numbers.reg(cost.detail.get('annual_calls', 0), '{:,.0f}')} 次估算。"
    )

    compliance_hits = score.risk.detail.get("compliance_hits", [])
    if compliance_hits:
        compliance_notes = "合规注意事项:" + ";".join(
            f"[{hit['id']}]{hit['remedy']}" for hit in compliance_hits
        )
    else:
        compliance_notes = "合规注意事项:未命中合规库中的红线或约束条目;仍须按企业变更流程完成内部评审。"

    plan = Plan(
        opportunity_ref=opportunity.id,
        plan_id=plan_id,
        before_process=before,
        after_process=after,
        data_plan=data_plan,
        agent_design=agent_design,
        integration=integration,
        milestones=milestones_for(build_days, numbers),
        acceptance_metrics=_acceptance_metrics(node, profile, coef, numbers),
        assumptions=assumptions,
        risks=_risks(node, score, coef, numbers),
        compliance_notes=compliance_notes,
    )
    return ComposedPlan(plan=plan, numbers=numbers)
