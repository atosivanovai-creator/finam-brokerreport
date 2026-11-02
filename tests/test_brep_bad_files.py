"""один битый/чужой файл справки не гасит весь «Отчёт брокера» счёта и не разбирается заново
на каждый запрос.

Было: `Account()` строится из всех файлов; исключение одного (пустой файл, обрезок, ручная справка в cp1251) —
`Store.error`, отчёт пуст (после перезапуска окна) и каждый `store.get()` заново разбирает битый файл.
Стало: битый файл пропускается с пометкой (`acc.bad_files`, шапка «файл … не разобран»), отрицательный кэш по
(путь, mtime, размер) — повторно не разбирается; справка в cp1251 без <meta charset> читается как cp1251.
Справки — синтетические (генератор test_brep_recon_na), всё во временном каталоге.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brep import brep_model as bm, brep_parse as bp  # noqa: E402
import test_brep_recon_na as t1  # noqa: E402


def _dirs(tmp_path: Path, files: dict, enc: dict | None = None):
    data, cache = tmp_path / "data", tmp_path / "cache"
    (data / "acc1").mkdir(parents=True)
    for n, txt in files.items():
        p = data / "acc1" / n
        if isinstance(txt, bytes):
            p.write_bytes(txt)
        else:
            p.write_text(txt, encoding=(enc or {}).get(n, "utf-8"))
    return str(data), str(cache)


GOOD = {"acc1_2026-10-01.xls": t1.report("01.10.2026", "01.10.2026"),
        "acc1_2026-10-02.xls": t1.report("02.10.2026", "02.10.2026", eq=("900,00", "800,00"))}


def test_one_broken_file_does_not_blank_account(tmp_path):
    files = dict(GOOD, **{"acc1_2026-10-03.xls": b"", "acc1_2026-10-04.xls": "<html><table><tr><td>обрезок"})
    data, cache = _dirs(tmp_path, files)
    st = bm.Store("acc1", data, cache)
    acc = st.get()
    assert acc is not None, st.error
    assert len(acc.reports) == 2, [r["file"] for r in acc.reports]
    bad = dict(getattr(acc, "bad_files", []))
    assert set(bad) == {"acc1_2026-10-03.xls", "acc1_2026-10-04.xls"}, bad


def test_broken_file_not_reparsed_on_every_get(tmp_path, monkeypatch):
    files = dict(GOOD, **{"acc1_2026-10-03.xls": b"garbage, not html"})
    data, cache = _dirs(tmp_path, files)
    calls = []
    real = bp.parse_report

    def spy(text, name=""):
        calls.append(name)
        return real(text, name)
    monkeypatch.setattr(bp, "parse_report", spy)
    st = bm.Store("acc1", data, cache)
    for _ in range(5):
        st.get()
    assert calls.count("acc1_2026-10-03.xls") == 1, calls
    st2 = bm.Store("acc1", data, cache)                          # другое окно того же процесса / перезапуск Store
    for _ in range(3):
        st2.get()
    assert calls.count("acc1_2026-10-03.xls") == 1, calls


def test_cp1251_report_without_meta_is_read(tmp_path):
    data, cache = _dirs(tmp_path, {"acc1_2026-10-01.xls": t1.report("01.10.2026", "01.10.2026")},
                        enc={"acc1_2026-10-01.xls": "cp1251"})
    st = bm.Store("acc1", data, cache)
    acc = st.get()
    assert acc is not None and len(acc.reports) == 1, (st.error, getattr(acc, "bad_files", None))
    assert acc.reports[0]["account"] == "КлФ-TEST"


def test_head_shows_broken_file_in_yellow(tmp_path):
    files = dict(GOOD, **{"acc1_2026-10-03.xls": b"garbage"})
    from flask import Flask
    from brep import brep_view as bv
    data, cache = _dirs(tmp_path, files)
    app, store = bv.mount(Flask("t"), "/brep/", "acc1", data, cache)
    cb = next(v for k, v in app.callback_map.items() if "checks.children" in k)
    fn = getattr(cb["callback"], "__wrapped__", cb["callback"])
    title, meta, opts, val, checks, foot = fn("title")
    t = t1._txt(checks) + " " + t1._txt(foot)
    assert "acc1_2026-10-03.xls" in t and "не разобран" in t, t
    assert "Отчёт брокера · КлФ-TEST" == title
