"""«Отчёт брокера» → один самодостаточный файл HTML: таблицы и графики, скачивается на диск.

Файл открывается без сети и без программы: свой встроенный <style> (светлая палитра, печать с разрывами между
блоками), plotly.js — один раз инлайном, маленький JS сортировки таблиц по клику на заголовок. Внешних ресурсов нет.

Числа — только из тех же функций, что экран окна (brep_view.summary_table / days_rows / sec_rows / ins_total,
Account.instruments) и окно «Графики» (brep_charts.series / figure / fig_* / kpi_items); второго расчёта нет.
Форматирование — точный перенос rub / pct / fmtv / ruDate из assets/dashAgGridFunctions.js.

register(server, url_base, account, store) — маршрут GET url_base + «export.html» на общем Flask-сервере окна
(вход — тот же, что у окна); вызывается из brep_view.mount.
"""
from __future__ import annotations

import datetime as dt
import html
import math
import re
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote

from . import brep_charts as bc
from . import brep_view as bv
from .brep_model import ru

MSK = dt.timezone(dt.timedelta(hours=3))      # без tzdata (на Windows его нет); в Москве нет летнего времени
MODES = ("day", "week", "month")
IMODES = ("base", "code")
SCALES = ("lin", "log")
C = bc.LIGHT                                    # палитра — та же, что светлая тема окна «Графики»


# ─────────────── форматирование: точный перенос assets/dashAgGridFunctions.js ───────────────

