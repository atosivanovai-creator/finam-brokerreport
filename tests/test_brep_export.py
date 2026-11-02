"""«Отчёт брокера» → один файл HTML (таблицы + графики, файл скачивается на диск).

Синтетический счёт без справок: Account собирается из итогов по дням (как _acc в test_brep_charts.py), строки по дням —
настоящим Account._daily. Проверяется: маршрут Flask (а не catch-all Dash), заголовки скачивания, все блоки, числа =
функции экрана в формате экрана, разбивки неделя/месяц, plotly.js ровно один раз, внешних ресурсов нет, экранирование,
пустая модель → 503, неверные параметры → умолчания.
"""
import datetime as dt
import json
import random
import re
import shutil
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brep import brep_export as bx
from brep import brep_model as bm
from brep import brep_parse as bp
from brep import brep_view as bv

EVIL = "<script>alert(1)</script>"
HERE = Path(__file__).resolve().parents[1] / "brep"


def make_acc(years=(2024, 2025), seed=7, evil=True, papers=False):
    """Счёт из годовых «справок»: каждый будний день — ВМ, комиссии, лоты по двум-трём контрактам; изредка ввод/вывод."""
    rnd = random.Random(seed)
    a = bm.Account.__new__(bm.Account)
    a.acc, a.account_no, a.notes, a.bad_files, a.recon_bad, a.recon_na = "test", "TST-001", [], [], [], []
    a.kind, a.ins, a.broker_pfi, a.lots, a.req = {}, {}, {}, {}, {}
    a._papers_init([])
    codes = ["Si-3.24", "CNY-3.24"] + ([f"{EVIL}-6.24"] if evil else [])
    reps, eq = [], 1_000_000.0
    for y in years:
        e0, go, go_ch = eq, 0.0, {}
        d = dt.date(y, 1, 1)
        while d.year == y:
            ds = d.isoformat()
            if d.weekday() < 5:
                kv, iv, lv, br = {}, {}, {}, 0.0
                for c in codes:
                    q = rnd.randint(1, 20)
                    vm, ex = round(rnd.gauss(300, 4000), 2), round(-0.5 * q, 2)
                    iv[c] = [vm, ex]
                    lv[c] = [float(q), rnd.randint(1, 5)]
                    br -= 0.45 * q
                kv[bp.KIND_VM] = sum(v[0] for v in iv.values())
                kv[bp.KIND_EXCH] = sum(v[1] for v in iv.values())
                kv[bp.KIND_BROKER] = round(br, 2)
                if rnd.random() < 0.02:
                    kv[bp.KIND_IN] = 100_000.0
                elif rnd.random() < 0.02:
                    kv[bp.KIND_OUT] = -50_000.0
                if rnd.random() < 0.05:
                    kv[bp.KIND_TAX] = -1300.0
                a.kind[ds], a.ins[ds], a.lots[ds], a.broker_pfi[ds] = kv, iv, lv, round(br, 2)
                eq += sum(kv.values())
                gd = round(rnd.uniform(-5000, 5000), 2)
                go_ch[ds] = gd
                go += gd
            d += dt.timedelta(days=1)
        reps.append({"file": f"r{y}.xml", "from": f"{y}-01-01", "to": f"{y}-12-31", "equity": [e0, round(eq, 2)],
                     "go": [0.0, round(go, 2)], "go_ch": go_ch, "account": "TST-001"})
    a.reports = reps
    pts = {}
    for r in reps:
        pts.setdefault(bp.day_before(r["from"]), r["equity"][0])
        pts[r["to"]] = r["equity"][1]
    a.points = sorted(pts.items())
    a.first, a.last = reps[0]["from"], reps[-1]["to"]
    a._daily()
    if papers:                                    # бумага — как в test_brep_charts (облигация: покупка, купон, погашение)
        y0, y1 = years[0], years[-1]
        a.p_names = {"RU0001": "ОФЗ 1"}
        a.p_at = {f"{y0 - 1}-12-31": ({}, 0.0, 0.0), f"{y1}-12-31": ({"RU0001": (1.0, 260.0)}, 260.0, 0.0)}
        a.p_trades = {"1": [f"{y0}-01-15", "RU0001", 1, 2.0, -300.0, True]}
        a.p_cash = [[f"{y0}-03-01", "RU0001", bp.KIND_COUPON, 20.0]]
    return a


