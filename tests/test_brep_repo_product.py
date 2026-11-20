"""REPO through the public product intake, cache, screen data and HTML download.

All statements and numbers are invented. No user data or external API is used.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brep import brep_export as bx
from brep import brep_model as bm
from brep import brep_parse as bp
from brep import brep_view as bv
import intake
from test_brep_export import parse, _texts
from test_brep_repo import DAY, REGULATION, _fee, _leg, _table


def _statement(rows, *, end=DAY, incoming="0", outgoing="-1000", fees="-2", equity="9998"):
    flows = ["Рубль"] + ["0"] * 19
    flows[4], flows[7], flows[15] = incoming, outgoing, fees
    return (
        _table(f"Справка за период с 10.01.2025 по {intake.ru(end)}", [["По счету:", "SYNTHETIC"]])
        + _table("1. Оценка состояния счета", [["ИТОГО оценка", "10000", equity]])
        + _table("Виды движений", [flows])
        + _table(REGULATION, rows)
    ).encode("utf-8")


def _load(tmp_path, *statements):
    reports, cache = tmp_path / "reports", tmp_path / "cache"
    for index, raw in enumerate(statements):
        accepted, message = intake.accept(str(reports), f"synthetic-{index}.xls", raw)
        assert accepted, message
    assert intake.accounts(str(reports))[0][:3] == ("SYNTHETIC", "SYNTHETIC", len(statements))
    store = bm.Store("SYNTHETIC", str(reports), str(cache))
    account = store.get()
    assert account is not None and not store.error
    assert store.get() is account
    return store, account


def _download(store):
    server = Flask(__name__)
    path = bx.register(server, "/synthetic/", "SYNTHETIC", store)
    response = server.test_client().get(path)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Content-Disposition"].startswith("attachment;")
    return response.get_data(as_text=True)


def test_repo_intake_reordered_overlap_summary_and_export(tmp_path):
    buy = [_leg("Покупка (РЕПО ч.1)", "250"), _leg("Покупка (РЕПО ч.1)", "500"),
           _leg("Покупка (РЕПО ч.1)", "250")]
    unpaid = _leg("Продажа (РЕПО ч.2)", "1001", "")
    total = [""] * 27
    total[2], total[14], total[15] = "ИТОГО", "2", "Рубль"
    first = _statement([*buy, unpaid, _fee(), total])
    second = _statement(
        [buy[1], buy[2], buy[0], _leg("Продажа (РЕПО ч.2)", "1001", "13.01.2025"),
         _fee(), _fee("1", "13.01.2025")],
        end="2025-01-13", incoming="1001", fees="-3",
    )
    store, account = _load(tmp_path, first, second)
    assert len(account.reports) == 2 and account.reports[1]["joined"] == 1
    assert account.notes == [] and account.recon_bad == [] and account.recon_na == [] and account.unknown == {}
    assert [i["amount"] for r in account.reports for i in r["repo_cash"]] == [-250, -500, -250, 1001]
    assert all(bp.KIND_REPO_BUY not in kv and bp.KIND_REPO_SELL not in kv for kv in account.kind.values())
    assert account.kind[DAY][bp.KIND_BROKER] == -2
    assert account.kind["2025-01-13"][bp.KIND_BROKER] == -1
    summary = account.summary(DAY, "2025-01-13", "Итого")
    # Capital is exchanged for collateral, not ordinary trading turnover or income.
    assert {k: summary[k] for k in ("profit", "broker", "costs", "income", "vm", "lots", "deals", "dep", "wd")} == {
        "profit": -2, "broker": -3, "costs": -3, "income": 0, "vm": 0, "lots": 0, "deals": 0, "dep": 0, "wd": 0,
    }
    assert summary["pct"] == pytest.approx(-0.02)
    securities = account.securities(DAY, "2025-01-13")
    assert securities["rows"] == [] and securities["comm_unalloc"] == 0
    _, checks, _ = bv.head_info(account)
    assert checks[0] == ("✓ ", "ok")
    assert "2 из 2 справок" in "".join(t for t, _ in checks)
    document = _download(store)
    assert "2 из 2 справок" in document and "не распознано строк" not in document
    summary_rows = [_texts(row) for row in parse(document).tables[0]["body"]]
    assert any(row[0] == "Прибыль" and row[-1] == "−2" for row in summary_rows)
    assert any(row[0] == "Брокер" and row[-1] == "−3" for row in summary_rows)


@pytest.mark.parametrize("mode", ["unknown", "mismatch", "conflict"])
def test_repo_warnings_survive_intake_store_and_export(tmp_path, mode):
    row = _leg("Покупка (РЕПО ч.1)")
    if mode == "unknown":
        row[14] = "2"  # Unsupported embedded fee is not silently assumed to be the separate fee.
    first = _statement([row, _fee()], outgoing="-1001" if mode == "mismatch" else "-1000")
    inputs = [first]
    if mode == "conflict":
        inputs.append(_statement([_leg("Покупка (РЕПО ч.1)", "1001"), _fee()],
                                 end="2025-01-13", outgoing="-1001"))
    store, account = _load(tmp_path, *inputs)
    _, checks, foot = bv.head_info(account)
    document = _download(store)
    if mode == "unknown":
        warning = "сделки по Регламенту: комиссия в строке РЕПО"
        assert account.unknown == {warning: 1}
        assert any(warning in text and cls == "warn" for text, cls in checks)
    elif mode == "mismatch":
        warning = "Не сошлось со справкой"
        assert account.recon_bad[0][1]["sec_out"] == (-1000, -1001, 1)
        assert checks[0] == ("⚠ ", "warn")
        assert any(warning in text and cls == "warn" for text, cls in foot)
    else:
        warning = "вторая не учтена"
        assert len(account.reports) == 1
        assert warning in account.notes[0]
        assert any(warning in text for text, _ in foot)
    assert warning in document
