"""Окно «Отчёт брокера» — Dash-приложение на общем Flask-сервере (только показ).

Сводка по календарным годам, по инструментам, по дням. Один счёт — одно окно. Справки читаются из
каталога <reports_dir>/<счёт>/<счёт>_*.xls; кэш разбора — отдельно (cache_dir).

mount(server, url_base, account, reports_dir, cache_dir) — поднимает окно на адресе url_base.
"""
from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict
from urllib.parse import urlencode

import dash_ag_grid as dag
from dash import Dash, Input, Output, dcc, html, no_update

from .brep_model import Store, ru
from .brep_parse import FLOW_MAP, day_after as bp_day_after

HERE = os.path.dirname(os.path.abspath(__file__))

# тема таблиц — из переменных окна (их копирует assets/brep-theme.js из родительского окна); запасные — «Графит»
THEME = ("themeQuartz.withParams({accentColor: 'var(--acc)', backgroundColor: 'var(--card)', "
         "foregroundColor: 'var(--txt)', headerBackgroundColor: 'var(--hdr)', chromeBackgroundColor: 'var(--hdr)', "
         "oddRowBackgroundColor: 'var(--odd)', borderColor: 'var(--line)', rowHoverColor: 'var(--hover)', "
         "inputBackgroundColor: 'var(--bg)', fontSize: 12.5, headerFontWeight: 600, headerFontSize: 12, "
         "headerTextColor: 'var(--htxt)', headerRowBorder: {color: 'var(--hline)'}, "
         "headerColumnResizeHandleColor: 'var(--line)', wrapperBorderRadius: 10, spacing: 5})")
GRID = {"theme": {"function": THEME}, "rowHeight": 28, "headerHeight": 34, "groupHeaderHeight": 28,
        "animateRows": False,
        "tooltipShowDelay": 200, "enableCellTextSelection": True,
        "rowClassRules": {"tot": "params.node.rowPinned === 'bottom'"}}
DEF = {"sortable": True, "resizable": True, "filter": True, "floatingFilter": False, "suppressHeaderMenuButton": True,
       "wrapHeaderText": True, "autoHeaderHeight": True}
SIGN = {"neg": "params.value < 0", "pos": "params.value > 0"}
LINKS = []   # внешние ссылки в шапке окна: (подпись, адрес, подсказка)
MON = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def money(field, name, dec=0, sign=False, w=None, tip=None, **kw):
    c = {"field": field, "headerName": name, "type": "rightAligned", "filter": "agNumberColumnFilter",
         "valueFormatter": {"function": f"rub(params.value, {dec})"}}
    if sign:
        c["cellClassRules"] = SIGN
    if w:
        c["width"] = w
        c["minWidth"] = w          # при растяжке на ширину экрана колонка не уже своего числа
    if tip:
        c["headerTooltip"] = tip
    c.update(kw)
    return c


def pct(field, name, sign=False, tip=None, dec=1):
    c = money(field, name, dec, sign, tip=tip)
    c["valueFormatter"] = {"function": f"pct(params.value, {dec})"}
    return c