class Tables(HTMLParser):
    """Таблицы файла: [{"cls", "head": [..], "body": [[ячейки]], "foot": [[..]], "row_cls": [..]}] и все теги."""

    def __init__(self):
        super().__init__()
        self.tables, self.tags, self._t, self._part, self._row, self._cell = [], [], None, None, None, None

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "table":
            self._t = {"cls": dict(attrs).get("class", ""), "head": [], "body": [], "foot": [], "row_cls": []}
            self.tables.append(self._t)
        elif tag in ("thead", "tbody", "tfoot") and self._t is not None:
            self._part = tag[1:]
        elif tag == "tr" and self._t is not None:
            self._row = []
            if self._part == "body":
                self._t["row_cls"].append(dict(attrs).get("class", ""))
        elif tag in ("td", "th") and self._row is not None:
            self._cell = [dict(attrs).get("class", ""), ""]

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(tuple(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._t[self._part].append(self._row)
            self._row = None
        elif tag == "table":
            self._t = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[1] += data


def parse(doc):
    p = Tables()
    p.feed(doc)
    return p


def _days_t(doc):
    return next(x for x in parse(doc).tables if "days" in x["cls"])


def _texts(row):
    return [t for _, t in row]


@pytest.fixture(scope="module")
def acc():
    return make_acc()


@pytest.fixture(scope="module")
def doc(acc):
    return bx.build_html(acc, None, None, "day", "base", "lin", "acc1")


# ── формат: точный перенос dashAgGridFunctions.js ──

def test_format_like_screen():
    assert bx.rub(-1234567.5) == "−1\u00a0234\u00a0568"
    assert bx.rub(1234.5, 0) == "1\u00a0235"                # половина — от нуля, как Intl (не банковское)
    assert bx.rub(-0.4) == "0" and bx.rub(None) == "" and bx.rub(float("nan")) == ""
    assert bx.rub(1.005, 2) == "1,01"                       # Intl округляет кратчайшую запись, а не двоичную
    assert bx.rub(12.3456, 3) == "12,346" and bx.rub(999.9996, 3) == "1\u00a0000,000"
    assert bx.pct(-12.34) == "−12,3%" and bx.pct(0.04) == "0,0%" and bx.pct(None) == ""
    assert bx.ru_date("2024-03-05") == "05.03.2024" and bx.ru_date(None) == ""
    assert bx.fmtv({"fmt": "rub3"}, 0.4567) == "0,457" and bx.fmtv({"fmt": "text"}, "01.02.2024") == "01.02.2024"
    assert bx.fmtv({"sec": True}, 5) == "" and bx.fmtv({"fmt": "pct"}, 5) == "5,0%"


@pytest.mark.skipif(not shutil.which("node"), reason="нет node — сверка с JS-форматтерами экрана пропущена")
def test_format_equals_js_functions():
    """Сверка с настоящими rub/pct из assets/dashAgGridFunctions.js (node с ICU)."""
    vals = [0, 0.4, 0.5, -0.5, 1.005, 2.5, -2.5, 1234.5, -1234567.891, 999.9995, 12.3456, 1e9 + 0.5, -0.0004,
            0.0005, 33.333333, -7.25, 100, 0.125]
    js = (HERE / "assets/dashAgGridFunctions.js").read_text(encoding="utf-8")
    prog = ("var window={};" + js + ";var f=window.dashAgGridFunctions;var v=" + json.dumps(vals) +
            ";console.log(JSON.stringify(v.map(function(x){return [f.rub(x,0),f.rub(x,2),f.rub(x,3),f.pct(x,1)]})))")
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=30, check=False)
    assert r.returncode == 0, r.stderr
    want = json.loads(r.stdout)
    got = [[bx.rub(x, 0), bx.rub(x, 2), bx.rub(x, 3), bx.pct(x, 1)] for x in vals]
    if want[3][0] != "1\u00a0235" and want[7][0] != "1\u00a0235":     # node без полного ICU (small-icu, en) — не сверить
        pytest.skip(f"node без ru-RU ICU: {want[7]}")
    assert got == want


# ── содержимое файла ──

def test_all_blocks_and_header(acc, doc):
    for t in ("Отчёт брокера · TST-001", "<h2>Сводка</h2>", "<h2>По инструментам</h2>", "<h2>По дням</h2>",
              "<h2>Графики</h2>", "Доходность по месяцам, %", "<h3>Затраты</h3>", "<h3>Инструменты</h3>",
              "период: 01.01.2024 – 31.12.2025", "справок 2 · 01.01.2024 – 31.12.2025", "МСК",
              "сверка с итогами брокера: 2 из 2 справок", "<footer>", "доходность за период"):
        assert t in doc, t
    assert "<h2>Ценные бумаги</h2>" not in doc                      # бумаг нет — блока нет (как на экране)
    assert '<span class="ok">✓ </span>' in doc
    # порядок блоков — как на экране
    pos = [doc.index(f"<h2>{h}</h2>") for h in ("Сводка", "По инструментам", "По дням", "Графики")]
    assert pos == sorted(pos)


def test_summary_numbers_equal_screen(acc, doc):
    cols_data, _ = bv.sum_data(acc, acc.first, acc.last)
    assert [lab for lab, _ in cols_data] == ["2024", "2025", "Итого"]
    t = parse(doc).tables[0]
    assert _texts(t["head"][0]) == ["Показатель", "2024", "2025", "Итого"]
    _, rows = bv.summary_table(cols_data)
    body = {r[0][1]: r for r in t["body"]}
    for r in rows:
        if r.get("sec"):
            assert r["name"] in body
            continue
        cells = body[r["name"]][1:]
        assert [c[1] for c in cells] == [bx.fmtv(r, r[f"c{k}"]) for k in range(3)], r["name"]
    tot = cols_data[-1][1]
    prof = body["Прибыль"]
    assert prof[3][1] == bx.rub(tot["profit"]) and "totc" in prof[3][0]
    assert ("neg" if tot["profit"] < 0 else "pos") in prof[3][0]
    assert body["% за период"][3][1] == bx.pct(tot["pct"], 1)
    assert body["Затраты на лот"][3][1] == bx.rub(tot["per_lot"], 3)
    assert 'title="изменение стоимости счёта без ввода и вывода"' in doc        # подсказка показателя — title строки


def test_instruments_and_total(acc, doc):
    t = parse(doc).tables[1]
    rows = acc.instruments(acc.first, acc.last, True)
    tot = bv.ins_total(rows)
    assert len(t["body"]) == len(rows)
    assert _texts(t["body"][0])[:5] == [rows[0]["instr"], bx.rub(rows[0]["vm"]), bx.rub(rows[0]["exch"]),
                                         bx.rub(rows[0]["broker"]), bx.rub(rows[0]["net"])]
    foot = _texts(t["foot"][0])
    assert foot[0] == "Итого" and foot[4] == bx.rub(tot["net"]) and foot[5] == bx.rub(tot["lots"])
    # контракт — другая группировка, тот же итог «после затрат»
    d2 = bx.build_html(acc, None, None, "day", "code", "lin", "acc1")
    t2 = parse(d2).tables[1]
    assert len(t2["body"]) == len(acc.instruments(acc.first, acc.last, False))
    assert _texts(t2["foot"][0])[4] == foot[4]


def test_ins_total_shared_with_screen_callback():
    """Итог инструментов — одна функция для экрана и файла (колбэк _ins зовёт bv.ins_total, копии кода нет)."""
    src = (HERE / "brep_view.py").read_text(encoding="utf-8")
    assert "pinnedBottomRowData=[ins_total(rows)]" in src
    assert src.count('for f in ("vm", "exch", "broker", "net", "lots", "deals")') == 1


@pytest.mark.parametrize("mode", ["day", "week", "month"])
def test_days_rows_count_and_order(acc, mode):
    d = bx.build_html(acc, "2024-03-01", "2025-02-28", mode, "base", "lin", "acc1")
    t = _days_t(d)
    rows, pin = bv.days_rows(acc, "2024-03-01", "2025-02-28", mode)
    assert len(t["body"]) == len(rows)
    first = max(rows, key=lambda r: r["date"])                        # дата по убыванию — как сортировка экрана
    assert _texts(t["body"][0])[0] == (first.get("lab") or bx.ru_date(first["date"]))
    assert _texts(t["body"][0])[1] == bx.rub(first["equity"])
    foot = _texts(t["foot"][0])
    assert foot[0] == "Итого" and foot[2] == bx.rub(pin[0]["result"]) and foot[3] == bx.rub(pin[0]["vm"])


def test_days_estimate_italic_and_sign(acc, doc):
    t = _days_t(doc)
    eq_cls = {r[0][1]: r[1][0] for r in t["body"]}
    assert "est" in eq_cls["30.12.2025"] and "est" not in eq_cls["31.12.2025"]   # конец справки — точно
    res = {r[0][1]: r[2] for r in t["body"]}
    day = next(r for r in acc.days if r["result"] < 0)
    assert "neg" in res[bx.ru_date(day["date"])][0]


def test_securities_block_when_visible():
    a = make_acc(years=(2024,), papers=True)
    d = bx.build_html(a, None, None, "day", "base", "lin", "acc1")
    assert "<h2>Ценные бумаги</h2>" in d and "ОФЗ 1" in d and "По справкам: 01.01.2024 – 31.12.2024" in d
    _, pin = bv.sec_rows(a.securities(a.first, a.last))
    t = next(x for x in parse(d).tables if any(c[1] == "ISIN" for c in x["head"][0]))
    assert len(t["head"]) == 2                                        # группы «На начало / Куплено / …»
    assert _texts(t["foot"][0])[0] == "Итого"
    assert bx.rub(pin[0]["result"]) in _texts(t["foot"][0])


def test_plotly_inline_once_no_external(doc):
    from plotly.offline import get_plotlyjs
    js = get_plotlyjs()
    assert doc.count(js[:300]) == 1 and len(js) > 1_000_000
    assert doc.count("Plotly.newPlot") == 4                          # главный + месяцы + затраты + инструменты
    rest = doc.replace(js, "")
    assert not re.search(r"""<(script|link|img|iframe)[^>]*\s(src|href)\s*=""", rest, re.IGNORECASE)
    assert not re.search(r"""(src|href)\s*=\s*["']?\s*(https?:)?//""", rest, re.IGNORECASE)
    assert "url(http" not in rest and "@import" not in rest
    assert '"displaylogo": false' in rest


def test_escaping(doc):
    assert EVIL not in doc                                            # ни в таблицах, ни в данных графиков
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in doc


def test_log_scale(acc):
    d = bx.build_html(acc, None, None, "day", "base", "log", "acc1")
    assert re.search(r'"type":\s*"log"', d) and "шкала лог" in d
    assert not re.search(r'"type":\s*"log"', bx.build_html(acc, None, None, "day", "base", "lin", "acc1"))


# ── маршрут ──

@pytest.fixture()
def client(tmp_path, acc):
    from flask import Flask
    server = Flask("t")
    _, store = bv.mount(server, "/acc/broker-report/", "acc1", str(tmp_path / "rep"), str(tmp_path / "cache"))
    store.get = lambda: acc
    return server.test_client(), store


def test_route_download(client, acc):
    c, _ = client
    r = c.get("/acc/broker-report/export.html?from=2024-02-01&to=2024-11-30&mode=week&imode=code&scale=log")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "text/html; charset=utf-8" and r.headers["Cache-Control"] == "no-store"
    cd = r.headers["Content-Disposition"]
    assert cd.startswith('attachment; filename="otchet-TST-001-2024-02-01_2024-11-30.html"')
    assert "filename*=UTF-8''%D0%BE%D1%82%D1%87%D1%91%D1%82-TST-001-2024-02-01_2024-11-30.html" in cd
    body = r.get_data(as_text=True)
    assert "период: 01.02.2024 – 30.11.2024" in body and "разбивка: неделя" in body and "· контракт" in body
    t = _days_t(body)
    assert len(t["body"]) == len(bv.days_rows(acc, "2024-02-01", "2024-11-30", "week")[0])
    # маршрут — наш, а не catch-all Dash: страница окна по-прежнему — Dash
    page = c.get("/acc/broker-report/")
    assert page.status_code == 200 and "_dash-config" in page.get_data(as_text=True)
    assert "attachment" not in page.headers.get("Content-Disposition", "")


def test_route_bad_params_defaults(client, acc):
    c, _ = client
    r = c.get("/acc/broker-report/export.html?from=2024-13-45&to=xx&mode=year&imode=zz&scale=%3Cb%3E")
    assert r.status_code == 200
    assert 'filename="otchet-TST-001-2024-01-01_2025-12-31.html"' in r.headers["Content-Disposition"]
    body = r.get_data(as_text=True)
    assert "разбивка: день" in body and "· базовый актив" in body and "шкала лог" not in body
    t = _days_t(body)
    assert len(t["body"]) == len(acc.days)
    # перепутанные концы — меняются местами
    r2 = c.get("/acc/broker-report/export.html?from=2025-06-30&to=2025-01-01")
    assert r2.status_code == 200 and "2025-01-01_2025-06-30" in r2.headers["Content-Disposition"]


def test_route_empty_model_503(tmp_path):
    from flask import Flask
    server = Flask("t2")
    _, store = bv.mount(server, "/x/broker-report/", "acc1", str(tmp_path / "rep"), str(tmp_path / "cache"))
    r = server.test_client().get("/x/broker-report/export.html")
    assert r.status_code == 503 and "справок счёта acc1 пока нет" in r.get_data(as_text=True)
    store.get = lambda: None
    store.error = "OSError: диск"
    r = server.test_client().get("/x/broker-report/export.html")
    assert r.status_code == 503 and "OSError: диск" in r.get_data(as_text=True)


def test_button_href():
    assert bv.export_href("2024-01-01", "2024-12-31", "week", "code") == \
        "export.html?from=2024-01-01&to=2024-12-31&mode=week&imode=code"
    assert bv.export_href(None, None, None, None) == "export.html"


def test_build_time_and_size(capsys):
    """Замер на синтетике: 5 лет будних дней × 3 контракта."""
    a = make_acc(years=(2021, 2022, 2023, 2024, 2025))
    t0 = time.perf_counter()
    d = bx.build_html(a, None, None, "day", "base", "lin", "acc1")
    sec = time.perf_counter() - t0
    size = len(d.encode("utf-8"))
    with capsys.disabled():
        print(f"\n[замер] дней {len(a.days)}: сборка {sec:.2f} с, файл {size / 1e6:.2f} МБ")
    assert size < 15_000_000
