"""命令行入口。

    python -m aoe validate data/profiles/xxx.json
    python -m aoe extract  描述.txt --out data/profiles/new.json
    python -m aoe run      data/profiles/xxx.json --composer llm
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .knowledge import load_knowledge
from .llm import LLMClient, LLMConfig, LLMError
from .pipeline import run_pipeline
from .profiles import dump_profile, load_profile
from .render import render_report
from .render_html import render_html
from .stages.extractor import extract_profile, gap_report


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _prepare_client(args: argparse.Namespace) -> LLMClient:
    config = LLMConfig.from_env(env_file=getattr(args, "env_file", None))
    if getattr(args, "model", None):
        config.model = args.model
    if getattr(args, "base_url", None):
        config.base_url = args.base_url
    config.require_key()
    return LLMClient(config)


def _cmd_validate(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    kb = load_knowledge()
    print(f"画像校验通过:{profile.company}({profile.industry})")
    print(
        f"  节点 {len(profile.nodes)} 个 / 系统 {len(profile.systems)} 个 / "
        f"数据源 {len(profile.data_sources)} 个"
    )
    print(
        f"  知识库:能力 {len(kb.capabilities)} 类 / 行业场景 {len(kb.scenarios)} 条 / "
        f"合规规则 {len(kb.compliance_rules)} 条"
    )
    if profile.missing:
        print(gap_report(profile))
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    """S2 业务建模:自由文本 → 结构化画像。"""
    text = Path(args.description).read_text(encoding="utf-8-sig")
    client = _prepare_client(args)
    print(f"按模型 {client.config.model} 抽取画像 ...")
    hints = args.hints or ""
    if args.hints_file:
        hints = (hints + "\n" if hints else "") + Path(args.hints_file).read_text(
            encoding="utf-8-sig"
        )
    profile, meta = extract_profile(
        description=text,
        client=client,
        hints=hints,
        industry=args.industry or "",
    )
    out = Path(args.out)
    dump_profile(profile, out)
    print(f"画像已写入:{out}")
    print(
        f"  节点 {len(profile.nodes)} 个 / 系统 {len(profile.systems)} 个 / "
        f"数据源 {len(profile.data_sources)} 个 / 校验尝试 {meta['attempts']} 次"
    )
    if meta["errors"]:
        print(f"  期间修正过 {len(meta['errors'])} 轮 Schema 报错")
    gap = gap_report(profile)
    if gap:
        print(gap)
        print("提示:补齐上述信息后重新生成,或手工编辑画像文件。")
    usage = client.ledger.summary()
    print(
        f"  token 用量:prompt {usage['prompt_tokens']} / completion "
        f"{usage['completion_tokens']}(含推理 {usage['reasoning_tokens']}),"
        f"耗时 {usage['seconds']}s"
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    client = _prepare_client(args) if args.composer == "llm" else None
    if client is not None:
        print(f"方案撰写:强模型 {client.config.model}")

    run_dir = None if args.no_persist else Path(args.run_dir)
    result = run_pipeline(
        profile=profile,
        run_dir=run_dir,
        top_n=args.top_n,
        composer=args.composer,
        client=client,
    )
    report = render_report(result)

    out_path = (
        Path(args.out) if args.out else Path("outputs") / f"{profile.company}-report.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    # 同一份结果再渲染一份自包含 HTML,方便直接打开看效果(docs/05 §7:明细要跟着结论一起给人看)。
    html_path = out_path.with_suffix(".html")
    html_path.write_text(render_html(result), encoding="utf-8")

    print(f"运行 ID:{result.run_id}")
    print(
        f"候选机会点 {len(result.all_opportunities)} 个,进入排序 {len(result.ranked)} 个,"
        f"输出方案 {len(result.plans)} 个(撰写方式:{result.composer})"
    )
    passed = sum(1 for c in result.critiques if c.passed)
    print(f"自检通过 {passed}/{len(result.critiques)}")
    for gap in result.gaps:
        print(f"  缺口:{gap}")
    if result.usage:
        by_purpose = result.usage.get("by_purpose", {})
        print(
            f"  token 用量:prompt {result.usage['prompt_tokens']} / completion "
            f"{result.usage['completion_tokens']}(含推理 {result.usage['reasoning_tokens']})"
        )
        for purpose, stats in by_purpose.items():
            print(
                f"    {purpose}:{stats['calls']} 次调用,重试 {stats['retries']} 次,"
                f"completion {stats['completion_tokens']} tokens"
            )
    print(f"报告已写入:{out_path}")
    print(f"HTML 报告已写入:{html_path}")
    if run_dir:
        print(f"中间态已写入:{run_dir / result.run_id}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .webapp import serve

    serve(
        host=args.host,
        port=args.port,
        run_dir=Path(args.run_dir),
        profile_dir=Path(args.profile_dir),
    )
    return 0


def _llm_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model", default=None, help="覆盖 AOE_MODEL,例如 glm-5.3-flash / kimi-k2.6"
    )
    parser.add_argument("--base-url", default=None, help="覆盖 AOE_BASE_URL")
    parser.add_argument("--env-file", default=None, help="指定 .env 路径,默认读当前目录 .env")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aoe", description="AI 落地契机引擎")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="校验业务画像是否符合 Schema")
    validate.add_argument("profile", help="画像 JSON 路径")
    validate.set_defaults(func=_cmd_validate)

    extract = sub.add_parser("extract", help="S2:把业务描述抽取为结构化画像")
    extract.add_argument("description", help="业务描述文本文件路径")
    extract.add_argument("--out", required=True, help="画像输出路径")
    extract.add_argument("--industry", default="", help="已确认的行业")
    extract.add_argument("--hints", default="", help="补充信息")
    extract.add_argument("--hints-file", default=None, help="补充信息文本文件路径")
    _llm_options(extract)
    extract.set_defaults(func=_cmd_extract)

    run = sub.add_parser("run", help="跑完整流水线并输出 Markdown 报告")
    run.add_argument("profile", help="画像 JSON 路径")
    run.add_argument("--out", help="报告输出路径,默认 outputs/<公司名>-report.md")
    run.add_argument("--top-n", type=int, default=None, help="生成方案的机会点数量上限")
    run.add_argument("--run-dir", default="work/runs", help="中间态落盘目录,默认 work/runs")
    run.add_argument("--no-persist", action="store_true", help="不落盘中间态")
    run.add_argument(
        "--composer",
        choices=("template", "llm"),
        default="template",
        help="template=模板组装(默认,不调模型);llm=强模型撰写",
    )
    _llm_options(run)
    run.set_defaults(func=_cmd_run)

    serve = sub.add_parser("serve", help="启动本地网页界面(选画像、跑诊断、看报告)")
    serve.add_argument("--host", default="127.0.0.1", help="默认只监听本机")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--run-dir", default="work/runs", help="运行历史目录")
    serve.add_argument("--profile-dir", default="data/profiles", help="画像目录")
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, LLMError) as exc:
        print(f"错误:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
