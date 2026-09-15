"""S5 评分引擎 —— docs/04-core-methodology.md §4 - §6。

本模块是纯确定性计算,不含任何模型调用。文档规定:
- §5.5 优先级 = (V × F) / (C × R),C 与 R 为分母,合规红线一票否决在本模块之外处理。
- §6 输出必须是区间(悲观/中性/乐观),必须给回收周期与敏感性分析。
- §6 每个输入量必须带来源标签与置信度。

**§5.4 的实现口径(已回写进文档):**
R = (幻觉影响面 × 合规等级 × (6 - 责任归属清晰度)) ** (1/3)。
三项因子各取 1-5,直乘的动态范围 1-125 会压过以「元」为量纲的 C,故取几何平均归一化回 1-5;
另外责任归属越清晰风险应越小,所以乘积里用的是反向因子 `6 - 清晰度`,而不是清晰度本身。
"""

from __future__ import annotations

import math

from ..knowledge import CapabilityClass, KnowledgeBase
from ..models import (
    Assumption,
    BusinessProfile,
    Confidence,
    CostBreakdown,
    FeasibilityBreakdown,
    Opportunity,
    ProcessNode,
    RiskBreakdown,
    ROI,
    Score,
    SensitivityItem,
    Source,
    Sourced,
    ValueBreakdown,
)

#: 置信度对应的不确定性带宽,用于 ROI 区间与敏感性分析
UNCERTAINTY_BY_CONFIDENCE: dict[Confidence, float] = {
    Confidence.HIGH: 0.10,
    Confidence.MEDIUM: 0.30,
    Confidence.LOW: 0.50,
}

#: 可行性任一项低于该阈值即判为「暂不可行」(docs/04 §5.2)
FEASIBILITY_FLOOR = 0.3

#: 无数据要求时的默认可得性,并强制标记为假设
DEFAULT_DATA_AVAILABILITY = 0.5

#: 可自动化系数的基础值(docs/04 §4 判定顺序第 1 步)
BASE_COEF_TEXT_OUTPUT = 0.8
BASE_COEF_NON_TEXT_OUTPUT = 0.5


def _uncertainty(confidence: Confidence) -> float:
    return UNCERTAINTY_BY_CONFIDENCE[confidence]


# --------------------------------------------------------------------------- #
# §4 可自动化系数
# --------------------------------------------------------------------------- #


def automation_coefficient(
    node: ProcessNode, capability: CapabilityClass
) -> tuple[float, dict]:
    """按 docs/04 §4 的判定顺序逐步折算,并按能力类别的上限封顶。

    保守原则:只允许下调,不允许超过能力目录给出的类别上限;类别下限不构成抬升理由。
    """
    is_text = node.output_is_text_or_number
    coef = BASE_COEF_TEXT_OUTPUT if is_text else BASE_COEF_NON_TEXT_OUTPUT
    steps = [
        {
            "step": 1,
            "rule": "输出为纯数字/文本资产" if is_text else "输出不是纯数字/文本资产",
            "factor": None,
            "value": coef,
        }
    ]

    for step_no, (flag, factor, label) in enumerate(
        [
            (node.requires_human_judgement, 0.7, "需人工做最终判断/承担后果"),
            (node.knowledge_intensive, 0.7, "依赖专有数据或隐性经验"),
            (node.physical_action, 0.3, "涉及物理动作或线下场景"),
            (node.regulated, 0.8, "有强合规审查要求"),
        ],
        start=2,
    ):
        if flag:
            coef *= factor
            steps.append(
                {"step": step_no, "rule": label, "factor": factor, "value": coef}
            )
        else:
            steps.append({"step": step_no, "rule": f"{label}:否", "factor": None, "value": coef})

    capped = min(coef, capability.coef_max)
    steps.append(
        {
            "step": "cap",
            "rule": f"按能力类别「{capability.name}」上限 {capability.coef_max} 封顶",
            "factor": None,
            "value": capped,
        }
    )
    detail = {
        "capability_class": capability.name,
        "class_range": list(capability.coef_range),
        "computed": round(coef, 4),
        "final": round(capped, 4),
        "below_class_floor": capped < capability.coef_range[0],
        "steps": steps,
    }
    return round(capped, 4), detail


# --------------------------------------------------------------------------- #
# §5.1 价值 V
# --------------------------------------------------------------------------- #


