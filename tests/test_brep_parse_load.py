"""нагрузка разбора справок — тройной разбор в окнах счёта и квадратичные «Инструменты».

Было: каждое из 3 окон счёта разбирает новую справку само (кэш JSON общий, но первый разбор — у каждого, кто открыл
вкладку одновременно); `parts_days()` зовёт `securities()` на каждом конце справки, и каждый вызов перебирает ВСЕ
сделки/деньги бумаг ⇒ O(справок × строк). Стало: разбор одного файла — под замком файла кэша (второе окно ждёт и берёт
готовый кэш); `securities()` перебирает только строки своего интервала (индекс по дате, порядок сложения — прежний).
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brep import brep_model as bm, brep_parse as bp  # noqa: E402
import test_brep_recon_na as t1  # noqa: E402


def test_one_parse_for_three_windows(tmp_path, monkeypatch):
    d = tmp_path / "data" / "acc1"
    d.mkdir(parents=True)
    p = d / "acc1_2026-10-01.xls"
    p.write_text(t1.report("01.10.2026", "01.10.2026"), encoding="utf-8")
    calls = []
    real = bp.parse_report

    def slow(text, name=""):
        calls.append(name)
        time.sleep(0.4)
        return real(text, name)
    monkeypatch.setattr(bp, "parse_report", slow)
    out, errs = [], []

    def win():
        try:
            out.append(bm._load(str(p), str(tmp_path / "cache" / "acc1")))
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    th = [threading.Thread(target=win) for _ in range(3)]
    for t in th:
        t.start()
    for t in th:
        t.join(10)
    assert not errs, errs
    import json
    assert len(out) == 3 and len({json.dumps(o, sort_keys=True) for o in out}) == 1
    assert len(calls) == 1, calls


class _CountList(list):
    seen = 0

    def __iter__(self):
        for x in list.__iter__(self):
            _CountList.seen += 1
            yield x


class _CountDict(dict):
    def values(self):
        for x in dict.values(self):
            _CountList.seen += 1
            yield x

    def items(self):
        for x in dict.items(self):
            _CountList.seen += 1
            yield x


def test_parts_days_not_quadratic(tmp_path):
    d = tmp_path / "data" / "acc1"
    d.mkdir(parents=True)
    n = 30
    for k in range(1, n + 1):
        ds = f"{k:02d}.10.2026"
        (d / f"acc1_2026-10-{k:02d}.xls").write_text(t1.report(ds, ds, eq=("1 000,00", "1 000,00")), encoding="utf-8")
    acc = bm.Account("acc1", str(tmp_path / "data"), str(tmp_path / "cache"))
    assert len(acc.reports) == n
    rows = 20 * n
    acc.p_trades = _CountDict({f"t{i}": [f"2026-10-{1 + i % n:02d}", "RU000A0JX0J2", 1, 1.0, 100.0, None, None]
                               for i in range(rows)})
    acc.p_cash = _CountList([[f"2026-10-{1 + i % n:02d}", "RU000A0JX0J2", bp.KIND_COUPON, 1.0] for i in range(rows)])
    acc.p_moves, acc.p_fx = _CountList(), _CountList()
    acc.p_comm = _CountDict({f"2026-10-{k:02d}": -1.0 for k in range(1, n + 1)})
    acc._parts = None
    _CountList.seen = 0
    acc.parts_days()
    total = 2 * rows + n
    assert _CountList.seen < 3 * total, f"перебрано {_CountList.seen} строк при {total} строках бумаг (квадратично)"


def test_securities_same_result_with_index(tmp_path):
    """Индекс по дате не меняет числа: interval-сумма = прямой фильтр по всем строкам (порядок сложения — прежний)."""
    d = tmp_path / "data" / "acc1"
    d.mkdir(parents=True)
    for k in (1, 2, 3):
        ds = f"{k:02d}.10.2026"
        (d / f"acc1_2026-10-{k:02d}.xls").write_text(t1.report(ds, ds, eq=("1 000,00", "1 000,00")), encoding="utf-8")
    acc = bm.Account("acc1", str(tmp_path / "data"), str(tmp_path / "cache"))
    acc.p_trades = {f"t{i}": [f"2026-10-0{1 + i % 3}", f"RU000A0JX0J{i % 2}", 1 if i % 3 else -1, 1.0 + i, 0.1 * i + 0.07,
                              None, None] for i in range(50)}
    acc.p_cash = [[f"2026-10-0{1 + i % 3}", f"RU000A0JX0J{i % 2}", bp.KIND_COUPON, 0.01 * i + 0.003] for i in range(50)]
    acc.p_comm = {"2026-10-02": -0.33, "2026-10-03": -0.77}
    got = acc.securities("2026-10-02", "2026-10-03")
    rows = {r["isin"]: r for r in got["rows"]}
    inside = lambda x: "2026-10-01" < x <= "2026-10-03"  # noqa: E731
    for isin in rows:
        s = 0.0
        for t in acc.p_trades.values():
            if t[1] == isin and inside(t[0]) and t[2] < 0:
                s += t[4]
        assert rows[isin]["s"] == round(s, 2)
        inc = 0.0
        for c in acc.p_cash:
            if c[1] == isin and inside(c[0]):
                inc += c[3]
        assert rows[isin]["inc"] == round(inc, 2)
