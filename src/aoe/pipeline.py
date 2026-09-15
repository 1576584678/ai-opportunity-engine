"""流水线编排 —— docs/06-ai-pipeline.md §2 与 docs/05-architecture.md §7。

确定性阶段(S3/S4/S5/S8/S9)完整实现;生成性阶段(S7 撰写)用模板组装代替强模型,
接口与自检逻辑保持不变。中间态落盘,支持重跑与对比(docs/05 §7)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .knowledge import KnowledgeBase, load_knowledge
from .llm import LLMClient, LLMError
from .models import BusinessProfile, Critique, Plan, Score
from .result import RunResult
from .stages.candidates import generate_candidates
from .stages.composer import compose_plan
from .stages.composer_llm import compose_plan_llm
from .stages.critic import critique_plan
from .stages.rules import apply_rules
from .stages.scoring import assumptions_for, score_opportunity

#: 可行性低于该值不进入优先级排序(docs/04 §5.2)
FEASIBILITY_FLOOR = 0.3

#: Top N 方案中同一节点最多出现的次数(docs/04 §3 step 4「去重与合并」的 MVP 形态)
MAX_PLANS_PER_NODE = 1


def _serialize(payload):
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    if isinstance(payload, (list, tuple)):
        return [_serialize(item) for item in payload]
    return payload


def _persist(run_path: Path, name: str, payload) -> None:
    run_path.mkdir(parents=True, exist_ok=True)
    with (run_path / name).open("w", encoding="utf-8") as fh:
        json.dump(_serialize(payload), fh, ensure_ascii=False, indent=2)


def run_pipeline(
    profile: BusinessProfile,
    kb: KnowledgeBase | None = None,
    run_dir: Path | None = None,
    top_n: int | None = None,
    composer: str = "template",
    client: LLMClient | None = None,
) -> RunResult:
    kb = kb or load_knowledge()
    gaps: list[str] = []

    if profile.missing:
        raise ValueError(
            "画像存在缺口,按 docs/06 §3 约束不得进入候选生成:"
            + ";".join(profile.missing)
        )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_path = (run_dir / run_id) if run_dir else None

    # ---- S3 候选生成 ----------------------------------------------------- #
    candidates, unmatched = generate_candidates(profile, kb)
    if run_path:
        _persist(run_path, "s3_candidates.json", candidates)

    # ---- S4 适用性过滤与合规红线 ------------------------------------------ #
    filtered = apply_rules(profile, candidates, kb)
    if run_path:
        _persist(run_path, "s4_filtered.json", filtered)

    # ---- S5 评分 --------------------------------------------------------- #
    node_map = {node.name: node for node in profile.nodes}
    scored: list[Score] = []
    plans: list[Plan] = []
    critiques: list[Critique] = []
    infeasible: list[str] = []

    for opp in filtered:
        if opp.status.value != "candidate":
            continue
        node = node_map[opp.node]
        score, assumptions = score_opportunity(node, profile, opp, kb)
        # docs/04 §5.2:任一项 < 0.3 时整体判为「暂不可行」,不进入优先级排序。
        blocked_by = score.feasibility.detail.get("blocked_by", [])
        if blocked_by:
            infeasible.append(
                f"{opp.id}:可行性 {score.feasibility.total:.2f},"
                f"受限项 {'、'.join(blocked_by)}"
            )
            continue
        scored.append(score)

    # docs/04 §5.5:得分相同看 V
    scored.sort(key=lambda s: (-s.priority, -s.value.total, s.opportunity_id))
    if run_path:
        _persist(run_path, "s5_scores.json", scored)

    # ---- S7 方案组装(含 Top N 与同节点合并) ------------------------------ #
    limit = top_n if top_n is not None else profile.params.top_n_plans
    opp_map = {opp.id: opp for opp in filtered}
    per_node: dict[str, int] = {}
    selected: list[Score] = []
    for score in scored:
        node_name = score.node
        if per_node.get(node_name, 0) >= MAX_PLANS_PER_NODE:
            continue
        if len(selected) >= limit:
            break
        per_node[node_name] = per_node.get(node_name, 0) + 1
        selected.append(score)

    for index, score in enumerate(selected, start=1):
        opp = opp_map[score.opportunity_id]
        node = node_map[score.node]
        plan_id = f"PLAN-{index:02d}-{score.node}"
        kwargs = dict(
            node=node,
            profile=profile,
            opportunity=opp,
            score=score,
            assumptions=assumptions_for(node, profile, opp.automation_coef),
            kb=kb,
            plan_id=plan_id,
        )
        if composer == "llm":
            if client is None:
                raise ValueError("composer='llm' 需要传入 LLMClient")
            try:
                composed = compose_plan_llm(client=client, **kwargs)
            except (ValueError, LLMError) as exc:
                # 不让单个方案的失败毁掉整轮运行,但要留下可见记录
                gaps.append(f"方案 {plan_id} 的模型撰写失败,已回退模板模式:{exc}")
                composed = compose_plan(**kwargs)
        else:
            composed = compose_plan(**kwargs)
        critique = critique_plan(
            plan=composed.plan,
            numbers=composed.numbers,
            node=node,
            profile=profile,
            kb=kb,
            build_days=float(score.cost.detail.get("build_days", 0)),
        )
        plans.append(composed.plan)
        critiques.append(critique)

    if run_path:
        _persist(run_path, "s7_plans.json", plans)
        _persist(run_path, "s8_critiques.json", critiques)
        if client is not None:
            _persist(run_path, "llm_usage.json", client.ledger.summary())

    if unmatched:
        gaps.append(
            "以下节点未匹配到任何能力,知识库需要补充对应场景:"
            + ", ".join(unmatched)
        )
    if infeasible:
        gaps.append("以下机会点因可行性不足被排除:" + ";".join(infeasible))

    result = RunResult(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc),
        profile=profile,
        all_opportunities=filtered,
        ranked=scored,
        plans=plans,
        critiques=critiques,
        unmatched_nodes=unmatched,
        gaps=gaps,
        composer=composer,
        usage=(client.ledger.summary() if client is not None else {}),
    )
    return result


