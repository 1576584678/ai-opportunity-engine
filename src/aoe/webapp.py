"""本地 Web 界面 —— 导入数据、跑诊断、看报告,不用记命令行参数。

只用标准库(http.server),不引入新依赖;页面与报告共用 theme.CSS 与 render_html。
    python -m aoe serve
"""

from __future__ import annotations

import json
import re
import traceback
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .models import BusinessProfile, Critique, Opportunity, Plan, Score
from .pipeline import run_pipeline
from .profiles import dump_profile, load_profile
from .render import render_report
from .render_html import render_html
from .result import RunResult
from .stages.extractor import extract_profile, gap_report
from .theme import CSS, UPLOAD_JS

DEFAULT_RUN_DIR = Path("work/runs")
DEFAULT_PROFILE_DIR = Path("data/profiles")
UPLOAD_SUBDIR = "uploads"
DESCRIPTION_DIRNAME = "descriptions"

#: 画像库里的中文说明(不认识的文件名退化为文件名本身)
PROFILE_LABELS = {
    "retail_ecommerce_demo.json": "某区域连锁零售企业(示例)",
    "manufacturing_complete.json": "苏州某汽车零部件制造企业(抽取 + 补充确认)",
    "manufacturing_extracted.json": "苏州某汽车零部件制造企业(仅抽取,含缺口)",
}

#: 内置画像的源描述(文件名与画像不同前缀,所以显式列出来,否则页面会误报「没有源描述」)
PROFILE_DESCRIPTIONS = {
    "manufacturing_extracted.json": "manufacturing_plant.txt",
    "manufacturing_complete.json": "manufacturing_plant.txt",
}

#: S2 在描述里读不出企业名时写进画像的占位值(见 stages/extractor.py 的抽取提示词)
PLACEHOLDER_COMPANY = "未命名企业"