def _role_cost(node: ProcessNode, profile: BusinessProfile) -> tuple[float, Sourced]:
    for role in profile.roles:
        if role.name == node.role and role.annual_cost_per_fte is not None:
            return role.annual_cost_per_fte.value, role.annual_cost_per_fte
    param = profile.params.human_cost_annual_per_fte
    return param.value, param


def _build_value_inputs(
    node: ProcessNode, profile: BusinessProfile, coef: float
) -> dict:
    total_hours = profile.total_hours_per_week
    hours_per_fte = profile.params.hours_per_fte_week
    process_fte = total_hours / hours_per_fte if hours_per_fte else 0.0
    share = (node.hours_per_week / total_hours) if total_hours else 0.0
    cost_value, cost_sourced = _role_cost(node, profile)

    inputs: dict = {
        "process_fte": {"value": process_fte, "uncertainty": 0.10, "label": "流程 FTE"},
        "hours_share": {"value": share, "uncertainty": 0.10, "label": f"{node.name} 耗时占比"},
        "automation_coef": {"value": coef, "uncertainty": 0.25, "label": "可自动化系数"},
        "human_cost": {
            "value": cost_value,
            "uncertainty": _uncertainty(cost_sourced.confidence),
            "label": "年人力成本/FTE",
        },
    }
    if node.revenue_uplift:
        up = node.revenue_uplift
        inputs["conversion_uplift"] = {
            "value": up.conversion_uplift.value,
            "uncertainty": _uncertainty(up.conversion_uplift.confidence),
            "label": "转化率提升",
        }
        inputs["avg_order_value"] = {
            "value": up.avg_order_value.value,
            "uncertainty": _uncertainty(up.avg_order_value.confidence),
            "label": "客单价",
        }
        inputs["orders_per_year"] = {
            "value": up.orders_per_year.value,
            "uncertainty": _uncertainty(up.orders_per_year.confidence),
            "label": "年单量",
        }
    if node.risk_event:
        ev = node.risk_event
        inputs["risk_frequency"] = {
            "value": ev.frequency_per_year.value,
            "uncertainty": _uncertainty(ev.frequency_per_year.confidence),
            "label": "年风险事件频率",
        }
        inputs["risk_loss"] = {
            "value": ev.loss_per_event.value,
            "uncertainty": _uncertainty(ev.loss_per_event.confidence),
            "label": "单次事件损失",
        }
        inputs["risk_reduction"] = {
            "value": ev.expected_reduction,
            "uncertainty": 0.25,
            "label": "预期降幅",
        }
    return inputs


def _value_from(inputs: dict, profile: BusinessProfile) -> tuple[float, float, float]:
    """由输入参数算出 (效率收益, 收入收益, 风险降低收益)。"""
    efficiency = (
        inputs["process_fte"]["value"]
        * inputs["hours_share"]["value"]
        * inputs["automation_coef"]["value"]
        * inputs["human_cost"]["value"]
    )
    revenue = 0.0
    if "conversion_uplift" in inputs:
        revenue = (
            inputs["conversion_uplift"]["value"]
            * inputs["avg_order_value"]["value"]
            * inputs["orders_per_year"]["value"]
            * profile.params.discount_factor
        )
    risk = 0.0
    if "risk_frequency" in inputs:
        risk = (
            inputs["risk_frequency"]["value"]
            * inputs["risk_loss"]["value"]
            * inputs["risk_reduction"]["value"]
        )
    return efficiency, revenue, risk