# ── сводка: строки — показатели разделами, колонки — годы и «Итого» ──
SUM_ROWS = [
    ("ДЕНЬГИ", "Стоимость на начало", "eq0", "rub", False, "стоимость счёта по справке на начало периода"),
    ("ДЕНЬГИ", "Ввод", "dep", "rub", False, "ввод денег на счёт, в т.ч. переводы со своих счетов (как в итогах брокера)"),
    ("ДЕНЬГИ", "Вывод", "wd", "rub", True, "вывод денег со счёта, в т.ч. переводы на свои счета (как в итогах брокера)"),
    ("ДЕНЬГИ", "Стоимость на конец", "eq1", "rub", False, "стоимость счёта по справке на конец периода"),
    ("ДОХОД", "Прибыль", "profit", "rub", True, "изменение стоимости счёта без ввода и вывода"),
    ("ДОХОД", "% за период", "pct", "pct", True, "год: по отрезкам между вводом/выводом — стоимость перед движением к стоимости после прошлого, отрезки перемножаются (без движений = (конец − начало) / начало); несколько лет («Итого»): произведение годовых (1 + %) − 1"),
    ("ДОХОД", "% годовых", "pct_y", "pct", True, "по стоимости на начало и конец в пересчёте на год (сложный процент); полный календарный год = «% за период»; от 30 дней данных"),
    ("ДОХОД", "в т.ч. срочный рынок (ВМ)", "vm", "rub", True,
     "вариационная маржа, включая начисленную и ещё не оплаченную на конец периода"),
    ("ДОХОД", "в т.ч. бумаги, купоны и прочее", "rest", "rub", True, "остаток прибыли: купоны, проценты, переоценка бумаг"),
    ("ЗАТРАТЫ", "Брокер", "broker", "rub", True, "комиссия брокера и депозитарий"),
    ("ЗАТРАТЫ", "Биржа", "exch", "rub", True, "биржевой сбор"),
    ("ЗАТРАТЫ", "Налоги", "tax", "rub", True, "НДФЛ: уплачено и удержано, минус возвраты"),
    ("ЗАТРАТЫ", "Затраты от прибыли до затрат", "cost_share", "pct", False, "(брокер + биржа) / прибыль до затрат и налогов"),
    ("РИСК", "ГО макс", "go_max", "rub", False, "наибольшее ГО на конец дня"),
    ("РИСК", "ГО / стоимость счёта макс", "go_pct_max", "pct", False, "наибольшая доля ГО от стоимости счёта на конец дня"),
    ("РИСК", "Худший день", "worst", "rub", True, "результат дня = ВМ − комиссии + доходы"),
    ("РИСК", "дата худшего дня", "worst_d_ru", "text", False, ""),
    ("РИСК", "Макс. просадка", "dd", "rub", True, "наибольшее падение кривой результата (без ввода/вывода) от пика"),
    ("РИСК", "Макс. просадка, %", "dd_pct", "pct", True,
     "наибольшее падение индекса доходности от пика за период (без влияния ввода/вывода) — как в окне «Графики»"),
    ("ОБОРОТ", "Лоты", "lots", "rub", False, "сумма лотов по сделкам"),
    ("ОБОРОТ", "Сделки", "deals", "rub", False, "число сделок (исполнений)"),
    ("ОБОРОТ", "Затраты на лот", "per_lot", "rub3", False, "(брокер + биржа) / лоты"),
]


def summary_table(cols_data):
    cols = [{"field": "name", "headerName": "Показатель", "pinned": "left", "width": 212, "tooltipField": "tip",
             "cellClassRules": {"sec": "params.data.sec === true"}}]
    for k, (lab, _) in enumerate(cols_data):
        # колонки лет делят ширину экрана поровну; не уже 96 px (число до 11 знаков) — дальше горизонтальная прокрутка
        # colId по подписи (год / «Итого»), а не по номеру: ширина, заданная человеком, держится за свой год
        # при смене периода (assets/brep-cols.js)
        cols.append({"field": f"c{k}", "colId": f"y-{lab}", "headerName": lab, "type": "rightAligned", "flex": 1,
                     "minWidth": 96,
                     "valueFormatter": {"function": "fmtv(params)"},
                     "cellClassRules": {"neg": "params.data.sign && params.value < 0",
                                        "pos": "params.data.sign && params.value > 0",
                                        "totc": f"'{lab}' === 'Итого'"}})
    rows, sec = [], None
    for section, name, field, fmt, sign, tip in SUM_ROWS:
        if section != sec:
            rows.append({"name": section, "sec": True})
            sec = section
        r = {"name": name, "fmt": fmt, "sign": sign, "tip": tip}
        for k, (_, d) in enumerate(cols_data):
            r[f"c{k}"] = d.get(field)
        rows.append(r)
    return cols, rows


