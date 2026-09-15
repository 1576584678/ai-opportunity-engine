"""结构化中间层 Schema —— 对应 docs/04-core-methodology.md §1 与 docs/06-ai-pipeline.md §3。

设计约束(来自文档,直接体现在类型里):
- 所有量级参数必须带来源标签与置信度,禁止编造(docs/04 §1 硬性规则)。
- `missing` 非空时不得进入候选生成阶段(docs/06 §3)。
- 每个结论都要能追到计算明细,所以明细字段是强制输出的一部分。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --------------------------------------------------------------------------- #
# 基础类型
# --------------------------------------------------------------------------- #


class Source(str, Enum):
    """量级参数来源标签。docs/04 §1:所有数值必须标注来源。"""

    USER = "user"
    BENCHMARK = "benchmark"
    ASSUMED = "assumed"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Sourced(BaseModel):
    """带来源与置信度的量级参数。"""

    value: float
    source: Source
    confidence: Confidence = Confidence.MEDIUM
    note: str = ""


class IntegrationMode(str, Enum):
    API = "api"
    RPA = "rpa"
    EMBEDDED = "embedded"
    STANDALONE = "standalone"


class OpportunityStatus(str, Enum):
    CANDIDATE = "candidate"
    DOWNGRADED = "downgraded"
    INFEASIBLE = "infeasible"
    REDLINE = "redline"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# --------------------------------------------------------------------------- #
# 业务画像(docs/04 §1)
# --------------------------------------------------------------------------- #


class Scale(BaseModel):
    headcount: int = 0
    revenue: float = 0
    sites: int = 0


class RiskEvent(BaseModel):
    """风险降低收益的输入:年风险事件频率 × 单次损失 × 预期降幅。"""

    frequency_per_year: Sourced
    loss_per_event: Sourced
    expected_reduction: float = Field(ge=0, le=1)


class RevenueUplift(BaseModel):
    """收入收益的输入:转化率提升 × 客单价 × 年单量 × 折减系数。"""

    conversion_uplift: Sourced
    avg_order_value: Sourced
    orders_per_year: Sourced


class ProcessNode(BaseModel):
    """流程节点 —— 整个引擎的匹配与计算单元。"""

    name: str
    domain: str
    trigger: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    role: str = ""
    systems: list[str] = Field(default_factory=list)
    hours_per_week: float = Field(default=0.0, ge=0)
    volume_per_month: float = Field(default=0.0, ge=0)
    pain_point: str = ""
    standardized: float = Field(default=0.5, ge=0, le=1)
    data_required: list[str] = Field(default_factory=list)

    # 可自动化系数的 5 个判定输入(docs/04 §4 的判定顺序)
    output_is_text_or_number: bool = True
    requires_human_judgement: bool = False
    knowledge_intensive: bool = False
    physical_action: bool = False
    regulated: bool = False

    # 幻觉影响面 1-5:输出错误的后果严重度
    hallucination_impact: int = Field(default=3, ge=1, le=5)

    risk_event: RiskEvent | None = None
    revenue_uplift: RevenueUplift | None = None


class System(BaseModel):
    name: str
    kind: Literal["self_dev", "purchased", "saas"] = "purchased"
    has_api: bool = False
    data_exportable: bool = True


class DataSource(BaseModel):
    name: str
    carrier: str = ""
    availability: float = Field(default=0.5, ge=0, le=1)
    has_pii: bool = False
    exportable: bool = True


class Role(BaseModel):
    name: str
    headcount: int = 1
    annual_cost_per_fte: Sourced | None = None


class KPI(BaseModel):
    name: str
    current: str = ""
    target: str = ""
    node: str = ""


class Params(BaseModel):
    """全局量级参数。缺任一必需项时上游必须追问,不得默认编造。"""

    human_cost_annual_per_fte: Sourced
    discount_factor: float = Field(default=0.5, ge=0, le=1)
    hours_per_fte_week: float = Field(default=40.0, gt=0)
    top_n_plans: int = Field(default=8, ge=1)


class BusinessProfile(BaseModel):
    """S2 建模阶段的结构化产物。"""

    model_config = ConfigDict(extra="forbid")

    company: str
    industry: str
    scale: Scale = Field(default_factory=Scale)
    nodes: list[ProcessNode]
    systems: list[System] = Field(default_factory=list)
    data_sources: list[DataSource] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    kpis: list[KPI] = Field(default_factory=list)
    org_willingness: float = Field(default=0.7, ge=0, le=1)
    params: Params
    missing: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_node_refs(self) -> "BusinessProfile":
        known_systems = {s.name for s in self.systems}
        known_data = {d.name for d in self.data_sources}
        for node in self.nodes:
            unknown_sys = [s for s in node.systems if s not in known_systems]
            if unknown_sys:
                raise ValueError(
                    f"节点 `{node.name}` 引用了未登记的系统: {unknown_sys}"
                )
            unknown_data = [d for d in node.data_required if d not in known_data]
            if unknown_data:
                raise ValueError(
                    f"节点 `{node.name}` 引用了未登记的数据源: {unknown_data}"
                )
        return self

    @property
    def total_hours_per_week(self) -> float:
        return sum(n.hours_per_week for n in self.nodes)


# --------------------------------------------------------------------------- #
# 机会点(docs/06 §3 Opportunity)
# --------------------------------------------------------------------------- #


class Opportunity(BaseModel):
    id: str
    node: str
    domain: str
    capability_class: str
    capability: str
    automation_coef: float = Field(ge=0, le=1)
    coef_detail: dict = Field(default_factory=dict)
    data_requirement: list[str] = Field(default_factory=list)
    integration_mode: IntegrationMode = IntegrationMode.STANDALONE
    rationale: str = ""
    matched_scenarios: list[str] = Field(default_factory=list)
    status: OpportunityStatus = OpportunityStatus.CANDIDATE
    status_reason: str = ""


# --------------------------------------------------------------------------- #
# 评分(docs/04 §5 - §6)
# --------------------------------------------------------------------------- #


class ValueBreakdown(BaseModel):
    efficiency: float
    revenue: float
    risk: float
    total: float
    detail: dict = Field(default_factory=dict)
    intervals: dict = Field(default_factory=dict)


class FeasibilityBreakdown(BaseModel):
    data_availability: float
    standardization: float
    system_access: float
    org_willingness: float
    total: float
    detail: dict = Field(default_factory=dict)


class CostBreakdown(BaseModel):
    build: float
    run: float
    maintain: float
    total: float
    detail: dict = Field(default_factory=dict)


class RiskBreakdown(BaseModel):
    hallucination_impact: float
    compliance_level: float
    responsibility_clarity: float
    # 责任清晰度越高,风险越小,故参与相乘的是其反向因子 (6 - clarity)。
    clarity_factor: float = 0.0
    raw: float
    total: float
    detail: dict = Field(default_factory=dict)


class Assumption(BaseModel):
    name: str
    value: str
    source: Source
    confidence: Confidence


class SensitivityItem(BaseModel):
    assumption: str
    swing_pct: float
    priority_pessimistic: float
    priority_optimistic: float
    note: str = ""


class ROI(BaseModel):
    benefit_pessimistic: float
    benefit_neutral: float
    benefit_optimistic: float
    payback_years: float | None = None


class Score(BaseModel):
    opportunity_id: str
    node: str
    value: ValueBreakdown
    feasibility: FeasibilityBreakdown
    cost: CostBreakdown
    risk: RiskBreakdown
    priority: float
    roi: ROI
    sensitivity: list[SensitivityItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 方案与自检(docs/06 §3 Plan / §5 Critic)
# --------------------------------------------------------------------------- #


class Milestone(BaseModel):
    name: str
    duration_days: int


class AcceptanceMetric(BaseModel):
    name: str
    target: str
    how_to_measure: str


class Plan(BaseModel):
    opportunity_ref: str
    plan_id: str
    before_process: str
    after_process: str
    data_plan: str
    agent_design: str
    integration: str
    milestones: list[Milestone] = Field(default_factory=list)
    acceptance_metrics: list[AcceptanceMetric] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    compliance_notes: str = ""
    version: int = 1


class CritiqueCheck(BaseModel):
    no: int
    name: str
    passed: bool
    detail: str


class Critique(BaseModel):
    plan_id: str
    passed: bool
    checks: list[CritiqueCheck]
    rework_instructions: list[str] = Field(default_factory=list)


class Outcome(BaseModel):
    """数据飞轮载体(docs/07 §5)。必须从第一天记录,即使早期手工录入。"""

    plan_id: str
    metric: str
    expected: str
    actual: str
    measured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
