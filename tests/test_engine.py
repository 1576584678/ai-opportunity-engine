"""确定性内核的单元测试 —— 这些都是「不能交给模型算」的部分。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aoe.knowledge import load_knowledge
from aoe.models import (
    BusinessProfile,
    Confidence,
    OpportunityStatus,
    ProcessNode,
    Source,
    Sourced,
)
from aoe.pipeline import run_pipeline
from aoe.profiles import load_profile
from aoe.stages.candidates import generate_candidates, select_integration_mode
from aoe.stages.composer import NumberRegistry, compose_plan
from aoe.stages.critic import (
    check_1_bound_to_node,
    check_4_integration,
    check_5_metrics,
    check_8_no_fabricated_numbers,
)
from aoe.stages.rules import apply_rules
from aoe.stages.scoring import (
    automation_coefficient,
    compute_priority,
    score_opportunity,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO = REPO_ROOT / "data/profiles/retail_ecommerce_demo.json"

KB = load_knowledge()


@pytest.fixture(scope="module")
def profile() -> BusinessProfile:
    return load_profile(DEMO)


@pytest.fixture(scope="module")
def result(profile: BusinessProfile):
    return run_pipeline(profile=profile, kb=KB, top_n=6)


# --------------------------------------------------------------------------- #
# §4 可自动化系数
# --------------------------------------------------------------------------- #


def _node(**kwargs) -> ProcessNode:
    base = dict(name="测试节点", domain="测试域", hours_per_week=40, volume_per_month=100)
    base.update(kwargs)
    return ProcessNode(**base)


def test_coef_uses_documented_base_value():
    coef, detail = automation_coefficient(_node(), KB.capability("信息抽取"))
    assert coef == pytest.approx(0.8)
    assert detail["steps"][0]["value"] == pytest.approx(0.8)


def test_coef_applies_documented_multipliers_in_order():
    node = _node(requires_human_judgement=True, knowledge_intensive=True)
    coef, detail = automation_coefficient(node, KB.capability("信息抽取"))
    # 0.8 × 0.7 × 0.7 = 0.392
    assert coef == pytest.approx(0.392)
    assert [s["factor"] for s in detail["steps"] if s["factor"]] == [0.7, 0.7]


def test_coef_is_capped_by_capability_class_ceiling():
    node = _node(physical_action=False)
    coef, _ = automation_coefficient(node, KB.capability("物理动作"))
    assert coef <= KB.capability("物理动作").coef_max


def test_coef_never_raised_back_up_to_class_floor():
    """保守原则:下游乘数只会下调,不允许因类别下限而抬高。"""
    node = _node(knowledge_intensive=True)
    coef, detail = automation_coefficient(node, KB.capability("内容生成"))
    assert coef == pytest.approx(0.56)
    assert coef < KB.capability("内容生成").coef_range[0]
    assert detail["below_class_floor"] is True


# --------------------------------------------------------------------------- #
# §5 评分
# --------------------------------------------------------------------------- #


def test_priority_is_value_times_feasibility_over_cost_times_risk(
    profile: BusinessProfile,
):
    candidates, _ = generate_candidates(profile, KB)
    opp = next(o for o in candidates if o.node == "售后咨询应答")
    score, _ = score_opportunity(
        next(n for n in profile.nodes if n.name == opp.node), profile, opp, KB
    )
    expected = (
        score.value.total * score.feasibility.total / (score.cost.total * score.risk.total)
    )
    assert score.priority == pytest.approx(expected, rel=1e-6)


def test_risk_is_normalized_to_one_to_five_scale(profile: BusinessProfile):
    """R 必须归一化回 1-5,否则可达 125,会压过成本项让排序失去意义(docs/04 §5.4)。"""
    candidates, _ = generate_candidates(profile, KB)
    opp = next(o for o in candidates if o.node == "订单异常处理")
    node = next(n for n in profile.nodes if n.name == opp.node)
    score, _ = score_opportunity(node, profile, opp, KB)
    assert 1.0 <= score.risk.total <= 5.0
    assert score.risk.raw == pytest.approx(
        score.risk.hallucination_impact
        * score.risk.compliance_level
        * score.risk.clarity_factor
    )
    assert score.risk.clarity_factor == pytest.approx(6 - score.risk.responsibility_clarity)


def test_clearer_responsibility_lowers_risk(profile: BusinessProfile):
    """责任归属越清晰,风险 R 越小——因子必须取反向,直乘会把语义做反。"""
    from aoe.stages.scoring import compute_risk

    node = next(n for n in profile.nodes if n.name == "订单异常处理")
    clear = node.model_copy(
        update={"role": "客服主管", "requires_human_judgement": True}
    )
    vague = node.model_copy(update={"role": None, "requires_human_judgement": False})
    assert compute_risk(clear, profile, KB).total < compute_risk(vague, profile, KB).total


def test_value_scales_linearly_with_automation_coefficient(profile: BusinessProfile):
    from aoe.stages.scoring import compute_value

    node = next(n for n in profile.nodes if n.name == "售后咨询应答")
    low, _ = compute_value(node, profile, 0.4)
    high, _ = compute_value(node, profile, 0.8)
    assert high.efficiency == pytest.approx(low.efficiency * 2, rel=1e-6)


def test_feasibility_lists_blocking_factor_below_threshold(profile: BusinessProfile):
    from aoe.stages.scoring import compute_feasibility

    node = next(n for n in profile.nodes if n.name == "生产设备故障预测")
    feasibility = compute_feasibility(node, profile, KB)
    assert "数据可得性" in feasibility.detail["blocked_by"]


def test_roi_reports_intervals_not_single_point(profile: BusinessProfile):
    candidates, _ = generate_candidates(profile, KB)
    opp = next(o for o in candidates if o.node == "售后咨询应答")
    node = next(n for n in profile.nodes if n.name == opp.node)
    score, _ = score_opportunity(node, profile, opp, KB)
    roi = score.roi
    assert roi.benefit_pessimistic < roi.benefit_neutral < roi.benefit_optimistic


def test_sensitivity_is_sorted_by_swing(profile: BusinessProfile):
    candidates, _ = generate_candidates(profile, KB)
    opp = next(o for o in candidates if o.node == "售后咨询应答")
    node = next(n for n in profile.nodes if n.name == opp.node)
    score, _ = score_opportunity(node, profile, opp, KB)
    swings = [abs(item.swing_pct) for item in score.sensitivity]
    assert swings == sorted(swings, reverse=True)


def test_priority_guards_against_zero_cost():
    from aoe.models import CostBreakdown, FeasibilityBreakdown, RiskBreakdown, ValueBreakdown

    value = ValueBreakdown(efficiency=1, revenue=0, risk=0, total=1)
    feasibility = FeasibilityBreakdown(
        data_availability=1,
        standardization=1,
        system_access=1,
        org_willingness=1,
        total=1,
    )
    cost = CostBreakdown(build=0, run=0, maintain=0, total=0)
    risk = RiskBreakdown(
        hallucination_impact=1,
        compliance_level=1,
        responsibility_clarity=1,
        clarity_factor=5,
        raw=1,
        total=1,
    )
    assert compute_priority(value, feasibility, cost, risk) == 0.0


# --------------------------------------------------------------------------- #
# S3 / S4 候选与规则
# --------------------------------------------------------------------------- #


def test_integration_mode_follows_system_capabilities(profile: BusinessProfile):
    nodes = {n.name: n for n in profile.nodes}
    assert select_integration_mode(nodes["售后咨询应答"], profile).value == "api"
    assert select_integration_mode(nodes["送货单与入库单录入"], profile).value == "rpa"
    assert select_integration_mode(nodes["供应商临时议价"], profile).value == "standalone"


def test_candidate_generation_is_exhaustive_over_nodes(profile: BusinessProfile):
    candidates, unmatched = generate_candidates(profile, KB)
    assert candidates, "应当产出候选机会点"
    assert set(unmatched).issubset({n.name for n in profile.nodes})
    # 每个候选都要能追到真实节点
    names = {n.name for n in profile.nodes}
    assert all(o.node in names for o in candidates)


def test_redline_opportunities_are_vetoed(profile: BusinessProfile):
    candidates, _ = generate_candidates(profile, KB)
    filtered = apply_rules(profile, candidates, KB)
    redlines = [o for o in filtered if o.status == OpportunityStatus.REDLINE]
    assert redlines, "门店客流与客群识别含人脸与个人信息,必须命中红线"
    assert all("CMP-PII-01" in o.status_reason for o in redlines)


def test_unstandardized_process_is_downgraded(profile: BusinessProfile):
    candidates, _ = generate_candidates(profile, KB)
    filtered = apply_rules(profile, candidates, KB)
    downgraded = [o for o in filtered if o.status == OpportunityStatus.DOWNGRADED]
    assert downgraded
    assert all("需先完成流程标准化" in o.status_reason for o in downgraded)


def test_unavailable_data_is_excluded(profile: BusinessProfile):
    candidates, _ = generate_candidates(profile, KB)
    filtered = apply_rules(profile, candidates, KB)
    infeasible = [o for o in filtered if o.status == OpportunityStatus.INFEASIBLE]
    assert infeasible
    assert all("设备传感器数据" in o.status_reason for o in infeasible)


# --------------------------------------------------------------------------- #
# S8 自检
# --------------------------------------------------------------------------- #


def test_number_registry_flags_fabricated_number():
    numbers = NumberRegistry()
    numbers.reg(100, "{:,.0f}")
    assert numbers.unregistered("成本约 100 元") == []
    assert numbers.unregistered("成本约 137 元") == ["137"]


def test_number_normalization_treats_variants_as_equal():
    numbers = NumberRegistry()
    numbers.reg(1.0, "{:g}")
    assert numbers.unregistered("第 1 项") == []
    assert numbers.unregistered("第 01 项") == []


def test_critic_check_8_catches_model_written_number(profile: BusinessProfile):
    """方案文本里出现的每个数字都必须经过登记表,否则一律判为疑似编造。"""
    candidates, _ = generate_candidates(profile, KB)
    filtered = apply_rules(profile, candidates, KB)
    opp = next(o for o in filtered if o.node == "售后咨询应答")
    node = next(n for n in profile.nodes if n.name == opp.node)
    score, assumptions = score_opportunity(node, profile, opp, KB)
    composed = compose_plan(
        node=node,
        profile=profile,
        opportunity=opp,
        score=score,
        assumptions=assumptions,
        kb=KB,
        plan_id="PLAN-TEST",
    )
    # 基线:真实组装的方案应当全部通过
    assert check_8_no_fabricated_numbers(composed.plan, composed.numbers).passed

    # 注入一个模型自由发挥的数字,必须被拦下
    tampered = composed.plan.model_copy(
        update={"agent_design": composed.plan.agent_design + "预计每年节省 99999 元。"}
    )
    check = check_8_no_fabricated_numbers(tampered, composed.numbers)
    assert not check.passed
    assert "99999" in check.detail


def test_template_composer_registers_customer_quoted_numbers(profile: BusinessProfile):
    """痛点原文里的数字是客户说的,不是模型编的,必须随客户文本一起登记。"""
    candidates, _ = generate_candidates(profile, KB)
    opp = next(o for o in candidates if o.node == "订单异常处理")
    node = next(n for n in profile.nodes if n.name == opp.node).model_copy(
        update={"pain_point": "去年漏发 12 单,单次赔付约 84 元"}
    )
    score, assumptions = score_opportunity(node, profile, opp, KB)
    composed = compose_plan(
        node=node,
        profile=profile,
        opportunity=opp,
        score=score,
        assumptions=assumptions,
        kb=KB,
        plan_id="PLAN-CLIENTTEXT",
    )
    assert "12" in composed.plan.before_process and "84" in composed.plan.before_process
    assert check_8_no_fabricated_numbers(composed.plan, composed.numbers).passed


def test_llm_fact_sheet_registers_every_text_number(profile: BusinessProfile):
    """事实表里所有文本(客户原文 + 知识库案例)的数字都必须已登记,否则模型一引用就被判编造。"""
    from aoe.stages.composer_llm import build_fact_sheet

    candidates, _ = generate_candidates(profile, KB)
    opp = next(o for o in candidates if o.node == "订单异常处理")
    node = next(n for n in profile.nodes if n.name == opp.node).model_copy(
        update={"pain_point": "去年漏发 12 单,单次赔付约 84 元"}
    )
    score, assumptions = score_opportunity(node, profile, opp, KB)
    numbers = NumberRegistry()
    fact = build_fact_sheet(node, profile, opp, score, KB, numbers, assumptions)

    leaks: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            leaks.extend(numbers.unregistered(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(fact)
    assert leaks == []


def test_critic_check_5_requires_measurement_method(result):
    plan = result.plans[0].model_copy(
        update={
            "acceptance_metrics": [
                m.model_copy(update={"how_to_measure": ""})
                for m in result.plans[0].acceptance_metrics
            ]
        }
    )
    assert not check_5_metrics(plan).passed


def test_critic_check_4_tolerates_spacing_variance(result):
    """模型常写「RPA自动化」,与标签「RPA 自动化」只差空白,不应判为缺失。"""
    plan = result.plans[0].model_copy(update={"integration": "集成方式是RPA自动化,涉及 ERP。"})
    assert check_4_integration(plan).passed
    missing = result.plans[0].model_copy(update={"integration": "通过改造现有流程完成对接。"})
    assert not check_4_integration(missing).passed


def test_critic_check_1_scans_whole_plan_for_node_name(result, profile: BusinessProfile):
    """节点名出现在正文任一字段即视为绑定成功,不限于 before/after。"""
    node = profile.nodes[0]
    plan = result.plans[0].model_copy(
        update={"before_process": "现状由人工处理。", "after_process": "改造后由 Agent 承担。"}
    )
    assert not check_1_bound_to_node(plan, node).passed
    plan = plan.model_copy(update={"agent_design": f"Agent 挂在节点「{node.name}」上。"})
    assert check_1_bound_to_node(plan, node).passed


# --------------------------------------------------------------------------- #
# 端到端
# --------------------------------------------------------------------------- #


def test_pipeline_passes_all_self_checks(result):
    assert result.critiques
    assert all(c.passed for c in result.critiques), [
        (c.plan_id, c.rework_instructions) for c in result.critiques if not c.passed
    ]


def test_pipeline_ranking_is_sorted(result):
    priorities = [s.priority for s in result.ranked]
    assert priorities == sorted(priorities, reverse=True)


def test_pipeline_merges_plans_per_node(result):
    nodes = [plan for plan in result.plans]
    refs = [plan.opportunity_ref for plan in nodes]
    assert len(refs) == len(set(refs))


def test_pipeline_rejects_profile_with_gaps(profile: BusinessProfile):
    broken = profile.model_copy(update={"missing": ["缺少客单价"]})
    with pytest.raises(ValueError, match="不得进入候选生成"):
        run_pipeline(profile=broken, kb=KB)


def test_all_sourced_values_carry_source_and_confidence(profile: BusinessProfile):
    assert isinstance(profile.params.human_cost_annual_per_fte, Sourced)
    assert profile.params.human_cost_annual_per_fte.source in Source
    assert profile.params.human_cost_annual_per_fte.confidence in Confidence


# --------------------------------------------------------------------------- #
# 知识库:docs/07 §4 冷启动标准
# --------------------------------------------------------------------------- #


def test_scenario_library_meets_cold_start_bar():
    """docs/07 §4:对任一目标行业都要能给出 10 个以上有行业针对性的机会点。"""
    industries = sorted({s.industry for s in KB.scenarios})
    counts = {i: sum(1 for s in KB.scenarios if s.industry == i) for i in industries}
    assert len(counts) >= 3
    assert all(count >= 10 for count in counts.values()), counts


def test_scenarios_reference_known_capability_classes():
    """场景库挂的能力类别必须真实存在于 L1 能力目录,否则会静默匹配不上。"""
    known = {c.name for c in KB.capabilities}
    unknown = sorted({s.capability_class for s in KB.scenarios} - known)
    assert unknown == []


def test_scenario_ids_are_unique():
    ids = [s.id for s in KB.scenarios]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# 运行目录:docs/05 §7 可回放
# --------------------------------------------------------------------------- #


def test_two_runs_in_same_second_go_to_different_dirs(
    profile: BusinessProfile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """同一秒内跑两次不能撞名,否则后一次会静默覆盖前一次的中间态。"""
    from datetime import datetime as real_datetime
    from datetime import timezone

    import aoe.pipeline as pipeline

    frozen = real_datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(pipeline, "_now_utc", lambda: frozen)

    first = run_pipeline(profile, KB, run_dir=tmp_path)
    second = run_pipeline(profile, KB, run_dir=tmp_path)

    assert first.run_id != second.run_id
    assert (tmp_path / first.run_id / "s3_candidates.json").exists()
    assert (tmp_path / second.run_id / "s3_candidates.json").exists()


def test_webapp_replays_persisted_run(profile: BusinessProfile, tmp_path: Path):
    """落盘产物必须能重建出等价的 RunResult,否则网页界面只能重跑、不能回看。"""
    from aoe.render_html import render_html
    from aoe.webapp import list_runs, load_result, render_landing

    result = run_pipeline(profile, KB, run_dir=tmp_path)

    runs = list_runs(tmp_path)
    assert [item["run_id"] for item in runs] == [result.run_id]
    assert runs[0]["plans"] == len(result.plans)
    assert runs[0]["passed"] == sum(1 for c in result.critiques if c.passed)

    replayed = load_result(tmp_path / result.run_id)
    assert replayed.profile.company == result.profile.company
    assert len(replayed.ranked) == len(result.ranked)
    assert [s.priority for s in replayed.ranked] == [s.priority for s in result.ranked]
    assert [p.plan_id for p in replayed.plans] == [p.plan_id for p in result.plans]
    assert [c.plan_id for c in replayed.critiques] == [
        c.plan_id for c in result.critiques
    ]

    landing = render_landing(tmp_path, Path("data/profiles"))
    assert result.run_id in landing
    assert "导入数据" in landing
    assert "画像库" in landing
    assert "关键指标" in render_html(replayed)


# --------------------------------------------------------------------------- #
# 数据导入:模板 / 校验 / 落盘
# --------------------------------------------------------------------------- #


def test_profile_template_matches_schema_keys():
    """模板字段必须和引擎 Schema 完全一致,否则用户填完才发现字段对不上。"""
    from aoe.webapp import build_profile_template

    demo = json.loads(
        (REPO_ROOT / "data/profiles/retail_ecommerce_demo.json").read_text(
            encoding="utf-8"
        )
    )
    template = build_profile_template(REPO_ROOT / "data/profiles")
    assert set(template) - {"_填写说明"} == set(demo)
    assert set(template["nodes"][0]) == set(demo["nodes"][0])
    assert set(template["data_sources"][0]) == set(demo["data_sources"][0])
    assert template["_填写说明"]


def test_import_rejects_bad_payload_without_writing(tmp_path: Path):
    from aoe.webapp import import_profile_json

    with pytest.raises(ValueError) as excinfo:
        import_profile_json("{ not json", profile_dir=tmp_path)
    assert "JSON 解析失败" in str(excinfo.value)

    with pytest.raises(ValueError):
        import_profile_json("[1, 2, 3]", profile_dir=tmp_path)

    # 模板占位内容还没替换完,必须被校验拦住,而不是写进画像库
    from aoe.webapp import build_profile_template

    with pytest.raises(ValueError):
        import_profile_json(
            json.dumps(build_profile_template(REPO_ROOT / "data/profiles")),
            profile_dir=tmp_path,
        )
    assert not (tmp_path / "uploads").exists()


def test_import_profile_roundtrip_strips_help_keys(tmp_path: Path):
    from aoe.webapp import import_profile_json, strip_help_keys

    payload = json.loads(
        (REPO_ROOT / "data/profiles/retail_ecommerce_demo.json").read_text(
            encoding="utf-8"
        )
    )
    payload["_填写说明"] = ["导入时应被忽略"]
    payload["nodes"][0]["_备注"] = "同样忽略"

    target, profile = import_profile_json(json.dumps(payload), profile_dir=tmp_path)
    assert target.parent.name == "uploads"
    assert profile.company == payload["company"]
    assert load_profile(target).company == profile.company
    assert strip_help_keys({"_a": 1, "b": {"_c": 2, "d": 3}}) == {"b": {"d": 3}}


def test_description_template_covers_intake_questions():
    from aoe.webapp import DESCRIPTION_TEMPLATE

    for section in ("企业基本情况", "业务流程", "数据情况", "目标与约束"):
        assert section in DESCRIPTION_TEMPLATE
    assert "每周投入多少工时" in DESCRIPTION_TEMPLATE


def _demo_payload() -> dict:
    return json.loads(
        (REPO_ROOT / "data/profiles/retail_ecommerce_demo.json").read_text(
            encoding="utf-8"
        )
    )


def test_builtin_profiles_resolve_their_source_description():
    """内置画像带着源描述,页面不能误报「没有源描述」而把重抽入口藏掉。"""
    from aoe.webapp import resolve_description_path

    root = REPO_ROOT / "data/profiles"
    for name in ("manufacturing_extracted.json", "manufacturing_complete.json"):
        assert resolve_description_path(name, root).exists(), name

    # 没有源描述时返回占位路径,由调用方用 .exists() 判断,不要抛错
    missing = resolve_description_path("retail_ecommerce_demo.json", root)
    assert not missing.exists()


def test_imported_profile_is_listed_and_named(tmp_path: Path):
    """回归:导入的画像落在 uploads/ 下,列表与路由都要能按相对路径找到它。"""
    from aoe.webapp import import_profile_json, list_profiles, profile_name

    payload = _demo_payload()
    target, _profile = import_profile_json(json.dumps(payload), profile_dir=tmp_path)
    name = profile_name(target, tmp_path)
    assert name == f"uploads/{payload['company']}.json"
    assert [item["name"] for item in list_profiles(tmp_path)] == [name]
    assert profile_name(tmp_path / "other.json", tmp_path) == "other.json"

    # 相对目录 + 绝对画像(重抽后跳转就是这种组合)不能丢掉 uploads/ 前缀
    relative_root = Path(os.path.relpath(tmp_path, Path.cwd()))
    assert profile_name(target.resolve(), relative_root) == name

    shipped = [item["name"] for item in list_profiles(REPO_ROOT / "data/profiles")]
    assert "retail_ecommerce_demo.json" in shipped


def test_webapp_serves_imported_profile(tmp_path: Path):
    """回归:导入后跳转到 /profiles/uploads/xxx.json 曾经 404(只查了根目录)。"""
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from urllib.parse import quote

    from aoe.webapp import Handler, import_profile_json, profile_name

    payload = _demo_payload()
    target, _profile = import_profile_json(json.dumps(payload), profile_dir=tmp_path)
    handler = type(
        "BoundHandler",
        (Handler,),
        {"run_dir": tmp_path / "runs", "profile_dir": tmp_path},
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        url = f"{base}/profiles/{quote(profile_name(target, tmp_path))}"
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert payload["company"] in body
    finally:
        httpd.shutdown()
        httpd.server_close()