DAY_COLS = [
    {"field": "date", "headerName": "Дата", "pinned": "left", "width": 118, "minWidth": 118, "sort": "desc",
     "valueFormatter": {"function": "params.data && params.data.lab ? params.data.lab : ruDate(params.value)"}},
    money("equity", "Стоимость счёта", w=118, tooltipField="eq_tip",
          cellClassRules={"est": "params.data && params.data.exact === false"},
          tip="точно — на день справки; курсивом — оценка по деньгам дня"),
    money("result", "Результат", sign=True, w=104, tip="ВМ − комиссии + доходы (без налогов, ввода/вывода)"),
    money("vm", "ВМ", sign=True, w=100), money("broker", "Брокер", sign=True, w=92), money("exch", "Биржа", sign=True, w=96),
    money("income", "Доходы", sign=True, w=88), money("tax", "Налоги", sign=True, w=88),
    money("flow", "Ввод/вывод", sign=True, w=100), money("lots", "Лоты", w=86), money("deals", "Сделки", w=78),
    money("go", "ГО", w=108, tip="на конец дня (неделя/месяц — максимум)"),
    dict(pct("go_pct", "ГО / счёт", tip="на конец дня (неделя/месяц — максимум)"), minWidth=78),
]

INS_COLS = [
    {"field": "instr", "headerName": "Инструмент", "pinned": "left", "width": 210, "minWidth": 180},
    money("vm", "ВМ", sign=True, w=130), money("exch", "Биржевой сбор", sign=True, w=124),
    money("broker", "Брокер", sign=True, w=118, tip="комиссия брокера за день, разделённая по лотам этого дня"),
    money("net", "Итог после затрат", sign=True, w=140),
    money("lots", "Лоты", w=110), money("deals", "Сделки", w=96),
    money("per_lot", "Затраты на лот", 3, w=116), pct("cost_share", "Затраты / |ВМ|"),
]


def _qty(field, name, w=92):
    return {"field": field, "headerName": name, "type": "rightAligned", "filter": "agNumberColumnFilter", "width": w,
            "minWidth": w, "valueFormatter": {"function": "rub(params.value, 0)"}}


# ── ценные бумаги (облигации, акции, фонды — по ISIN): позиция на границах справок + деньги и сделки периода ──
SEC_COLS = [
    {"field": "name", "headerName": "Бумага", "pinned": "left", "width": 104, "minWidth": 104, "tooltipField": "isin"},
    {"field": "isin", "headerName": "ISIN", "width": 108, "minWidth": 108},
    {"headerName": "На начало", "children": [_qty("q0", "шт.", 66), money("v0", "оценка, ₽", w=96,
                                                                         tip="оценка брокера с НКД, включая непоставленное и выданное в займ")]},
    {"headerName": "Куплено", "children": [_qty("bq", "шт.", 66), money("b", "₽", w=96)]},
    {"headerName": "Продано", "children": [_qty("sq", "шт.", 66), money("s", "₽", w=96)]},
    money("red", "Погашено, ₽", w=92, tip="погашение облигаций (номинал) по дате выплаты"),
    money("inc", "Купоны, дивиденды, ₽", w=88, tip="до удержания налога (налог — в сводке, «Налоги»)"),
    money("cost", "Комиссии, ₽", sign=True, w=80,
          tip="брокер (комиссия дня делится по объёму сделок дня) + депозитарий за выплату дохода"),
    {"headerName": "На конец", "children": [_qty("q1", "шт.", 66), money("v1", "оценка, ₽", w=96)]},
    money("result", "Результат, ₽", sign=True, w=100,
          tip="оценка на конец − на начало + продажи − покупки + погашения + купоны/дивиденды + комиссии"),
    {"field": "chk", "headerName": "Кол‑во", "width": 58, "minWidth": 58, "tooltipField": "chk_tip",
     "cellStyle": {"textAlign": "center"}, "headerClass": "hc",
     # количество не сошлось — на торговлю не влияет ⇒ жёлтый (warn, --warnt), было — красный (bad)
     "cellClassRules": {"warn": "params.value && params.value !== '✓'", "ok": "params.value === '✓'"}},
]


