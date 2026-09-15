"""S7 方案撰写(强模型版) —— docs/06-ai-pipeline.md §5 / §6。

与模板版(`composer.py`)的差别只有一处:**正文由模型写**。
其余全部不变——尤其是不变的部分才是重点:

1. **数字隔离(docs/06 §6)。** 系统先把所有允许出现的数字整理成「事实表」并全部登记,
   模型只能引用事实表里的数字,不得自行做任何算术。生成后由 S8 反向扫描,
   发现未登记数字即判为编造,把违规数字回喂给模型重写(docs/06 §5「打回重写」)。
2. **里程碑与假设仍由代码产出。** 算术不交给模型(docs/04 §7)。
3. **本模块不信任模型输出。** 字段缺失、类型不对、口径为空,一律走重写路径。

这条重写回路是「接上大模型之后」仍然能守住质量下限的原因。
"""

from __future__ import annotations

import json

from ..knowledge import KnowledgeBase
from ..llm import LLMClient
from ..models import (
    AcceptanceMetric,
    Assumption,
    BusinessProfile,
    Opportunity,
    Plan,
    ProcessNode,
    Score,
)
from .composer import (
    WEEKS_PER_MONTH,
    WEEKS_PER_YEAR,
    ComposedPlan,
    INTEGRATION_LABELS,
    NumberRegistry,
    milestones_for,
)
from .critic import check_1_bound_to_node, check_8_no_fabricated_numbers

ROLE = """你是企业 AI 落地顾问。你的任务是基于系统给出的「事实表」,撰写一份可执行的 AI 落地改造方案。"""

FORBIDDEN = """禁止行为清单(违反即打回重写):
1. **不得输出事实表之外的任何数字。** 事实表里没有的数字,一个都不许出现。
2. **不得做任何算术。** 不要自己相加、换算、估算、四舍五入——需要哪个数字,事实表里就有。
3. 不得编造客户未提供的业务事实(系统名、数据源、角色、流程环节)。
4. 不得给出无法测量的验收指标。每条指标必须有明确口径。
5. 不得写「加强管理」「提升效率」这类无法落地的空话。
6. 不得输出解释性前后文,只输出纯 JSON。"""

OUTPUT_SCHEMA = """输出 JSON,且只包含以下字段:
{
  "before_process": "改造前现状描述,150-250 字,必须逐字出现事实表中的「节点」名称",
  "after_process": "改造后流程描述,明确人机分工与例外处理路径,150-250 字,同样必须逐字出现该节点名称",
  "data_plan": "数据方案:逐项列出所需数据源与可得性,说明获取方式与治理前置条件,100-200 字",
  "agent_design": "Agent 设计:链路步骤、模型档位、结构化校验、置信度分流、人工复核点,150-250 字",
  "integration": "集成方式与成本说明,须引用事实表中的成本构成,80-150 字",
  "acceptance_metrics": [
    {"name": "指标名", "target": "目标值(只能用事实表里的数字)", "how_to_measure": "测量口径"}
  ],
  "risks": ["风险条目,每条一句话,须具体"],
  "compliance_notes": "合规注意事项"
}"""

SOURCE_RULES = """来源标注规则:
- 事实表里每个数字都标了 source 与 confidence,你在正文提到关键数字时,应说明其依据强度
  (例如「该值来自行业基准,置信度中」)。
- 若某项数据置信度为 low,必须在风险或数据方案里明确提示"需实测确认"。"""


