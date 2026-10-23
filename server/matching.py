"""Ashtakoota (36-guna) milan between two charts (bride/girl and groom/boy)."""
from engine import SIGN_LORD, NAKSHATRAS, SIGNS

VARNA_NAMES = ["Shudra", "Vaishya", "Kshatriya", "Brahmin"]
VARNA = {0: 2, 1: 1, 2: 0, 3: 3, 4: 2, 5: 1, 6: 0, 7: 3, 8: 2, 9: 1, 10: 0, 11: 3}

VASHYA_NAMES = ["Chatushpada", "Manava", "Jalachara", "Vanachara", "Keeta"]
VASHYA_TABLE = [  # rows boy, cols girl
    [2, 1, 1, 0.5, 1],
    [1, 2, 0.5, 0, 1],
    [1, 0.5, 2, 1, 1],
    [0.5, 0, 1, 2, 0],
    [1, 1, 1, 0, 2],
]


def vashya(sign: int, deg: float) -> int:
    if sign in (0, 1): return 0
    if sign == 8: return 1 if deg < 15 else 0
    if sign == 9: return 0 if deg < 15 else 2
    if sign in (2, 5, 6, 10): return 1
    if sign in (3, 11): return 2
    if sign == 4: return 3
    return 4  # Scorpio


YONI_NAMES = ["Horse", "Elephant", "Sheep", "Serpent", "Dog", "Cat", "Rat", "Cow", "Buffalo",
              "Tiger", "Deer", "Monkey", "Mongoose", "Lion"]
YONI_OF_NAK = [0, 1, 2, 3, 3, 4, 5, 2, 5, 6, 6, 7, 8, 9, 8, 9, 10, 10, 4, 11, 12, 11, 13, 0, 13, 7, 1]
YONI_TABLE = [
    [4, 2, 2, 3, 2, 2, 2, 1, 0, 1, 3, 3, 2, 1],
    [2, 4, 3, 3, 2, 2, 2, 2, 3, 1, 2, 3, 2, 0],
    [2, 3, 4, 2, 1, 2, 1, 3, 3, 1, 2, 0, 3, 1],
    [3, 3, 2, 4, 2, 1, 1, 1, 1, 2, 2, 2, 0, 2],
    [2, 2, 1, 2, 4, 2, 1, 2, 2, 1, 0, 2, 1, 1],
    [2, 2, 2, 1, 2, 4, 0, 2, 2, 1, 3, 3, 2, 1],
    [2, 2, 1, 1, 1, 0, 4, 2, 2, 2, 2, 2, 1, 2],
    [1, 2, 3, 1, 2, 2, 2, 4, 3, 0, 3, 2, 2, 1],
    [0, 3, 3, 1, 2, 2, 2, 3, 4, 1, 2, 2, 2, 1],
    [1, 1, 1, 2, 1, 1, 2, 0, 1, 4, 1, 1, 2, 1],
    [3, 2, 2, 2, 0, 3, 2, 3, 2, 1, 4, 2, 2, 1],
    [3, 3, 0, 2, 2, 3, 2, 2, 2, 1, 2, 4, 3, 2],
    [2, 2, 3, 0, 1, 2, 1, 2, 2, 2, 2, 3, 4, 2],
    [1, 0, 1, 2, 1, 1, 2, 1, 1, 1, 1, 2, 2, 4],
]
MAITRI_ORDER = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
MAITRI_TABLE = [
    [5, 5, 5, 4, 5, 0, 0],
    [5, 5, 4, 1, 4, 0.5, 0.5],
    [5, 4, 5, 0.5, 5, 3, 0.5],
    [4, 1, 0.5, 5, 0.5, 5, 4],
    [5, 4, 5, 0.5, 5, 0.5, 3],
    [0, 0.5, 3, 5, 0.5, 5, 5],
    [0, 0.5, 0.5, 4, 3, 5, 5],
]
GANA_NAMES = ["Deva", "Manushya", "Rakshasa"]
GANA_OF_NAK = [0, 1, 2, 1, 0, 1, 0, 0, 2, 2, 1, 1, 0, 2, 0, 2, 0, 2, 2, 1, 1, 0, 2, 2, 1, 1, 0]
GANA_TABLE = [  # rows girl, cols boy
    [6, 5, 1],
    [6, 6, 0],
    [0, 0, 6],
]
NADI_NAMES = ["Adi (Vata)", "Madhya (Pitta)", "Antya (Kapha)"]
NADI_OF_NAK = [[0, 1, 2, 2, 1, 0][i % 6] for i in range(27)]
TARA_NAMES = ["Janma", "Sampat", "Vipat", "Kshema", "Pratyari", "Sadhaka", "Vadha", "Mitra", "Ati-Mitra"]


def _tara(from_nak: int, to_nak: int) -> tuple[int, str]:
    n = (to_nak - from_nak) % 27 + 1
    r = n % 9 or 9
    return r, TARA_NAMES[r - 1]