def _num(v):
    """Число, как его видит JS-форматтер (isFinite), или None — пустая ячейка."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _loc(x: float, d: int) -> str:
    """toLocaleString("ru-RU", {min=max=d знаков}): округление половины от нуля по кратчайшей десятичной записи
    числа (как Intl), группы — неразрывным пробелом, дробная часть — запятая; минус «−» (replace первого «-»)."""
    q = Decimal(repr(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    s = f"{q:,.{d}f}".replace(",", "\u00a0").replace(".", ",")
    return s.replace("-", "\u2212", 1)


def rub(v, d: int = 0) -> str:
    x = _num(v)
    if x is None:
        return ""
    if abs(x) < 0.5 * 10 ** -(d or 0):
        x = 0.0
    return _loc(x, d or 0)


def pct(v, d: int = 1) -> str:
    x = _num(v)
    if x is None:
        return ""
    if abs(x) < 0.5 * 10 ** -d:
        x = 0.0
    return _loc(x, d) + "%"


def ru_date(v) -> str:
    if not v:
        return ""
    return f"{v[8:10]}.{v[5:7]}.{v[:4]}"


def fmtv(row: dict, v) -> str:
    """Ячейка сводки по формату строки (fmtv в JS)."""
    if row.get("sec"):
        return ""
    f = row.get("fmt")
    if f == "text":
        return str(v) if v else ""
    if f == "pct":
        return pct(v, 1)
    if f == "rub3":
        return rub(v, 3)
    return rub(v, 0)


def _sign_cls(v) -> str:
    x = _num(v)
    return "neg" if x is not None and x < 0 else "pos" if x is not None and x > 0 else ""


# ─────────────── таблицы: колонки берутся из определений экрана (brep_view.*_COLS) ───────────────
_VF = re.compile(r"^(rub|pct)\(params\.value, (\d)\)$")


def _leaf(c: dict) -> dict:
    fn = (c.get("valueFormatter") or {}).get("function", "")
    m = _VF.match(fn)
    kind, dec = (m.group(1), int(m.group(2))) if m else ("date" if c.get("field") == "date" else "text", 0)
    rules = c.get("cellClassRules") or {}
    return {"field": c["field"], "head": c["headerName"], "kind": kind, "dec": dec, "sign": rules == bv.SIGN,
            "est": "est" in rules, "chk": "warn" in rules and "ok" in rules, "tipf": c.get("tooltipField"),
            "htip": c.get("headerTooltip")}


def _attr(name: str, v) -> str:
    return f' {name}="{html.escape(str(v), quote=True)}"' if v not in (None, "") else ""


def _cell(col: dict, r: dict) -> str:
    v = r.get(col["field"])
    cls, dv = [], None
    if col["kind"] in ("rub", "pct"):
        txt = rub(v, col["dec"]) if col["kind"] == "rub" else pct(v, col["dec"])
        cls.append("n")
        x = _num(v)
        dv = repr(x) if x is not None else None
        if col["sign"]:
            cls.append(_sign_cls(v))
    elif col["kind"] == "date":
        txt = r.get("lab") or ru_date(v)
        dv = v
    else:
        txt = "" if v is None else str(v)
    if col["est"] and r.get("exact") is False:
        cls.append("est")
    if col["chk"]:
        cls.append("ok" if v == "✓" else "warn" if v else "")
    tip = r.get(col["tipf"]) if col["tipf"] else None
    return (f"<td{_attr('class', ' '.join(c for c in cls if c))}{_attr('data-v', dv)}{_attr('title', tip)}>"
            f"{html.escape(txt)}</td>")


def grid_table(cols: list, rows: list, pinned: list, cls: str = "") -> str:
    """Таблица по определениям колонок экрана; группы колонок (children) — двухэтажная шапка; pinned — «Итого»."""
    leaves, top, low, grouped = [], [], [], any("children" in c for c in cols)
    for c in cols:
        if "children" in c:
            top.append(f'<th colspan="{len(c["children"])}" class="grp">{html.escape(c["headerName"])}</th>')
            for ch in c["children"]:
                lf = _leaf(ch)
                low.append((len(leaves), lf))
                leaves.append(lf)
        else:
            lf = _leaf(c)
            top.append((len(leaves), lf))
            leaves.append(lf)

    def th(i, lf, extra=""):
        num = lf["kind"] in ("rub", "pct")
        return (f'<th data-i="{i}"{_attr("data-t", "n" if num else "")}{_attr("title", lf["htip"])}'
                f'{_attr("class", "n" if num else "")}{extra}>{html.escape(lf["head"])}</th>')

    head = "<tr>" + "".join(t if isinstance(t, str) else th(*t, ' rowspan="2"' if grouped else "") for t in top) + "</tr>"
    if grouped:
        head += "<tr>" + "".join(th(*t) for t in low) + "</tr>"
    body = "".join("<tr>" + "".join(_cell(lf, r) for lf in leaves) + "</tr>" for r in rows)
    foot = "".join('<tr class="tot">' + "".join(_cell(lf, r) for lf in leaves) + "</tr>" for r in pinned)
    return (f'<table class="t sortable {cls}"><thead>{head}</thead><tbody>{body}</tbody>'
            f'{"<tfoot>" + foot + "</tfoot>" if foot else ""}</table>')


def summary_html(cols_data: list) -> str:
    """Сводка: строки и колонки — ровно summary_table экрана (разделы, подсказки показателей в title строки)."""
    cols, rows = bv.summary_table(cols_data)
    ycols = cols[1:]
    head = "<tr><th>Показатель</th>" + "".join(
        f'<th class="n{" totc" if c["headerName"] == "Итого" else ""}">{html.escape(c["headerName"])}</th>'
        for c in ycols) + "</tr>"
    body = []
    for r in rows:
        if r.get("sec"):
            body.append(f'<tr class="sec"><td colspan="{len(cols)}">{html.escape(r["name"])}</td></tr>')
            continue
        tds = []
        for c in ycols:
            v = r.get(c["field"])
            cl = ["n"] + ([_sign_cls(v)] if r.get("sign") else []) + (["totc"] if c["headerName"] == "Итого" else [])
            tds.append(f'<td{_attr("class", " ".join(x for x in cl if x))}{_attr("title", r.get(c["field"] + "_tip"))}>{html.escape(fmtv(r, v))}</td>')
        body.append(f'<tr{_attr("title", r.get("tip"))}><td class="nm">{html.escape(r["name"])}</td>{"".join(tds)}</tr>')
    return f'<table class="t sum"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


# ─────────────── документ ───────────────
CSS = f"""
:root{{--bg:{C['bg']};--line:{C['line']};--txt:{C['txt']};--mut:{C['mut']};--acc:{C['acc']};--ok:{C['ok']};
--bad:{C['bad']};--warn:#9a6700;--sec:#a65a1c;--hdr:#f3ede2;--odd:#f8f4ec}}
*{{box-sizing:border-box}}
html,body{{background:var(--bg)}}
body{{margin:0;color:var(--txt);font:13px/1.4 "IBM Plex Sans",system-ui,"Segoe UI",Roboto,sans-serif}}
.page{{max-width:1500px;margin:0 auto;padding:14px 18px 28px}}
h1{{font-size:19px;margin:0 0 4px}}h2{{font-size:15px;margin:0}}h3{{font-size:13.5px;margin:10px 0 4px}}
.meta,.hint,.notes{{color:var(--mut);font-size:12px}}
header{{margin-bottom:12px}}header div{{margin:2px 0}}
.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}
.card{{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:14px;background:var(--bg)}}
.bh{{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:8px}}
.scroll{{overflow-x:auto}}
table.t{{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}}
.t th,.t td{{border-bottom:1px solid var(--line);padding:4px 8px;white-space:nowrap}}
.t th{{background:var(--hdr);font-weight:600;font-size:12px;text-align:left;vertical-align:bottom}}
.t th.grp{{text-align:center;border-bottom:2px solid color-mix(in srgb,var(--line),var(--acc) 55%)}}
.t .n{{text-align:right}}
.t tbody tr:nth-child(even) td{{background:var(--odd)}}
.t td.neg{{color:var(--bad)}}.t td.pos{{color:var(--ok)}}.t td.est{{font-style:italic;color:var(--mut)}}
.t tr.tot td,.t .totc{{font-weight:650}}.t tfoot tr.tot td{{border-top:2px solid var(--line);background:var(--hdr)}}
.t tr.sec td{{font-weight:700;color:var(--sec);letter-spacing:.06em;font-size:11.5px;background:var(--bg)!important;
padding-top:8px}}
.t.sum td.nm{{min-width:220px}}
.sortable th[data-i]{{cursor:pointer;user-select:none}}
.sortable th[data-dir=asc]::after{{content:" ▲";font-size:9px}}.sortable th[data-dir=desc]::after{{content:" ▼";font-size:9px}}
.kpi{{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 10px}}
.k{{border:1px solid var(--line);border-radius:8px;padding:6px 12px}}
.k b{{display:block;font-size:15px}}.k.main b{{font-size:20px}}.k i{{font-style:normal;color:var(--mut);font-size:11.5px}}
.k .pos{{color:var(--ok)}}.k .neg{{color:var(--bad)}}
.chart{{border:1px solid var(--line);border-radius:10px;padding:4px 6px;margin-bottom:12px}}
footer{{color:var(--mut);font-size:12px}}footer p{{margin:3px 0}}
@media print{{
  @page{{size:A4 landscape;margin:10mm}}
  .page{{max-width:none;padding:0}}
  .card{{border:none;padding:0;margin:0}}
  .card+.card{{break-before:page}}
  .bh,h3{{break-after:avoid}}
  thead{{display:table-header-group}}tfoot{{display:table-footer-group}}tr{{break-inside:avoid}}
  .chart,.kpi{{break-inside:avoid}}
  .scroll{{overflow:visible}}
  .sortable th[data-i]::after{{content:""!important}}
}}
"""

JS = """
(function(){
  function key(c){var v=c.getAttribute('data-v');return v===null?null:v;}
  document.querySelectorAll('table.sortable').forEach(function(t){
    t.querySelectorAll('th[data-i]').forEach(function(th){
      th.addEventListener('click',function(){
        var i=+th.getAttribute('data-i'),num=th.getAttribute('data-t')==='n',tb=t.tBodies[0];
        var dir=th.getAttribute('data-dir')==='asc'?'desc':'asc',s=dir==='asc'?1:-1;
        t.querySelectorAll('th[data-i]').forEach(function(h){h.removeAttribute('data-dir');});
        th.setAttribute('data-dir',dir);
        var rows=Array.prototype.slice.call(tb.rows);
        rows.sort(function(a,b){
          var ca=a.cells[i],cb=b.cells[i];
          if(num){var x=key(ca),y=key(cb);
            if(x===null&&y===null)return 0;if(x===null)return 1;if(y===null)return -1;
            return s*(parseFloat(x)-parseFloat(y));}
          var p=key(ca)||ca.textContent,q=key(cb)||cb.textContent;
          return s*p.localeCompare(q,'ru');
        });
        rows.forEach(function(r){tb.appendChild(r);});   /* строка «Итого» — в tfoot, остаётся внизу */
      });
    });
  });
  function fit(){if(window.Plotly){document.querySelectorAll('.js-plotly-plot').forEach(function(g){
    try{Plotly.Plots.resize(g);}catch(e){}});}}
  window.addEventListener('beforeprint',fit);window.addEventListener('afterprint',fit);
})();
"""


def _block(title: str, hint: str, *body: str) -> str:
    return (f'<section class="card"><div class="bh"><h2>{html.escape(title)}</h2>'
            f'<span class="hint">{html.escape(hint)}</span></div>{"".join(body)}</section>')


def _notes(items) -> str:
    """[(текст, класс | None)] → абзацы заметок."""
    return "".join(f"<p{_attr('class', c)}>{html.escape(t)}</p>" for t, c in items)


def _spans(items) -> str:
    """[(текст, класс | None)] → строка сверки (span с классом ok/warn)."""
    return "".join(f"<span{_attr('class', c)}>{html.escape(t)}</span>" for t, c in items)


def _fig_html(fig) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=False, config=bc.CHART_CONFIG)


def charts_html(acc, a: str, b: str, scale: str) -> str:
    s = bc.series(acc, a, b)
    if not s["dates"]:
        return _block("Графики", "", '<p class="notes">за выбранный период данных нет</p>')
    kpi = "".join(f'<div class="k{" main" if m else ""}"><i>{html.escape(n)}</i><b{_attr("class", c)}>'
                  f"{html.escape(v)}</b></div>" for n, v, c, m in bc.kpi_items(s))
    figs = {"c-months": bc.fig_months(s, "light"), "c-costs": bc.fig_costs(acc, s, "light"),
            "c-ins": bc.fig_ins(acc, s, "light")}
    parts = [f'<div class="kpi">{kpi}</div>',
             f'<div class="chart">{_fig_html(bc.figure(s, "light", log=scale == "log"))}</div>']
    for h, n, i in bc.CHART_BLOCKS:
        parts.append(f'<h3>{html.escape(h)}</h3><p class="hint">{html.escape(n)}</p>'
                     f'<div class="chart">{_fig_html(figs[i])}</div>')
    parts.append(f'<p class="hint">{html.escape(bc.CHARTS_NOTE)}</p>')
    return _block("Графики", "как в окне «Графики»: доходность (индекс), просадка, стоимость счёта, месяцы, затраты, "
                             "инструменты" + (" · шкала лог" if scale == "log" else ""), *parts)


def build_html(acc, a, b, d_mode: str = "day", i_mode: str = "base", scale: str = "lin", account: str = "",
               store_error: str = "", now: dt.datetime | None = None) -> str:
    """Весь отчёт за [a, b] одной строкой HTML. a/b — «ГГГГ-ММ-ДД» или пусто (вся история, как rng() экрана)."""
    a, b = (a or acc.first)[:10], (b or acc.last)[:10]
    if a > b:
        a, b = b, a
    d_mode = d_mode if d_mode in MODES else "day"
    i_mode = i_mode if i_mode in IMODES else "base"
    scale = scale if scale in SCALES else "lin"
    now = now or dt.datetime.now(MSK)
    from plotly.offline import get_plotlyjs

    meta, checks, foot = bv.head_info(acc, store_error)
    title = f"Отчёт брокера · {acc.account_no}"
    head = (f"<header><h1>{html.escape(title)}</h1>"
            f'<div class="meta">период: {html.escape(ru(a))} – {html.escape(ru(b))} · {html.escape(meta)}</div>'
            f'<div class="meta">{_spans(checks)}</div>'
            f'<div class="meta">сформирован {now.astimezone(MSK):%d.%m.%Y %H:%M} МСК · счёт окна {html.escape(account)}'
            f"</div></header>")
    # Сводка
    cols_data, sum_note = bv.sum_data(acc, a, b)
    blocks = [_block("Сводка", bv.HINTS["sum"], f'<div class="scroll">{summary_html(cols_data)}</div>',
                     f'<div class="notes">{html.escape(sum_note)}</div>' if sum_note else "")]
    # По инструментам
    ins = acc.instruments(a, b, i_mode == "base")
    blocks.append(_block("По инструментам", bv.HINTS["ins"] + (" · базовый актив" if i_mode == "base" else " · контракт"),
                         f'<div class="scroll">{grid_table(bv.INS_COLS, ins, [bv.ins_total(ins)])}</div>'))
    # Ценные бумаги — то же условие показа, что на экране (_sec)
    if bv.sec_visible(acc):
        sec = acc.securities(a, b)
        if sec is None:
            body = '<div class="notes"><p>за выбранный период справок нет</p></div>'
        else:
            rows, pin = bv.sec_rows(sec)
            body = (f'<div class="scroll">{grid_table(bv.SEC_COLS, rows, pin)}</div><div class="notes">'
                    + _notes((n, "warn" if n.startswith("⚠") else None) for n in bv.sec_notes(sec)) + "</div>")
        blocks.append(_block("Ценные бумаги", bv.HINTS["sec"], body))
    # По дням — все строки, дата по убыванию (как сортировка экрана)
    days, dpin = bv.days_rows(acc, a, b, d_mode)
    days = sorted(days, key=lambda r: r["date"], reverse=True)
    lab = {"day": "день", "week": "неделя", "month": "месяц"}[d_mode]
    blocks.append(_block("По дням", f"{bv.HINTS['days']} · разбивка: {lab}",
                         f'<div class="scroll">{grid_table(bv.DAY_COLS, days, dpin, "days")}</div>'))
    blocks.append(charts_html(acc, a, b, scale))
    return ("<!DOCTYPE html>\n<html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)} · {html.escape(ru(a))} – {html.escape(ru(b))}</title>"
            f"<style>{CSS}</style>\n<script>{get_plotlyjs()}</script>\n</head><body><div class=\"page\">"
            + head + "".join(blocks) + f"<footer>{_notes(foot)}</footer></div><script>{JS}</script></body></html>")


# ─────────────── маршрут Flask ───────────────

def _date(v) -> str | None:
    try:
        return dt.date.fromisoformat((v or "")[:10]).isoformat() if v else None
    except ValueError:
        return None


def parse_args(args) -> dict:
    """Параметры запроса → безопасные значения (неверное — умолчание, не ошибка)."""
    def g(k, ok, d):
        return args.get(k) if args.get(k) in ok else d
    return {"a": _date(args.get("from")), "b": _date(args.get("to")), "d_mode": g("mode", MODES, "day"),
            "i_mode": g("imode", IMODES, "base"), "scale": g("scale", SCALES, "lin")}


def filenames(acc_no: str, a: str, b: str) -> tuple[str, str]:
    """(ASCII-запасное имя, имя UTF-8) файла отчёта."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(acc_no)).strip("_-.") or "account"
    return f"otchet-{safe}-{a}_{b}.html", f"отчёт-{acc_no}-{a}_{b}.html"


def register(server, url_base: str, account: str, store) -> str:
    """Маршрут файла отчёта; возвращает его адрес. Справок нет / сборка счёта упала — 503 с понятным текстом."""
    from flask import Response, request
    path = url_base + bv.EXPORT_PATH

    def _export():
        acc = store.get()
        if acc is None or not acc.reports:
            return Response("Отчёт брокера: " + bv.empty_msg(acc, store, account), 503,
                            {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"})
        p = parse_args(request.args)
        a, b = (p["a"] or acc.first)[:10], (p["b"] or acc.last)[:10]
        a, b = min(a, b), max(a, b)
        body = build_html(acc, a, b, p["d_mode"], p["i_mode"], p["scale"], account, store.error)
        ascii_name, utf_name = filenames(acc.account_no or account, a, b)
        return Response(body, 200, {
            "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(utf_name)}"})

    server.add_url_rule(path, endpoint="brep_export:" + url_base, view_func=_export, methods=["GET"])
    return path
