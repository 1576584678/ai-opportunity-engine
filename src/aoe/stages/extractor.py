"""S2 业务建模 —— docs/06-ai-pipeline.md §2 / §4。

把非结构化的业务描述抽取为 `BusinessProfile`。这是「交给模型」的那一半:
语义理解由模型做,但**校验、缺口判定、以及后续全部算账仍由代码做**。

三条硬约束直接来自文档:
- docs/06 §4:系统提示必须包含 角色定义、输出 Schema、禁止行为清单、来源标注规则。
- docs/06 §3:`missing` 非空时不得进入 S3,必须先回到 S1 追问。
- docs/04 §1:所有数值必须标注来源(user / benchmark / assumed),禁止编造。
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from ..llm import LLMClient, LLMError
from ..models import BusinessProfile

ROLE = """你是企业业务流程建模师。你的唯一任务是把客户的口语化业务描述,抽取成结构化的「业务画像」JSON。"""

FORBIDDEN = """禁止行为清单(违反即视为失败):
1. 不得编造客户未提供的量级数字。描述里没给的数字,一律不许猜。
2. 不得为了通过校验而虚构节点、系统或数据源。
3. 节点引用的 systems 与 data_required 必须是你在 systems / data_sources 中真实列出过的名称。
4. 不得输出任何解释性前后文,只输出纯 JSON。
5. 不得省略字段;结构里出现的每个字段都要给值。"""

SOURCE_RULES = """来源标注规则(每个数值字段都必须带 source 与 confidence):
- source=user:描述里明确给出的数字,confidence 用 high。
- source=benchmark:你依据行业常识补充的参考值,confidence 用 low。
- source=assumed:纯假设,confidence 用 low。
- missing 数组只列「无法从描述中确定、且下游算账必需」的项(如年人力成本、月处理量)。
  例如:description="我们有客服团队处理售后咨询" → 没有给出人数、单量、耗时,这些就要进 missing。
  只要描述里能算出来,就不要放进 missing。"""

NODE_FLAG_RULES = """流程节点上五个布尔标记的判定标准(它们决定可自动化系数,必须准确):
- output_is_text_or_number:输出是纯数字或文本资产(如回复内容、结构化字段、报告)
- requires_human_judgement:需要人工做最终判断或承担后果(如定价决策、异常定责、排产拍板)
- knowledge_intensive:依赖专有数据或隐性经验(如老员工凭经验判断、依赖内部非公开规则)
- physical_action:涉及物理动作或线下场景(如搬货、巡检拍照、现场作业)
- regulated:有强合规审查要求(如涉及资金、征信、人事决定、医疗建议)
- hallucination_impact:输出错误的后果严重度,1-5。
  1=几乎无影响(如内部草稿) 3=有明显返工成本 5=直接影响客户或资金/合规

auto 参考:如果节点是「录入/识别/分类」这类结构化程度高的环节,flags 通常全 false;
如果是「决策/议价/定责」这类,requires_human_judgement 通常为 true。"""

EXAMPLE = """一个节点的完整示例(注意 flags 与来源标注):
{
  "name": "送货单录入",
  "domain": "仓储",
  "role": "仓管员",
  "trigger": "供应商送货到仓",
  "inputs": ["送货单影像"],
  "outputs": ["入库单"],
  "systems": ["WMS"],
  "hours_per_week": 70,
  "volume_per_month": 3000,
  "standardized": 0.9,
  "pain_point": "手工录入易错,月底对账差异多",
  "data_required": ["送货单影像"],
  "output_is_text_or_number": true,
  "requires_human_judgement": false,
  "knowledge_intensive": false,
  "physical_action": false,
  "regulated": false,
  "hallucination_impact": 3
}"""


def _schema_text() -> str:
    schema = BusinessProfile.model_json_schema()
    return json.dumps(schema, ensure_ascii=False, indent=2)


def build_system_prompt() -> str:
    return "\n\n".join(
        [
            ROLE,
            FORBIDDEN,
            SOURCE_RULES,
            NODE_FLAG_RULES,
            EXAMPLE,
            "下面是必须严格遵循的 JSON Schema(注意:additionalProperties 为 false,不得新增字段):\n"
            + _schema_text(),
        ]
    )


def extract_profile(
    description: str,
    client: LLMClient,
    hints: str = "",
    industry: str = "",
) -> tuple[BusinessProfile, dict]:
    """返回 (画像, 元信息)。校验失败会把 pydantic 的报错回喂给模型重试。"""
    system = build_system_prompt()
    user_parts = [f"客户业务描述:\n{description}"]
    if industry:
        user_parts.append(f"行业(已确认):{industry}")
    if hints:
        user_parts.append(f"补充信息:\n{hints}")
    user_parts.append(
        "请输出完整的业务画像 JSON。company 用描述里的公司名,"
        "没有就用「未命名企业」;industry 用上面的行业或你从描述判断的行业。"
    )
    base_user = "\n\n".join(user_parts)

    attempts: list[str] = []
    last_error: Exception | None = None
    user = base_user
    for attempt in range(1, client.config.max_attempts + 1):
        payload = client.chat_json(system, user, purpose="S2 建模", temperature=client.config.extract_temperature)
        try:
            profile = BusinessProfile.model_validate(payload)
            return profile, {
                "attempts": attempt,
                "errors": attempts,
                "missing": list(profile.missing),
            }
        except ValidationError as exc:
            last_error = exc
            message = _format_errors(exc)
            attempts.append(message)
            # 把具体报错回喂,要求只修这些点,不要重写整个画像
            user = (
                base_user
                + "\n\n上一次输出未通过 Schema 校验,错误如下:\n"
                + message
                + "\n\n请只修正上述错误后重新输出完整 JSON,不要改变其它已经正确的内容。"
            )
    raise LLMError(
        f"S2 建模在 {client.config.max_attempts} 次尝试后仍未通过 Schema 校验:{last_error}"
    )


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors()[:25]:
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"- 字段 `{location}`:{error['msg']}")
    if len(exc.errors()) > 25:
        lines.append(f"- (还有 {len(exc.errors()) - 25} 处未列出)")
    return "\n".join(lines)


def gap_report(profile: BusinessProfile) -> str:
    """missing 非空时的追问清单(docs/06 §3 要求回到 S1)。"""
    if not profile.missing:
        return ""
    lines = ["画像存在缺口,按 docs/06 §3 不得进入候选生成,需先追问:"]
    lines += [f"  - {item}" for item in profile.missing]
    return "\n".join(lines)
