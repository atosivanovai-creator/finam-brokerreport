"""Synthetic REPO contracts derived from the statement's column layout, not its amounts."""
import json
import sys
from decimal import Decimal
from itertools import permutations
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brep import brep_model as bm
from brep import brep_parse as bp

ISIN = "RU0000000001"
DAY = "2025-01-10"
REGULATION = "Сделки, условия которых содержатся в тексте Регламента брокерского обслуживания"


def _table(title, rows):
    return f"<p>{title}</p><table>" + "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    ) + "</table>"


def _leg(op, amount="1000", paid="10.01.2025"):
    row = [""] * 27
    row[0:5] = ["10.01.2025", "12:00:00", "Synthetic equity", ISIN, op]
    row[5], row[7], row[11], row[13] = "10", "Рубль", "Рубль", amount
    row[16], row[17] = ("0", "0") if paid else (amount, "-10")
    row[18:22] = ["10.01.2025", "10.01.2025", paid, paid]
    row[23] = "Внебиржевая"
    return row


def _fee(amount="2", paid="10.01.2025"):
    row = [""] * 27
    row[0], row[4], row[14], row[15], row[20] = (
        "10.01.2025", "Комиссия брокерская", amount, "Рубль", paid
    )
    return row


def _report(rows, *, short=False):
    flows = ["Рубль"] + ["0"] * 19
    flows[7], flows[15] = "-1000", "-2"
    flows[4] = "1100" if short else "0"
    text = _table("Справка за период с 10.01.2025 по 10.01.2025", [["По счету:", "SYNTHETIC"]])
    text += _table("1. Оценка состояния счета", [
        ["ИТОГО оценка", "10000", "10047" if short else "10000"],
        ["Оценка требований и обязательств", "0", "999" if short else "0"],
    ])
    text += _table("Виды движений", [flows])
    text += _table(REGULATION, [["Дата сделки"] + [""] * 26, *rows])
    if short:
        sale = [""] * 28
        sale[0], sale[3], sale[4], sale[5], sale[11], sale[13], sale[25] = (
            "10.01.2025", ISIN, "Продажа", "10", "Рубль", "1100", "SYNTHETIC-SALE"
        )
        sale[18:22] = ["10.01.2025"] * 4
        text += _table("в качестве ценных бумаг (включая незавершенные", [sale])
        text += _table("Оценка позиции по ценным бумагам", [
            ["Synthetic equity", "Synthetic equity", ISIN, "0", "0", "0", "10", "10", "0", "0", "0"],
        ])
        text += _table("Оценка обязательств по незавершенным сделкам", [
            ["Synthetic equity", "Synthetic equity", ISIN, "0", "0", "0", "Рубль", "0",
             "-10", "-1050", "999", "Рубль", "999"],
        ])
    return text


def test_paid_repo_cash_and_fee_reconcile_once():
    rows = [_leg("Покупка (РЕПО ч.1)"), _leg("Продажа (РЕПО ч.2)", "999", ""), _fee()]
    # The summary repeats the fee but has no transaction date: it must not be booked twice.
    total = [""] * 27
    total[2], total[14], total[15] = "ИТОГО", "2", "Рубль"
    r = bp.parse_report(_report([*rows, total]))
    # Baseline must fail here for missing cash/fee, not for missing new constants.
    assert all(v[2] == 0 for v in bp.reconcile(r).values())
    assert [(i["date"], i["kind"], i["amount"]) for i in r["items"]] == [
        (DAY, bp.KIND_REPO_BUY, -1000), (DAY, bp.KIND_BROKER, -2),
    ]
    assert r["unknown"] == {}
    assert r["trades"] == {} and r["papers"]["trades"] == {} and r["papers"]["comm"] == {}


def test_only_actual_payment_date_is_used():
    rows = [_leg("Покупка (РЕПО ч.1)", paid=""), _fee(paid="")]
    assert bp.parse_report(_report(rows))["items"] == []
    rows = [_leg("Продажа (РЕПО ч.2)", "999", "13.01.2025"), _fee(paid="13.01.2025")]
    r = bp.parse_report(_report(rows))
    assert [(i["date"], i["amount"]) for i in r["items"]] == [("2025-01-13", 999), ("2025-01-13", -2)]
    assert bp.reconcile(r)["sec_in"][0] == 999


