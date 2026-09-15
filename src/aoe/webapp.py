"""本地 Web 界面 —— 不用记命令行参数,浏览器里选画像、点运行、看报告。

只用标准库(http.server),不引入新依赖;页面复用 render_html 的自包含样式。
    python -m aoe serve
"""

from __future__ import annotations

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import Critique, Opportunity, Plan, Score
from .pipeline import run_pipeline
from .profiles import load_profile
from .render import render_report
from .render_html import CSS, render_html
from .result import RunResult

#: 运行历史目录 / 画像目录(相对仓库根)
DEFAULT_RUN_DIR = Path("work/runs")
DEFAULT_PROFILE_DIR = Path("data/profiles")

NAV_CSS = """
.topnav{background:#0f1b2d;color:#fff}
.topnav .wrap{display:flex;align-items:center;gap:18px;padding:11px 20px;max-width:1080px;margin:0 auto}
.topnav a{color:#cfe0ff;text-decoration:none;font-size:14px}
.topnav a:hover{color:#fff}
.topnav .spacer{flex:1}
form.run{display:grid;gap:12px;max-width:560px}
form.run label{font-size:14px;color:var(--muted)}
select,input[type=text]{padding:8px 11px;border:1px solid var(--line);border-radius:9px;
  font-size:14px;background:#fff;min-width:280px}
.radio{display:flex;gap:18px;font-size:14px}
.radio label{display:flex;gap:6px;align-items:center;color:var(--ink)}
.btn-primary{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:600}
.btn-primary:hover{background:#1a5fd0;color:#fff}
.hint{color:var(--muted);font-size:12.5px}
.error{background:#fdf3f3;border:1px solid #f2c9c9;color:var(--bad);
  border-radius:10px;padding:12px 14px;font-size:14px;white-space:pre-wrap}
"""

PROFILE_LABELS = {
    "retail_ecommerce_demo.json": "某区域连锁零售企业(示例,数据完整)",
    "manufacturing_complete.json": "苏州某汽车零部件制造企业(由描述抽取 + 补全)",
    "manufacturing_extracted.json": "苏州某汽车零部件制造企业(仅抽取,含缺口)",
}


def _keyword(profile_path: Path) -> str:
    try:
        return json.loads(profile_path.read_text(encoding="utf-8")).get("company", "")
    except (OSError, json.JSONDecodeError):
        return ""


def list_profiles(profile_dir: Path = DEFAULT_PROFILE_DIR) -> list[dict]:
    items = []
    for path in sorted(profile_dir.glob("*.json")):
        items.append(
            {
                "name": path.name,
                "path": path,
                "company": _keyword(path) or path.stem,
                "label": PROFILE_LABELS.get(path.name, path.stem),
            }
        )
    return items


def list_runs(run_dir: Path = DEFAULT_RUN_DIR) -> list[dict]:
    """列出运行历史。只认带 run_meta.json 的目录,避免把半途失败的运行当成可回放产物。"""
    items = []
    if not run_dir.exists():
        return items
    for path in sorted(run_dir.iterdir(), reverse=True):
        meta_path = path / "run_meta.json"
        if not path.is_dir() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        profile = meta.get("profile", {})
        critiques = _read_json(path / "s8_critiques.json", [])
        items.append(
            {
                "run_id": path.name,
                "generated_at": str(meta.get("generated_at", ""))[:19].replace("T", " "),
                "company": profile.get("company", "未命名企业"),
                "industry": profile.get("industry", ""),
                "composer": meta.get("composer", "template"),
                "plans": len(_read_json(path / "s7_plans.json", [])),
                "passed": sum(1 for item in critiques if item.get("passed")),
                "checks": len(critiques),
            }
        )
    return items


def count_legacy_runs(run_dir: Path = DEFAULT_RUN_DIR) -> int:
    """统计没有 run_meta.json 的旧运行目录:它们无法重建结果,只能重新生成。"""
    if not run_dir.exists():
        return 0
    return sum(
        1
        for path in run_dir.iterdir()
        if path.is_dir() and not (path / "run_meta.json").exists()
    )


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_result(run_path: Path) -> RunResult:
    """从落盘产物重建一次运行,用于重新渲染(docs/05 §7 的可回放要求)。"""
    meta = json.loads((run_path / "run_meta.json").read_text(encoding="utf-8"))
    usage = _read_json(run_path / "llm_usage.json", {})
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
            "usage": usage,
        }
    )


