"""知识库加载与检索(docs/07-knowledge-base.md)。

MVP 阶段只实现 L1 / L2 / L4 / L6 四层的结构化表格 + 关键词检索。
docs/07 §4 明确:条目超过 500 条再引入 Embedding,现在不急于上向量库。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class CapabilityClass(BaseModel):
    name: str
    coef_range: tuple[float, float]
    base_coef: float
    typical: list[str] = Field(default_factory=list)
    node_keywords: list[str] = Field(default_factory=list)
    model_tier: str = "fast"
    build_person_days: int = 12
    calls_per_unit: float = 1.0
    data_dependency: str = "medium"

    @property
    def coef_max(self) -> float:
        return self.coef_range[1]


class Scenario(BaseModel):
    id: str
    industry: str
    domain: str
    node_keywords: list[str] = Field(default_factory=list)
    capability_class: str
    title: str
    content: str


class IntegrationMode(BaseModel):
    mode: str
    person_days: int
    coef_multiplier: float = 1.0


class ComplianceRule(BaseModel):
    id: str
    level: str
    industry: str | None = None
    match: dict = Field(default_factory=dict)
    reason: str = ""
    remedy: str = ""

    @property
    def is_redline(self) -> bool:
        return self.level == "redline"


class Costs(BaseModel):
    person_day_price: float
    maintenance_rate: float
    retry_factor: float = 1.0
    integration_person_days: dict[str, int]
    integration_coef_multiplier: dict[str, float] = Field(default_factory=dict)
    model_pricing_per_1k_tokens: dict[str, dict[str, float]]
    tokens_per_call: dict[str, dict[str, float]]
    human_cost_annual_per_fte: float
    build_person_days_cap: int = 40
    currency: str = "CNY"

    def integration(self, mode: str) -> IntegrationMode:
        return IntegrationMode(
            mode=mode,
            person_days=self.integration_person_days[mode],
            coef_multiplier=self.integration_coef_multiplier.get(mode, 1.0),
        )


def data_dir() -> Path:
    """定位知识库目录。支持 AOE_DATA_DIR 覆盖,便于私有化部署时替换知识库。"""
    env = os.environ.get("AOE_DATA_DIR")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data"
        if (candidate / "knowledge").is_dir():
            return candidate
    raise FileNotFoundError("未找到 data/ 目录,请设置环境变量 AOE_DATA_DIR")


def _load_json(path: Path) -> dict:
    # utf-8-sig 同时兼容带 BOM 与不带 BOM 的文件
    with path.open(encoding="utf-8-sig") as fh:
        return json.load(fh)


class KnowledgeBase(BaseModel):
    capabilities: list[CapabilityClass]
    scenarios: list[Scenario]
    compliance_rules: list[ComplianceRule]
    costs: Costs

    # -- L1 ------------------------------------------------------------------ #
    def capability(self, name: str) -> CapabilityClass:
        for item in self.capabilities:
            if item.name == name:
                return item
        raise KeyError(f"能力目录中不存在: {name}")

    # -- L2 ------------------------------------------------------------------ #
    def match_scenarios(
        self,
        industry: str,
        node_name: str,
        domain: str = "",
        capability_class: str | None = None,
        limit: int = 3,
    ) -> list[Scenario]:
        """过滤优先 + 关键词打分(docs/07 §3)。

        硬过滤:行业 + 能力类别;相关性门槛:必须命中节点关键词或业务域,
        否则不返回。避免「同行业只要有该类场景就强行匹配」的噪声。
        """
        scored: list[tuple[float, Scenario]] = []
        for scenario in self.scenarios:
            if scenario.industry != industry:
                continue
            if capability_class and scenario.capability_class != capability_class:
                continue
            keyword_hits = sum(1 for kw in scenario.node_keywords if kw in node_name)
            domain_hit = bool(domain) and scenario.domain == domain
            if keyword_hits == 0 and not domain_hit:
                continue
            score = keyword_hits * 2.0 + (1.5 if domain_hit else 0.0)
            scored.append((score, scenario))
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [s for _, s in scored[:limit]]

    # -- L6 ------------------------------------------------------------------ #
    def compliance_hits(
        self, industry: str, text: str, has_pii: bool
    ) -> list[ComplianceRule]:
        hits: list[ComplianceRule] = []
        for rule in self.compliance_rules:
            if rule.industry and rule.industry != industry:
                continue
            keywords = rule.match.get("keywords", [])
            if not any(kw in text for kw in keywords):
                continue
            if rule.match.get("requires_pii") and not has_pii:
                continue
            hits.append(rule)
        return hits


@lru_cache(maxsize=1)
def load_knowledge() -> KnowledgeBase:
    base = data_dir() / "knowledge"
    caps = _load_json(base / "capabilities.json")
    scenarios = _load_json(base / "scenarios.json")
    compliance = _load_json(base / "compliance.json")
    costs = _load_json(base / "costs.json")
    return KnowledgeBase(
        capabilities=[CapabilityClass.model_validate(c) for c in caps["classes"]],
        scenarios=[Scenario.model_validate(s) for s in scenarios["scenarios"]],
        compliance_rules=[ComplianceRule.model_validate(r) for r in compliance["rules"]],
        costs=Costs.model_validate(costs),
    )
