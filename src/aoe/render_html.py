"""S9b 渲染 —— 自包含 HTML 报告。

单文件、无外部依赖、离线可看:直接把 RunResult 渲染成一张可以双击打开的页面,
给客户演示或内部评审用。所有数字都来自 RunResult,渲染层不做任何计算。
样式与本地界面共用 theme.CSS,避免两处各写一套。
"""

from __future__ import annotations

from html import escape

from .models import Critique, Opportunity, Plan, Score
from .result import RunResult
from .theme import CSS, REPORT_JS


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _kpis(result: RunResult) -> str:
    passed = sum(1 for item in result.critiques if item.passed)
    total_benefit = sum(score.roi.benefit_neutral for score in result.ranked)
    total_cost = sum(score.cost.total for score in result.ranked)
    check_ratio = f"{passed}/{len(result.critiques)}" if result.critiques else "—"
    cards = [
        ("候选机会点", str(len(result.all_opportunities)), "S3 生成,未过滤"),
        ("进入排序", str(len(result.ranked)), "通过规则与可行性门槛"),
        ("输出方案", str(len(result.plans)), f"撰写方式:{result.composer}"),
        ("自检通过", check_ratio, "S8 十项自检"),
        ("年收益合计", f"{_money(total_benefit)}", "元,中性口径"),
        ("首年成本合计", f"{_money(total_cost)}", "元,建设 + 运行 + 维护"),
    ]
    cells = "".join(
        f'<div class="kpi"><div class="label">{_e(label)}</div>'
        f'<div class="value">{_e(value)}</div><div class="sub">{_e(sub)}</div></div>'
        for label, value, sub in cards
    )
    return f'<div class="grid kpis">{cells}</div>'


def _summary(result: RunResult) -> str:
    if not result.ranked:
        return '<p class="muted">本次运行没有产出可排序的机会点。</p>'
    cards = []
    for index, score in enumerate(result.ranked[:3], start=1):
        opp = result.opportunity(score.opportunity_id)
        payback = (
            f"{score.roi.payback_years:.2f} 年" if score.roi.payback_years else "不可估算"
        )
        cards.append(
            '<div class="card">'
            f'<h3><span class="rank-badge">{index}</span> {_e(score.node)}</h3>'
            f'<p class="note">{_e(opp.capability_class)} · {_e(opp.capability)}</p>'
            f'<div class="kv" style="margin-top:10px">'
            f"<dt>优先级</dt><dd><strong>{score.priority:.4f}</strong></dd>"
            f"<dt>年收益区间</dt><dd>{_money(score.roi.benefit_pessimistic)} ~ "
            f"{_money(score.roi.benefit_optimistic)} 元</dd>"
            f"<dt>回收周期</dt><dd>{_e(payback)}</dd></div>"
            f'<p class="note" style="margin-top:10px">{_e(opp.rationale)}</p>'
            "</div>"
        )
    return f'<div class="grid summary">{"".join(cards)}</div>'


def _score_detail(score: Score) -> str:
    value, feas, cost, risk = score.value, score.feasibility, score.cost, score.risk
    sensitivity = "".join(
        f"<li>{_e(item.assumption)}:摆动 {_pct(item.swing_pct)}"
        f"{f'({_e(item.note)})' if item.note else ''}</li>"
        for item in score.sensitivity
    )
    rows = [
        ("价值 V", f"{_money(value.total)} 元/年",
         f"效率 {_money(value.efficiency)} + 收入 {_money(value.revenue)} + "
         f"风险降低 {_money(value.risk)}"),
        ("可行性 F", f"{feas.total:.2f}",
         f"数据 {feas.data_availability:.2f} × 标准化 {feas.standardization:.2f} × "
         f"系统 {feas.system_access:.2f} × 意愿 {feas.org_willingness:.2f}"),
        ("成本 C", f"{_money(cost.total)} 元",
         f"建设 {_money(cost.build)} + 运行 {_money(cost.run)} + "
         f"维护 {_money(cost.maintain)}"),
        ("风险 R", f"{risk.total:.4f}",
         f"幻觉影响面 {risk.hallucination_impact:g} × 合规等级 {risk.compliance_level:g} × "
         f"责任不清晰因子 {risk.clarity_factor:g}(责任清晰度 "
         f"{risk.responsibility_clarity:g} 取反),几何平均归一化"),
        ("优先级", f"{score.priority:.4f}", "= (V × F) / (C × R)"),
    ]
    body = "".join(
        f"<tr><th>{_e(name)}</th><td><strong>{_e(shown)}</strong></td>"
        f'<td class="note">{_e(detail)}</td></tr>'
        for name, shown, detail in rows
    )
    sensitivity_html = (
        '<p class="note" style="margin-top:12px">敏感性分析</p>'
        f'<ul class="tight">{sensitivity}</ul>'
        if sensitivity
        else ""
    )
    return f"<table>{body}</table>{sensitivity_html}"


