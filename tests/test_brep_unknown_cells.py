"""нераспознанное в справке — видно в шапке «Отчёта брокера».

`unknown` выводился только в блоке «Ценные бумаги», скрытом на счетах без бумаг. Теперь
`unknown` — в шапке всегда; тест — проверка на счёте без бумаг с новой операцией ПФИ.
`num()` на любом формате, кроме «1 234,56»/«−», молча давал 0. Стало: пустая ячейка и прочерк — 0, как было;
непустая нечисловая ячейка в числовом поле — тоже 0 (числа не меняем), но счётчик «нечисловых ячеек» в `unknown`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brep import brep_parse as bp  # noqa: E402
import test_brep_old_format as tf  # noqa: E402
import test_brep_recon_na as t1  # noqa: E402


def _pfi_report(op: str, vm: str = "-100,00") -> str:
    flows = ["Рубль", "0,00", "0,00"] + ["0,00"] * 8 + [vm if vm.strip() else "0,00"] + ["0,00"] * 8
    pfi = [["01.10.2026", "10:00:00", "Фьючерсы", "CRZ6", "Вариационная маржа", "", "", "", "", "0,00", vm]
           + [""] * 7,
           ["01.10.2026", "10:00:00", "Фьючерсы", "CRZ6", op, "", "", "", "", "0,00", "0,00"] + [""] * 7]
    return ("<html>" + tf._t("Справка о состоянии обязательств за период c 01.10.2026 по 01.10.2026",
                             [["По счету:", "КлФ-TEST"]])
            + tf._t("1. Оценка состояния счета Клиента", [["Наименование", "на начало", "на конец"],
                                                           ["ИТОГО оценка", "1 000,00", "900,00"]])
            + tf._t("Виды движений денежных средств", [flows])
            + tf._t("Торговые движения ПФИ, в т.ч. Комиссии", pfi) + "</html>")


def test_b8b03_unknown_visible_without_securities(tmp_path):
    acc, checks, foot = t1._head(tmp_path, {"acc1_2026-10-01.xls": _pfi_report("Новая операция")})
    assert not (acc.p_names or acc.p_trades or acc.p_fx)                    # бумаг нет — блок бумаг скрыт
    t = t1._txt(checks)
    assert "ПФИ: Новая операция" in t, t


def test_b8b04_num_known_formats_unchanged():
    assert bp.num("1 234,56") == 1234.56 and bp.num("\u22121,5") == -1.5 and bp.num("") == 0.0
    assert bp.num("-") == 0.0 and bp.num(None) == 0.0


def test_b8b04_non_numeric_cell_is_counted_not_silent():
    r = bp.parse_report(_pfi_report("Вариационная маржа", vm="(100,00)"), "x")
    assert r["flows"]["vm"] == 0.0                                           # число то же, что и было (0)
    k = [k for k in r["unknown"] if "нечислов" in k]
    assert k, r["unknown"]
    assert r["unknown"][k[0]] >= 1


def test_b8b04_header_rows_are_not_counted():
    r = bp.parse_report(_pfi_report("Вариационная маржа"), "y")              # строка-заголовок в «1. Оценка»
    assert not [k for k in r["unknown"] if "нечислов" in k], r["unknown"]
    old = bp.parse_report(tf._report(), "acc_2021.xls")
    assert old["unknown"] == {}, old["unknown"]
