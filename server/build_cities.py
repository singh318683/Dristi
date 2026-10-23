"""Build data/cities.tsv from GeoNames cities15000 (CC-BY 4.0)."""
import csv, os, re
D = os.path.join(os.path.dirname(__file__), "data")
countries = {}
for line in open(os.path.join(D, "countries.txt"), encoding="utf-8"):
    if line.startswith("#"): continue
    f = line.rstrip("\n").split("\t")
    countries[f[0]] = f[4]
admin = {}
for line in open(os.path.join(D, "admin1.txt"), encoding="utf-8"):
    f = line.rstrip("\n").split("\t"); admin[f[0]] = f[1]
out = open(os.path.join(D, "cities.tsv"), "w", encoding="utf-8")
n = 0
for line in open(os.path.join(D, "cities15000.txt"), encoding="utf-8"):
    f = line.rstrip("\n").split("\t")
    name, ascii_, alts, lat, lon, cc, a1, pop, tz = f[1], f[2], f[3], f[4], f[5], f[8], f[10], f[14], f[17]
    alt = sorted({a for a in alts.split(",") if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{2,30}", a) and a.lower() != ascii_.lower()})[:8]
    out.write("\t".join([name, ascii_, admin.get(f"{cc}.{a1}", ""), countries.get(cc, cc), cc, lat, lon, tz, pop, "|".join(alt)]) + "\n")
    n += 1
print(n, "cities")