def build_fact_sheet(
    node: ProcessNode,
    profile: BusinessProfile,
    opportunity: Opportunity,
    score: Score,
    kb: KnowledgeBase,
    numbers: NumberRegistry,
    assumptions: list[Assumption],
) -> dict:
    """整理出模型可以引用的全部数字,并逐一登记到 NumberRegistry。

    登记之后,S8 的数字自检就变成了一道精确的闸门:凡是不在事实表里的数字都是编造。
    """
    coef = opportunity.automation_coef
    value = score.value
    cost = score.cost
    feasibility = score.feasibility
    risk = score.risk
    build_days = float(cost.detail.get("build_days", 0))

    weekly_volume = node.volume_per_month / WEEKS_PER_MONTH
    unit_hours = (node.hours_per_week / weekly_volume) if weekly_volume > 0 else 0.0

    fact: dict = {
        "节点": node.name,
        "业务域": node.domain,
        "执行角色": node.role or "未指定",
        "触发条件": node.trigger or "按固定周期",
        "输入": node.inputs,
        "输出": node.outputs,
        "痛点": node.pain_point or "未记录",
        "涉及系统": node.systems,
        "集成方式": INTEGRATION_LABELS[opportunity.integration_mode.value],
        "能力类别": opportunity.capability_class,
        "具体能力": opportunity.capability,
        "模型档位": kb.capability(opportunity.capability_class).model_tier,
        "每周工时": numbers.reg(node.hours_per_week, "{:g}"),
        "月处理量": numbers.reg(node.volume_per_month, "{:,.0f}"),
        "年处理量": numbers.reg(node.volume_per_month * 12, "{:,.0f}"),
        "单件当前工时_小时": numbers.reg(unit_hours, "{:.2f}"),
        "单件目标工时_小时": numbers.reg(unit_hours * (1 - coef), "{:.2f}"),
        "年节省工时": numbers.reg(node.hours_per_week * WEEKS_PER_YEAR * coef, "{:,.0f}"),
        "可自动化系数": numbers.reg(coef, "{:g}"),
        "自动化百分比": numbers.reg(coef * 100, "{:.0f}"),
        "流程FTE": numbers.reg(value.detail.get("process_fte", 0), "{:.2f}"),
        "本节点FTE": numbers.reg(value.detail.get("node_fte", 0), "{:.2f}"),
        "本节点耗时占比百分比": numbers.reg(
            value.detail.get("hours_share", 0) * 100, "{:.0f}"
        ),
        "年人力成本": numbers.reg(value.detail.get("human_cost_annual_per_fte", 0), "{:,.0f}"),
        "价值V_元年": numbers.reg(value.total, "{:,.0f}"),
        "效率收益": numbers.reg(value.efficiency, "{:,.0f}"),
        "收入收益": numbers.reg(value.revenue, "{:,.0f}"),
        "风险降低收益": numbers.reg(value.risk, "{:,.0f}"),
        "收益悲观": numbers.reg(score.roi.benefit_pessimistic, "{:,.0f}"),
        "收益中性": numbers.reg(score.roi.benefit_neutral, "{:,.0f}"),
        "收益乐观": numbers.reg(score.roi.benefit_optimistic, "{:,.0f}"),
        "可行性_数据可得性": numbers.reg(feasibility.data_availability, "{:.2f}"),
        "可行性_流程标准化": numbers.reg(feasibility.standardization, "{:.2f}"),
        "可行性_系统可接入性": numbers.reg(feasibility.system_access, "{:.2f}"),
        "可行性_组织意愿": numbers.reg(feasibility.org_willingness, "{:.2f}"),
        "可行性_总": numbers.reg(feasibility.total, "{:.2f}"),
        "建设成本": numbers.reg(cost.build, "{:,.0f}"),
        "运行成本": numbers.reg(cost.run, "{:,.0f}"),
        "维护成本": numbers.reg(cost.maintain, "{:,.0f}"),
        "首年总成本": numbers.reg(cost.total, "{:,.0f}"),
        "建设人日": numbers.reg(build_days, "{:g}"),
        "数据治理人日": numbers.reg(cost.detail.get("data_governance_person_days", 0), "{:g}"),
        "年调用次数": numbers.reg(cost.detail.get("annual_calls", 0), "{:,.0f}"),
        "人日单价": numbers.reg(cost.detail.get("person_day_price", 0), "{:,.0f}"),
        "幻觉影响面": numbers.reg(risk.hallucination_impact, "{:g}"),
        "合规等级": numbers.reg(risk.compliance_level, "{:g}"),
        "责任清晰度": numbers.reg(risk.responsibility_clarity, "{:g}"),
        "风险R": numbers.reg(risk.total, "{:.2f}"),
        "优先级": numbers.reg(score.priority, "{:.2f}"),
    }
    if score.roi.payback_years is not None:
        fact["回收周期_年"] = numbers.reg(score.roi.payback_years, "{:.2f}")

    data_map = {d.name: d for d in profile.data_sources}
    fact["数据源"] = [
        {
            "名称": name,
            "载体": data_map[name].carrier or "未记录",
            "可得性百分比": numbers.reg(data_map[name].availability * 100, "{:.0f}"),
            "含个人信息": data_map[name].has_pii,
        }
        for name in node.data_required
        if name in data_map
    ]
    if not node.data_required:
        fact["数据源"] = "客户未声明数据要求,必须要求补充清单后再进入开发"

    cases = []
    for scenario in kb.match_scenarios(
        profile.industry, node.name, domain=node.domain, limit=3
    ):
        cases.append(
            {
                "id": numbers.reg_text(scenario.id),
                "标题": numbers.reg_text(scenario.title),
                "做法": scenario.content,
            }
        )
    fact["可引用知识库案例"] = cases or "本行业暂无可引用案例"

    compliance_hits = risk.detail.get("compliance_hits", [])
    fact["合规约束"] = (
        [
            {
                "id": numbers.reg_text(hit["id"]),
                "要求": hit["reason"],
                "整改前提": hit["remedy"],
            }
            for hit in compliance_hits
        ]
        or "未命中合规库中的红线或约束条目"
    )

    assumptions_text = [
        {
            "名称": item.name,
            "取值": numbers.reg_text(item.value),
            "来源": item.source.value,
            "置信度": item.confidence.value,
        }
        for item in assumptions
    ]
    fact["关键假设"] = assumptions_text
    return fact


