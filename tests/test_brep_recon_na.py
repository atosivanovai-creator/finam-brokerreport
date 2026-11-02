"""смена раскладки справки ФИНАМ не должна давать нули и зелёную «✓ сверка».

Было: строки «Виды движений» берутся только при 18/20 ячейках, «Торговые движения ПФИ» — при 18/25, прочие молча
пропускаются ⇒ items=[], flows={} ⇒ reconcile 0 = 0 ⇒ в шапке «✓ сверка с итогами брокера: N из N». Стало: строка
с датой (или «Рубль» в итогах) неизвестной ширины → `unknown`, пустые итоги при таблице «Виды движений» → «сверка
невозможна», `unknown` — в шапке отчёта. Справки — синтетические (генератор ниже), всё во временном каталоге.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brep import brep_model as bm, brep_parse as bp  # noqa: E402
import test_brep_old_format as tf  # noqa: E402  (генератор старой справки и таблиц)


def report(d0: str, d1: str, flows_cells: int = 20, pfi_cells: int = 18, eq=("1 000,00", "900,00")) -> str:
    flows = ["Рубль", "0,00", "0,00"] + ["0,00"] * 8 + ["-100,00"] + ["0,00"] * 6
    flows = (flows + ["0,00"] * 10)[:flows_cells]
    pfi = [d0, "10:00:00", "Фьючерсы", "CRZ6", "Вариационная маржа", "", "", "", "", "0,00", "-100,00"]
    pfi = (pfi + [""] * 30)[:pfi_cells]
    return ("<html>" + tf._t(f"Справка о состоянии обязательств за период c {d0} по {d1}", [["По счету:", "КлФ-TEST"]])
            + tf._t("1. Оценка состояния счета Клиента", [["ИТОГО оценка", eq[0], eq[1]]])
            + tf._t("Виды движений денежных средств", [flows])
            + tf._t("Торговые движения ПФИ, в т.ч. Комиссии", [pfi]) + "</html>")


def _bad(r):
    return {k: v for k, v in bp.reconcile(r).items() if abs(v[2]) > 0.01}


def test_known_layout_unchanged():
    r = bp.parse_report(report("01.10.2026", "01.10.2026"), "a")
    assert r["flows"]["vm"] == -100 and len(r["items"]) == 1 and r["unknown"] == {} and not r.get("recon_na")
    assert _bad(r) == {}
    old = bp.parse_report(tf._report(), "acc_2021.xls")                       # старая раскладка (18 ячеек)
    assert old["unknown"] == {} and not old.get("recon_na")


def test_both_tables_new_layout_is_not_silent():
    r = bp.parse_report(report("01.10.2026", "01.10.2026", flows_cells=21, pfi_cells=26), "c")
    assert r["items"] == [] and r["flows"] == {}                               # разобрать нечего — как и было
    assert r["unknown"].get("ПФИ: 26 ячеек") == 1, r["unknown"]
    assert r["unknown"].get("Виды движений: 21 ячеек") == 1, r["unknown"]
    assert r.get("recon_na"), "пустые итоги при таблице «Виды движений» — сверка невозможна, а не 0 = 0"


def test_pfi_only_new_layout_counted():
    r = bp.parse_report(report("01.10.2026", "01.10.2026", pfi_cells=26), "b")
    assert r["unknown"] == {"ПФИ: 26 ячеек": 1} and not r.get("recon_na")
    assert "vm" in _bad(r)                                                     # и как было — расхождение видно


def _head(tmp_path: Path, files: dict):
    from flask import Flask
    from brep import brep_view as bv
    data, cache = tmp_path / "data", tmp_path / "cache"
    (data / "acc1").mkdir(parents=True)
    for n, txt in files.items():
        (data / "acc1" / n).write_text(txt, encoding="utf-8")
    app, store = bv.mount(Flask("t"), "/brep/", "acc1", str(data), str(cache))
    cb = next(v for k, v in app.callback_map.items() if "checks.children" in k)
    fn = getattr(cb["callback"], "__wrapped__", cb["callback"])
    title, meta, opts, val, checks, foot = fn("title")
    return store.get(), checks, foot


def _txt(x) -> str:
    if isinstance(x, (list, tuple)):
        return "".join(_txt(i) for i in x)
    ch = getattr(x, "children", None)
    if ch is not None or hasattr(x, "className"):
        return _txt(ch) if ch is not None else ""
    return "" if x is None else str(x)


def _classes(x) -> list:
    if isinstance(x, (list, tuple)):
        return [c for i in x for c in _classes(i)]
    out = [getattr(x, "className", None)] if hasattr(x, "className") else []
    ch = getattr(x, "children", None)
    return out + (_classes(ch) if ch is not None else [])


def test_head_no_green_check_on_unknown_layout(tmp_path):
    acc, checks, foot = _head(tmp_path, {
        "acc1_2026-10-01.xls": report("01.10.2026", "01.10.2026"),
        "acc1_2026-10-02.xls": report("02.10.2026", "02.10.2026", flows_cells=21, pfi_cells=26,
                                      eq=("900,00", "800,00"))})
    t = _txt(checks)
    assert not t.startswith("✓"), t
    assert "ok" not in _classes(checks), checks
    assert "сверка невозможна" in t, t
    assert "ПФИ: 26 ячеек" in t and "Виды движений: 21 ячеек" in t, t       # unknown — в шапке
    assert "сверка невозможна" in _txt(foot).lower() or "acc1_2026-10-02" in _txt(foot), _txt(foot)


def test_head_green_when_all_known(tmp_path):
    acc, checks, foot = _head(tmp_path, {"acc1_2026-10-01.xls": report("01.10.2026", "01.10.2026")})
    t = _txt(checks)
    assert t.startswith("✓") and "1 из 1" in t and "не распознано" not in t, t
    assert _classes(checks)[0] == "ok"