DESCRIPTION_TEMPLATE = """# 业务描述模板

把下面每一条尽量填清楚,填完直接粘贴回网页(或存成 .txt 上传)。
写得越具体,抽出来的画像越准;**有数字就写数字**,不要写「很多」「经常」。

## 1. 企业基本情况
- 企业名称:
- 所属行业(零售电商 / 连锁服务 / 制造):
- 员工人数:
- 年营收(元):
- 门店或生产基地数量:
- 目前使用的系统(ERP / CRM / WMS / OA …):

## 2. 业务流程(每个环节写一段,建议先写最痛的 3-5 个)
对每个环节请写清:
- 环节名称:
- 属于哪个业务域(如 客服 / 订单履约 / 来料检验 / 采购议价):
- 谁在做(岗位):
- 触发条件或频次:
- 输入是什么、输出是什么:
- 每月大概处理多少件 / 多少单 / 多少份:
- 每周投入多少工时:
- 现在最痛的问题(例如:漏检 12 次、损失约 84 万元、月底对账要 3 天):

## 3. 数据情况
- 每个环节用到的数据存在哪里(系统 / 纸质单据 / Excel / 扫描件):
- 数据是否涉及个人信息或行业监管要求:

## 4. 目标与约束
- 最想先解决的 1-3 个环节:
- 期望半年内达到什么指标:
- 有哪些不能碰的红线(例如不允许上传客户数据到公网):

## 5. 补充说明
- 任何上面没问到、但你觉得重要的情况:
"""


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def slugify(text: str) -> str:
    """把企业名转成安全的文件名片段。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (text or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned[:48] or "profile"


def _unique_path(directory: Path, slug: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{slug}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{slug}-{index}{suffix}"
        index += 1
    return candidate


def profile_name(path: Path, profile_dir: Path = DEFAULT_PROFILE_DIR) -> str:
    """画像在画像库内的相对路径:导入的画像在 uploads/ 下,示例画像在根目录。

    这个相对路径同时用作 URL 里的画像名,路由按前缀匹配,不做分段解析。
    """
    root = Path(profile_dir)
    try:
        # 两侧都取绝对路径:调用方给进来的可能是相对目录 + 绝对画像(或反过来),
        # 直接 relative_to 会抛 ValueError 并静默退化成文件名,丢掉 uploads/ 前缀。
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def list_profiles(profile_dir: Path = DEFAULT_PROFILE_DIR) -> list[dict]:
    items = []
    if not profile_dir.exists():
        return items
    paths = sorted(profile_dir.glob("*.json")) + sorted(
        (profile_dir / UPLOAD_SUBDIR).glob("*.json")
    )
    for path in paths:
        payload = _read_json(path, {})
        items.append(
            {
                "name": profile_name(path, profile_dir),
                "company": payload.get("company") or path.stem,
                "industry": payload.get("industry", ""),
                "label": PROFILE_LABELS.get(path.name, path.stem),
                "nodes": len(payload.get("nodes", [])),
                "missing": len(payload.get("missing", [])),
            }
        )
    return items


def resolve_description_path(
    name: str, profile_dir: Path = DEFAULT_PROFILE_DIR
) -> Path:
    """找到画像对应的源描述(用于「补充信息后重新抽取」)。

    找不到时返回一个不存在的占位路径,调用方用 `.exists()` 判断即可。
    内置画像的描述与画像不同前缀,靠 PROFILE_DESCRIPTIONS 显式对应;
    导入的画像则是同名(同前缀),放在 descriptions/uploads/ 下。
    """
    root = Path(profile_dir)
    mapped = PROFILE_DESCRIPTIONS.get(Path(name).name)
    if mapped:
        candidate = root / DESCRIPTION_DIRNAME / mapped
        if candidate.exists():
            return candidate
    directory = root / DESCRIPTION_DIRNAME / UPLOAD_SUBDIR
    slug = Path(name).stem
    if directory.exists():
        matches = sorted(directory.glob(f"{slug}*.txt"))
        if matches:
            return matches[0]
    return directory / f"{slug}.txt"


def count_legacy_runs(run_dir: Path = DEFAULT_RUN_DIR) -> int:
    """统计没有 run_meta.json 的旧运行目录:它们无法重建结果,只能重新生成。"""
    if not run_dir.exists():
        return 0
    return sum(
        1
        for path in run_dir.iterdir()
        if path.is_dir() and not (path / "run_meta.json").exists()
    )


def list_runs(run_dir: Path = DEFAULT_RUN_DIR) -> list[dict]:
    """列出运行历史。只认带 run_meta.json 的目录,避免把半途失败的运行当成可回放产物。"""
    items = []
    if not run_dir.exists():
        return items
    for path in sorted(run_dir.iterdir(), reverse=True):
        meta_path = path / "run_meta.json"
        if not path.is_dir() or not meta_path.exists():
            continue
        meta = _read_json(meta_path, {})
        profile = meta.get("profile", {})
        critiques = _read_json(path / "s8_critiques.json", [])
        items.append(
            {
                "run_id": path.name,
                "generated_at": str(meta.get("generated_at", ""))[:19].replace("T", " "),
                "company": profile.get("company") or PLACEHOLDER_COMPANY,
                "industry": profile.get("industry", ""),
                "composer": meta.get("composer", "template"),
                "plans": len(_read_json(path / "s7_plans.json", [])),
                "passed": sum(1 for item in critiques if item.get("passed")),
                "checks": len(critiques),
            }
        )
    return items


def load_result(run_path: Path) -> RunResult:
    """从落盘产物重建一次运行,用于重新渲染(docs/05 §7 的可回放要求)。"""
    meta = _read_json(run_path / "run_meta.json", None)
    if not meta:
        raise FileNotFoundError(f"{run_path.name} 缺少 run_meta.json,无法重建运行结果")
    return RunResult.model_validate(
        {
            "run_id": meta.get("run_id", run_path.name),
            "generated_at": meta["generated_at"],
            "profile": meta["profile"],
            "all_opportunities": [
                Opportunity.model_validate(item)
                for item in _read_json(run_path / "s4_filtered.json", [])
            ],
            "ranked": [
                Score.model_validate(item)
                for item in _read_json(run_path / "s5_scores.json", [])
            ],
            "plans": [
                Plan.model_validate(item)
                for item in _read_json(run_path / "s7_plans.json", [])
            ],
            "critiques": [
                Critique.model_validate(item)
                for item in _read_json(run_path / "s8_critiques.json", [])
            ],
            "unmatched_nodes": meta.get("unmatched_nodes", []),
            "gaps": meta.get("gaps", []),
            "composer": meta.get("composer", "template"),
            "usage": _read_json(run_path / "llm_usage.json", {}),
        }
    )


# --------------------------------------------------------------------------- #
# 模板与导入
# --------------------------------------------------------------------------- #


def build_profile_template(profile_dir: Path = DEFAULT_PROFILE_DIR) -> dict:
    """按真实 Schema 生成空画像骨架。

    抄一份示例画像再清空,模板字段就与引擎要求完全一致(画像模型是 extra="forbid",
    字段错一个就导入失败);以 _ 开头的说明键在导入时会被 strip_help_keys 去掉。
    """
    demo = _read_json(profile_dir / "retail_ecommerce_demo.json", None)
    if demo is None:
        raise FileNotFoundError("缺少示例画像,无法生成模板")

    def blank(value, hint: str):
        if isinstance(value, str):
            return hint
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return 0
        if isinstance(value, list):
            return []
        if isinstance(value, dict):
            return {key: blank(item, hint) for key, item in value.items()}
        return value

    nodes = demo.get("nodes") or [{}]
    sources = demo.get("data_sources") or [{}]
    blank_node = {key: blank(value, "<请填写>") for key, value in nodes[0].items()}
    blank_node.update(
        {
            "name": "请填写流程节点名称",
            "domain": "请填写业务域,例如 客服 / 订单履约",
            "role": "请填写执行岗位",
            "pain_point": "请写具体痛点,带数字最好,例如 每月漏检 12 次",
        }
    )
    blank_source = {key: blank(value, "<请填写>") for key, value in sources[0].items()}
    blank_source.update(
        {"name": "请填写数据源名称", "carrier": "系统 / 纸质 / Excel / 扫描件"}
    )
    return {
        **blank(demo, "<请填写>"),
        "company": "请填写企业全称",
        "industry": "零售电商 / 连锁服务 / 制造",
        "systems": demo.get("systems", []),
        "nodes": [blank_node],
        "data_sources": [blank_source],
        "missing": [],
        "_填写说明": [
            "把带「请填写」的占位内容全部替换掉,这就是引擎的输入格式。",
            "以 _ 开头的键只是说明,导入时会自动忽略。",
            "nodes 里一个环节复制一份上面的节点对象,别把不同环节写在一起。",
            "数字字段不要留空,确实不知道的填 0,并把字段名写进 missing 数组。",
            "params 里的成本参数可先用示例默认值,签约后再换成客户口径。",
            "不想手填:下载业务描述问卷,写完用网页上的「让模型抽取」。",
        ],
    }


def strip_help_keys(payload):
    """去掉以 _ 开头的说明键,让模板能带注释又不触发 extra="forbid"。"""
    if isinstance(payload, dict):
        return {
            key: strip_help_keys(value)
            for key, value in payload.items()
            if not str(key).startswith("_")
        }
    if isinstance(payload, list):
        return [strip_help_keys(item) for item in payload]
    return payload


def import_profile_json(
    text: str,
    *,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    slug: str | None = None,
) -> tuple[Path, BusinessProfile]:
    """导入画像 JSON:先解析、再校验,通过后才落盘;失败信息直接带回页面。"""
    if not text.strip():
        raise ValueError("还没有内容:粘贴 JSON 或选择 .json 文件")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON 解析失败:第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("画像必须是 JSON 对象(顶层用 { } 包起来)")
    profile = BusinessProfile.model_validate(strip_help_keys(payload))
    target = _unique_path(
        profile_dir / UPLOAD_SUBDIR, slugify(slug or profile.company), ".json"
    )
    dump_profile(profile, target)
    return target, profile


def import_description(
    text: str,
    *,
    client,
    industry: str = "",
    hints: str = "",
    profile_dir: Path = DEFAULT_PROFILE_DIR,
) -> tuple[Path, BusinessProfile, dict, Path]:
    """用 S2 把业务描述抽成画像,并保存描述原文,方便之后补缺口重抽。"""
    if not text.strip():
        raise ValueError("业务描述是空的,请先填写或上传 .txt 文件")
    profile, meta = extract_profile(
        description=text, client=client, hints=hints, industry=industry
    )
    slug = slugify(profile.company)
    desc_path = _unique_path(profile_dir / DESCRIPTION_DIRNAME / UPLOAD_SUBDIR, slug, ".txt")
    desc_path.write_text(text, encoding="utf-8")
    profile_path = _unique_path(profile_dir / UPLOAD_SUBDIR, slug, ".json")
    dump_profile(profile, profile_path)
    return profile_path, profile, meta, desc_path


def _notice(kind: str, title: str, detail: str = "") -> str:
    body = f"<strong>{_e(title)}</strong>"
    if detail:
        body += f"<pre>{_e(detail)}</pre>" if kind == "error" else f" {_e(detail)}"
    return f'<div class="notice {kind}">{body}</div>'


def _company_cell(company: str) -> str:
    """占位企业名标灰,免得看起来真有个客户叫「未命名企业」。"""
    if not company or company == PLACEHOLDER_COMPANY:
        return f'<span class="muted">{PLACEHOLDER_COMPANY}</span>'
    return _e(company)


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="topbar"><div class="wrap">
<a class="brand" href="/"><span class="dot"></span>AI 落地机会引擎</a>
<nav>
<a href="/">首页</a>
<a href="/#import">数据导入</a>
<a href="/#profiles">画像库</a>
<a href="/#runs">运行历史</a>
</nav>
<span class="spacer"></span>
</div></div>
{body}
<script>{UPLOAD_JS}</script>
</body>
</html>
"""