@pytest.mark.parametrize("op,sign", [("Покупка (РЕПО ч.1)", -1), ("Продажа (РЕПО ч.2)", 1)])
def test_repo_cash_sign_and_conservation_for_bounded_cent_amounts(op, sign):
    # Exhaustive bounded property: independent Decimal oracle, no new test dependency.
    for cents in range(1, 101):
        money = str(Decimal(cents) / 100)
        r = bp.parse_report(_report([_leg(op, money)]))
        assert len(r["items"]) == 1
        assert Decimal(str(r["items"][0]["amount"])) == sign * Decimal(money)
        assert not r["papers"]["trades"] and not r["trades"]


@pytest.mark.parametrize("change,key", [
    (lambda row: row.append("new column"), "сделки по Регламенту: 28 ячеек"),
    (lambda row: row.__setitem__(11, "USD"), "сделки по Регламенту: расчёты в USD"),
    (lambda row: row.__setitem__(20, "invalid-date"), "сделки по Регламенту: дата оплаты факт"),
    (lambda row: row.__setitem__(13, "-1000"), "сделки по Регламенту: отрицательная сумма"),
    (lambda row: row.__setitem__(13, "not-a-number"), bp.NONNUM),
    (lambda row: row.__setitem__(13, "nan"), "сделки по Регламенту: не конечная сумма"),
    (lambda row: row.__setitem__(13, ""), "сделки по Регламенту: нет суммы"),
    (lambda row: row.__setitem__(13, "—"), "сделки по Регламенту: нет суммы"),
])
def test_unsupported_repo_values_stay_visible(change, key):
    row = _leg("Покупка (РЕПО ч.1)")
    change(row)
    r = bp.parse_report(_report([row]))
    assert r["items"] == []
    assert r["unknown"][key] == 1


def test_unknown_operation_is_not_whitelisted():
    r = bp.parse_report(_report([_leg("РЕПО ч.1")]))
    assert r["unknown"] == {"сделки по Регламенту: РЕПО ч.1": 1}
    assert r["items"] == []


def test_embedded_repo_fee_is_not_silently_ignored():
    row = _leg("Покупка (РЕПО ч.1)")
    row[14] = "2"
    r = bp.parse_report(_report([row]))
    assert r["unknown"] == {"сделки по Регламенту: комиссия в строке РЕПО": 1}
    assert [i["amount"] for i in r["items"]] == [-1000]


def test_repo_fee_currency_is_checked():
    row = _fee()
    row[15] = "USD"
    r = bp.parse_report(_report([row]))
    assert r["unknown"] == {"сделки по Регламенту: расчёты в USD": 1}
    assert r["items"] == []


def test_short_plan_and_requirements_remain_separate_from_repo_trades(tmp_path):
    text = _report([_leg("Покупка (РЕПО ч.1)"), _leg("Продажа (РЕПО ч.2)", "999", ""), _fee()], short=True)
    r = bp.parse_report(text)
    c = bm.compact(dict(r, recon=bp.reconcile(r)))
    assert c["req"] == [0, 0]  # The REPO requirement must not become unpaid futures VM.
    assert c["papers"]["hold"][ISIN][3:] == [-10, -1050]
    assert len(c["papers"]["trades"]) == 1
    assert c["papers"]["comm"] == {} and c["broker_pfi"] == {} and c["lots_paid"] == {}
    assert c["repo_cash"][0]["amount"] == -1000
    assert bp.KIND_REPO_BUY not in c["day_kind"][DAY]
    assert all(v[2] == 0 for v in c["recon"].values())
    directory = tmp_path / "reports" / "synthetic"
    directory.mkdir(parents=True)
    (directory / "synthetic_2025-01-10.xls").write_text(text, encoding="utf-8")
    account = bm.Account("synthetic", str(directory.parent), str(tmp_path / "cache"))
    sec = account.securities(DAY, DAY)
    row = next(r for r in sec["rows"] if r["isin"] == ISIN)
    assert (row["bq"], row["sq"], row["q1"], row["dq"], row["ok"]) == (0, 10, -10, 0, True)
    assert row["comm"] == 0 and sec["comm_unalloc"] == 0
    assert account.kind[DAY][bp.KIND_BROKER] == -2
    assert account.broker_pfi.get(DAY, 0) == 0


