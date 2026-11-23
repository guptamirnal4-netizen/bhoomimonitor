"""
district_registry.py
-----------------------
Maps schema.py's canonical 31-district vocabulary to the live Service2/
RTC form's district dropdown text, confirmed live (32 entries - see
below). This is the single place that mapping is decided, so
district_check.py never has to guess.

Confirmed live (fresh fetch of the district dropdown):
    32 entries, e.g. {'2': 'BAGALKOTE', '12': 'BALLARI', ...,
    '29': 'BENGALURU SOUTH', '29': 'RAMANAGARA', ...}

Two real discrepancies with schema.py's 31, neither invented, both
flagged explicitly rather than silently resolved:

1. Spelling: several districts use an older/alternate spelling on the
   portal than schema.py's canonical name (e.g. "MYSORE" vs "Mysuru",
   "YADAGIR" vs "Yadgir", "UTTAR KANNADA" vs "Uttara Kannada",
   "CHIKKABALLAPUR" vs "Chikkaballapura", "BANGALORE RURAL" vs
   "Bengaluru Rural"). These are confident, unambiguous 1:1 mappings -
   just different spellings of the same place.

2. Genuine ambiguity, NOT resolved by guessing:
   - schema.py has "Bengaluru Urban"; the portal has a plain "BENGALURU"
     AND a separate "BENGALURU SOUTH" entry with no schema.py
     counterpart. Mapped to "BENGALURU" as the best-available single
     match (LOW_CONFIDENCE=True) - "BENGALURU SOUTH" is NOT tracked as
     a 32nd district, since every other part of this system (dashboard,
     schema.py, aggregation) is built around the canonical 31.
   - The portal's own dropdown has a bug: "BENGALURU SOUTH" and
     "RAMANAGARA" share the SAME <option value="29">. Selecting either
     by its option value would be ambiguous; this registry selects by
     matching the option TEXT (not value), which sidesteps the
     collision for submission purposes, but if the portal's own
     server-side logic keys off that shared value internally, the two
     could theoretically resolve to the same underlying district. Flagged
     here, not silently assumed to be fine.

Districts monitored: schema.py's 31, always - never more, never fewer,
so every downstream aggregation/report stays consistent with the rest
of the project.
"""

from schema import DISTRICTS

# canonical (schema.py) -> live Service2 dropdown text (case-insensitive
# match against list_options() is fine; stored here as observed).
DISTRICT_PORTAL_TEXT = {
    "Bagalkote": "BAGALKOTE",
    "Ballari": "BALLARI",
    "Belagavi": "BELAGAVI",
    "Bengaluru Rural": "BANGALORE RURAL",
    "Bengaluru Urban": "BENGALURU",  # see LOW_CONFIDENCE_MAPPINGS below
    "Bidar": "BIDAR",
    "Chamarajanagara": "CHAMARAJANAGARA",
    "Chikkaballapura": "CHIKKABALLAPUR",
    "Chikkamagaluru": "CHIKKAMAGALURU",
    "Chitradurga": "CHITRADURGA",
    "Dakshina Kannada": "DAKSHINA KANNADA",
    "Davanagere": "DAVANAGERE",
    "Dharwad": "DHARWAD",
    "Gadag": "GADAG",
    "Hassan": "HASSAN",
    "Haveri": "HAVERI",
    "Kalaburagi": "KALABURAGI",
    "Kodagu": "KODAGU",
    "Kolar": "KOLAR",
    "Koppal": "KOPPAL",
    "Mandya": "MANDYA",
    "Mysuru": "MYSORE",
    "Raichur": "RAICHUR",
    "Ramanagara": "RAMANAGARA",  # shares dropdown value with "BENGALURU SOUTH" - see docstring
    "Shivamogga": "SHIVAMOGGA",
    "Tumakuru": "TUMAKURU",
    "Udupi": "UDUPI",
    "Uttara Kannada": "UTTAR KANNADA",
    "Vijayapura": "VIJAYAPURA",
    "Yadgir": "YADAGIR",
    "Vijayanagara": "VIJAYANAGARA",
}

# Mappings that are best-effort, not a confirmed 1:1 correspondence -
# surfaced in reports/UI as worth a human's periodic sanity check,
# rather than silently trusted forever.
LOW_CONFIDENCE_MAPPINGS = {
    "Bengaluru Urban": (
        "Portal has both a plain 'BENGALURU' and a separate 'BENGALURU SOUTH' "
        "entry with no schema.py counterpart; mapped to 'BENGALURU' as the "
        "best-available single match, not a confirmed exact correspondence."
    ),
    "Ramanagara": (
        "Portal's own dropdown has 'BENGALURU SOUTH' and 'RAMANAGARA' sharing "
        "the same underlying option value (a live portal data bug, not ours) - "
        "selection is done by matching option TEXT, which avoids the immediate "
        "collision, but if the portal's server-side logic keys off the shared "
        "value internally the two could still resolve to the same district."
    ),
}

assert set(DISTRICT_PORTAL_TEXT.keys()) == set(DISTRICTS), (
    "district_registry.py's mapping must cover exactly schema.py's DISTRICTS - "
    "no more, no fewer, so every downstream report stays consistent."
)


def portal_text(canonical_district: str) -> str:
    """The live Service2 dropdown text for a canonical schema.py district
    name. Raises KeyError for anything not in schema.py's DISTRICTS -
    fail loudly rather than silently monitor an unrecognized name."""
    return DISTRICT_PORTAL_TEXT[canonical_district]