def _steps() -> str:
    items = [
        ("1", "导入数据", "下载模板填写,或直接贴一段业务描述让模型抽取成结构化画像。"),
        ("2", "跑诊断", "确定性引擎算 V / F / C / R、ROI 区间与合规红线,再生成落地方案。"),
        ("3", "看报告", "机会地图、方案明细、十项自检,可导出 Markdown 或打印成 PDF。"),
    ]
    return (
        '<div class="steps">'
        + "".join(
            f'<div class="step"><span class="no">{no}</span><h3>{name}</h3>'
            f'<p class="note">{desc}</p></div>'
            for no, name, desc in items
        )
        + "</div>"
    )


def _import_section(message: str = "", message_kind: str = "") -> str:
    notice = _notice(message_kind, message) if message else ""
    return f"""<section id="import">
<div class="section-head"><h2>1 · 导入数据</h2>
<span class="note">引擎只认结构化画像,先选一种方式把业务情况交进来。</span></div>
{notice}
<div class="card">
  <h3>先拿模板</h3>
  <p class="note">JSON 是引擎的输入格式;嫌手填麻烦就下描述问卷,写完让模型抽取。</p>
  <div class="btn-row" style="margin-top:12px">
    <a class="btn" href="/templates/profile.json">下载画像模板(JSON)</a>
    <a class="btn" href="/templates/description.txt">下载业务描述问卷(TXT)</a>
    <a class="btn" href="/templates/example.json">下载完整示例画像(JSON)</a>
  </div>
</div>
<div class="grid cols-2" style="margin-top:14px">
  <form class="card" method="post" action="/import/description" data-busy="正在抽取 …"
        data-busy-note="desc-busy">
    <h3>A · 我有业务描述,让模型抽取</h3>
    <p class="note">需要项目根目录 <span class="mono">.env</span> 里配好模型地址与密钥,
      抽取会真实调用模型,通常十几秒到一分钟。</p>
    <div class="field" style="margin-top:12px">
      <label for="desc">业务描述(可粘贴,也可选文件自动载入)</label>
      <textarea id="desc" name="description"
        placeholder="例如:我们是一家做汽车零部件的制造企业,主要给主机厂配套 …"></textarea>
      <input type="file" accept=".txt,.md,.text" data-fill="#desc" data-note="desc-file">
      <span class="hint" id="desc-file">支持 .txt / .md,内容会读进上面的输入框</span>
    </div>
    <div class="field">
      <label for="industry">行业(可选,留空由模型判断)</label>
      <input id="industry" name="industry" type="text"
        placeholder="零售电商 / 连锁服务 / 制造">
    </div>
    <div class="field">
      <label for="hints">补充信息(可选,用来补缺口)</label>
      <textarea id="hints" name="hints" style="min-height:80px"
        placeholder="例如:来料检验去年漏检 12 次,单次损失约 7 万元"></textarea>
    </div>
    <div class="btn-row"><button class="btn btn-primary" type="submit">抽取画像</button></div>
    <div class="notice info" id="desc-busy" style="display:none;margin-top:12px">
      正在调用模型抽取画像,请勿关闭页面 …</div>
  </form>
  <form class="card" method="post" action="/import/profile">
    <h3>B · 我已按模板填好 JSON</h3>
    <p class="note">校验通过才入库;失败会告诉你具体哪个字段不对,不会写坏数据。</p>
    <div class="field" style="margin-top:12px">
      <label for="profilejson">画像 JSON(可粘贴,也可选文件自动载入)</label>
      <textarea id="profilejson" name="payload" class="mono"
        placeholder='{{"company": "…", "industry": "…", "nodes": [ … ]}}'></textarea>
      <input type="file" accept=".json" data-fill="#profilejson" data-note="json-file">
      <span class="hint" id="json-file">支持 .json,内容会读进上面的输入框</span>
    </div>
    <div class="btn-row"><button class="btn btn-primary" type="submit">校验并导入</button></div>
  </form>
</div>
</section>"""