def compute_value(
    node: ProcessNode, profile: BusinessProfile, coef: float
) -> tuple[ValueBreakdown, dict]:
    inputs = _build_value_inputs(node, profile, coef)
    efficiency, revenue, risk = _value_from(inputs, profile)
    total = efficiency + revenue + risk
    detail = {
        "process_hours_per_week": profile.total_hours_per_week,
        "node_hours_per_week": node.hours_per_week,
        "process_fte": round(inputs["process_fte"]["value"], 4),
        "node_fte": round(
            inputs["process_fte"]["value"] * inputs["hours_share"]["value"], 4
        ),
        "hours_share": round(inputs["hours_share"]["value"], 4),
        "automation_coef": inputs["automation_coef"]["value"],
        "human_cost_annual_per_fte": inputs["human_cost"]["value"],
        "formula": (
            "效率收益 = 流程FTE × 本节点耗时占比 × 可自动化系数 × 年人力成本;"
            "收入收益 = 转化率提升 × 客单价 × 年单量 × 折减系数;"
            "风险降低收益 = 年风险事件频率 × 单次事件损失 × 预期降幅"
        ),
        "discount_factor": profile.params.discount_factor,
    }
    # 区间:按各输入不确定性的均方根合成(假设相互独立)
    variance = sum(item["uncertainty"] ** 2 for item in inputs.values())
    band = min(math.sqrt(variance), 0.6)
    intervals = {
        "band": round(band, 4),
        "pessimistic": round(total * (1 - band), 2),
        "neutral": round(total, 2),
        "optimistic": round(total * (1 + band), 2),
        "inputs": inputs,
    }
    breakdown = ValueBreakdown(
        efficiency=round(efficiency, 2),
        revenue=round(revenue, 2),
        risk=round(risk, 2),
        total=round(total, 2),
        detail=detail,
        intervals=intervals,
    )
    return breakdown, inputs


# --------------------------------------------------------------------------- #
# §5.2 可行性 F
# --------------------------------------------------------------------------- #


def compute_feasibility(
    node: ProcessNode, profile: BusinessProfile, kb: KnowledgeBase
) -> FeasibilityBreakdown:
    data_map = {d.name: d for d in profile.data_sources}
    system_map = {s.name: s for s in profile.systems}

    gaps: list[str] = []
    if node.data_required:
        scores = []
        for name in node.data_required:
            src = data_map[name]
            scores.append(src.availability * (1.0 if src.exportable else 0.0))
        data_availability = sum(scores) / len(scores)
    else:
        data_availability = DEFAULT_DATA_AVAILABILITY
        gaps.append("未声明数据要求,可得性按默认中值处理,需向客户追问")

    if node.systems:
        system_access = 0.0
        for name in node.systems:
            sys = system_map[name]
            if sys.has_api and sys.data_exportable:
                system_access += 1.0
            elif sys.data_exportable:
                system_access += 0.5
            else:
                system_access += 0.0
        system_access /= len(node.systems)
    else:
        system_access = 1.0

    standardization = node.standardized
    org = profile.org_willingness

    total = data_availability * standardization * system_access * org
    blocked = [
        label
        for label, value in [
            ("数据可得性", data_availability),
            ("流程标准化度", standardization),
            ("系统可接入性", system_access),
            ("组织意愿", org),
        ]
        if value < FEASIBILITY_FLOOR
    ]
    return FeasibilityBreakdown(
        data_availability=round(data_availability, 4),
        standardization=round(standardization, 4),
        system_access=round(system_access, 4),
        org_willingness=round(org, 4),
        total=round(total, 4),
        detail={
            "formula": "F = 数据可得性 × 流程标准化度 × 系统可接入性 × 组织意愿",
            "blocked_by": blocked,
            "threshold": FEASIBILITY_FLOOR,
            "gaps": gaps,
        },
    )


# --------------------------------------------------------------------------- #
# §5.3 成本 C
# --------------------------------------------------------------------------- #


def compute_cost(
    node: ProcessNode,
    profile: BusinessProfile,
    opportunity: Opportunity,
    capability: CapabilityClass,
    kb: KnowledgeBase,
) -> CostBreakdown:
    costs = kb.costs
    mode = opportunity.integration_mode.value
    integration = costs.integration(mode)

    # 数据就绪度不足时追加数据治理人日
    feas_data = node.data_required
    if feas_data:
        data_map = {d.name: d for d in profile.data_sources}
        availability = sum(data_map[n].availability for n in feas_data) / len(feas_data)
    else:
        availability = DEFAULT_DATA_AVAILABILITY
    data_governance_days = round((1.0 - availability) * 10)

    build_days = (
        integration.person_days + capability.build_person_days + data_governance_days
    ) * costs.retry_factor
    build = build_days * costs.person_day_price

    tier = capability.model_tier
    pricing = costs.model_pricing_per_1k_tokens[tier]
    tokens = costs.tokens_per_call[tier]
    cost_per_call = (
        tokens["input"] / 1000 * pricing["input"]
        + tokens["output"] / 1000 * pricing["output"]
    )
    annual_units = node.volume_per_month * 12
    annual_calls = annual_units * capability.calls_per_unit
    run = annual_calls * cost_per_call

    maintain = build * costs.maintenance_rate
    total = build + run + maintain

    return CostBreakdown(
        build=round(build, 2),
        run=round(run, 2),
        maintain=round(maintain, 2),
        total=round(total, 2),
        detail={
            "formula": "C = 建设成本(人日×人日单价) + 运行成本(调用量×单价) + 维护成本",
            "integration_mode": mode,
            "integration_person_days": integration.person_days,
            "capability_person_days": capability.build_person_days,
            "data_governance_person_days": data_governance_days,
            "retry_factor": costs.retry_factor,
            "person_day_price": costs.person_day_price,
            "build_days": round(build_days, 2),
            "model_tier": tier,
            "cost_per_call": round(cost_per_call, 6),
            "volume_per_month": node.volume_per_month,
            "annual_calls": round(annual_calls, 1),
            "maintenance_rate": costs.maintenance_rate,
        },
    )