def sec_rows(sec):
    rows = []
    for r in sec["rows"]:
        rows.append(dict(r, cost=round(r["comm"] + r["depo"], 2), chk="✓" if r["ok"] else f"⚠ {r['dq']:+,.0f}".replace(",", " "),
                         chk_tip="на начало + куплено − продано + зачислено − списано = на конец" if r["ok"] else
                         f"не сошлось на {r['dq']:+g} шт.: в справке была операция с бумагой, которую разбор не знает — "
                         "результат по этой бумаге неточен"))
    if sec["fx"]:
        cur = sorted({c[:3] for c in sec["fx"]["isin"].split(", ") if c})
        rows.append(dict(sec["fx"], name="Валюта", isin=", ".join(cur), q0=None, q1=None, bq=None, sq=None, chk="",
                         cost=None, chk_tip="валютная позиция по курсу ЦБ на концах справок + покупки и продажи валюты"))
    if sec["comm_unalloc"] or sec["cash_unalloc"]:
        rows.append({"name": "без привязки", "isin": "", "cost": sec["comm_unalloc"], "inc": sec["cash_unalloc"],
                     "result": round(sec["comm_unalloc"] + sec["cash_unalloc"], 2), "chk": ""})
    tot = {"name": "Итого", "isin": ""}
    for f in ("v0", "b", "s", "red", "inc", "cost", "v1", "result"):
        tot[f] = round(sum(r.get(f) or 0 for r in rows), 2)
    return rows, [tot]


def _wkey(d: str, mode: str) -> str:
    if mode == "week":
        x = dt.date.fromisoformat(d)
        return (x - dt.timedelta(days=x.weekday())).isoformat()
    return d[:7] + "-01"


def days_rows(acc, a: str, b: str, mode: str):
    rows = [r for r in acc.days if a <= r["date"] <= b]
    out = []
    if mode == "day":
        for r in rows:
            out.append(dict(r, flow=round(r["dep"] + r["wd"] + r["tr"], 2),
                            eq_tip="точно, по справке" if r["exact"] else "оценка: последняя справка + деньги дня"))
    else:
        g = defaultdict(list)
        for r in rows:
            g[_wkey(r["date"], mode)].append(r)
        for k, rs in sorted(g.items()):
            last = rs[-1]
            lab = f"нед. {k[8:10]}.{k[5:7]}" if mode == "week" else f"{MON[int(k[5:7]) - 1]} {k[:4]}"
            s = lambda f: round(sum(x[f] for x in rs), 2)  # noqa: E731
            gp = [x["go_pct"] for x in rs if x["go_pct"] is not None]
            out.append({"date": k, "lab": lab, "equity": last["equity"], "exact": last["exact"],
                        "eq_tip": "на последний день периода" + ("" if last["exact"] else " (оценка)"),
                        "result": s("result"), "vm": s("vm"), "broker": s("broker"), "exch": s("exch"),
                        "income": s("income"), "tax": s("tax"), "flow": round(s("dep") + s("wd") + s("tr"), 2),
                        "lots": s("lots"), "deals": s("deals"), "go": max(x["go"] for x in rs),
                        "go_pct": max(gp) if gp else None})
    tot = {"date": None, "lab": "Итого"}
    for f in ("result", "vm", "broker", "exch", "income", "tax", "flow", "lots", "deals"):
        tot[f] = round(sum(r[f] for r in out), 2)
    return out, [tot]


def ins_total(rows):
    """Строка «Итого» блока «По инструментам» — общая для экрана (_ins) и файла HTML (brep_export)."""
    tot = {"instr": "Итого"}
    for f in ("vm", "exch", "broker", "net", "lots", "deals"):
        tot[f] = round(sum(r[f] for r in rows), 2)
    return tot


