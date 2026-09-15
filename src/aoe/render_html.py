"""S9b 渲染 —— 自包含 HTML 报告。

单文件、无外部依赖、离线可看:直接把 RunResult 渲染成一张可以双击打开的页面,
给客户演示或内部评审用。所有数字都来自 RunResult,渲染层不做任何计算。
"""

from __future__ import annotations

from html import escape

from .models import Critique, Opportunity, Plan, Score
from .result import RunResult

CSS = """
:root{
  --bg:#f6f7f9; --panel:#ffffff; --ink:#1b1f24; --muted:#5f6b7a;
  --line:#e3e7ec; --brand:#1f6feb; --brand-soft:#eaf1fe;
  --ok:#1a7f45; --warn:#b35400; --bad:#c02b2b;
  --shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.1);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
a{color:var(--brand)}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 72px}
header.hero{background:linear-gradient(135deg,#123a7a,#1f6feb);color:#fff;padding:36px 0 30px}
header.hero .wrap{padding-bottom:0}
h1{margin:0 0 6px;font-size:26px;letter-spacing:.2px}
.hero .meta{opacity:.9;font-size:13.5px}
.hero .meta span{display:inline-block;margin-right:14px}
.chips{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}
.chip{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);
  border-radius:999px;padding:3px 11px;font-size:13px}
section{margin-top:28px}
h2{font-size:19px;margin:0 0 12px;padding-left:10px;border-left:4px solid var(--brand)}
h3{font-size:16px;margin:0 0 8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  box-shadow:var(--shadow);padding:18px 20px}
.grid{display:grid;gap:14px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(168px,1fr))}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  box-shadow:var(--shadow);padding:14px 16px}
.kpi .label{color:var(--muted);font-size:13px}
.kpi .value{font-size:23px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi .sub{color:var(--muted);font-size:12.5px;margin-top:2px}
.summary{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.rank-badge{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;
  border-radius:7px;background:var(--brand-soft);color:var(--brand);font-weight:650;
  font-size:13px;margin-right:8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}
th{background:#fafbfc;color:var(--muted);font-weight:600;font-size:13px;white-space:nowrap}
td.num,th.num{text-align:right}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12.5px;border:1px solid}
.pill.ok{color:var(--ok);border-color:#bfe3cc;background:#f1f9f4}
.pill.bad{color:var(--bad);border-color:#f2c9c9;background:#fdf3f3}
.pill.warn{color:var(--warn);border-color:#f0d5b0;background:#fdf7ef}
.bar{height:8px;border-radius:999px;background:#eef1f5;overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;background:var(--brand)}
details{border:1px solid var(--line);border-radius:10px;background:#fff;margin-top:10px}
details>summary{cursor:pointer;padding:11px 14px;font-weight:600;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"+";display:inline-block;width:16px;color:var(--brand);font-weight:700}
details[open]>summary::before{content:"\\2212"}
details .body{padding:2px 16px 16px}
.kv{display:grid;grid-template-columns:150px 1fr;gap:4px 12px;font-size:14px}
.kv dt{color:var(--muted)}
.kv dd{margin:0}
ul.tight{margin:6px 0 0;padding-left:20px}
ul.tight li{margin:2px 0}
.note{color:var(--muted);font-size:13px}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
input[type=search]{padding:7px 11px;border:1px solid var(--line);border-radius:9px;
  font-size:14px;min-width:230px;background:#fff}
button{cursor:pointer;border:1px solid var(--line);background:#fff;border-radius:9px;
  padding:7px 12px;font-size:14px}
button:hover{border-color:var(--brand);color:var(--brand)}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:760px){.two-col{grid-template-columns:1fr}.kv{grid-template-columns:1fr}}
footer{margin-top:36px;color:var(--muted);font-size:12.5px;border-top:1px solid var(--line);
  padding-top:16px}
.empty{color:var(--muted);font-size:14px}
"""

