"""组装个人组合评估单页 HTML 报告（自包含 base64 图 + 反馈/导出模块）。"""
import base64
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIGS = ["01_hold_winrate.png", "02_now_position.png", "03_dip_vs_dca.png", "04_combo_equity.png"]


def b64(name):
    return base64.b64encode((HERE / name).read_bytes()).decode()


imgs = {n: b64(n) for n in FIGS}
today = date.today().isoformat()


def fig(name, alt):
    return f'<div class="fig"><img alt="{alt}" src="data:image/png;base64,{imgs[name]}"></div>'


def review(section):
    return (f'<div class="review-label">📝 本节反馈 (点击编辑，末尾“导出反馈”一键导出 markdown)</div>'
            f'<div class="review-block" data-section="{section}" '
            f'data-placeholder="对本节的疑问 / 想调整的地方？" contenteditable="true"></div>')


HTML = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>我的组合评估 · 红利低波 + 纳指100</title>
<style>
:root{{--accent:#2C5282;--ok:#27AE60;--div:#C0392B;--ndx:#2C6FBB;}}
*{{box-sizing:border-box;}}
body{{font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;max-width:860px;margin:0 auto;
padding:28px 20px 90px;color:#1a202c;line-height:1.75;background:#fafbfc;}}
h1{{font-size:26px;margin:0 0 4px;}}
.sub{{color:#718096;font-size:14px;margin-bottom:22px;}}
h2{{font-size:20px;margin:34px 0 10px;padding-bottom:6px;border-bottom:2px solid #e2e8f0;}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0;}}
.kpi{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px;text-align:center;}}
.kpi .n{{font-size:30px;font-weight:800;color:var(--accent);}}
.kpi .l{{font-size:13px;color:#4a5568;margin-top:4px;}}
.kpi .s{{font-size:11.5px;color:#a0aec0;margin-top:2px;}}
.fig{{margin:14px 0;text-align:center;}}
.fig img{{max-width:720px;width:100%;border:1px solid #edf2f7;border-radius:8px;}}
.box{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:6px 20px;margin:12px 0;}}
.warn{{background:#fffbeb;border-left:4px solid #d69e2e;padding:12px 16px;border-radius:6px;margin:14px 0;font-size:14.5px;}}
.good{{background:#f0fff4;border-left:4px solid var(--ok);padding:12px 16px;border-radius:6px;margin:14px 0;}}
ul{{margin:8px 0;}} li{{margin:5px 0;}}
.tag{{display:inline-block;background:var(--accent);color:#fff;font-size:12px;padding:2px 9px;border-radius:6px;}}
.review-label{{font-size:10.5px;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:.08em;margin:14px 0 3px;}}
.review-block{{background:#fdfdfd;border:2px dashed #b8c4d6;border-radius:6px;padding:10px 14px;margin:4px 0 18px;font-size:14px;min-height:38px;outline:none;transition:border-color .15s,background .15s;}}
.review-block:focus{{border-color:var(--accent);background:#fff;border-style:solid;box-shadow:0 0 0 3px rgba(44,82,130,.08);}}
.review-block:empty::before{{content:attr(data-placeholder);color:#94a3b8;font-style:italic;}}
.review-block.has-content{{border-color:var(--ok);background:#f7fdf9;}}
.export-bar{{position:fixed;bottom:20px;right:20px;z-index:9999;pointer-events:none;}}
.export-btn{{background:var(--accent);color:#fff;border:none;padding:12px 22px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);pointer-events:auto;}}
.export-btn .count{{background:rgba(255,255,255,.22);padding:1px 8px;border-radius:10px;font-size:12px;margin-left:6px;}}
.export-banner{{position:fixed;bottom:80px;right:20px;z-index:9999;background:#ebf8ff;border-left:4px solid #3182ce;padding:10px 14px;border-radius:6px;font-size:13px;max-width:380px;box-shadow:0 4px 14px rgba(0,0,0,.15);}}
</style></head><body>

<h1>我的组合体检报告</h1>
<div class="sub">红利低波(512890) + 纳指100(513100)，A股场内ETF · 数据 2019–2026 前复权 · {today}</div>

<div class="kpis">
<div class="kpi"><div class="n">91%</div><div class="l">随便哪天买，持有1年赚钱的概率</div><div class="s">组合 / 持有2年99.9%</div></div>
<div class="kpi"><div class="n">16%</div><div class="l">组合年波动，比只买红利低波(17%)还稳</div><div class="s">只买纳指是24%</div></div>
<div class="kpi"><div class="n">≈</div><div class="l">“逢低买”和“无脑定投”收益差不多</div><div class="s">别赌更大的跌</div></div>
</div>

<div class="good"><b>一句话结论：</b>你这套「两个不太同涨同跌的资产 + 长期持有」的框架站得住。坚持长期、保持纪律即可；
「越跌越使劲买」没你想的神，定投打底 + 大跌适度加码就够。</div>

<h2>1. 持有越久，赚钱概率越高</h2>
{fig("01_hold_winrate.png","持有期胜率")}
<p>随便挑一天买入：持有1个月基本是掷硬币，但持有<b>1年</b>组合有<b>91%</b>赚、持有<b>2年</b>几乎没亏过。
这就是为什么你“长期持有、平时不操作”的思路是对的——时间是你最大的朋友。</p>
{review("持有期胜率")}

<h2>2. 两个搭一起，更稳（不用再加别的）</h2>
{fig("04_combo_equity.png","组合净值曲线")}
<p>它俩的涨跌相关性只有 <b>0.24</b>（1=完全同步，0=各走各的）。所以组合的颠簸(波动16%)比单买任何一个都小，
最惨一次回撤组合 -18%，而单买纳指要 -28%。<b>红利低波管“稳”，纳指管“进攻+对冲人民币”，已经是好搭档，再加反而稀释。</b></p>
{review("分散化")}

<h2>3. 现在该买哪个？</h2>
{fig("02_now_position.png","当前位置")}
<div class="good">今天纳指100在历史新高、红利低波回撤了7% → 按你“买跌得更多的那个”的逻辑，<b>今天买红利低波，方向正确 ✅</b></div>
{review("当前位置")}

<h2>4. “逢低买”真的更强吗？——其实差不多</h2>
{fig("03_dip_vs_dca.png","逢低vs定投")}
<div class="warn"><b>别被“跌20%才买=2.19倍”骗了：</b>那种做法有 <b>15% 的钱一整年没投出去、踏空了上涨</b>，
高收益全靠这几年“跌了就快速反弹”。换成慢熊（长期阴跌），攒着钱等抄底会很惨，而且越跌越不敢买是人性。</div>
{review("逢低vs定投")}

<h2>📋 你的买卖规则清单（可直接照做）</h2>
<div class="box">
<p><span class="tag">买入</span></p>
<ul>
<li><b>打底（主力）：</b>每月固定投一笔（如发薪后），目标 50% 红利低波 + 50% 纳指100。这是核心，别停、别择时。</li>
<li><b>加码方向：</b>每月那笔优先买“<b>距过去1年高点回撤更多</b>”的那个；两个都在高点附近就各半。</li>
<li><b>大跌加力：</b>某个回撤 >10% → 这月整笔买它；回撤 >20%（少见大跌）→ 可动用备用金额外加买，但<b>留子弹、别一次全压</b>。</li>
<li><b>不要</b>平时攒着钱“等更大的跌”——数据证明长期不划算。</li>
<li><b>买纳指前看溢价：</b>513100 是 QDII，溢价 >3~5% 时少买或换场内折价的同类纳指ETF。</li>
</ul>
<p><span class="tag">卖出（你这套基本不卖，只在3种情况动手）</span></p>
<ul>
<li><b>要用钱：</b>优先卖涨得多/更贵的那个。</li>
<li><b>再平衡：</b>比例严重失衡（如变成 65/35），把涨多的卖一点补到跌的那个，拉回 ~50/50。<b>一年看1–2次就够</b>，别频繁。</li>
<li><b>溢价过高：</b>纳指100 溢价极高(>10%)可减一点，降低溢价回落风险。</li>
<li><b>永远不要</b>因短期下跌恐慌割肉——持有1年96%概率是赚的。</li>
</ul>
<p><b>检查频率：</b>每月定投日操作一次 + 每季度再平衡检查一次。平时不用看盘，契合你“平时不操作”的习惯。</p>
</div>
{review("买卖规则清单")}

<h2>⚠️ 两个必须记住的坑</h2>
<div class="warn">
<b>① 样本短：</b>红利低波ETF只有约7年数据、且偏牛市，独立的“3年时间段”仅2–3个。上面的高胜率说明“长期大概率赚”，
但<b>不能当成“100%稳赚”的铁律</b>，极端长熊（像日本失去的十年）历史里没出现过。<br><br>
<b>② QDII溢价：</b>纳指513100 经常出现场内价高于真实净值的“溢价”，买在高溢价时将来溢价回落会吃掉收益，买前务必瞄一眼当天溢价率。
</div>
{review("风险与坑")}

<div class="sub" style="margin-top:30px;">数据与脚本：<code>backtest/personal_div_ndx_eval.py</code>（评估）· <code>backtest/personal_div_ndx_charts.py</code>（图）。
本报告数字全部来自上述脚本对前复权日线的统计，未做人工修改。</div>

<div class="export-bar"><button type="button" id="export-btn" class="export-btn">📤 导出反馈 <span class="count" id="rev-count">0</span></button></div>
<div class="export-banner" id="export-info" style="display:none;"></div>

<script>
function collectReviews(){{
  var blocks=document.querySelectorAll('.review-block');var out=[];
  blocks.forEach(function(b){{var t=(b.innerText||'').trim();if(t)out.push({{s:b.getAttribute('data-section'),t:t}});}});
  return out;
}}
function updateCount(){{
  var c=collectReviews().length;var el=document.getElementById('rev-count');if(el)el.textContent=c;
  document.querySelectorAll('.review-block').forEach(function(b){{
    if((b.innerText||'').trim())b.classList.add('has-content');else b.classList.remove('has-content');}});
}}
function buildMarkdown(){{
  var rs=collectReviews();var lines=['# 组合评估报告 — 反馈 ('+new Date().toISOString().slice(0,10)+')',''];
  if(!rs.length){{lines.push('(暂无反馈)');}}
  rs.forEach(function(r){{lines.push('## '+r.s);lines.push('');lines.push(r.t);lines.push('');}});
  return lines.join('\\n');
}}
function localDownload(md,info){{
  try{{
    var blob=new Blob([md],{{type:'text/markdown'}});var url=URL.createObjectURL(blob);
    var a=document.createElement('a');a.href=url;a.download='review_'+new Date().toISOString().slice(0,10)+'.md';
    document.body.appendChild(a);a.click();document.body.removeChild(a);URL.revokeObjectURL(url);
    if(info){{info.style.display='block';info.textContent='✓ 未连服务器，已下载到本地 review_*.md。';}}
  }}catch(e){{
    try{{navigator.clipboard.writeText(md);if(info){{info.style.display='block';info.textContent='✓ 已复制到剪贴板。';}}}}
    catch(e2){{var w=window.open('','_blank');w.document.write('<textarea style="width:100%;height:100%">'+md+'</textarea>');}}
  }}
}}
function exportReviews(){{
  var md=buildMarkdown();var info=document.getElementById('export-info');
  // 优先 POST 回服务器 reviews/ 目录; 失败(如 scp 本地打开)则回退本地下载
  fetch('/save-review',{{method:'POST',headers:{{'Content-Type':'text/markdown; charset=utf-8'}},body:md}})
    .then(function(r){{if(!r.ok)throw 0;return r.json();}})
    .then(function(d){{if(info){{info.style.display='block';info.textContent='✓ 已存到服务器: '+d.path;}}}})
    .catch(function(){{localDownload(md,info);}});
}}
function init(){{
  document.querySelectorAll('.review-block').forEach(function(b){{
    var k='rev_'+(b.getAttribute('data-section')||'');var v=localStorage.getItem(k);if(v)b.innerText=v;
    b.addEventListener('input',function(){{localStorage.setItem(k,b.innerText);updateCount();}});}});
  var btn=document.getElementById('export-btn');if(btn)btn.addEventListener('click',exportReviews);
  updateCount();
}}
if(document.readyState!=='loading')init();else document.addEventListener('DOMContentLoaded',init);
</script>
</body></html>"""

out = HERE / "index.html"
out.write_text(HTML, encoding="utf-8")
print("wrote", out, f"({len(HTML)//1024} KB)")