# --------------------------------------------------------------------------- #
# §5.4 风险 R
# --------------------------------------------------------------------------- #


def compute_risk(
    node: ProcessNode, profile: BusinessProfile, kb: KnowledgeBase
) -> RiskBreakdown:
    data_map = {d.name: d for d in profile.data_sources}
    has_pii = any(data_map[n].has_pii for n in node.data_required)
    text = f"{node.name} {node.domain} {node.pain_point} {' '.join(node.outputs)}"
    hits = kb.compliance_hits(profile.industry, text, has_pii)

    hallucination = float(node.hallucination_impact)

    compliance = 2.0
    if node.regulated:
        compliance += 2.0
    if has_pii:
        compliance += 1.0
    compliance = min(compliance, 5.0)

    # 责任归属清晰度:1=不清晰,5=清晰
    clarity = 3.0
    if node.role:
        clarity += 1.0
    if node.requires_human_judgement:
        clarity += 1.0
    if any(rule.id == "CMP-GEN-01" for rule in hits):
        clarity -= 1.0
    clarity = min(max(clarity, 1.0), 5.0)

    # 责任归属越清晰,出错后的风险越小,因此进入风险乘积的是反向因子。
    clarity_factor = 6.0 - clarity
    raw = hallucination * compliance * clarity_factor
    total = raw ** (1.0 / 3.0)

    return RiskBreakdown(
        hallucination_impact=hallucination,
        compliance_level=compliance,
        responsibility_clarity=clarity,
        clarity_factor=clarity_factor,
        raw=round(raw, 3),
        total=round(total, 4),
        detail={
            "formula": (
                "R = (幻觉影响面 × 合规等级 × (6 - 责任归属清晰度)) ** (1/3),"
                "三项因子各取 1-5,结果回到 1-5"
            ),
            "has_pii": has_pii,
            "regulated": node.regulated,
            "clarity_factor": clarity_factor,
            "compliance_hits": [
                {"id": r.id, "level": r.level, "reason": r.reason, "remedy": r.remedy}
                for r in hits
            ],
            "normalization_note": (
                "docs/04 §5.4:R 与成本 C(元)同为分母,若三项直乘则动态范围 1-125,"
                "会被 R 单独主导排序。故取几何平均归一化回 1-5,量纲与 V、F 可比。"
            ),
        },
    )


# --------------------------------------------------------------------------- #
# §5.5 优先级 / §6 ROI 与敏感性
# --------------------------------------------------------------------------- #


def compute_priority(
    value: ValueBreakdown,
    feasibility: FeasibilityBreakdown,
    cost: CostBreakdown,
    risk: RiskBreakdown,
) -> float:
    denominator = cost.total * risk.total
    if denominator <= 0:
        return 0.0
    return value.total * feasibility.total / denominator


def compute_roi(value: ValueBreakdown, cost: CostBreakdown) -> ROI:
    band = value.intervals.get("band", 0.0)
    neutral = value.total
    # 收益取年化口径,回收周期按首年成本与中性收益估算
    payback = (cost.total / neutral) if neutral > 0 else None
    return ROI(
        benefit_pessimistic=round(neutral * (1 - band), 2),
        benefit_neutral=round(neutral, 2),
        benefit_optimistic=round(neutral * (1 + band), 2),
        payback_years=round(payback, 2) if payback is not None else None,
    )


