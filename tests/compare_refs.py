"""Print engine vs AstroSage reference values (run: python tests/compare_refs.py)."""
import os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
sys.path.insert(0, os.path.dirname(__file__))
import engine
from test_engine import REFS, NOW, dm

def f(x): d = int(x); m = (x - d) * 60; return f"{d}°{int(m):02d}'{int(round((m - int(m)) * 60)):02d}\""
print(f"{'Chart':18} {'Item':10} {'AstroSage':28} {'Drishti':28} Δ")
for name, d, t, lat, lon, (ls, ld), (ms, mdeg), nak, pada, dashas, cur in REFS:
    utc, _ = engine.local_to_utc(d, t, "Asia/Kolkata", lon, 5.5)
    c = engine.compute_chart(utc, lat, lon, now=NOW)
    L, M = c["lagna"], c["planets"]["Moon"]
    print(f"{name:18} {'Lagna':10} {ls+' '+f(ld):28} {L['sign']+' '+f(L['degree']):28} {abs(L['degree']-ld)*60:.1f}'")
    print(f"{'':18} {'Moon':10} {ms+' '+f(mdeg):28} {M['sign']+' '+f(M['degree']):28} {abs(M['degree']-mdeg)*3600:.0f}\"")
    print(f"{'':18} {'Nakshatra':10} {nak+' '+str(pada):28} {c['janma_nakshatra']+' '+str(c['janma_pada']):28}")
    st = {m['lord']: m['start'] for m in c['dasha']['mahadashas']}
    for lord, s in dashas:
        dd = (datetime.fromisoformat(st[lord]) - datetime.fromisoformat(s)).days
        print(f"{'':18} {'MD '+lord:10} {s:28} {st[lord]:28} {dd:+d} d")
    if cur:
        print(f"{'':18} {'Current MD':10} {cur:28} {c['dasha']['current']['mahadasha']['lord']+' › '+c['dasha']['current']['antardasha']['lord']:28}")
