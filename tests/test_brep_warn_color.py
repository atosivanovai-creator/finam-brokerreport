"""«если на саму торговлю не влияет — цвет жёлтый». «Отчёт брокера» на торговлю не влияет
никогда: «Не сошлось со справкой», «не распознано строк» и ⚠ в шапке — жёлтые (класс warn, переменная темы --warnt),
как «сверка невозможна» (cd19ef4). Было: красные (bad, --badt).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_brep_recon_na as t1  # noqa: E402

ASSETS = Path(__file__).resolve().parents[1] / "brep" / "assets"


def test_mismatch_and_unknown_are_yellow(tmp_path):
    acc, checks, foot = t1._head(tmp_path, {
        "acc1_2026-10-01.xls": t1.report("01.10.2026", "01.10.2026", pfi_cells=26)})   # ВМ не сошлась + unknown
    assert acc.recon_bad, "сценарий: «не сошлось»"
    assert acc.unknown
    cls = t1._classes(checks)
    assert "bad" not in cls, cls
    assert cls[0] == "warn", cls                                           # ⚠ шапки
    t = t1._txt(checks)
    assert "не распознано строк" in t
    fc = [(t1._txt(p), getattr(p, "className", None)) for p in foot]
    mism = [c for txt, c in fc if "Не сошлось со справкой" in txt]
    assert mism == ["warn"], fc


def test_warn_class_uses_theme_variable_both_themes():
    css = (ASSETS / "brep.css").read_text(encoding="utf-8")
    assert ".warn{color:var(--warnt)}" in css and "--warnt:" in css             # Графит (по умолчанию)
    js = (ASSETS / "brep-theme.js").read_text(encoding="utf-8")
    assert '"--warnt"' in js                                                    # копируется из родительского окна (iframe)
    light = next(ln for ln in js.splitlines() if ln.strip().startswith("light:"))
    assert "--warnt:" in light                                                  # светлая тема без родительского окна
