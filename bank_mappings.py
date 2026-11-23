"""
bank_mappings.py
-----------------
Explicit, auditable raw-text -> normalized-bank-name table, plus the
matcher that scans a Preview cell's raw text for a KNOWN lender token.

This is the ONLY place bank names get invented from abbreviations. The
policy (per the extraction brief, "zero assumption"):

  - A raw token is only ever recognized as a bank/lender if it appears,
    verbatim (case-insensitive), in KNOWN_BANK_ALIASES below.
  - Every entry here must be traceable to either (a) an explicit mapping
    given in the extraction brief/spec, or (b) a full, unambiguous
    institution name already spelled out in a Bhoomi source record (not
    a guessed expansion of an abbreviation nobody confirmed).
  - Anything not in this table stays bank=None, bankRaw=None. Do NOT add
    an alias here because it "looks like" a bank code (e.g. a raw
    4-digit society code like "1478") - that is exactly the invented
    category #29/E ("reasonable assumption") the brief forbids. Only add
    an alias once a specific Bhoomi record has been observed that
    unambiguously spells out what it stands for.

Growing this table: add the raw form seen in Bhoomi source text as a key,
and the confirmed full name as the value. Keep the source note in a
comment next to each entry.
"""

# raw-source alias (as it appears verbatim in Bhoomi Preview text, or a
# normalized-case form of it) -> canonical/normalized name.
KNOWN_BANK_ALIASES = {
    # From the extraction brief's own worked example (Sec. 4/8/24/25).
    "PKPS": "Pra-Krupa Sahakari Bank (PKPS)",

    # From the extraction brief's multiple-charge example (Sec. 25) - the
    # full name was spelled out in that example's raw text itself.
    "KARNATAKA VIKAS GRAMEENA BANK": "Karnataka Vikas Grameena Bank",
    "KVGB": "Karnataka Vikas Grameena Bank",

    # Universally unambiguous full/short names of nationalised/major banks
    # that, when they appear spelled out (not as a bare numeric/alpha
    # society code), can be pass-through normalized with no guesswork.
    "STATE BANK OF INDIA": "State Bank of India",
    "SBI": "State Bank of India",
    "CANARA BANK": "Canara Bank",
    "SYNDICATE BANK": "Syndicate Bank",
    "CORPORATION BANK": "Corporation Bank",
    "VIJAYA BANK": "Vijaya Bank",
    "UNION BANK OF INDIA": "Union Bank of India",
}


def match_bank(raw_text: str):
    """Scan `raw_text` for a known alias.

    Returns (bank_raw, bank_normalized, score) where:
      - bank_raw is the EXACT substring matched from raw_text (original
        casing/script preserved, never altered)
      - bank_normalized is the mapped canonical name, or None if the
        matched alias has no better normalized form than itself
      - score is 1.0 for an exact known-alias match, else None

    Returns (None, None, None) if no known alias appears anywhere in
    raw_text. This function NEVER invents a match - absence of a hit is
    the correct, honest result for unrecognized lender text (e.g. a bare
    numeric society code).
    """
    if not raw_text:
        return None, None, None

    upper = raw_text.upper()
    best_alias = None
    for alias in KNOWN_BANK_ALIASES:
        if alias in upper:
            # Prefer the longest matching alias (e.g. "KVGB" vs the full
            # "KARNATAKA VIKAS GRAMEENA BANK" phrase both matching -
            # longest match is the more specific/confident one).
            if best_alias is None or len(alias) > len(best_alias):
                best_alias = alias

    if best_alias is None:
        return None, None, None

    # Recover the exact original-cased substring for bank_raw.
    idx = upper.find(best_alias)
    bank_raw = raw_text[idx: idx + len(best_alias)]
    return bank_raw, KNOWN_BANK_ALIASES[best_alias], 1.0