def sum_data(acc, a: str, b: str):
    """Колонки сводки [(подпись, итоги)] и заметка о границах по справкам — для экрана (_sum) и файла HTML."""
    ys = acc.years(a, b)
    tot = acc.summary(a, b, "Итого")
    notes = [f"{s['label']}: {s['note']}" for s in ys + ([tot] if tot else []) if s["note"]]
    cols_data = [(s["label"], s) for s in ys] + ([("Итого", tot)] if tot and len(ys) != 1 else [])
    note = ("Границы по справкам — " + "; ".join(notes) + ": стоимость счёта известна только на концах справок, "
            "справка за период внутри себя по стоимости не делится.") if notes else ""
    return cols_data, note


def sec_visible(acc) -> bool:
    """Блок «Ценные бумаги» показывается, только если бумаги на счёте были хоть раз за всю историю."""
    return bool(acc is not None and acc.reports and (acc.p_names or acc.p_trades or acc.p_fx))


def sec_notes(sec) -> list:
    notes = [f"По справкам: {ru(bp_day_after(sec['from']))} – {ru(sec['to'])}. Позиция — «плановый остаток» брокера: "
             "с купленным, но ещё не поставленным, и с бумагами, выданными брокеру в займ; сделки — по дате сделки."]
    if sec["lend"]:
        notes.append(f"Займ ЦБ брокеру: {sec['lend']} операций выдачи/возврата за всё время — в количестве и "
                     "результате не участвуют (бумага на займе остаётся вашей).")
    if sec["bad"]:
        notes.append("⚠ Количество не сошлось: " + ", ".join(sec["bad"]) + " — результат по ним неточен.")
    if sec["unknown"]:
        notes.append("⚠ Не распознано строк справок: " + "; ".join(f"{k} — {v}" for k, v in sec["unknown"].items()))
    return notes


def empty_msg(acc, store, account: str) -> str:
    """Почему отчёта нет (справок нет / не разобрались / ошибка сборки) — экран (_head) и файл HTML (ответ 503)."""
    msg = store.error or f"справок счёта {account} пока нет"
    bad = getattr(acc, "bad_files", None) if acc is not None else None
    if bad:                                   # все справки не разобрались — сказать какие и почему
        msg = "справки не разобраны: " + "; ".join(f"{n} — {w}" for n, w in bad)
    return msg


def head_info(acc, store_error: str = ""):
    """Шапка, строка сверки и подвал отчёта: (meta, checks, foot); checks/foot — [(текст, класс | None)].
    Общие для экрана (_head) и файла HTML (brep_export) — один текст и одни условия ✓/⚠."""
    # справка с нераспознанными итогами брокера — «сверка невозможна», а не «✓» (0 = 0);
    # нераспознанные строки справок — в шапке всегда (прежде — только в блоке бумаг, скрытом без бумаг)
    na = getattr(acc, "recon_na", [])
    mism = bool(acc.recon_bad or acc.go_bad)
    bad = getattr(acc, "bad_files", [])       # битые файлы пропущены — «✓» за них не ставим
    ok = not mism and not na and not bad
    n_ok = len(acc.reports) - len({f for f, _ in acc.recon_bad} | {f for f, _ in na})
    # на торговлю не влияет — жёлтый (красный — только «мешает торговать»); сверка
    # «Отчёта брокера» на торговлю не влияет никогда ⇒ ⚠ шапки жёлтый и при «не сошлось» (S3I, было — красный)
    checks = [("✓ " if ok else "⚠ ", "ok" if ok else "warn"),
              (f"сверка с итогами брокера: {n_ok} из {len(acc.reports)} справок, {len(FLOW_MAP)} видов денег", None)]
    if na:
        checks.append((f" · сверка невозможна: {len(na)} — итоги брокера в справке не распознаны", "warn"))
    if acc.unknown:
        checks.append((" · не распознано строк: " + "; ".join(f"{k} — {v}" for k, v in acc.unknown.items()), "warn"))
    if bad:                                   # битый файл пропущен — с пометкой (жёлтая)
        checks.append((f" · файл не разобран: {len(bad)}", "warn"))
    meta = f"справок {len(acc.reports)} · {ru(acc.first)} – {ru(acc.last)}"
    foot = [("Деньги — оплаченные рублёвые строки справок; по каждой справке их сумма по видам сверена с "
             "итогами брокера «Виды движений денежных средств». Прибыль = изменение стоимости счёта без "
             "ввода и вывода между ближайшими точными точками (начало/конец справок).", None)] + \
           [("Замечания: " + "; ".join(acc.notes), None)] * bool(acc.notes) + \
           [("⚠ Не сошлось со справкой: " + str(acc.recon_bad + acc.go_bad), "warn")] * mism + \
           [("⚠ Сверка невозможна (итоги «Виды движений» не распознаны — раскладка справки сменилась?): "
             + ", ".join(f for f, _ in na), "warn")] * bool(na) + \
           [("⚠ Справка не разобрана, в отчёте не учтена: " + "; ".join(f"{n} — {w}" for n, w in bad),
             "warn")] * bool(bad) + \
           [("⚠ " + store_error, "warn")] * bool(store_error)
    return meta, checks, foot


