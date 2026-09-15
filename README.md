# AI 落地契机引擎 (AI Opportunity Engine)

把企业的业务模式翻译成「AI 该插在哪、怎么插、值多少钱、先做哪个」的引擎。

## 一句话定位

输入业务画像(运营流程、组织、系统、数据现状),输出结构化的 AI 机会地图与可执行落地方案。

## 当前状态

`阶段: MVP 内核可用` —— 确定性内核已实现并可端到端运行,尚未接入大模型,也尚未验证付费。

- **已实现:** 候选机会点生成 → 规则过滤与合规红线 → 评分/ROI/敏感性 → 方案组装 → 自检 → Markdown 导出。
  全流程不需要任何模型 API Key 即可运行。详见 `docs/10-mvp-implementation.md`。
- **未实现:** 自由文本采集与建模(S1/S2)、向量检索(S6)、用强模型撰写方案(S7 的 LLM 版本)。
- **未验证:** 付费意愿。`docs/08-roadmap.md` 的 M0 仍然没有完成。

## 快速开始

```bash
pip install -e ".[dev]"

# 校验业务画像
python -m aoe validate data/profiles/retail_ecommerce_demo.json

# 跑完整流水线,输出 Markdown 报告
python -m aoe run data/profiles/retail_ecommerce_demo.json

# 打开本地网页界面(选画像、点运行、直接看报告)
python -m aoe serve

# 测试
python -m pytest
```

安装后也可以直接用 `aoe`(`console script`);若该脚本所在目录不在 `PATH` 上,
或被企业应用控制策略拦截,用 `python -m aoe` 即可,二者等价。

`serve` 启动后打开 `http://127.0.0.1:8765/`:页面上选业务画像、点「运行诊断」,
跑完直接进报告页;页面是服务端渲染的自包含 HTML,不依赖前端构建工具。

报告写入 `outputs/`(`.md` 与同名的单文件 `.html`,双击即可看),
各阶段中间态写入 `work/runs/<run_id>/`(含画像快照 `run_meta.json`,支持重跑、对比与回放)。

## 这个 MVP 验证什么

验证**确定性内核能不能产出可信、可追溯的结论**,而不是「模型能不能写方案」。

引擎的立场是:算术、评分、合规判定、ROI 区间全部交给代码,模型只负责语义与文字。
所以方案正文里的每一个数字都必须经过显式登记,自检会反向扫描并把未经登记的数字判为编造打回。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [01-product-vision](docs/01-product-vision.md) | 产品定位、目标用户、用户旅程、价值主张、不做什么 |
| [02-business-model](docs/02-business-model.md) | 付费方分析、商业化路径、定价、最便宜的验证实验 |
| [03-market-analysis](docs/03-market-analysis.md) | 市场驱动、竞争格局、差异化定位、时机判断 |
| [04-core-methodology](docs/04-core-methodology.md) | 业务建模、AI 能力矩阵、可自动化系数、评分与 ROI 模型 |
| [05-architecture](docs/05-architecture.md) | 系统架构、模块划分、技术选型、数据模型 |
| [06-ai-pipeline](docs/06-ai-pipeline.md) | Agent 流水线、JSON Schema、Prompt 规范、防幻觉策略 |
| [07-knowledge-base](docs/07-knowledge-base.md) | 知识库分层与冷启动、检索策略、数据飞轮 |
| [08-roadmap](docs/08-roadmap.md) | MVP 范围、里程碑、迭代路线、资源估算 |
| [09-risks-and-metrics](docs/09-risks-and-metrics.md) | 风险清单与缓解、北极星指标、go/no-go 标准 |
| [10-mvp-implementation](docs/10-mvp-implementation.md) | **实际代码实现**与文档设计的对应关系、工程偏差、运行方式、下一步 |

## 核心判断(必读)

1. **价值不在「生成方案」这一层。** 通用模型已经能生成看起来专业的 AI 方案,这一层没有壁垒。
2. **护城河来自三件事:** 行业场景库与 ROI 基准数据、落地效果回收闭环、方案到可运行 Agent 的交付能力。
3. **产品定位应为「卖方的售前提效引擎 + 落地入口」**,而非面向终端中小企业的方案生成 SaaS。
4. **先验证付费,再写代码。** 详见 `docs/02-business-model.md` 的验证实验。

## 代码结构

```
src/aoe/
  models.py            结构化中间层 Schema(docs/04 §1、docs/06 §3)
  knowledge.py         知识库加载与检索(L1/L2/L4/L6)
  profiles.py          业务画像读写
  stages/candidates.py S3 候选机会点生成
  stages/rules.py      S4 适用性过滤与合规红线
  stages/scoring.py    S5 评分、ROI 区间、敏感性分析(纯代码,不含模型调用)
  stages/composer.py   S7 方案组装(含数字登记表)
  stages/critic.py     S8 十项自检
  render.py            S9 Markdown 渲染
  pipeline.py          流水线编排与中间态落盘
data/knowledge/        知识库:L1 能力目录 / L2 行业场景 / L4 成本价格 / L6 合规
data/profiles/         业务画像样例
```

## 命名

- 仓库名: `ai-opportunity-engine`
- 中文名: AI 落地契机引擎