def _profiles_section(profile_dir: Path) -> str:
    items = list_profiles(profile_dir)
    if not items:
        return (
            '<section id="profiles"><div class="section-head"><h2>2 · 画像库</h2></div>'
            '<div class="card"><p class="muted" style="margin:0">'
            "还没有画像,先在上面导入一份。</p></div></section>"
        )
    rows = []
    for item in items:
        badge = (
            '<span class="pill ok">无缺口</span>'
            if not item["missing"]
            else f'<span class="pill warn">缺 {item["missing"]} 项</span>'
        )
        link = f"/profiles/{quote(item['name'])}"
        title, note = item["company"], item["label"]
        if title == PLACEHOLDER_COMPANY:
            # S2 没从描述里读到企业名时会留占位值,直接列表出来就是一整列「未命名企业」
            title = item["label"]
            note = (
                f"画像里的 company 是占位值「{PLACEHOLDER_COMPANY}」,"
                "可在画像页补充信息后重抽"
            )
        rows.append(
            f'<tr><td><a href="{link}">{_e(title)}</a>'
            f'<div class="note">{_e(note)}</div></td>'
            f'<td>{_e(item["industry"])}</td>'
            f'<td class="num">{item["nodes"]}</td>'
            f'<td class="num">{badge}</td>'
            f'<td><a class="btn btn-sm" href="{link}">查看并运行</a></td></tr>'
        )
    head = (
        "<tr><th>画像</th><th>行业</th><th class='num'>节点数</th>"
        "<th class='num'>缺口</th><th></th></tr>"
    )
    return (
        f'<section id="profiles"><div class="section-head"><h2>2 · 画像库</h2>'
        f'<span class="note">{len(items)} 份可用画像</span></div>'
        f'<div class="card" style="padding:6px 10px"><table><thead>{head}</thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _runs_section(run_dir: Path) -> str:
    runs = list_runs(run_dir)
    if runs:
        rows = "".join(
            f'<tr><td><a class="mono" href="/runs/{item["run_id"]}">{item["run_id"]}</a></td>'
            f"<td>{_company_cell(item['company'])}</td><td>{_e(item['industry'])}</td>"
            f"<td>{_e(item['composer'])}</td>"
            f'<td class="num">{item["plans"]}</td>'
            f'<td class="num">{item["passed"]}/{item["checks"]}</td>'
            f'<td class="note">{_e(item["generated_at"])} UTC</td>'
            f'<td><a class="btn btn-sm" href="/runs/{item["run_id"]}">打开</a> '
            f'<a class="btn btn-sm" href="/runs/{item["run_id"]}/report.md">导出</a></td></tr>'
            for item in runs
        )
        head = (
            "<tr><th>运行 ID</th><th>企业</th><th>行业</th><th>撰写</th>"
            "<th class='num'>方案数</th><th class='num'>自检</th><th>时间</th>"
            "<th></th></tr>"
        )
        body = (
            f'<div class="card" style="padding:6px 10px"><table><thead>{head}</thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )
    else:
        body = (
            '<div class="card"><p class="muted" style="margin:0">还没有运行记录。</p></div>'
        )
    legacy = count_legacy_runs(run_dir)
    if legacy:
        body += (
            f'<p class="hint" style="margin-top:10px">另有 {legacy} 个更早的运行目录缺少'
            " run_meta.json(无法重建结果),重新跑一次即可在这里看到。</p>"
        )
    return (
        f'<section id="runs"><div class="section-head"><h2>3 · 运行历史</h2>'
        f'<span class="note">{len(runs)} 次可回看</span></div>{body}</section>'
    )


def render_landing(
    run_dir: Path = DEFAULT_RUN_DIR,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    message: str = "",
    message_kind: str = "",
) -> str:
    body = f"""<div class="wrap">
<div class="page-head">
  <h1>AI 落地机会引擎</h1>
  <div class="sub">把企业的业务情况,变成可排序、可追溯、可落地的 AI 机会清单。</div>
</div>
{_steps()}
{_import_section(message, message_kind)}
{_profiles_section(profile_dir)}
{_runs_section(run_dir)}
<footer>本地运行,数据不出机器;模型调用只发生在你配置的端点上。</footer>
</div>"""
    return _layout("AI 落地机会引擎", body)


def _profile_params_table(profile: BusinessProfile) -> str:
    params = profile.params
    human = params.human_cost_annual_per_fte
    rows = [
        ("年人力成本 / FTE", f"{human.value:,.0f} 元", human.source.value, human.note),
        ("可自动化折扣", f"{params.discount_factor:g}", "引擎参数", "自动化收益折减系数"),
        ("每周工时", f"{params.hours_per_fte_week:g} 小时", "引擎参数", "单个 FTE 的周工时"),
        ("Top N 方案", str(params.top_n_plans), "引擎参数", "报告里保留的方案数上限"),
    ]
    body = "".join(
        f"<tr><td>{name}</td><td class='num'>{value}</td><td>{source}</td>"
        f"<td class='note'>{_e(note)}</td></tr>"
        for name, value, source, note in rows
    )
    return (
        "<table><thead><tr><th>参数</th><th class='num'>取值</th><th>来源</th>"
        "<th>说明</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def render_profile_page(
    profile: BusinessProfile,
    name: str,
    *,
    has_description: bool = False,
    message: str = "",
    message_kind: str = "",
) -> str:
    gaps = gap_report(profile) if profile.missing else ""
    gap_html = (
        f'<div class="notice warn" style="margin-bottom:14px"><strong>画像有 '
        f"{len(profile.missing)} 项缺口</strong><pre>{_e(gaps)}</pre>"
        "缺口不补齐,引擎不会进入候选生成(docs/06 §3)。可在下面补充后重新抽取。</div>"
        if profile.missing
        else '<div class="notice ok" style="margin-bottom:14px">'
        "画像完整,可以直接运行诊断。</div>"
    )
    node_rows = "".join(
        f"<tr><td>{_e(node.name)}</td><td>{_e(node.domain)}</td>"
        f"<td>{_e(node.role or '—')}</td>"
        f"<td class='num'>{node.hours_per_week:g}</td>"
        f"<td class='num'>{node.volume_per_month:,.0f}</td>"
        f"<td class='note'>{_e(node.pain_point or '—')}</td>"
        f"<td class='note'>{_e('、'.join(node.data_required) or '—')}</td></tr>"
        for node in profile.nodes
    )
    source_rows = "".join(
        f"<tr><td>{_e(source.name)}</td><td>{_e(source.carrier or '—')}</td>"
        f"<td class='num'>{source.availability * 100:.0f}%</td>"
        f"<td>{'是' if source.has_pii else '否'}</td></tr>"
        for source in profile.data_sources
    )
    if has_description:
        hints_form = f"""<form class="card" method="post"
      action="/profiles/{quote(name)}/re-extract" style="margin-top:14px"
      data-busy="正在重新抽取 …" data-busy-note="reextract-busy">
  <h3>补充信息后重新抽取</h3>
  <p class="note">针对上面的缺口补充事实(有数字就写数字),模型会基于原描述 + 补充重抽一遍。</p>
  <div class="field" style="margin-top:12px">
    <label for="hints">补充信息</label>
    <textarea id="hints" name="hints" style="min-height:110px"
      placeholder="例如:来料检验去年漏检 12 次,单次返工与停线损失平均 7 万元;漏检率基线 3.5%"></textarea>
  </div>
  <div class="btn-row"><button class="btn" type="submit">重新抽取</button></div>
  <div class="notice info" id="reextract-busy" style="display:none;margin-top:12px">
    正在调用模型重新抽取 …</div>
</form>"""
    else:
        hints_form = (
            '<p class="hint" style="margin-top:12px">这份画像是直接导入的 JSON,没有源描述,'
            "无法自动重抽;补齐后重新导入 JSON 即可。</p>"
        )
    notice = _notice(message_kind, message) if message else ""
    body = f"""<div class="wrap">
<div class="page-head">
  <h1>{_e(profile.company)}</h1>
  <div class="sub">{_e(profile.industry)} · 员工 {profile.scale.headcount:,} 人 ·
    年营收 {profile.scale.revenue:,.0f} 元 · 网点 {profile.scale.sites} 个 ·
    流程节点 {len(profile.nodes)} 个</div>
</div>
<section style="margin-top:20px">
  <div class="btn-row" style="margin-bottom:14px">
    <a class="btn btn-sm" href="/">← 返回首页</a>
    <a class="btn btn-sm" href="/profiles/{quote(name)}/download">下载这份画像 JSON</a>
  </div>
  {notice}
  {gap_html}
  <form class="card" method="post" action="/profiles/{quote(name)}/run"
        data-busy="正在跑诊断 …" data-busy-note="run-busy">
    <h3>运行诊断</h3>
    <p class="note">模板撰写是纯计算,秒级完成;大模型撰写会真实调用模型,通常几分钟。</p>
    <div class="choice" style="margin-top:12px">
      <label><input type="radio" name="composer" value="template" checked> 模板撰写(秒级)</label>
      <label><input type="radio" name="composer" value="llm"> 大模型撰写(需 .env)</label>
    </div>
    <div class="btn-row" style="margin-top:14px">
      <button class="btn btn-primary" type="submit">开始运行</button>
    </div>
    <div class="notice info" id="run-busy" style="display:none;margin-top:12px">
      正在运行流水线,请勿关闭页面 …</div>
  </form>
  {hints_form}
</section>
<section><div class="section-head"><h2>流程节点</h2></div>
<div class="card" style="padding:6px 10px"><table><thead><tr>
<th>节点</th><th>业务域</th><th>岗位</th><th class="num">每周工时</th>
<th class="num">月处理量</th><th>痛点</th><th>数据要求</th></tr></thead>
<tbody>{node_rows}</tbody></table></div></section>
<section><div class="section-head"><h2>数据源</h2></div>
<div class="card" style="padding:6px 10px"><table><thead><tr>
<th>数据源</th><th>载体</th><th class="num">可得性</th><th>含个人信息</th></tr></thead>
<tbody>{source_rows}</tbody></table></div></section>
<section><div class="section-head"><h2>计算参数</h2></div>
<div class="card" style="padding:6px 10px">{_profile_params_table(profile)}</div></section>
</div>"""
    return _layout(f"{profile.company} · 画像", body)


class Handler(BaseHTTPRequestHandler):
    run_dir: Path = DEFAULT_RUN_DIR
    profile_dir: Path = DEFAULT_PROFILE_DIR
    server_version = "AoeWeb/0.2"

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")

    # ---------- 输出工具 ---------- #
    def _send(self, body: str, status: int = 200, content_type="text/html; charset=utf-8"):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_download(self, payload: bytes, filename: str, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}"
        )
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _error_page(self, title: str, detail: str, status: int = 400) -> None:
        self._send(
            _layout(
                title,
                '<div class="wrap"><section><div class="notice error">'
                f"<strong>{_e(title)}</strong><pre>{_e(detail)}</pre></div>"
                '<p><a class="btn" href="/">返回首页</a></p></section></div>',
            ),
            status=status,
        )

    def _landing_error(self, message: str) -> None:
        self._send(
            render_landing(
                self.run_dir, self.profile_dir, message=message, message_kind="error"
            ),
            status=400,
        )

    def _profile_path(self, name: str) -> Path:
        """只允许访问画像目录下的 .json,避免路径穿越。"""
        root = Path(self.profile_dir).resolve()
        candidate = (root / name).resolve()
        if root not in candidate.parents or candidate.suffix != ".json":
            raise FileNotFoundError(name)
        if not candidate.is_file():
            raise FileNotFoundError(f"画像不存在:{name}")
        return candidate

    def _llm_client(self):
        from argparse import Namespace

        from .cli import _prepare_client

        return _prepare_client(Namespace(model=None, base_url=None, env_file=None))

    # ---------- 读取 ---------- #
    def do_GET(self) -> None:  # noqa: N802
        # 画像名是中文,浏览器会送来百分号编码,这里先解码再匹配
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/":
                self._send(render_landing(self.run_dir, self.profile_dir))
                return
            if path == "/templates/profile.json":
                payload = json.dumps(
                    build_profile_template(self.profile_dir), ensure_ascii=False, indent=2
                )
                self._send_download(
                    payload.encode("utf-8") + b"\n",
                    "aoe-profile-template.json",
                    "application/json; charset=utf-8",
                )
                return
            if path == "/templates/example.json":
                example = (Path(self.profile_dir) / "retail_ecommerce_demo.json").read_bytes()
                self._send_download(
                    example, "aoe-profile-example.json", "application/json; charset=utf-8"
                )
                return
            if path == "/templates/description.txt":
                self._send_download(
                    DESCRIPTION_TEMPLATE.encode("utf-8"),
                    "aoe-description-template.txt",
                    "text/plain; charset=utf-8",
                )
                return
            if path.startswith("/profiles/"):
                tail = path[len("/profiles/") :]
                if tail.endswith("/download"):
                    target = self._profile_path(tail[: -len("/download")])
                    self._send_download(
                        target.read_bytes(), target.name, "application/json; charset=utf-8"
                    )
                    return
                if not tail or tail.endswith("/"):
                    self._error_page("页面不存在", f"没有这个地址:{path}", status=404)
                    return
                profile = load_profile(self._profile_path(tail))
                self._send(
                    render_profile_page(
                        profile,
                        tail,
                        has_description=resolve_description_path(
                            tail, self.profile_dir
                        ).exists(),
                    )
                )
                return
            if path.startswith("/runs/"):
                parts = path.strip("/").split("/")
                run_path = self.run_dir / parts[1]
                if len(parts) in (2, 3):
                    try:
                        result = load_result(run_path)
                    except FileNotFoundError as exc:
                        self._error_page("找不到这次运行", str(exc), status=404)
                        return
                    if len(parts) == 3:
                        if parts[2] != "report.md":
                            self._error_page("页面不存在", f"没有这个地址:{path}", status=404)
                            return
                        self._send_download(
                            render_report(result).encode("utf-8"),
                            f"{run_path.name}.md",
                            "text/markdown; charset=utf-8",
                        )
                        return
                    nav = (
                        '<div class="topbar"><div class="wrap">'
                        '<a class="brand" href="/"><span class="dot"></span>'
                        'AI 落地机会引擎</a>'
                        '<nav><a href="/">首页</a><a href="/#runs">运行历史</a></nav>'
                        '<span class="spacer"></span>'
                        f'<a class="btn btn-sm" href="/runs/{parts[1]}/report.md">'
                        "导出 Markdown</a>"
                        '<button class="btn btn-sm" type="button" '
                        'onclick="window.print()">打印 / 存 PDF</button>'
                        "</div></div>"
                    )
                    self._send(
                        render_html(result).replace("<body>", "<body>" + nav, 1)
                    )
                    return
            self._error_page("页面不存在", f"没有这个地址:{path}", status=404)
        except FileNotFoundError as exc:
            self._error_page("没找到", str(exc), status=404)
        except Exception:
            detail = traceback.format_exc()
            print(detail)
            self._error_page("出错了", detail, status=500)

    # ---------- 写入 ---------- #
    def _form(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return parse_qs(self.rfile.read(length).decode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            form = self._form()
            if path == "/import/profile":
                text = (form.get("payload") or [""])[0]
                try:
                    target, _profile = import_profile_json(
                        text, profile_dir=self.profile_dir
                    )
                except ValueError as exc:
                    self._landing_error(f"导入失败:{exc}")
                    return
                self._redirect(f"/profiles/{quote(profile_name(target, self.profile_dir))}")
                return
            if path == "/import/description":
                text = (form.get("description") or [""])[0]
                industry = (form.get("industry") or [""])[0]
                hints = (form.get("hints") or [""])[0]
                try:
                    client = self._llm_client()
                except Exception as exc:
                    self._landing_error(
                        "无法调用模型,请先在项目根目录 .env 里配置 AOE_BASE_URL / "
                        f"AOE_API_KEY / AOE_MODEL。原因:{exc}"
                    )
                    return
                try:
                    target, _profile, _meta, _desc = import_description(
                        text,
                        client=client,
                        industry=industry,
                        hints=hints,
                        profile_dir=self.profile_dir,
                    )
                except Exception as exc:
                    self._landing_error(f"抽取失败:{exc}")
                    return
                self._redirect(f"/profiles/{quote(profile_name(target, self.profile_dir))}")
                return
            if path.startswith("/profiles/"):
                tail = path[len("/profiles/") :]
                if tail.endswith("/run"):
                    name = tail[: -len("/run")]
                    profile = load_profile(self._profile_path(name))
                    composer = (form.get("composer") or ["template"])[0]
                    client = self._llm_client() if composer == "llm" else None
                    result = run_pipeline(
                        profile=profile, run_dir=self.run_dir, composer=composer, client=client
                    )
                    self._redirect(f"/runs/{result.run_id}")
                    return
                if tail.endswith("/re-extract"):
                    name = tail[: -len("/re-extract")]
                    description = resolve_description_path(name, self.profile_dir)
                    if not description.exists():
                        self._error_page(
                            "没有可用的源描述",
                            "这份画像是直接导入的 JSON,无法自动重抽;补齐后重新导入 JSON。",
                        )
                        return
                    hints = (form.get("hints") or [""])[0]
                    profile, _meta = extract_profile(
                        description=description.read_text(encoding="utf-8"),
                        client=self._llm_client(),
                        hints=hints,
                    )
                    target = self._profile_path(name)
                    dump_profile(profile, target)
                    self._redirect(
                        f"/profiles/{quote(profile_name(target, self.profile_dir))}"
                    )
                    return
            self._error_page("页面不存在", f"没有这个地址:{path}", status=404)
        except Exception:
            detail = traceback.format_exc()
            print(detail)
            self._error_page("操作失败", detail, status=500)


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    run_dir: Path = DEFAULT_RUN_DIR,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
) -> None:
    handler = type(
        "BoundHandler", (Handler,), {"run_dir": run_dir, "profile_dir": profile_dir}
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"本地界面已启动:http://{host}:{port}/")
    print("按 Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
