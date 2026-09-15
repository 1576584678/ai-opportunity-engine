"""设计系统 —— 报告页与本地界面共用同一份 CSS,避免两处样式各写一套。"""

from __future__ import annotations

CSS = """
:root{
  --ink:#0f172a; --ink-2:#334155; --muted:#64748b; --muted-2:#94a3b8;
  --line:#e2e8f0; --line-2:#eef2f7; --bg:#f6f8fb; --surface:#ffffff;
  --accent:#2563eb; --accent-2:#1d4ed8; --accent-soft:#eff4ff; --accent-line:#c7d7fe;
  --ok:#15803d; --ok-soft:#eefaf1; --ok-line:#bfe6cb;
  --warn:#b45309; --warn-soft:#fef8ec; --warn-line:#f2ddb5;
  --bad:#b91c1c; --bad-soft:#fef2f2; --bad-line:#f3c9c9;
  --shadow-1:0 1px 2px rgba(15,23,42,.04), 0 1px 3px rgba(15,23,42,.06);
  --shadow-2:0 4px 12px rgba(15,23,42,.06), 0 2px 4px rgba(15,23,42,.04);
  --radius:14px; --radius-sm:10px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
    "PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;
}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
h1,h2,h3,h4{line-height:1.35;margin:0}
h2{font-size:20px;font-weight:650;letter-spacing:.1px}
h3{font-size:16px;font-weight:650}
h4{font-size:14px;font-weight:650;color:var(--ink-2);margin:18px 0 6px}
p{margin:8px 0}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px 64px}
.muted{color:var(--muted)}
.note{color:var(--muted);font-size:13px;line-height:1.65}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
.num,.mono{font-variant-numeric:tabular-nums}
.right{text-align:right}

/* ---------- 顶栏 ---------- */
.topbar{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.86);
  backdrop-filter:saturate(180%) blur(12px);border-bottom:1px solid var(--line)}
.topbar .wrap{display:flex;align-items:center;gap:22px;padding:0 24px;max-width:1120px;height:58px}
.brand{display:flex;align-items:center;gap:9px;font-weight:680;color:var(--ink)}
.brand:hover{text-decoration:none}
.brand .dot{width:9px;height:9px;border-radius:3px;background:var(--accent)}
.topbar nav{display:flex;gap:20px;font-size:14px}
.topbar nav a{color:var(--ink-2)}
.topbar nav a:hover{color:var(--accent);text-decoration:none}
.spacer{flex:1}

/* ---------- 页头 ---------- */
.page-head{padding:34px 0 6px}
.page-head h1{font-size:27px;font-weight:700;letter-spacing:-.2px}
.page-head .sub{color:var(--muted);margin-top:6px;font-size:14.5px}

/* ---------- 区块 ---------- */
section{margin-top:30px}
.section-head{display:flex;align-items:baseline;gap:12px;margin-bottom:12px}
.section-head h2{display:flex;align-items:center;gap:9px}
.section-head h2::before{content:"";width:4px;height:17px;border-radius:2px;
  background:var(--accent);display:inline-block}

/* ---------- 卡片 ---------- */
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow-1);padding:20px 22px}
.card + .card{margin-top:14px}
.grid{display:grid;gap:14px}
.cols-2{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.cols-3{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}

/* ---------- KPI ---------- */
.kpis{grid-template-columns:repeat(auto-fit,minmax(172px,1fr))}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow-1);padding:16px 18px}
.kpi .label{color:var(--muted);font-size:13px}
.kpi .value{font-size:24px;font-weight:680;letter-spacing:-.3px;margin-top:5px;
  font-variant-numeric:tabular-nums}
.kpi .sub{color:var(--muted-2);font-size:12.5px;margin-top:3px}

/* ---------- 徽标 ---------- */
.pill{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:2px 10px;
  font-size:12.5px;font-weight:600;border:1px solid;white-space:nowrap}
.pill.ok{color:var(--ok);border-color:var(--ok-line);background:var(--ok-soft)}
.pill.bad{color:var(--bad);border-color:var(--bad-line);background:var(--bad-soft)}
.pill.warn{color:var(--warn);border-color:var(--warn-line);background:var(--warn-soft)}
.pill.info{color:var(--accent-2);border-color:var(--accent-line);background:var(--accent-soft)}
.pill.plain{color:var(--ink-2);border-color:var(--line);background:#f8fafc}
.rank-badge{display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:24px;
  padding:0 7px;border-radius:8px;background:var(--accent-soft);color:var(--accent-2);
  font-weight:680;font-size:13px}

/* ---------- 表格 ---------- */
table{width:100%;border-collapse:separate;border-spacing:0;
  font-variant-numeric:tabular-nums}
th,td{padding:10px 12px;text-align:left;font-size:14px;border-bottom:1px solid var(--line-2)}
thead th{background:#fbfcfe;color:var(--muted);font-size:12.5px;font-weight:650;
  text-transform:none;white-space:nowrap;border-bottom:1px solid var(--line)}
tbody tr:hover td{background:#fbfcfe}
tbody tr:last-child td{border-bottom:none}
td .note{margin-top:2px}

/* ---------- 按钮 ---------- */
.btn{display:inline-flex;align-items:center;gap:7px;cursor:pointer;
  border:1px solid var(--line);background:var(--surface);color:var(--ink);
  border-radius:var(--radius-sm);padding:9px 15px;font-size:14px;font-weight:560;
  font-family:inherit;transition:.15s ease}
.btn:hover{border-color:var(--accent-line);color:var(--accent-2);text-decoration:none}
.btn-primary{background:var(--accent);border-color:var(--accent);color:#fff;
  box-shadow:0 1px 2px rgba(37,99,235,.25)}
.btn-primary:hover{background:var(--accent-2);border-color:var(--accent-2);color:#fff}
.btn-sm{padding:6px 11px;font-size:13px}
.btn-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}

/* ---------- 表单 ---------- */
.field{display:grid;gap:7px;margin-bottom:14px}
.field > label{font-size:13.5px;font-weight:600;color:var(--ink-2)}
input[type=text],input[type=file],select,textarea{
  width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:var(--radius-sm);
  font:inherit;font-size:14px;color:var(--ink);background:var(--surface)}
input[type=text]:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
textarea{min-height:150px;resize:vertical;line-height:1.6}
select{min-width:0}
.hint{color:var(--muted);font-size:12.5px;line-height:1.6}
.choice{display:flex;gap:18px;flex-wrap:wrap;font-size:14px;padding-top:2px}
.choice label{display:flex;align-items:center;gap:7px;font-weight:500;cursor:pointer}
input[type=radio],input[type=checkbox]{accent-color:var(--accent)}

/* ---------- 提示条 ---------- */
.notice{border-radius:var(--radius-sm);padding:13px 15px;font-size:13.5px;
  border:1px solid;line-height:1.65}
.notice.error{color:var(--bad);border-color:var(--bad-line);background:var(--bad-soft)}
.notice.warn{color:var(--warn);border-color:var(--warn-line);background:var(--warn-soft)}
.notice.ok{color:var(--ok);border-color:var(--ok-line);background:var(--ok-soft)}
.notice.info{color:var(--accent-2);border-color:var(--accent-line);background:var(--accent-soft)}
.notice pre{margin:8px 0 0;white-space:pre-wrap;font-size:12.5px;
  font-family:ui-monospace,Menlo,Consolas,monospace}

/* ---------- 展开明细 ---------- */
details{border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--surface);
  margin-top:10px;overflow:hidden}
details > summary{cursor:pointer;padding:11px 15px;font-weight:600;font-size:14px;
  list-style:none;display:flex;align-items:center;gap:8px}
details > summary::-webkit-details-marker{display:none}
details > summary::before{content:"";width:7px;height:7px;border-right:1.5px solid var(--muted);
  border-bottom:1.5px solid var(--muted);transform:rotate(-45deg);transition:.15s;margin-left:2px}
details[open] > summary::before{transform:rotate(45deg)}
details > summary:hover{background:#fbfcfe}
details .body{padding:4px 16px 16px}

/* ---------- 定义列表 ---------- */
.kv{display:grid;grid-template-columns:132px 1fr;gap:6px 14px;font-size:14px}
.kv dt{color:var(--muted)}
.kv dd{margin:0}

/* ---------- 步骤条 ---------- */
.steps{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.step{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px 18px;box-shadow:var(--shadow-1)}
.step .no{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;
  border-radius:8px;background:var(--accent);color:#fff;font-size:13px;font-weight:680;
  margin-bottom:8px}
.step h3{font-size:15px;margin-bottom:4px}

/* ---------- 报告页 ---------- */
.hero{background:linear-gradient(135deg,#0f2c66 0%,#1d4ed8 100%);color:#fff;
  padding:38px 0 34px;margin-bottom:6px}
.hero h1{font-size:28px;font-weight:700;color:#fff}
.hero .meta{opacity:.86;font-size:13.5px;margin-top:8px}
.hero .meta span{display:inline-block;margin-right:16px}
.chips{margin-top:16px;display:flex;flex-wrap:wrap;gap:8px}
.chip{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.24);
  border-radius:999px;padding:3px 12px;font-size:13px}
.summary{grid-template-columns:repeat(auto-fit,minmax(310px,1fr))}
.bar{height:7px;border-radius:999px;background:var(--line-2);overflow:hidden;min-width:80px}
.bar > i{display:block;height:100%;background:var(--accent)}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
input[type=search]{padding:9px 12px;border:1px solid var(--line);border-radius:var(--radius-sm);
  font:inherit;font-size:14px;background:var(--surface);min-width:260px}
input[type=search]:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-soft)}
ul.tight{margin:8px 0 0;padding-left:20px}
ul.tight li{margin:3px 0}
.timeline{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.timeline .node{border:1px solid var(--line);border-radius:999px;padding:5px 12px;
  font-size:13px;background:#fbfcfe}
footer{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12.5px}
@media(max-width:760px){
  .kv{grid-template-columns:1fr}
  .wrap{padding:0 16px 48px}
  .hero h1{font-size:22px}
}
@media print{
  .topbar,.btn,.toolbar{display:none}
  body{background:#fff}
  .card,.kpi,.step{box-shadow:none}
  details{break-inside:avoid}
  details > summary{list-style:none}
}
"""

