import re
from typing import Dict, List, Set

KEYWORDS = [
    ("baby", ["baby", "krabbel", "still", "rückbildung"]),
    ("kleinkind", ["kleinkind", "spielgruppe", "kita", "spiel-"]),
    ("familie", ["familie", "familien", "eltern-kind", "mami", "papi"]),
    ("eltern", ["eltern", "austausch", "beratung", "infoabend", "referat"]),
    ("kreativ", ["bastel", "atelier", "malen", "werken"]),
    ("bewegung", ["turnen", "bewegung", "yoga", "tanzen", "sport"]),
    ("outdoor", ["wald", "draussen", "spielplatz", "outdoor", "spaziergang"]),
    ("anmeldung", ["anmeldung", "reservation", "anmelden", "plätze begrenzt", "platz begrenzt"]),
    ("gratis", ["gratis", "kostenlos", "kostenfrei"]),
    ("kosten", ["chf", "fr.", "franken", "kostenbeitrag", "eintritt"]),
]

def normalize_text(*parts: str) -> str:
    s = " ".join([p for p in parts if p]).lower()
    s = re.sub(r"\s+", " ", s)
    return s

def infer_tags(title: str, description: str = "") -> List[str]:
    hay = normalize_text(title, description)
    tags: Set[str] = set()
    for tag, kws in KEYWORDS:
        if any(kw in hay for kw in kws):
            tags.add(tag)

    # Wenn "kosten" und "gratis" beides auftaucht, lass beides stehen.
    return sorted(tags)

def infer_flags(title: str, description: str = "") -> Dict[str, bool]:
    hay = normalize_text(title, description)
    return {
        "anmeldung": any(k in hay for k in ["anmeldung", "reservation", "anmelden", "plätze begrenzt", "platz begrenzt"]),
        "gratis": any(k in hay for k in ["gratis", "kostenlos", "kostenfrei"]),
    }