def test_cache_v6_is_not_reused(tmp_path):
    path = tmp_path / "synthetic.xls"
    path.write_text(_report([_leg("Покупка (РЕПО ч.1)"), _fee()]), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    stat = path.stat()
    key = cache / "synthetic.xls.json"
    key.write_text(json.dumps({"sig": [6, int(stat.st_mtime), stat.st_size], "r": {"stale": True}}), encoding="utf-8")
    loaded = bm._load(str(path), str(cache))
    assert "stale" not in loaded
    assert loaded["repo_cash"][0]["amount"] == -1000
    assert bp.KIND_REPO_BUY not in loaded["day_kind"][DAY]
    assert json.loads(key.read_text())["sig"][0] == bm.CACHE_V
    assert bm.CACHE_V > 6


def test_repo_principal_does_not_change_equity_seam_or_duplicate_on_overlap():
    r = bp.parse_report(_report([_leg("Покупка (РЕПО ч.1)"), _fee()]))
    # Principal exchanges cash for collateral; only the fee changes this synthetic equity boundary.
    r["equity"] = (10000, 9998)
    a = bm.compact(dict(r, recon=bp.reconcile(r)))
    b = dict(a, to="2025-01-13")
    joined = bm.join_overlap(a, b)
    assert joined is not None and joined["from"] == "2025-01-11"
    assert joined["repo_cash"] == [] and joined["day_kind"] == {}
    conflicting = dict(b, repo_cash=[dict(b["repo_cash"][0], amount=-1001)])
    assert bm.join_overlap(a, conflicting) is None


def _repo_overlap_pair():
    # Two identical rows are intentional: overlap is a multiset, not a set or a total.
    r = bp.parse_report(_report([
        _leg("Покупка (РЕПО ч.1)", "250"), _leg("Покупка (РЕПО ч.1)", "500"),
        _leg("Покупка (РЕПО ч.1)", "250"), _fee(),
    ]))
    assert all(v[2] == 0 for v in bp.reconcile(r).values())
    r["equity"] = (10000, 9998)
    a = bm.compact(dict(r, recon=bp.reconcile(r)))
    later = dict(a["repo_cash"][0], date="2025-01-13", amount=-100)
    b = dict(a, to="2025-01-13", repo_cash=[*a["repo_cash"], later])
    return a, b, later


def test_repo_overlap_accepts_every_row_permutation_with_duplicates():
    a, b, later = _repo_overlap_pair()
    original = json.dumps([a, b], sort_keys=True)
    # Exhaustive bounded permutation property; key insertion order is not row identity either.
    for rows in permutations(a["repo_cash"]):
        reordered = [dict(reversed(list(row.items()))) for row in rows]
        joined = bm.join_overlap(a, dict(b, repo_cash=[later, *reordered]))
        assert joined is not None
        assert joined["from"] == "2025-01-11" and joined["to"] == "2025-01-13"
        assert joined["repo_cash"] == [later]
        assert joined["day_kind"] == {} and joined["equity"] == (9998, 9998)
    assert json.dumps([a, b], sort_keys=True) == original


@pytest.mark.parametrize("copies", [0, 1, 3])
def test_repo_overlap_rejects_changed_duplicate_multiplicity(copies):
    a, b, later = _repo_overlap_pair()
    duplicate, distinct, _ = a["repo_cash"]
    rows = [distinct, *[dict(duplicate) for _ in range(copies)], later]
    assert bm.join_overlap(a, dict(b, repo_cash=rows)) is None


@pytest.mark.parametrize("conflict", ["amount", "same-total", "kind", "instr", "comment", "src"])
def test_repo_overlap_rejects_conflicting_rows(conflict):
    a, b, _ = _repo_overlap_pair()
    rows = [dict(row) for row in b["repo_cash"]]
    if conflict == "same-total":
        # Totals and row count still match; individual cash rows must match too.
        rows[0]["amount"] -= 1
        rows[1]["amount"] += 1
    elif conflict == "amount":
        rows[0]["amount"] -= 1
    else:
        rows[0][conflict] = "conflicting row"
    assert bm.join_overlap(a, dict(b, repo_cash=rows)) is None
