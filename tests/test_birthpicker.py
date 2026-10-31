"""The birth wheels must keep YYYY-MM-DD and HH:MM, and refuse impossible dates."""
import datetime as dt
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = dt.date(2026, 10, 31)


def picker(expr, today=TODAY):
    script = f"""
const P = require({json.dumps(os.path.join(ROOT, "web", "birthpicker.js"))});
const today = new Date({today.year}, {today.month - 1}, {today.day});
const out = (function () {{ {expr} }})();
process.stdout.write(JSON.stringify(out));
"""
    raw = subprocess.check_output(["node", "-e", script], text=True)
    return json.loads(raw)


def test_page_uses_wheels_not_native_pickers():
    html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    assert 'type="date"' not in html and 'type="time"' not in html
    assert "birthpicker.js" in html
    assert "wheel-y" in html and "wheel-ap" in html
    assert 'id="dob"' in html and 'id="tob"' in html
    assert 'id="open-date"' in html and 'id="open-time"' in html and 'id="when-done"' in html
    assert "That's okay, many people don't know" not in html


def test_empty_date_lands_on_15_june_1990_and_roundtrips():
    assert picker("return P.formatDate(P.resolveDate('', today));") == "1990-06-15"
    assert picker("return P.labelDateShort(P.resolveDate('', today), 'en');") == "15 Jun 1990"
    assert picker("return P.labelTime(P.resolveTime(''), 'en');") == "12:00 PM"
    assert picker("return P.formatDate(P.resolveDate('1994-03-14', today));") == "1994-03-14"
    assert picker("return P.formatTime(P.resolveTime('06:45'));") == "06:45"
    assert picker("return P.formatTime(P.resolveTime(''));") == "12:00"
    assert picker("return P.formatTime(P.parseTime('00:00'));") == "00:00"
    assert picker("return P.formatTime(P.parseTime('12:00'));") == "12:00"
    assert picker("return P.formatTime(P.parseTime('23:05'));") == "23:05"


def test_impossible_and_future_dates_are_clamped():
    assert picker("return P.formatDate(P.clampDate({y:1990,m:2,d:30}, today));") == "1990-02-28"
    assert picker("return P.formatDate(P.clampDate({y:1900,m:2,d:29}, today));") == "1900-02-28"
    assert picker("return P.formatDate(P.clampDate({y:2000,m:2,d:29}, today));") == "2000-02-29"
    assert picker("return P.formatDate(P.clampDate({y:2026,m:12,d:15}, today));") == "2026-10-15"
    assert picker("return P.formatDate(P.resolveDate('2027-05-01', today));") == "2026-05-01"
    assert picker("return P.parseDate('1994-02-30');") is None


def test_year_list_runs_back_to_1900_and_keeps_older_profiles():
    years = picker("return P.yearOptions(today, 1900);")
    assert years[0] == 2026 and years[-1] == 1900
    assert 1981 in years and 1990 in years
    older = picker("return P.yearOptions(today, 1888);")
    assert older[-1] == 1888 and older[0] == 2026
    assert picker("return P.monthOptions(2026, today, 'en').length;") == 10
    assert picker("return P.monthOptions(1990, today, 'en').length;") == 12
    assert picker("return P.dayOptions(1990, 2, today).length;") == 28
    assert picker("return P.dayOptions(2000, 2, today).length;") == 29
    assert picker("return P.dayOptions(2026, 10, today).length;") == 31
    assert picker("return P.monthOptions(1981, today, 'hi')[1].label;") == "फ़रवरी"