def compute_sensitivity(
    inputs: dict,
    profile: BusinessProfile,
    feasibility: FeasibilityBreakdown,
    cost: CostBreakdown,
    risk: RiskBreakdown,
    top: int = 3,
) -> list[SensitivityItem]:
    """逐项扰动关键假设,看优先级摆动幅度,找出最敏感的那一项(docs/04 §6)。"""
    base_value = sum(_value_from(inputs, profile))
    denominator = cost.total * risk.total
    if base_value <= 0 or denominator <= 0:
        return []

    base_priority = base_value * feasibility.total / denominator
    items: list[SensitivityItem] = []
    for name, spec in inputs.items():
        band = spec["uncertainty"]
        pessimistic = dict(inputs)
        optimistic = dict(inputs)
        pessimistic[name] = {**spec, "value": spec["value"] * (1 - band)}
        optimistic[name] = {**spec, "value": spec["value"] * (1 + band)}
        p_low = sum(_value_from(pessimistic, profile)) * feasibility.total / denominator
        p_high = sum(_value_from(optimistic, profile)) * feasibility.total / denominator
        swing = (p_high - p_low) / base_priority if base_priority else 0.0
        items.append(
            SensitivityItem(
                assumption=spec["label"],
                swing_pct=round(swing * 100, 2),
                priority_pessimistic=round(p_low, 4),
                priority_optimistic=round(p_high, 4),
                note=f"不确定性带宽 ±{band:.0%}",
            )
        )
    items.sort(key=lambda item: -abs(item.swing_pct))
    return items[:top]


def collect_assumptions(
    node: ProcessNode, profile: BusinessProfile, inputs: dict
) -> list[Assumption]:
    out: list[Assumption] = []
    cost_value, cost_sourced = _role_cost(node, profile)
    out.append(
        Assumption(
            name="年人力成本/FTE",
            value=f"{cost_value:,.0f} 元",
            source=cost_sourced.source,
            confidence=cost_sourced.confidence,
        )
    )
    out.append(
        Assumption(
            name=f"{node.name} 每周工时",
            value=f"{node.hours_per_week:g} 小时",
            source=Source.USER,
            confidence=Confidence.MEDIUM,
        )
    )
    if node.revenue_uplift:
        up = node.revenue_uplift
        for label, sourced in [
            ("转化率提升", up.conversion_uplift),
            ("客单价", up.avg_order_value),
            ("年单量", up.orders_per_year),
        ]:
            out.append(
                Assumption(
                    name=label,
                    value=f"{sourced.value:g}",
                    source=sourced.source,
                    confidence=sourced.confidence,
                )
            )
    if node.risk_event:
        ev = node.risk_event
        for label, sourced in [
            ("年风险事件频率", ev.frequency_per_year),
            ("单次事件损失", ev.loss_per_event),
        ]:
            out.append(
                Assumption(
                    name=label,
                    value=f"{sourced.value:g}",
                    source=sourced.source,
                    confidence=sourced.confidence,
                )
            )
    if not node.data_required:
        out.append(
            Assumption(
                name="数据可得性",
                value=f"{DEFAULT_DATA_AVAILABILITY:g}(默认中值)",
                source=Source.ASSUMED,
                confidence=Confidence.LOW,
            )
        )
    return out


def assumptions_for(
    node: ProcessNode, profile: BusinessProfile, coef: float
) -> list[Assumption]:
    """供方案组装阶段取用同一套假设表,避免两处口径漂移。"""
    return collect_assumptions(node, profile, _build_value_inputs(node, profile, coef))


def score_opportunity(
    node: ProcessNode,
    profile: BusinessProfile,
    opportunity: Opportunity,
    kb: KnowledgeBase,
) -> tuple[Score, list[Assumption]]:
    capability = kb.capability(opportunity.capability_class)
    value, inputs = compute_value(node, profile, opportunity.automation_coef)
    feasibility = compute_feasibility(node, profile, kb)
    cost = compute_cost(node, profile, opportunity, capability, kb)
    risk = compute_risk(node, profile, kb)
    priority = compute_priority(value, feasibility, cost, risk)
    roi = compute_roi(value, cost)
    sensitivity = compute_sensitivity(inputs, profile, feasibility, cost, risk)
    assumptions = collect_assumptions(node, profile, inputs)

    score = Score(
        opportunity_id=opportunity.id,
        node=node.name,
        value=value,
        feasibility=feasibility,
        cost=cost,
        risk=risk,
        priority=round(priority, 6),
        roi=roi,
        sensitivity=sensitivity,
    )
    return score, assumptions