def _page(title: str, body: str, *, nav: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{CSS}{NAV_CSS}</style>
</head>
<body>
<nav class="topnav"><div class="wrap">
<a href="/">机会引擎</a><a href="/">运行历史</a>
<span class="spacer"></span>{nav}
</div></nav>
{body}
</body>
</html>
"""


def render_landing(
    run_dir: Path = DEFAULT_RUN_DIR, profile_dir: Path = DEFAULT_PROFILE_DIR
) -> str:
    profiles = list_profiles(profile_dir)
    options = "".join(
        f'<option value="{item["name"]}">{item["label"]}</option>' for item in profiles
    )
    runs = list_runs(run_dir)
    if runs:
        rows = "".join(
            f'<tr><td><a href="/runs/{item["run_id"]}">{item["run_id"]}</a></td>'
            f'<td>{item["company"]}</td><td>{item["industry"]}</td>'
            f'<td>{item["composer"]}</td><td class="num">{item["plans"]}</td>'
            f'<td class="num">{item["passed"]}/{item["checks"]}</td>'
            f'<td class="note">{item["generated_at"]} UTC</td>'
            f'<td><a href="/runs/{item["run_id"]}/report.md">Markdown</a></td></tr>'
            for item in runs
        )
        runs_html = (
            '<div class="card"><table><thead><tr><th>运行 ID</th><th>企业</th><th>行业</th>'
            '<th>撰写</th><th class="num">方案数</th><th class="num">自检</th>'
            f"<th>时间</th><th>导出</th></tr></thead><tbody>{rows}</tbody></table></div>"
        )
    else:
        runs_html = '<p class="empty">还没有运行记录,先在上面跑一次。</p>'
    legacy = count_legacy_runs(run_dir)
    if legacy:
        runs_html += (
            f'<p class="hint">另有 {legacy} 个更早的运行目录缺少 run_meta.json'
            "(无法重建结果与画像),重新跑一次即可在这里看到。</p>"
        )

    body = f"""<div class="wrap">
<header class="hero" style="margin-top:0"><div class="wrap" style="padding:34px 0">
<h1>AI 落地机会引擎</h1>
<div class="meta"><span>选一个画像 → 点运行 → 看报告</span></div>
</div></header>
<section><h2>跑一次诊断</h2>
<div class="card"><form class="run" method="post" action="/run">
<label for="profile">业务画像</label>
<select id="profile" name="profile">{options}</select>
<div class="radio">
<label><input type="radio" name="composer" value="template" checked> 模板撰写(秒级)</label>
<label><input type="radio" name="composer" value="llm"> 大模型撰写(需配置 .env,数分钟)</label>
</div>
<div><button class="btn-primary" type="submit">运行诊断</button></div>
<div class="hint">大模型模式会真实调用 API 并消耗 token;慢是正常的,页面会等到跑完。</div>
</form></div></section>
<section><h2>运行历史({len(runs)})</h2>{runs_html}</section>
</div>"""
    return _page("AI 落地机会引擎", body)


class Handler(BaseHTTPRequestHandler):
    run_dir: Path = DEFAULT_RUN_DIR
    profile_dir: Path = DEFAULT_PROFILE_DIR
    server_version = "AoeWeb/0.1"

    def log_message(self, fmt, *args):  # 精简日志,避免刷屏
        print(f"  {self.address_string()} {fmt % args}")

    def _send(self, body: str, status: int = 200, content_type="text/html; charset=utf-8"):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send(render_landing(self.run_dir, self.profile_dir))
                return
            if path.startswith("/runs/"):
                parts = path.strip("/").split("/")
                run_path = self.run_dir / parts[1]
                if len(parts) == 2:
                    if not (run_path / "run_meta.json").exists():
                        self._send(
                            _page("未找到", '<div class="wrap"><section>'
                                  '<div class="error">没有这次运行的记录。</div>'
                                  '<p><a href="/">返回</a></p></section></div>'),
                            status=404,
                        )
                        return
                    result = load_result(run_path)
                    nav = (
                        '<nav class="topnav"><div class="wrap">'
                        '<a href="/">← 返回运行列表</a><span class="spacer"></span>'
                        f'<a href="/runs/{parts[1]}/report.md">导出 Markdown</a>'
                        '<a href="javascript:window.print()">打印 / 存 PDF</a>'
                        "</div></nav>"
                    )
                    html = render_html(result)
                    html = html.replace("</head>", f"<style>{NAV_CSS}</style></head>", 1)
                    self._send(html.replace("<body>", "<body>" + nav, 1))
                    return
                if len(parts) == 3 and parts[2] == "report.md":
                    if not (run_path / "run_meta.json").exists():
                        self._send(
                            _page("未找到", '<div class="wrap"><section>'
                                  '<div class="error">这次运行缺少 run_meta.json,'
                                  '无法重建结果,请重新运行一次。</div>'
                                  '<p><a href="/">返回</a></p></section></div>'),
                            status=404,
                        )
                        return
                    result = load_result(run_path)
                    payload = render_report(result).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="{run_path.name}.md"',
                    )
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
            self._send(
                _page("未找到", '<div class="wrap"><section><div class="error">'
                      f"没有这个地址:{path}</div>"
                      '<p><a href="/">返回首页</a></p></section></div>'),
                status=404,
            )
        except Exception:  # 服务端错误要看得见,不能被浏览器静默吞掉
            detail = traceback.format_exc()
            print(detail)
            self._send(
                _page("出错了", '<div class="wrap"><section><div class="error">'
                      f"{detail}</div></section></div>"),
                status=500,
            )

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/run":
            self._send("not found", status=404, content_type="text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        name = (form.get("profile") or [""])[0]
        composer = (form.get("composer") or ["template"])[0]
        try:
            profile = load_profile(self.profile_dir / name)
        except (OSError, ValueError) as exc:
            self._send(
                _page("画像读取失败", '<div class="wrap"><section><div class="error">'
                      f"{exc}</div><p><a href='/'>返回</a></p></section></div>"),
                status=400,
            )
            return
        try:
            client = None
            if composer == "llm":
                from .cli import _prepare_client  # 复用命令行里的 .env 与模型配置
                from argparse import Namespace

                client = _prepare_client(
                    Namespace(
                        model=None,
                        base_url=None,
                        env_file=None,
                    )
                )
            result = run_pipeline(
                profile=profile,
                run_dir=self.run_dir,
                composer=composer,
                client=client,
            )
        except Exception as exc:
            self._send(
                _page("运行失败", '<div class="wrap"><section><div class="error">'
                      f"{traceback.format_exc()}</div>"
                      f"<p class='hint'>{exc}</p><p><a href='/'>返回</a></p></section></div>"),
                status=500,
            )
            return
        self._redirect(f"/runs/{result.run_id}")


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    run_dir: Path = DEFAULT_RUN_DIR,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
) -> None:
    handler = type("BoundHandler", (Handler,), {"run_dir": run_dir, "profile_dir": profile_dir})
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"本地界面已启动:http://{host}:{port}/")
    print("按 Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