JS = """
const box = document.getElementById('q');
if (box) {
  box.addEventListener('input', () => {
    const kw = box.value.trim().toLowerCase();
    document.querySelectorAll('#map tbody tr').forEach(tr => {
      tr.hidden = kw && !tr.dataset.search.includes(kw);
    });
  });
}
const toggle = document.getElementById('toggle');
if (toggle) {
  toggle.addEventListener('click', () => {
    const list = [...document.querySelectorAll('#plans details, #map details')];
    const open = list.some(d => !d.open);
    list.forEach(d => { d.open = open; });
    toggle.textContent = open ? '收起全部明细' : '展开全部明细';
  });
}
"""


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
    check_ratio = f"{passed}/{len(result.critiques)}" if result.critiques else "-"
    cards = [
        ("候选机会点", str(len(result.all_opportunities)), "S3 生成,未过滤"),
        ("进入排序", str(len(result.ranked)), "通过规则与可行性门槛"),
        ("输出方案", str(len(result.plans)), f"撰写方式:{result.composer}"),
        ("自检通过", check_ratio, "S8 十项自检"),
        ("年收益合计", f"{_money(total_benefit)} 元", "中性口径,全部入排机会点"),
        ("首年成本合计", f"{_money(total_cost)} 元", "建设 + 运行 + 维护"),
    ]
    cells = "".join(
        f'<div class="kpi"><div class="label">{_e(label)}</div>'
        f'<div class="value">{_e(value)}</div><div class="sub">{_e(sub)}</div></div>'
        for label, value, sub in cards
    )
    return f'<div class="grid kpis">{cells}</div>'


def _summary(result: RunResult) -> str:
    if not result.ranked:
        return '<p class="empty">本次运行没有产出可排序的机会点。</p>'
    cards = []
    for index, score in enumerate(result.ranked[:3], start=1):
        opp = result.opportunity(score.opportunity_id)
        payback = (
            f"{score.roi.payback_years:.2f} 年" if score.roi.payback_years else "不可估算"
        )
        cards.append(
            '<div class="card">'
            f'<h3><span class="rank-badge">{index}</span>{_e(score.node)} · '
            f"{_e(opp.capability_class)}</h3>"
            f'<div class="kv"><dt>优先级</dt><dd>{score.priority:.4f}</dd>'
            f"<dt>年收益区间</dt><dd>{_money(score.roi.benefit_pessimistic)} ~ "
            f"{_money(score.roi.benefit_optimistic)} 元</dd>"
            f"<dt>回收周期</dt><dd>{_e(payback)}</dd></div>"
            f'<p class="note">{_e(opp.rationale)}</p>'
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
        f"<tr><th>{_e(name)}</th><td>{_e(shown)}</td><td class=\"note\">{_e(detail)}</td></tr>"
        for name, shown, detail in rows
    )
    sensitivity_html = (
        f'<div class="note" style="margin-top:10px">敏感性分析<ul class="tight">'
        f"{sensitivity}</ul></div>"
        if sensitivity
        else ""
    )
    return f'<table>{body}</table>{sensitivity_html}'