def milan(girl: dict, boy: dict) -> dict:
    g, b = girl["planets"]["Moon"], boy["planets"]["Moon"]
    gs, bs = g["sign_index"], b["sign_index"]
    gn, bn = g["nakshatra_index"], b["nakshatra_index"]
    kootas = []

    gv, bv = VARNA[gs], VARNA[bs]
    kootas.append({"name": "Varna", "max": 1, "score": 1 if bv >= gv else 0,
                   "girl": VARNA_NAMES[gv], "boy": VARNA_NAMES[bv], "about": "spiritual / work temperament"})
    gva, bva = vashya(gs, g["degree"]), vashya(bs, b["degree"])
    kootas.append({"name": "Vashya", "max": 2, "score": VASHYA_TABLE[bva][gva],
                   "girl": VASHYA_NAMES[gva], "boy": VASHYA_NAMES[bva], "about": "mutual attraction and influence"})
    r1, t1 = _tara(gn, bn); r2, t2 = _tara(bn, gn)
    ts = (0 if r1 in (3, 5, 7) else 1.5) + (0 if r2 in (3, 5, 7) else 1.5)
    kootas.append({"name": "Tara", "max": 3, "score": ts, "girl": t2, "boy": t1, "about": "health and well-being of the pair"})
    gy, by = YONI_OF_NAK[gn], YONI_OF_NAK[bn]
    kootas.append({"name": "Yoni", "max": 4, "score": YONI_TABLE[by][gy],
                   "girl": YONI_NAMES[gy], "boy": YONI_NAMES[by], "about": "physical and intimate compatibility"})
    gl, bl = SIGN_LORD[gs], SIGN_LORD[bs]
    kootas.append({"name": "Graha Maitri", "max": 5,
                   "score": MAITRI_TABLE[MAITRI_ORDER.index(bl)][MAITRI_ORDER.index(gl)],
                   "girl": gl, "boy": bl, "about": "mental wavelength (friendship of Moon-sign lords)"})
    gg, bg = GANA_OF_NAK[gn], GANA_OF_NAK[bn]
    kootas.append({"name": "Gana", "max": 6, "score": GANA_TABLE[gg][bg],
                   "girl": GANA_NAMES[gg], "boy": GANA_NAMES[bg], "about": "temperament"})
    d = (bs - gs) % 12 + 1
    pair = tuple(sorted((d, (gs - bs) % 12 + 1)))
    bk_bad = pair in {(2, 12), (5, 9), (6, 8)}
    kootas.append({"name": "Bhakoot", "max": 7, "score": 0 if bk_bad else 7,
                   "girl": SIGNS[gs], "boy": SIGNS[bs], "about": "family welfare, finances, love",
                   "note": f"Moon signs are {pair[0]}/{pair[1]} from each other" + (" (Bhakoot dosha)" if bk_bad else "")})
    gnd, bnd = NADI_OF_NAK[gn], NADI_OF_NAK[bn]
    kootas.append({"name": "Nadi", "max": 8, "score": 0 if gnd == bnd else 8,
                   "girl": NADI_NAMES[gnd], "boy": NADI_NAMES[bnd], "about": "health and progeny"})
    total = sum(k["score"] for k in kootas)

    doshas = []
    if gnd == bnd:
        exc = []
        if gn == bn and g["pada"] != b["pada"]: exc.append("same nakshatra but different padas")
        if gs == bs and gn != bn: exc.append("same Moon sign but different nakshatras")
        if gl == bl: exc.append("Moon-sign lords are the same")
        doshas.append({"name": "Nadi Dosha", "exceptions": exc})
    if bk_bad:
        exc = []
        if gl == bl: exc.append("Moon-sign lords are the same")
        elif MAITRI_TABLE[MAITRI_ORDER.index(bl)][MAITRI_ORDER.index(gl)] >= 4: exc.append("Moon-sign lords are friends")
        doshas.append({"name": "Bhakoot Dosha", "exceptions": exc})
    gm = next(d for d in girl["doshas"] if d["name"].startswith("Manglik"))
    bm = next(d for d in boy["doshas"] if d["name"].startswith("Manglik"))
    gmp, bmp = gm["level"] in ("present", "mild (cancelled/reduced)"), bm["level"] in ("present", "mild (cancelled/reduced)")
    manglik = {"girl": gm["level"], "boy": bm["level"],
               "verdict": "both Manglik: dosha cancels out" if gmp and bmp else
               "neither is Manglik" if not (gmp or bmp) else
               "only one partner is Manglik: traditionally needs review or remedies"}
    if total < 18: verdict = "Below the traditional minimum of 18: not recommended without deeper chart analysis"
    elif total < 25: verdict = "Acceptable (18–24): average match"
    elif total < 33: verdict = "Good match (25–32)"
    else: verdict = "Excellent match (33–36)"
    return {"total": total, "max": 36, "verdict": verdict, "kootas": kootas, "doshas": doshas,
            "manglik": manglik,
            "girl_moon": {"sign": SIGNS[gs], "nakshatra": NAKSHATRAS[gn], "pada": g["pada"]},
            "boy_moon": {"sign": SIGNS[bs], "nakshatra": NAKSHATRAS[bn], "pada": b["pada"]}}
