"""Правило «если на саму торговлю не влияет — цвет жёлтый»: «Отчёт брокера» на
торговлю не влияет никогда. Остатки красного после S3I: блок «Бумаги» — «⚠ Количество не сошлось» (подвал блока) и
ячейка «Кол‑во» с ⚠ у бумаги, сообщение об ошибке сборки справок в подвале шапки — были классом `bad` (--badt,
красный). Стало: `warn` (переменная темы --warnt). Отрицательные числа P&L красятся по знаку (`neg`) — не статус,
не меняются.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_brep_securities as ts  # noqa: E402  (генератор справки с бумагами)
import test_brep_recon_na as t1  # noqa: E402


def _mount(tmp_path):
    from flask import Flask
    from brep import brep_view as bv
    data = tmp_path / "data"
    (data / "acc1").mkdir(parents=True)
    (data / "acc1" / "acc1_2025-01-01..2025-12-31.xls").write_text(
        ts._report(end_hold_b="4 000", extra_lend_op="РЕПО ч.1"), encoding="utf-8")   # количество не сошлось
    app, store = bv.mount(Flask("t"), "/brep/", "acc1", str(data), str(tmp_path / "cache"))
    return bv, app, store


def _cb(app, key):
    cb = next(v for k, v in app.callback_map.items() if key in k)
    return getattr(cb["callback"], "__wrapped__", cb["callback"])


def test_quantity_mismatch_note_is_yellow(tmp_path):
    _bv, app, store = _mount(tmp_path)
    rows, _opts, notes, _style = _cb(app, "sec-notes.children")("2025-01-01", "2025-12-31")
    got = {t1._txt(p)[:40]: getattr(p, "className", None) for p in notes}
    mism = [c for t, c in got.items() if "Количество не сошлось" in t]
    assert mism == ["warn"], got
    assert "bad" not in got.values(), got
    assert any(r.get("chk", "").startswith("⚠") for r in rows), "сценарий: у бумаги ⚠ в ячейке «Кол‑во»"


def test_quantity_cell_rule_is_yellow():
    from brep import brep_view as bv
    col = next(c for c in bv.SEC_COLS if c.get("field") == "chk")
    rules = col["cellClassRules"]
    assert "bad" not in rules and "warn" in rules, rules
    assert rules["warn"] == "params.value && params.value !== '✓'"


def test_build_error_in_footer_is_yellow(tmp_path):
    _bv, app, store = _mount(tmp_path)
    assert store.get() is not None
    store.error = "OSError: справка не читается"         # сбой пересборки — прежний счёт остаётся, ошибка в подвале
    title, meta, opts, val, checks, foot = _cb(app, "checks.children")("title")
    fc = [(t1._txt(p), getattr(p, "className", None)) for p in foot]
    err = [c for t, c in fc if "справка не читается" in t]
    assert err == ["warn"], fc