#: 报告页少量交互(筛选 + 批量展开)
REPORT_JS = """
const q = document.getElementById('q');
if (q) {
  q.addEventListener('input', () => {
    const kw = q.value.trim().toLowerCase();
    document.querySelectorAll('#map tbody tr[data-search]').forEach(tr => {
      tr.hidden = kw && !tr.dataset.search.includes(kw);
    });
  });
}
const toggle = document.getElementById('toggle');
if (toggle) {
  toggle.addEventListener('click', () => {
    const list = [...document.querySelectorAll('#plans details, #map details')];
    const open = list.some(d => !d.open);
    list.forEach(d => { d.open = open; });
    toggle.textContent = open ? '收起全部明细' : '展开全部明细';
  });
}
"""

#: 界面用:文件选择后把内容读进 textarea,避免 multipart 上传
UPLOAD_JS = """
document.querySelectorAll('[data-fill]').forEach(input => {
  input.addEventListener('change', () => {
    const file = input.files && input.files[0];
    const target = document.querySelector(input.dataset.fill);
    if (!file || !target) return;
    const reader = new FileReader();
    reader.onload = () => {
      target.value = reader.result;
      const note = document.getElementById(input.dataset.note || '');
      if (note) note.textContent = '已载入 ' + file.name + '(' + reader.result.length + ' 字符)';
    };
    reader.readAsText(file, 'utf-8');
  });
});
document.querySelectorAll('form[data-busy]').forEach(form => {
  form.addEventListener('submit', () => {
    const btn = form.querySelector('button[type=submit]');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = form.dataset.busy;
    const box = document.getElementById(form.dataset.busyNote || '');
    if (box) box.style.display = 'block';
  });
});
"""
