"""S9 渲染 —— 输出 Markdown(docs/04 §5.5:最终排序必须展示计算明细,不可只给结论分)。"""

from __future__ import annotations

from .models import Plan, Score
from .result import RunResult


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _summary(result: RunResult) -> list[str]:
    lines = ["## 1. 结论摘要", ""]
    top = result.ranked[:3]
    if not top:
        lines.append("本次运行没有产出可排序的机会点。")
        lines.append("")
        return lines
    for index, score in enumerate(top, start=1):
        opp = result.opportunity(score.opportunity_id)
        payback = (
            f"{score.roi.payback_years:.2f} 年" if score.roi.payback_years else "不可估算"
        )
        lines.append(
            f"{index}. **{score.node} · {opp.capability_class}** — 优先级 "
            f"{score.priority:.4f},年收益区间 "
            f"{_money(score.roi.benefit_pessimistic)} ~ {_money(score.roi.benefit_optimistic)} 元,"
            f"回收周期 {payback}"
        )
        lines.append(f"   - 理由:{opp.rationale}")
    lines.append("")
    return lines


def _opportunity_map(result: RunResult) -> list[str]:
    lines = ["## 2. 机会地图(按优先级排序)", ""]
    lines.append(
        "| 排名 | 流程节点 | 能力类别 | 优先级 | 价值 V(元/年) | 可行性 F | 首年成本 C(元) | 风险 R | 回收周期 |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for index, score in enumerate(result.ranked, start=1):
        opp = result.opportunity(score.opportunity_id)
        payback = (
            f"{score.roi.payback_years:.2f} 年" if score.roi.payback_years else "-"
        )
        lines.append(
            f"| {index} | {score.node} | {opp.capability_class} | {score.priority:.4f} | "
            f"{_money(score.value.total)} | {score.feasibility.total:.3f} | "
            f"{_money(score.cost.total)} | {score.risk.total:.2f} | {payback} |"
        )
    lines.append("")
    lines.append(
        "优先级 = (V × F) / (C × R)。V 为年化收益,C 为首年总成本,"
        "F 为四项可行性之积,R 为三项风险因子归一化后的几何平均。"
    )
    lines.append("")
    return lines


def _excluded(result: RunResult) -> list[str]:
    lines = ["## 3. 被排除与降级的机会点", ""]
    buckets = {
        "redline": ("合规红线(一票否决)", []),
        "downgraded": ("需先标准化后重新评估", []),
        "infeasible": ("数据不可得或不可导出", []),
    }
    for opp in result.all_opportunities:
        key = opp.status.value
        if key in buckets:
            buckets[key][1].append(opp)

    any_found = False
    for key, (title, items) in buckets.items():
        if not items:
            continue
        any_found = True
        lines.append(f"### {title}")
        lines.append("")
        for opp in items:
            lines.append(f"- `{opp.id}` — {opp.node} × {opp.capability_class}")
            lines.append(f"  - {opp.status_reason}")
        lines.append("")
    if not any_found:
        lines.append("本次运行没有被排除或降级的机会点。")
        lines.append("")
    return lines


def _plan_detail(plan: Plan, score: Score, rank: int) -> list[str]:
    lines = [f"### 方案 {rank}:{score.node} · {plan.plan_id}", ""]
    lines.append(f"- **改造前**:{plan.before_process}")
    lines.append(f"- **改造后**:{plan.after_process}")
    lines.append(f"- **数据方案**:{plan.data_plan}")
    lines.append(f"- **Agent 设计**:{plan.agent_design}")
    lines.append(f"- **集成与成本**:{plan.integration}")
    lines.append("")

    lines.append("**里程碑**")
    lines.append("")
    lines.append("| 阶段 | 工期(天) |")
    lines.append("| --- | --- |")
    for milestone in plan.milestones:
        lines.append(f"| {milestone.name} | {milestone.duration_days} |")
    lines.append("")

    lines.append("**验收指标**")
    lines.append("")
    lines.append("| 指标 | 目标 | 测量口径 |")
    lines.append("| --- | --- | --- |")
    for metric in plan.acceptance_metrics:
        lines.append(f"| {metric.name} | {metric.target} | {metric.how_to_measure} |")
    lines.append("")

    lines.append("**关键假设**")
    lines.append("")
    lines.append("| 假设 | 取值 | 来源 | 置信度 |")
    lines.append("| --- | --- | --- | --- |")
    for assumption in plan.assumptions:
        lines.append(
            f"| {assumption.name} | {assumption.value} | {assumption.source.value} | "
            f"{assumption.confidence.value} |"
        )
    lines.append("")

    lines.append("**风险**")
    lines.append("")
    for risk in plan.risks:
        lines.append(f"- {risk}")
    lines.append("")
    lines.append(f"**合规**:{plan.compliance_notes}")
    lines.append("")

    lines.append("<details><summary>计算明细(可追溯)</summary>")
    lines.append("")
    value = score.value
    lines.append(
        f"- 价值 V = 效率收益 {_money(value.efficiency)} + 收入收益 {_money(value.revenue)} "
        f"+ 风险降低收益 {_money(value.risk)} = **{_money(value.total)} 元/年**"
    )
    lines.append(
        f"  - 流程 FTE {value.detail.get('process_fte')} × 节点耗时占比 "
        f"{_pct(value.detail.get('hours_share', 0))} = 本节点 FTE "
        f"{value.detail.get('node_fte')};可自动化系数 {value.detail.get('automation_coef')};"
        f"年人力成本 {_money(value.detail.get('human_cost_annual_per_fte', 0))} 元"
    )
    lines.append(
        f"  - 收益区间:悲观 {_money(score.roi.benefit_pessimistic)} / 中性 "
        f"{_money(score.roi.benefit_neutral)} / 乐观 {_money(score.roi.benefit_optimistic)} 元"
    )
    lines.append(
        f"- 可行性 F = {score.feasibility.data_availability:.3f} × "
        f"{score.feasibility.standardization:.3f} × {score.feasibility.system_access:.3f} × "
        f"{score.feasibility.org_willingness:.3f} = **{score.feasibility.total:.4f}**"
    )
    lines.append(
        f"- 成本 C = 建设 {_money(score.cost.build)} + 运行 {_money(score.cost.run)} + "
        f"维护 {_money(score.cost.maintain)} = **{_money(score.cost.total)} 元(首年)**"
    )
    lines.append(
        f"  - 集成方式 {score.cost.detail.get('integration_mode')},建设人日 "
        f"{score.cost.detail.get('build_days')}(含数据治理 "
        f"{score.cost.detail.get('data_governance_person_days')} 人日),"
        f"年调用 {score.cost.detail.get('annual_calls'):,.0f} 次"
    )
    lines.append(
        f"- 风险 R = 幻觉影响面 {score.risk.hallucination_impact:g} × 合规等级 "
        f"{score.risk.compliance_level:g} × 责任不清晰因子 "
        f"{score.risk.clarity_factor:g}(由责任清晰度 "
        f"{score.risk.responsibility_clarity:g} 取反)= {score.risk.raw:g},"
        f"几何平均归一化后 **{score.risk.total:.4f}**"
    )
    lines.append("")
    if score.sensitivity:
        lines.append("**敏感性分析(哪一项假设最影响结论)**")
        lines.append("")
        lines.append("| 假设 | 优先级摆动幅度 | 说明 |")
        lines.append("| --- | --- | --- |")
        for item in score.sensitivity:
            lines.append(
                f"| {item.assumption} | ±{item.swing_pct:.1f}% | {item.note} |"
            )
        lines.append("")
    lines.append("</details>")
    lines.append("")
    return lines


def _critique_section(result: RunResult) -> list[str]:
    lines = ["## 5. 自检结果(docs/06 §5 的 10 条清单)", ""]
    if not result.critiques:
        lines.append("没有生成任何方案。")
        lines.append("")
        return lines
    for plan, critique in zip(result.plans, result.critiques):
        status = "通过" if critique.passed else "未通过"
        lines.append(f"### {plan.plan_id} — {status}")
        lines.append("")
        lines.append("| # | 检查项 | 结果 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        for check in critique.checks:
            mark = "通过" if check.passed else "**未通过**"
            lines.append(f"| {check.no} | {check.name} | {mark} | {check.detail} |")
        lines.append("")
        if critique.rework_instructions:
            lines.append("**打回修改指令**")
            lines.append("")
            for instruction in critique.rework_instructions:
                lines.append(f"- {instruction}")
            lines.append("")
    return lines


def render_report(result: RunResult) -> str:
    profile = result.profile
    lines = [
        f"# AI 落地契机报告 — {profile.company}",
        "",
        f"- 行业:{profile.industry}",
        f"- 规模:员工 {profile.scale.headcount} 人 / 营收 {_money(profile.scale.revenue)} / 网点 {profile.scale.sites}",
        f"- 画像节点数:{len(profile.nodes)},候选机会点:{len(result.all_opportunities)},"
        f"进入排序:{len(result.ranked)},输出方案:{len(result.plans)}",
        f"- 方案撰写方式:{'强模型撰写' if result.composer == 'llm' else '模板组装(未调用模型)'}",
        f"- 运行 ID:{result.run_id}",
        f"- 生成时间:{result.generated_at.isoformat()}",
        "",
    ]
    lines += _summary(result)
    lines += _opportunity_map(result)
    lines += _excluded(result)
    lines += ["## 4. 落地方案", ""]
    for index, plan in enumerate(result.plans, start=1):
        score = result.score(plan.opportunity_ref)
        lines += _plan_detail(plan, score, index)
    lines += _critique_section(result)

    if result.usage:
        lines += ["## 6. 模型用量与成本", ""]
        usage = result.usage
        lines.append(
            f"- 调用 {usage.get('calls', 0)} 次,prompt {usage.get('prompt_tokens', 0)} tokens,"
            f"completion {usage.get('completion_tokens', 0)} tokens"
            f"(其中推理 {usage.get('reasoning_tokens', 0)} tokens),累计耗时 {usage.get('seconds', 0)}s"
        )
        lines.append("")
        lines.append("| 用途 | 调用次数 | 重试次数 | prompt tokens | completion tokens |")
        lines.append("| --- | --- | --- | --- | --- |")
        for purpose, stats in usage.get("by_purpose", {}).items():
            lines.append(
                f"| {purpose} | {stats['calls']} | {stats['retries']} | "
                f"{stats['prompt_tokens']} | {stats['completion_tokens']} |"
            )
        lines.append("")
        lines.append(
            "> 参数中的单位价格是占位基准,不能用上面这些 token 数直接对外报价;"
            "需用真实账单校准 `data/knowledge/costs.json`。"
        )
        lines.append("")

    lines += ["## 7. 缺口与下一步", ""]
    if result.gaps:
        for gap in result.gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("- 本次运行未发现知识库或数据缺口。")
    lines.append("")
    lines.append(
        "- 桥接提醒:`outcome` 表必须从第一天开始记录(docs/07 §5),否则数据飞轮无法启动。"
    )
    lines.append("")
    return "\n".join(lines)