def _map(result: RunResult) -> str:
    if not result.ranked:
        return '<p class="empty">没有进入排序的机会点。</p>'
    rows = []
    for index, score in enumerate(result.ranked, start=1):
        opp = result.opportunity(score.opportunity_id)
        payback = (
            f"{score.roi.payback_years:.2f}" if score.roi.payback_years else "—"
        )
        search = _e(
            f"{score.node} {opp.capability_class} {opp.capability} {opp.domain}".lower()
        )
        rows.append(
            f'<tr data-search="{search}">'
            f'<td>{index}</td><td>{_e(score.node)}</td>'
            f'<td>{_e(opp.capability_class)}<div class="note">{_e(opp.capability)}</div></td>'
            f'<td class="num">{score.priority:.4f}</td>'
            f'<td class="num">{_money(score.value.total)}</td>'
            f'<td class="num">{score.feasibility.total:.2f}</td>'
            f'<td class="num">{_money(score.cost.total)}</td>'
            f'<td class="num">{score.risk.total:.2f}</td>'
            f'<td class="num">{payback}</td>'
            f"<td>{_e(_pct(opp.automation_coef))}</td></tr>"
        )
        rows.append(
            '<tr><td colspan="10" style="padding:0 0 10px 10px">'
            f"<details><summary>第 {index} 名 · {_e(score.node)} 计算明细</summary>"
            f'<div class="body">{_score_detail(score)}</div></details></td></tr>'
        )
    head = (
        "<tr><th>#</th><th>流程节点</th><th>能力</th><th class='num'>优先级</th>"
        "<th class='num'>价值 V(元/年)</th><th class='num'>可行性 F</th>"
        "<th class='num'>首年成本 C(元)</th><th class='num'>风险 R</th>"
        "<th class='num'>回收周期(年)</th><th>可自动化</th></tr>"
    )
    return (
        '<div class="toolbar">'
        '<input id="q" type="search" placeholder="按节点 / 能力 / 业务域筛选…">'
        '<button id="toggle" type="button">展开全部明细</button>'
        "</div>"
        f"<div class=\"card\"><table id=\"map\"><thead>{head}</thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _excluded(result: RunResult) -> str:
    excluded = [
        opp
        for opp in result.all_opportunities
        if opp.status.value != "candidate"
    ]
    if not excluded:
        return '<p class="empty">本次运行没有命中排除规则或合规红线的机会点。</p>'
    rows = "".join(
        f"<tr><td>{_e(opp.node)}</td><td>{_e(opp.capability_class)}</td>"
        f"<td>{_e(opp.status.value)}</td><td>{_e(opp.status_reason)}</td></tr>"
        for opp in excluded
    )
    head = "<tr><th>流程节点</th><th>能力</th><th>判定</th><th>原因</th></tr>"
    return f'<div class="card"><table><thead>{head}</thead><tbody>{rows}</tbody></table></div>'


def _plan_card(
    plan: Plan,
    score: Score | None,
    opportunity: Opportunity | None,
    critique: Critique | None,
) -> str:
    milestones = "".join(
        f"<li>{_e(step.name)} — {step.duration_days} 天</li>" for step in plan.milestones
    )
    metrics = "".join(
        f"<tr><td>{_e(metric.name)}</td><td>{_e(metric.target)}</td>"
        f"<td class=\"note\">{_e(metric.how_to_measure)}</td></tr>"
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
            f'<tr><td>{check.no}</td><td>{_e(check.name)}</td>'
            f'<td><span class="pill {"ok" if check.passed else "bad"}">'
            f'{"通过" if check.passed else "未通过"}</span></td>'
            f'<td class="note">{_e(check.detail)}</td></tr>'
            for check in critique.checks
        )
        rework = "".join(
            f"<li>{_e(item)}</li>" for item in critique.rework_instructions
        )
        checks = (
            "<details><summary>S8 自检明细</summary><div class=\"body\">"
            f"<table><thead><tr><th>#</th><th>检查项</th><th>结论</th>"
            f"<th>说明</th></tr></thead><tbody>{items}</tbody></table>"
            + (f'<div class="note" style="margin-top:10px">打回指令<ul class="tight">'
               f"{rework}</ul></div>" if rework else "")
            + "</div></details>"
        )
    head_pills = ""
    if score:
        head_pills = (
            f'<span class="chip" style="background:var(--brand-soft);color:var(--brand);'
            f'border-color:#cfe0ff">优先级 {score.priority:.4f}</span>'
            f'<span class="chip">回收周期 '
            f'{(f"{score.roi.payback_years:.2f} 年" if score.roi.payback_years else "不可估算")}'
            f"</span>"
        )
    capability = (
        f"{_e(opportunity.capability_class)} / {_e(opportunity.capability)}"
        if opportunity
        else "未关联到候选机会点"
    )
    return (
        '<div class="card" style="margin-top:14px">'
        f"<h3>{_e(plan.plan_id)}</h3>"
        f'<div class="note" style="margin-bottom:10px">能力:{capability} '
        f"{head_pills}</div>"
        '<div class="two-col">'
        f'<div><h4>改造前</h4><p>{_e(plan.before_process)}</p></div>'
        f'<div><h4>改造后</h4><p>{_e(plan.after_process)}</p></div>'
        "</div>"
        f"<h4>数据方案</h4><p>{_e(plan.data_plan)}</p>"
        f"<h4>Agent 设计</h4><p>{_e(plan.agent_design)}</p>"
        f"<h4>集成方式</h4><p>{_e(plan.integration)}</p>"
        f"<h4>合规注意事项</h4><p>{_e(plan.compliance_notes)}</p>"
        f"<h4>里程碑</h4><ul class='tight'>{milestones}</ul>"
        f"<h4>验收指标</h4><table><thead><tr><th>指标</th><th>目标</th>"
        f"<th>测量口径</th></tr></thead><tbody>{metrics}</tbody></table>"
        f"<h4>关键假设</h4><table><thead><tr><th>假设</th><th>取值</th><th>来源</th>"
        f"<th>置信度</th></tr></thead><tbody>{assumptions}</tbody></table>"
        f"<h4>风险提示</h4><ul class='tight'>{risks}</ul>"
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
                (
                    o
                    for o in result.all_opportunities
                    if o.id == plan.opportunity_ref
                ),
                None,
            ),
            critiques.get(plan.plan_id),
        )
        for plan in result.plans
    )
    if not plans:
        plans = '<p class="empty">本次运行没有产出方案。</p>'

    gaps = "".join(f"<li>{_e(gap)}</li>" for gap in result.gaps)
    gaps_html = (
        f'<section><h2>缺口与待补数据</h2><div class="card"><ul class="tight">'
        f"{gaps}</ul></div></section>"
        if gaps
        else ""
    )
    unmatched = "".join(f"<li>{_e(node)}</li>" for node in result.unmatched_nodes)
    unmatched_html = (
        f'<section><h2>未匹配到能力的机会点节点</h2><div class="card">'
        f'<ul class="tight">{unmatched}</ul></div></section>'
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
            "<section><h2>模型用量</h2><div class=\"card\"><table><thead><tr>"
            "<th>环节</th><th class='num'>调用</th><th class='num'>重试</th>"
            "<th class='num'>completion tokens</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            f"<p class=\"note\">合计 prompt {result.usage['prompt_tokens']:,} / "
            f"completion {result.usage['completion_tokens']:,}"
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
<header class="hero"><div class="wrap">
<h1>{_e(profile.company)} · AI 落地机会诊断</h1>
<div class="meta"><span>生成时间 {_e(result.generated_at.strftime('%Y-%m-%d %H:%M UTC'))}</span>
<span>报告版本 MVP(模板 + 大模型双路径)</span></div>
<div class="chips">{chips}</div>
</div></header>
<div class="wrap">
<section><h2>关键指标</h2>{_kpis(result)}</section>
<section><h2>结论摘要</h2>{_summary(result)}</section>
<section><h2>机会地图(按优先级排序)</h2>{_map(result)}</section>
<section><h2>排除清单</h2>{_excluded(result)}</section>
<section><h2>落地方案</h2>{plans}</section>
{gaps_html}
{unmatched_html}
{usage_html}
<section>
  <h2>阅读说明</h2>
  <div class="card">
    <ul class="tight">
      <li>所有金额与指标均由确定性代码计算,模型只负责组织文字,不参与算术。</li>
      <li>每条方案正文里的数字都经过数字登记表校验,未登记的数字会被自检打回。</li>
      <li>知识库中的成本与收益基准置信度为「中」,用于机会排序,<strong>不可直接作为对外报价依据</strong>。</li>
      <li>中间态 JSON 与逐条自检记录见 <code>work/runs/{_e(result.run_id)}/</code>。</li>
    </ul>
  </div>
</section>
<footer>
本报告由 AI 落地机会引擎自动生成 · 运行 ID {_e(result.run_id)} ·
共 {len(result.all_opportunities)} 个候选机会点、{len(result.plans)} 份方案。
</footer>
</div>
<script>{JS}</script>
</body>
</html>
"""
