"""一次完整运行的产物。docs/05 §7 要求「可追溯、可回放」,所有中间态都挂在结果上。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import (
    BusinessProfile,
    Critique,
    Opportunity,
    Plan,
    Score,
)


class RunResult(BaseModel):
    run_id: str
    generated_at: datetime
    profile: BusinessProfile
    all_opportunities: list[Opportunity] = Field(default_factory=list)
    ranked: list[Score] = Field(default_factory=list)
    plans: list[Plan] = Field(default_factory=list)
    critiques: list[Critique] = Field(default_factory=list)
    unmatched_nodes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    #: 模型用量(docs/05 §7 配额与成本的 MVP 形态);模板模式下为空
    composer: str = "template"
    usage: dict = Field(default_factory=dict)

    def opportunity(self, opportunity_id: str) -> Opportunity:
        for opp in self.all_opportunities:
            if opp.id == opportunity_id:
                return opp
        raise KeyError(opportunity_id)

    def score(self, opportunity_id: str) -> Score:
        for score in self.ranked:
            if score.opportunity_id == opportunity_id:
                return score
        raise KeyError(opportunity_id)
