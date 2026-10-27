"""Free kundli PDF: chart houses, planets, dasha timeline, doshas. English or Hindi.

Needs the OFL Noto Sans Devanagari files in server/fonts/ so Devanagari shapes correctly.
"""
from __future__ import annotations

import os

from fpdf import FPDF

import engine

FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "NotoSansDevanagari-Regular.ttf")
FONT_B = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "NotoSansDevanagari-Bold.ttf")

PL_HI = {"Sun": "सूर्य", "Moon": "चंद्र", "Mars": "मंगल", "Mercury": "बुध", "Jupiter": "गुरु",
         "Venus": "शुक्र", "Saturn": "शनि", "Rahu": "राहु", "Ketu": "केतु"}

LABELS = {
    "en": {
        "title": "Drishti · Kundli",
        "sub": "A computed birth chart, not a prediction. Acharya, the AI astrologer, reads this same chart.",
        "person": "Person", "born": "Born", "place": "Place", "time_unknown": "birth time unknown (houses from the Moon)",
        "lagna": "Lagna (rising sign)", "moon": "Moon sign", "nak": "Janma nakshatra",
        "houses": "Chart (whole-sign houses)", "house": "House", "sign": "Sign", "lord": "Lord", "planets": "Planets",
        "graha": "Planets", "deg": "Degree", "dignity": "Dignity", "retro": "retrograde", "combust": "combust",
        "dasha": "Vimshottari dasha", "now": "Now", "md": "Mahadasha", "ad": "Antardasha", "pd": "Pratyantardasha",
        "dosha": "Doshas", "none": "none of the planets listed",
        "foot": "Remedies are not part of this chart. Guidance is optional. Swiss Ephemeris, Lahiri, whole-sign houses.",
    },
    "hi": {
        "title": "दृष्टि · कुंडली",
        "sub": "यह गणना की हुई जन्म कुंडली है, भविष्यवाणी नहीं। AI ज्योतिषी आचार्य इसी कुंडली को पढ़ता है।",
        "person": "नाम", "born": "जन्म", "place": "स्थान", "time_unknown": "जन्म समय अज्ञात (भाव चंद्र से)",
        "lagna": "लग्न", "moon": "चंद्र राशि", "nak": "जन्म नक्षत्र",
        "houses": "कुंडली (पूर्ण राशि भाव)", "house": "भाव", "sign": "राशि", "lord": "स्वामी", "planets": "ग्रह",
        "graha": "ग्रह", "deg": "अंश", "dignity": "बल", "retro": "वक्री", "combust": "अस्त",
        "dasha": "विंशोत्तरी दशा", "now": "अभी", "md": "महादशा", "ad": "अंतर्दशा", "pd": "प्रत्यंतर",
        "dosha": "दोष", "none": "कोई ग्रह नहीं",
        "foot": "इस कुंडली में उपाय नहीं हैं। मार्गदर्शन वैकल्पिक है। स्विस एफेमेरिस, लाहिरी, पूर्ण राशि भाव।",
    },
}


def _sign(name: str, lang: str) -> str:
    if lang != "hi":
        return name
    return engine.SIGNS_HI[engine.SIGNS.index(name)]


def _pl(name: str, lang: str) -> str:
    return PL_HI[name] if lang == "hi" else name


def sections(chart: dict, lang: str) -> list[str]:
    """Plain lines drawn into the PDF. Tests check these, not glyph bytes."""
    lang = "hi" if lang == "hi" else "en"
    L = LABELS[lang]
    prof = chart.get("profile") or {}
    name = prof.get("name") or ""
    lines = [L["title"], L["sub"], f"{L['person']}: {name}",
             f"{L['born']}: {prof.get('date', '')} {prof.get('time') or L['time_unknown']}",
             f"{L['place']}: {prof.get('place', '')}"]
    if not chart.get("time_known", True):
        lines.append(L["time_unknown"])
    lagna = chart["lagna"]["sign"] if chart.get("time_known", True) else L["time_unknown"]
    lines.append(f"{L['lagna']}: {_sign(chart['lagna']['sign'], lang) if chart.get('time_known', True) else lagna}")
    lines.append(f"{L['moon']}: {_sign(chart['moon_sign'], lang)}")
    lines.append(f"{L['nak']}: {chart.get('janma_nakshatra', '')} {chart.get('janma_pada', '')}")
    lines.append(L["houses"])
    for h in chart["houses"]:
        occ = ", ".join(_pl(p, lang) for p in (h.get("occupants") or [])) or L["none"]
        lines.append(f"{L['house']} {h['house']}: {_sign(h['sign'], lang)} · {L['lord']} {_pl(h['lord'], lang)} · {occ}")
    lines.append(L["graha"])
    for p, r in chart["planets"].items():
        flags = []
        if r.get("retrograde") and p not in ("Rahu", "Ketu"):
            flags.append(L["retro"])
        if r.get("combust"):
            flags.append(L["combust"])
        extra = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"{_pl(p, lang)} {_sign(r['sign'], lang)} {r['degree_dms']} {L['house']} {r['house']} {r['dignity']}{extra}")
    lines.append(L["dasha"])
    cur = (chart.get("dasha") or {}).get("current") or {}
    if cur.get("mahadasha"):
        lines.append(f"{L['now']}: {L['md']} {_pl(cur['mahadasha']['lord'], lang)} {cur['mahadasha']['start']} – {cur['mahadasha']['end']}")
        lines.append(f"{L['ad']} {_pl(cur['antardasha']['lord'], lang)} {cur['antardasha']['start']} – {cur['antardasha']['end']}")
        if cur.get("pratyantardasha"):
            lines.append(f"{L['pd']} {_pl(cur['pratyantardasha']['lord'], lang)} {cur['pratyantardasha']['start']} – {cur['pratyantardasha']['end']}")
    for m in (chart.get("dasha") or {}).get("mahadashas") or []:
        lines.append(f"{L['md']} {_pl(m['lord'], lang)} {m['start']} – {m['end']}")
    lines.append(L["dosha"])
    for d in chart.get("doshas") or []:
        state = "present" if d.get("present") else "clear"
        if lang == "hi":
            state = "है" if d.get("present") else "नहीं"
        bit = d.get("level") or d.get("phase") or ""
        window = ""
        w = d.get("current_window") or d.get("next_window")
        if isinstance(w, dict) and (w.get("start") or w.get("end")):
            window = f" {w.get('start') or ''}–{w.get('end') or ''}"
        lines.append(f"{d.get('name', '')}: {state} {bit}{window}".strip())
    lines.append(L["foot"])
    return lines


def render_pdf(chart: dict, lang: str = "en") -> bytes:
    lines = sections(chart, lang)
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_font("Noto", "", FONT)
    pdf.add_font("Noto", "B", FONT_B)
    pdf.add_page()
    pdf.set_text_shaping(True)
    pdf.set_font("Noto", "B", 18)
    pdf.multi_cell(0, 10, lines[0], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Noto", "", 11)
    heads = {LABELS[k][key] for k in ("en", "hi") for key in ("houses", "graha", "dasha", "dosha")}
    for line in lines[1:]:
        if line in heads:
            pdf.ln(2)
            pdf.set_font("Noto", "B", 13)
            pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Noto", "", 10)
            continue
        pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
