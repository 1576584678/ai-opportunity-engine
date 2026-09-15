# 05 系统架构

## 1. 分层视图

```
┌─────────────────────────────────────────────────┐
│ 前端:采集对话 + 业务模型编辑器 + 方案工作台        │
├─────────────────────────────────────────────────┤
│ API 网关 / 鉴权 / 配额                            │
├─────────────────────────────────────────────────┤
│ 编排层:多阶段流水线 DAG                            │
│  采集 → 建模 → 候选生成 → 过滤 → 评分 → 生成 → 自审 │
├──────────────┬──────────────┬───────────────────┤
│ 知识库(RAG)  │ 规则引擎      │ 确定性计算引擎      │
│ 能力/场景/   │ 合规红线      │ 评分 / 成本 / ROI   │
│ 案例/价格/   │ 适用性过滤    │ 敏感性分析          │
│ 集成/合规    │              │                     │
├──────────────┴──────────────┴───────────────────┤
│ 模型层:分层调度(快模型 + 强模型 + Embedding)      │
├─────────────────────────────────────────────────┤
│ 存储:Postgres + pgvector / 对象存储                │
├─────────────────────────────────────────────────┤
│ 输出渲染:Markdown / Word / PPT / 图表              │
└─────────────────────────────────────────────────┘
```

## 2. 模块清单

| 模块 | 职责 | 备注 |
| --- | --- | --- |
| Intake | 引导式问卷与自由文本采集,缺口追问 | 决定输出质量的关键环节 |
| BusinessModeler | 把描述抽取为结构化业务画像并校验 | 输出必须过 JSON Schema |
| CandidateGenerator | 能力 × 流程节点 穷举候选 | 纯计算 + 语义匹配 |
| RuleFilter | 适用性过滤与合规红线判定 | 确定性规则,可审计 |
| ScoringEngine | V/F/C/R 与优先级计算 | 纯代码实现,含敏感性分析 |
| PlanComposer | 生成方案正文(流程、选型、集成、验收) | 强模型 + 案例 Few-shot |
| Critic | 自检并打回重写 | 见 06 文档的自检清单 |
| KnowledgeService | 知识库检索与更新 | 见 07 文档 |
| Renderer | 输出文档 / 表格 / 图表 | 模板化,不靠模型排版 |
| Workspace | 方案版本、编辑、对比、导出 | 二期重点 |

## 3. 技术选型

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 前端 | Next.js + TypeScript | SSR 与工作台交互兼顾 |
| 后端 | Python (FastAPI) | LLM 生态与数据处理最顺 |
| 编排 | 自研轻量 DAG(可选 LangGraph) | 需要确定性控制与中间态持久化 |
| 结构化输出 | JSON Schema + function calling + 校验重试 | 防止解析失败与字段漂移 |
| 数据库 | Postgres + pgvector | 关系数据与向量检索一套搞定 |
| 缓存/队列 | Redis | 异步任务与限流 |
| 模型 | 分层:抽取/评分用快模型,方案生成用强模型 | 成本可降一半以上 |
| 渲染 | Markdown 为源,Pandoc 转 Word/PPT | 避免模型直接排版 |

**实现状态与偏差:** 技术选型已按本表落地(MVP 阶段渲染先用 Markdown 与自包含 HTML 直出,
未接 Pandoc;本地单用户界面用 Python 标准库 `http.server` 实现,没有引入 Next.js + FastAPI,
理由见 `docs/10 §2.7`)。实际代码与本文档的对应关系、以及各项工程偏差的处理状态,
记录在 `docs/10-mvp-implementation.md`,请一并评审。

## 4. 数据模型(核心表)

| 表 | 关键字段 |
| --- | --- |
| `company` | id, name, industry, scale, created_at |
| `business_profile` | id, company_id, version, profile_json, completeness_score |
| `process_node` | id, profile_id, domain, name, role, system, volume, pain_point |
| `data_source` | id, profile_id, name, carrier, availability, has_pii |
| `opportunity` | id, profile_id, node_id, capability, automation_coef, status |
| `score` | id, opportunity_id, value, feasibility, cost, risk, priority, detail_json |
| `plan` | id, opportunity_id, version, content_md, status, adopted |
| `assumption` | id, plan_id, name, value, source, confidence, sensitivity |
| `knowledge_item` | id, type, industry, content, embedding, updated_at |
| `outcome` | id, plan_id, metric, expected, actual, measured_at |

`outcome` 表是数据飞轮的载体,**必须从第一天就设计进去**,即使早期是手工录入。

## 5. API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/profiles` | 创建业务画像(启动采集) |
| POST | `/api/profiles/{id}/answers` | 提交问卷/追问回答 |
| GET | `/api/profiles/{id}` | 获取画像与完整度评分 |
| POST | `/api/profiles/{id}/opportunities` | 触发生成候选机会点 |
| GET | `/api/profiles/{id}/opportunities` | 机会点列表(含评分明细) |
| POST | `/api/opportunities/{id}/plan` | 生成详细方案 |
| POST | `/api/plans/{id}/review` | 提交采纳/驳回与原因 |
| POST | `/api/plans/{id}/export` | 导出 Word / PPT / Markdown |
| POST | `/api/outcomes` | 回填落地效果 |

## 6. 部署形态

- 早期:单机 Docker Compose(API + Postgres + Redis + Worker)。
- 增长期:API 与 Worker 分离,知识库检索独立服务。
- 客户私有化部署需求高(涉及企业流程数据),**必须支持私有化**,这是付费方的硬要求。

## 7. 横切关注点

- **可追溯:** 每个结论都存计算明细与来源标签。
- **可回放:** 流水线中间态持久化,支持重跑与对比。
- **配额与成本:** 按租户统计 token 消耗,超限降级到快模型。
- **数据隔离:** 多租户下知识库私有层严格隔离。
