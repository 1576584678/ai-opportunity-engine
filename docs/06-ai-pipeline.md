# 06 AI 流水线设计

## 1. 总原则

1. **分阶段,禁止一次性生成全案。** 单次生成必然产出「正确的废话」。
2. **每阶段强制结构化输出**,JSON Schema 校验失败自动重试。
3. **算术交给代码**,模型只负责语义与文字。
4. **缺口追问,禁止编造。** 缺量级参数就问,不许猜。
5. **自检闭环。** 不合格的方案打回重写,不直接交付。

## 2. 阶段定义

| 阶段 | 输入 | 输出 | 用模型 |
| --- | --- | --- | --- |
| S1 采集 | 自由文本 + 问卷 | 对话状态、待追问清单 | 快模型 |
| S2 建模 | 全部已采集信息 | `BusinessProfile` JSON | 快模型 |
| S3 候选生成 | BusinessProfile | `Opportunity[]`(未过滤) | 快模型 + Embedding |
| S4 过滤与合规 | Opportunity[] + 规则库 | 保留项 / 降级项 / 红线项 | 规则引擎 |
| S5 评分 | 保留项 + 量级参数 | `Score[]` + 计算明细 | **代码** |
| S6 案例检索 | Top N 机会点 | 同行业相似案例 | Embedding |
| S7 方案生成 | 机会点 + 评分 + 案例 | `Plan` | 强模型 |
| S8 自审 | Plan | 通过 / 打回 + 修改意见 | 强模型(Critic) |
| S9 渲染 | 已通过 Plan | Word / PPT / Markdown | 模板引擎 |

## 3. 关键 Schema(节选)

### BusinessProfile

```json
{
  "industry": "string",
  "scale": { "headcount": 0, "revenue": 0, "sites": 0 },
  "domains": [
    {
      "name": "string",
      "weight": 0.0,
      "nodes": [
        {
          "name": "string",
          "trigger": "string",
          "inputs": ["string"],
          "outputs": ["string"],
          "role": "string",
          "systems": ["string"],
          "hours_per_week": 0,
          "volume_per_month": 0,
          "pain_point": "string",
          "standardized": 0.0
        }
      ]
    }
  ],
  "data_sources": [
    { "name": "string", "carrier": "string", "availability": 0.0, "has_pii": true }
  ],
  "kpis": [{ "name": "string", "current": "string", "target": "string", "node": "string" }],
  "missing": ["string"]
}
```

**约束:** `missing` 非空时不得进入 S3,必须先回到 S1 追问。

### Opportunity

```json
{
  "node": "string",
  "capability": "string",
  "automation_coef": 0.0,
  "data_requirement": ["string"],
  "integration_mode": "api | rpa | embedded | standalone",
  "rationale": "string",
  "status": "candidate | downgraded | redline"
}
```

### Plan

```json
{
  "opportunity_ref": "string",
  "before_process": "string",
  "after_process": "string",
  "data_plan": "string",
  "agent_design": "string",
  "integration": "string",
  "milestones": [{ "name": "string", "duration_days": 0 }],
  "acceptance_metrics": [{ "name": "string", "target": "string", "how_to_measure": "string" }],
  "assumptions": [{ "name": "string", "value": "string", "source": "user|benchmark|assumed", "confidence": "high|medium|low" }],
  "risks": ["string"],
  "compliance_notes": "string"
}
```

## 4. Prompt 规范

- **系统提示必须包含:** 角色定义、输出 Schema、禁止行为清单、来源标注规则。
- **禁止行为清单(写进 Prompt):**
  - 不得编造客户未提供的量级数字
  - 不得输出无法测量 KPI 的验收指标
  - 不得给出与流程节点无关的通用建议
  - 不得自行计算收益数字(由系统注入)
- **Few-shot:** 从知识库检索 2-3 个同行业、同能力类别的历史方案片段作为示例。
- **温度:** 抽取类 0-0.2;方案生成类 0.3-0.5;不得高于 0.7。
- **输出必须为纯 JSON**,不带解释性前后文;解析失败按指数退避重试(最多 3 次),仍失败则降级到拆分为多次小请求。

## 5. Critic 自检清单

方案必须逐条通过,任一不过则打回:

1. 是否绑定到**具体的流程节点**(而非泛泛的业务域)?
2. 是否说明**改造前后**的流程差异?
3. 是否列出**数据要求**及可得性?
4. 是否给出**集成方式**(API / RPA / 嵌入 / 独立)?
5. 每条验收指标是否**可测量**、有明确口径?
6. 每个数字是否**有来源标签**(user / benchmark / assumed)?
7. 是否列出风险与合规注意事项?
8. 是否出现客户未提供的量级数字(出现即判不合格)?
9. 是否存在与业务画像矛盾的内容?
10. 是否可以用更小的 PoC 先验证(若方案规模过大,要求拆分)?

打回时 Critic 必须给出**具体的修改指令**,而不是「请改进」。

## 6. 防幻觉策略

| 策略 | 做法 |
| --- | --- |
| 数字隔离 | 量级与收益数字由系统注入模板占位符,模型不生成数字 |
| 来源标注 | 每个输入量必须带来源标签与置信度,页面上可见可点开 |
| 缺口追问 | `missing` 非空即阻断后续流程 |
| 交叉校验 | 画像与方案做一致性检查,矛盾即打回 |
| 证据引用 | 行业案例引用必须指向知识库真实条目 id |
| 不确定性表达 | 数据不足时输出区间与「需实测」,禁止给确定值 |

## 7. 模型分层与成本估算

| 阶段 | 模型档位 | 单次调用量级 |
| --- | --- | --- |
| S1 / S2 | 快模型 | 5-15 次 |
| S3 | 快模型 + Embedding | 1-5 次 |
| S6 | 仅 Embedding | 1-N 次 |
| S7 | 强模型 | 每机会点 1-3 次 |
| S8 | 强模型 | 每机会点 1-2 次 |

**成本控制手段:** 候选机会点先按规则裁剪到 Top N(N 默认 8),再进入 S7;评分与过滤完全走代码。