# пояснения в заголовках блоков — экран и файл HTML (brep_export)
HINTS = {
    "sum": "по календарным годам и за выбранный период; наведите на показатель — пояснение",
    "ins": "срочный рынок; комиссия брокера за день делится по лотам дня",
    "sec": "облигации, акции, фонды — по ISIN; позиция и оценка — на концах справок",
    "days": "стоимость счёта точна в дни справок, между ними (курсив) — оценка",
}
EXPORT_PATH = "export.html"      # файл HTML отчёта: адрес под url_base окна (маршрут — brep_export.register)


def export_href(a, b, mode, imode) -> str:
    """Ссылка кнопки «HTML ↓»: относительно страницы окна (url_base) — с выбранным периодом и разбивками."""
    q = {k: v for k, v in (("from", (a or "")[:10]), ("to", (b or "")[:10]), ("mode", mode), ("imode", imode)) if v}
    return EXPORT_PATH + ("?" + urlencode(q) if q else "")


def _block(title, note, *children):
    return html.Section([html.Div([html.H2(title), html.Span(note, className="hint")], className="bh"), *children],
                        className="card")


def mount(server, url_base: str, account: str, reports_dir: str, cache_dir: str, stand_nav: list | None = None):
    """Поднять окно на общем Flask-сервере. stand_nav — ссылки в шапке (например, «← Счета»)."""
    store = Store(account, reports_dir, cache_dir)
    app = Dash(__name__, server=server, url_base_pathname=url_base, assets_folder=os.path.join(HERE, "assets"),
               title="Отчёт брокера", update_title=None, suppress_callback_exceptions=True)

    # инфографика — отдельное окно по ссылке из отчёта (brep_charts, адрес …/broker-charts/)
    charts_url = url_base.rstrip("/").rsplit("/", 1)[0] + "/broker-charts/"
    try:
        from brep import brep_charts
        brep_charts.mount(server, charts_url, account, store, url_base)
        charts_link = [html.A("Графики ↗", href=charts_url, target="_blank", rel="noopener", className="lnk",
                              title="Отдельное окно: доходность (индекс), просадка и стоимость счёта по справкам")]
    except Exception as e:                                      # noqa: BLE001 — отчёт не падает из-за графиков
        print(f"⚠ окно «Графики» не поднялось: {type(e).__name__}: {e}", flush=True)
        charts_link = []
    try:                                                        # файл HTML отчёта за период
        from . import brep_export
        brep_export.register(server, url_base, account, store)
    except Exception as e:                                      # noqa: BLE001 — отчёт не падает из-за выгрузки
        print(f"⚠ выгрузка отчёта в HTML не поднялась: {type(e).__name__}: {e}", flush=True)
    links = html.Span(charts_link + [html.A(t,
                                            href=u, target="_blank", rel="noopener noreferrer", title=tip, className="lnk")
                                     for t, u, tip in LINKS] +
                     # CSV и «HTML ↓» — в общей строке шапки
                     [html.Button("CSV", id="d-csv", className="btn hbtn",
                                  title="Скачать таблицу «По дням» (выбранный период и разбивка) в CSV"),
                      # весь отчёт за период (таблицы + графики) одним файлом — на диск
                      html.A("HTML ↓", id="d-html", href=EXPORT_PATH, download="", className="btn hbtn",
                             title="Скачать отчёт за выбранный период одним файлом HTML: все таблицы и графики, "
                                   "открывается без сети и без программы, удобно печатать")], className="links")
    app.layout = html.Div([
        html.Header([html.H1(id="title"), links, html.Nav(stand_nav or []), html.Span(id="meta", className="meta")]),
        html.Div([dcc.RadioItems(id="preset", inline=True, className="seg"),
                  html.Span("с", className="meta"), dcc.Input(id="d-from", type="date", className="inp date"),
                  html.Span("по", className="meta"), dcc.Input(id="d-to", type="date", className="inp date"),
                  html.Span(id="checks", className="checks")], className="bar"),
        _block("Сводка", HINTS["sum"],
               dag.AgGrid(id="g-sum", defaultColDef={"sortable": False, "resizable": True, "suppressMovable": True},
                          dashGridOptions=dict(GRID, domLayout="autoHeight"), className="g", style={"height": "auto"}),
               html.Div(id="sum-notes", className="notes")),
        _block("По инструментам", HINTS["ins"],
               html.Div([dcc.RadioItems(id="i-mode", options=[{"label": "базовый актив", "value": "base"},
                                                              {"label": "контракт", "value": "code"}],
                                        value="base", inline=True, className="seg")], className="tools"),
               # ширина колонок — assets/brep-cols.js: сохранённая человеком, иначе «по ширине экрана», как прежде
               # columnSize="responsiveSizeToFit" (он же затирал ширины, заданные вручную)
               dag.AgGrid(id="g-i", columnDefs=INS_COLS, defaultColDef=DEF,
                          dashGridOptions=dict(GRID, domLayout="autoHeight"), className="g", style={"height": "auto"})),
        html.Div(_block("Ценные бумаги", HINTS["sec"],
                        dag.AgGrid(id="g-sec", columnDefs=SEC_COLS, defaultColDef=DEF,
                                   dashGridOptions=dict(GRID, domLayout="autoHeight"), className="g",
                                   style={"height": "auto"}),
                        html.Div(id="sec-notes", className="notes")), id="sec-wrap", style={"display": "none"}),
        _block("По дням", HINTS["days"],
               html.Div([dcc.RadioItems(id="d-mode", options=[{"label": "день", "value": "day"},
                                                              {"label": "неделя", "value": "week"},
                                                              {"label": "месяц", "value": "month"}],
                                        value="day", inline=True, className="seg"),
                         # сброс запомненных колонок (обработчик — assets/brep-cols.js)
                         html.Button("Сбросить колонки", id="cols-reset", className="btn",
                                     title="Вернуть ширину и порядок колонок всех трёх таблиц отчёта к виду по "
                                           "умолчанию (для окон всех счетов в этом браузере)")],
                        className="tools"),
               dag.AgGrid(id="g-d", columnDefs=DAY_COLS, defaultColDef=DEF,               # ширины — brep-cols.js
                          dashGridOptions=GRID, className="g", style={"height": 430},
                          csvExportParams={"fileName": f"{account}-po-dnyam.csv", "columnSeparator": ";"})),
        html.Footer(id="foot"),
    ], className="page")

    def acc_or_none():
        return store.get()

    def rng(acc, a, b):
        return (a or acc.first)[:10], (b or acc.last)[:10]

    @app.callback(Output("title", "children"), Output("meta", "children"), Output("preset", "options"),
                  Output("preset", "value"), Output("checks", "children"), Output("foot", "children"),
                  Input("title", "id"))
    def _head(_):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return "Отчёт брокера", empty_msg(acc, store, account), [], None, "", ""
        years = sorted({r["date"][:4] for r in acc.days})
        opts = [{"label": "всё", "value": "all"}] + [{"label": y, "value": y} for y in years] + \
               [{"label": "свой период", "value": "custom"}]
        meta, chk, ft = head_info(acc, store.error)    # текст и условия ✓/⚠ — общие с файлом HTML (brep_export)
        checks = [html.Span(t, className=c) if c else t for t, c in chk]
        foot = [html.P(t, className=c) if c else html.P(t) for t, c in ft]
        return f"Отчёт брокера · {acc.account_no}", meta, opts, "all", checks, foot

    @app.callback(Output("d-from", "value"), Output("d-to", "value"), Input("preset", "value"))
    def _preset(v):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return no_update, no_update
        if v == "all":
            return acc.first, acc.last
        if v and v.isdigit():
            return max(acc.first, f"{v}-01-01"), min(acc.last, f"{v}-12-31")
        return no_update, no_update

    @app.callback(Output("g-sum", "columnDefs"), Output("g-sum", "rowData"), Output("sum-notes", "children"),
                  Input("d-from", "value"), Input("d-to", "value"))
    def _sum(a, b):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return [], [], ""
        a, b = rng(acc, a, b)
        cols_data, note = sum_data(acc, a, b)
        cols, rows = summary_table(cols_data)
        return cols, rows, note

    @app.callback(Output("g-d", "rowData"), Output("g-d", "dashGridOptions"),
                  Input("d-from", "value"), Input("d-to", "value"), Input("d-mode", "value"))
    def _days(a, b, mode):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return [], GRID
        a, b = rng(acc, a, b)
        data, pin = days_rows(acc, a, b, mode)
        return data, dict(GRID, pinnedBottomRowData=pin)

    @app.callback(Output("g-i", "rowData"), Output("g-i", "dashGridOptions"),
                  Input("d-from", "value"), Input("d-to", "value"), Input("i-mode", "value"))
    def _ins(a, b, mode):
        acc = acc_or_none()
        if acc is None or not acc.reports:
            return [], dict(GRID, domLayout="autoHeight")
        a, b = rng(acc, a, b)
        rows = acc.instruments(a, b, mode == "base")
        return rows, dict(GRID, domLayout="autoHeight", pinnedBottomRowData=[ins_total(rows)])

    @app.callback(Output("g-d", "exportDataAsCsv"), Input("d-csv", "n_clicks"), prevent_initial_call=True)
    def _csv(_):
        return True


    @app.callback(Output("g-sec", "rowData"), Output("g-sec", "dashGridOptions"), Output("sec-notes", "children"),
                  Output("sec-wrap", "style"), Input("d-from", "value"), Input("d-to", "value"))
    def _sec(a, b):
        acc = acc_or_none()
        hide = {"display": "none"}
        if not sec_visible(acc):
            return [], dict(GRID, domLayout="autoHeight"), "", hide       # бумаг за всю историю нет — блок не показываем
        a, b = rng(acc, a, b)
        sec = acc.securities(a, b)
        if sec is None:
            return [], dict(GRID, domLayout="autoHeight"), "за выбранный период справок нет", {}
        rows, pin = sec_rows(sec)
        return rows, dict(GRID, domLayout="autoHeight", pinnedBottomRowData=pin), \
            [html.P(n, className="warn" if n.startswith("⚠") else None)   # «не сошлось» — жёлтым
             for n in sec_notes(sec)], {}

    # отчёт одним файлом HTML (таблицы + графики) — скачивается на диск (brep_export)
    @app.callback(Output("d-html", "href"), Input("d-from", "value"), Input("d-to", "value"),
                  Input("d-mode", "value"), Input("i-mode", "value"))
    def _html_href(a, b, mode, imode):
        return export_href(a, b, mode, imode)

    return app, store