def compose_plan_llm(
    node: ProcessNode,
    profile: BusinessProfile,
    opportunity: Opportunity,
    score: Score,
    assumptions: list[Assumption],
    kb: KnowledgeBase,
    plan_id: str,
    client: LLMClient,
    max_rewrites: int = 3,
) -> ComposedPlan:
    numbers = NumberRegistry()
    fact_sheet = build_fact_sheet(node, profile, opportunity, score, kb, numbers, assumptions)
    fact_text = json.dumps(fact_sheet, ensure_ascii=False, indent=2)

    system = "\n\n".join([ROLE, FORBIDDEN, SOURCE_RULES, OUTPUT_SCHEMA])
    base_user = (
        f"以下是本次方案涉及的全部事实数据(唯一可信来源):\n\n{fact_text}\n\n"
        "请基于以上事实撰写方案 JSON。再次强调:正文中出现的每一个数字都必须在上面出现过,"
        "且必须与上面完全一致;不要做任何计算。"
    )

    build_days = float(score.cost.detail.get("build_days", 0))
    violations: list[str] = []
    attempts: list[str] = []
    user = base_user

    for attempt in range(1, max_rewrites + 1):
        payload = client.chat_json(
            system,
            user,
            purpose="S7 方案撰写",
            temperature=client.config.compose_temperature,
        )
        try:
            plan = _build_plan(payload, opportunity, assumptions, numbers, build_days, plan_id)
        except ValueError as exc:
            attempts.append(str(exc))
            user = base_user + f"\n\n上一次输出不合格:{exc}\n请修正后重新输出完整 JSON。"
            continue

        # 自检项一:方案必须绑定到本流程节点,节点名称需原样出现在正文中。
        binding = check_1_bound_to_node(plan, node)
        if not binding.passed:
            violations.append(binding.detail)
            user = (
                base_user
                + f"\n\n上一次输出不合格:正文里没有逐字出现流程节点名称「{node.name}」。"
                "请在 before_process 和 after_process 中原样引用该节点名称,不要改写或缩写,"
                "重新输出完整 JSON。"
            )
            continue

        check = check_8_no_fabricated_numbers(plan, numbers)
        if check.passed:
            return ComposedPlan(plan=plan, numbers=numbers)

        # docs/06 §5:打回时必须给出具体修改指令,而不是「请改进」
        bad = _extract_unregistered(check.detail)
        violations.append(check.detail)
        user = (
            base_user
            + "\n\n上一次输出里出现了事实表中不存在的数字:"
            + "、".join(bad)
            + "。请删掉或替换为事实表中的对应数字,不要做任何计算,重新输出完整 JSON。"
        )

    raise ValueError(
        f"方案 {plan_id} 连续 {max_rewrites} 次未通过数字隔离自检。"
        f"最后一次违规:{violations[-1] if violations else '结构校验失败'}。"
        f"结构错误:{attempts}"
    )


def _extract_unregistered(detail: str) -> list[str]:
    start, end = detail.find("["), detail.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        value = json.loads(detail[start : end + 1].replace("'", '"'))
        return [str(item) for item in value]
    except json.JSONDecodeError:
        return []


def _build_plan(
    payload: dict,
    opportunity: Opportunity,
    assumptions: list[Assumption],
    numbers: NumberRegistry,
    build_days: float,
    plan_id: str,
) -> Plan:
    required = (
        "before_process",
        "after_process",
        "data_plan",
        "agent_design",
        "integration",
        "acceptance_metrics",
        "risks",
        "compliance_notes",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"缺少必需字段:{missing}")

    metrics: list[AcceptanceMetric] = []
    for item in payload["acceptance_metrics"]:
        if not isinstance(item, dict):
            raise ValueError("acceptance_metrics 的元素必须是对象")
        gaps = [k for k in ("name", "target", "how_to_measure") if not item.get(k)]
        if gaps:
            raise ValueError(f"验收指标缺少 {gaps}:{item}")
        metrics.append(
            AcceptanceMetric(
                name=str(item["name"]),
                target=str(item["target"]),
                how_to_measure=str(item["how_to_measure"]),
            )
        )
    if not metrics:
        raise ValueError("acceptance_metrics 不能为空")

    risks = [str(item) for item in payload["risks"] if str(item).strip()]
    if not risks:
        raise ValueError("risks 不能为空")

    return Plan(
        opportunity_ref=opportunity.id,
        plan_id=plan_id,
        before_process=str(payload["before_process"]),
        after_process=str(payload["after_process"]),
        data_plan=str(payload["data_plan"]),
        agent_design=str(payload["agent_design"]),
        integration=str(payload["integration"]),
        milestones=milestones_for(build_days, numbers),
        acceptance_metrics=metrics,
        assumptions=assumptions,
        risks=risks,
        compliance_notes=str(payload["compliance_notes"]),
    )