def _map(result: RunResult) -> str:
    if not result.ranked:
        return '<p class="muted">没有进入排序的机会点。</p>'
    rows = []
    for index, score in enumerate(result.ranked, start=1):
        opp = result.opportunity(score.opportunity_id)
        payback = f"{score.roi.payback_years:.2f}" if score.roi.payback_years else "—"
        search = _e(
            f"{score.node} {opp.capability_class} {opp.capability} {opp.domain}".lower()
        )
        rows.append(
            f'<tr data-search="{search}"><td class="num">{index}</td>'
            f"<td>{_e(score.node)}</td>"
            f'<td>{_e(opp.capability_class)}<div class="note">{_e(opp.capability)}</div></td>'
            f'<td class="num"><strong>{score.priority:.4f}</strong></td>'
            f'<td class="num">{_money(score.value.total)}</td>'
            f'<td class="num">{score.feasibility.total:.2f}</td>'
            f'<td class="num">{_money(score.cost.total)}</td>'
            f'<td class="num">{score.risk.total:.2f}</td>'
            f'<td class="num">{payback}</td>'
            f'<td class="num">{_e(_pct(opp.automation_coef))}</td></tr>'
        )
        rows.append(
            '<tr data-search=""><td colspan="10" style="padding:0 0 12px 12px;'
            'border-bottom:1px solid var(--line-2)">'
            f"<details><summary>第 {index} 名 · {_e(score.node)} 计算明细</summary>"
            f'<div class="body">{_score_detail(score)}</div></details></td></tr>'
        )
    head = (
        '<tr><th class="num">#</th><th>流程节点</th><th>能力</th>'
        '<th class="num">优先级</th><th class="num">价值 V(元/年)</th>'
        '<th class="num">可行性 F</th><th class="num">首年成本 C(元)</th>'
        '<th class="num">风险 R</th><th class="num">回收周期(年)</th>'
        "<th>可自动化</th></tr>"
    )
    return (
        '<div class="toolbar">'
        '<input id="q" type="search" placeholder="按节点 / 能力 / 业务域筛选 …">'
        '<button id="toggle" class="btn btn-sm" type="button">展开全部明细</button>'
        "</div>"
        f'<div class="card" style="padding:6px 10px"><table id="map">'
        f"<thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _excluded(result: RunResult) -> str:
    excluded = [opp for opp in result.all_opportunities if opp.status.value != "candidate"]
    if not excluded:
        return '<div class="card"><p class="muted" style="margin:0">'\
               "本次运行没有命中排除规则或合规红线的机会点。</p></div>"
    rows = "".join(
        f"<tr><td>{_e(opp.node)}</td><td>{_e(opp.capability_class)}</td>"
        f'<td><span class="pill warn">{_e(opp.status.value)}</span></td>'
        f'<td class="note">{_e(opp.status_reason)}</td></tr>'
        for opp in excluded
    )
    head = "<tr><th>流程节点</th><th>能力</th><th>判定</th><th>原因</th></tr>"
    return (
        f'<div class="card" style="padding:6px 10px"><table><thead>{head}</thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def _plan_card(
    plan: Plan,
    score: Score | None,
    opportunity: Opportunity | None,
    critique: Critique | None,
) -> str:
    milestones = "".join(
        f'<span class="node">{_e(step.name)} · {step.duration_days} 天</span>'
        for step in plan.milestones
    )
    metrics = "".join(
        f"<tr><td>{_e(metric.name)}</td><td>{_e(metric.target)}</td>"
        f'<td class="note">{_e(metric.how_to_measure)}</td></tr>'
        for metric in plan.acceptance_metrics
    )
    assumptions = "".join(
        f"<tr><td>{_e(item.name)}</td><td>{_e(item.value)}</td>"
        f"<td>{_e(item.source.value)}</td><td>{_e(item.confidence.value)}</td></tr>"
        for item in plan.assumptions
    )
    risks = "".join(f"<li>{_e(risk)}</li>" for risk in plan.risks)
    checks = ""
    if critique:
        items = "".join(
            f'<tr><td class="num">{check.no}</td><td>{_e(check.name)}</td>'
            f'<td><span class="pill {"ok" if check.passed else "bad"}">'
            f'{"通过" if check.passed else "未通过"}</span></td>'
            f'<td class="note">{_e(check.detail)}</td></tr>'
            for check in critique.checks
        )
        rework = "".join(f"<li>{_e(item)}</li>" for item in critique.rework_instructions)
        checks = (
            f'<details><summary>S8 十项自检'
            f'({"全部通过" if critique.passed else "存在未通过项"})</summary>'
            '<div class="body"><table><thead><tr><th class="num">#</th><th>检查项</th>'
            f"<th>结论</th><th>说明</th></tr></thead><tbody>{items}</tbody></table>"
            + (
                '<p class="note" style="margin-top:12px">打回指令</p>'
                f'<ul class="tight">{rework}</ul>'
                if rework
                else ""
            )
            + "</div></details>"
        )
    head_pills = ""
    if score:
        payback = (
            f"{score.roi.payback_years:.2f} 年" if score.roi.payback_years else "不可估算"
        )
        head_pills = (
            f'<span class="pill info">优先级 {score.priority:.4f}</span> '
            f'<span class="pill plain">回收周期 {payback}</span>'
        )
    capability = (
        f"{_e(opportunity.capability_class)} / {_e(opportunity.capability)}"
        if opportunity
        else "未关联到候选机会点"
    )
    return (
        '<div class="card" style="margin-top:14px">'
        f"<h3>{_e(plan.plan_id)}</h3>"
        f'<p class="note" style="margin:6px 0 14px">能力:{capability} {head_pills}</p>'
        '<div class="grid cols-2">'
        f'<div><h4 style="margin-top:0">改造前</h4><p>{_e(plan.before_process)}</p></div>'
        f'<div><h4 style="margin-top:0">改造后</h4><p>{_e(plan.after_process)}</p></div>'
        "</div>"
        f"<h4>数据方案</h4><p>{_e(plan.data_plan)}</p>"
        f"<h4>Agent 设计</h4><p>{_e(plan.agent_design)}</p>"
        f"<h4>集成方式</h4><p>{_e(plan.integration)}</p>"
        f"<h4>合规注意事项</h4><p>{_e(plan.compliance_notes)}</p>"
        f'<h4>里程碑</h4><div class="timeline">{milestones}</div>'
        f"<h4>验收指标</h4><table><thead><tr><th>指标</th><th>目标</th>"
        f"<th>测量口径</th></tr></thead><tbody>{metrics}</tbody></table>"
        f"<h4>关键假设</h4><table><thead><tr><th>假设</th><th>取值</th><th>来源</th>"
        f"<th>置信度</th></tr></thead><tbody>{assumptions}</tbody></table>"
        f'<h4>风险提示</h4><ul class="tight">{risks}</ul>'
        f"{checks}"
        "</div>"
    )


def render_html(result: RunResult) -> str:
    """把一次运行渲染成自包含 HTML(docs/05 §7:结论必须能带着明细一起给人看)。"""
    critiques = {item.plan_id: item for item in result.critiques}
    plans = "".join(
        _plan_card(
            plan,
            next(
                (s for s in result.ranked if s.opportunity_id == plan.opportunity_ref),
                None,
            ),
            next(
                (o for o in result.all_opportunities if o.id == plan.opportunity_ref),
                None,
            ),
            critiques.get(plan.plan_id),
        )
        for plan in result.plans
    )
    if not plans:
        plans = '<div class="card"><p class="muted" style="margin:0">本次运行没有产出方案。</p></div>'

    gaps = "".join(f"<li>{_e(gap)}</li>" for gap in result.gaps)
    gaps_html = (
        '<section><div class="section-head"><h2>缺口与待补数据</h2></div>'
        f'<div class="card"><ul class="tight">{gaps}</ul></div></section>'
        if gaps
        else ""
    )
    unmatched = "".join(f"<li>{_e(node)}</li>" for node in result.unmatched_nodes)
    unmatched_html = (
        '<section><div class="section-head"><h2>未匹配到能力的节点</h2></div>'
        f'<div class="card"><ul class="tight">{unmatched}</ul></div></section>'
        if unmatched
        else ""
    )

    usage_html = ""
    if result.usage:
        by_purpose = result.usage.get("by_purpose", {})
        rows = "".join(
            f"<tr><td>{_e(purpose)}</td><td class='num'>{stats['calls']}</td>"
            f"<td class='num'>{stats['retries']}</td>"
            f"<td class='num'>{stats['completion_tokens']:,}</td></tr>"
            for purpose, stats in by_purpose.items()
        )
        usage_html = (
            '<section><div class="section-head"><h2>模型用量</h2></div>'
            '<div class="card" style="padding:6px 10px"><table><thead><tr>'
            "<th>环节</th><th class='num'>调用</th><th class='num'>重试</th>"
            "<th class='num'>completion tokens</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            f'<p class="note" style="padding:0 6px 8px">合计 prompt '
            f"{result.usage['prompt_tokens']:,} / completion "
            f"{result.usage['completion_tokens']:,}"
            f"(含推理 {result.usage['reasoning_tokens']:,})</p></div></section>"
        )

    profile = result.profile
    chips = "".join(
        f'<span class="chip">{_e(text)}</span>'
        for text in (
            f"行业:{profile.industry}",
            f"流程节点 {len(profile.nodes)} 个",
            f"运行 ID:{result.run_id}",
            f"撰写方式:{result.composer}",
        )
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(profile.company)} · AI 落地机会引擎</title>
<style>{CSS}</style>
</head>
<body>
<header class="hero"><div class="wrap" style="padding-bottom:0">
<h1>{_e(profile.company)} · AI 落地机会诊断</h1>
<div class="meta">
<span>生成时间 {_e(result.generated_at.strftime('%Y-%m-%d %H:%M UTC'))}</span>
<span>撰写方式 {_e(result.composer)}</span>
</div>
<div class="chips">{chips}</div>
</div></header>
<div class="wrap">
<section style="margin-top:22px"><div class="section-head"><h2>关键指标</h2></div>
{_kpis(result)}</section>
<section><div class="section-head"><h2>结论摘要</h2></div>{_summary(result)}</section>
<section><div class="section-head"><h2>机会地图</h2>
<span class="note">按优先级排序,点开每行可看 V / F / C / R 的计算明细</span></div>
{_map(result)}</section>
<section><div class="section-head"><h2>排除清单</h2></div>{_excluded(result)}</section>
<section><div class="section-head"><h2>落地方案</h2></div>{plans}</section>
{gaps_html}
{unmatched_html}
{usage_html}
<section><div class="section-head"><h2>阅读说明</h2></div>
<div class="card"><ul class="tight">
<li>所有金额与指标均由确定性代码计算,模型只负责组织文字,不参与算术。</li>
<li>方案正文里的每个数字都经过数字登记表校验,未登记的数字会被自检打回。</li>
<li>知识库中的成本与收益基准置信度为「中」,用于机会排序,<strong>不可直接作为对外报价依据</strong>。</li>
<li>中间态 JSON 与逐条自检记录见 <span class="mono">work/runs/{_e(result.run_id)}/</span>。</li>
</ul></div></section>
<footer>本报告由 AI 落地机会引擎自动生成 · 运行 ID
<span class="mono">{_e(result.run_id)}</span> ·
共 {len(result.all_opportunities)} 个候选机会点、{len(result.plans)} 份方案。</footer>
</div>
<script>{REPORT_JS}</script>
</body>
</html>
"""
