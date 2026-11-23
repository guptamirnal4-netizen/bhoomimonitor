"""
loan_parser.py
---------------
Turns one parsed Preview page (preview_fetcher.parse_preview_html output)
plus its mutation-grid metadata into a loan_history event, per the
zero-assumption policy (Sec. 7):

  - A raw token is only ever called a "bank" if bank_mappings.match_bank
    finds it verbatim in the known-alias table.
  - An amount is only ever extracted via a deterministic regex on
    digits/commas immediately followed by the portal's own "/-" rupee
    marker - never inferred from context.
  - When N amounts and M bank names appear in the same cell but the
    source doesn't put them in an unambiguous 1:1 relationship (same
    line/clause), NONE of the pairings are guessed - every amount is
    preserved as its own unresolved charge and the whole row is flagged
    verificationRequired with an explicit reasonCode.

Verified against 4 real Preview pages before this was written:
  - MR 3760/2003-2004 (Bagalkote/Mudhol/Lokapura/Lokapura 56/1): a single
    court-ordered charge ("ಸಿವ್ಹಿಲ್ ಜಡ್ಜ"/JMFC), amount 12,000/-, name
    and case details run together with NO delimiting whitespace on the
    second <br>-separated line - no recognized bank alias appears in it,
    so bank=None/bankRaw=None is the correct (not incomplete) result.
  - MR 55/2005-2006 and MR 47/2011-2012 (56/2, 56/3): the party/charge
    cell holds ONLY a bare amount ("40,000/-", "1,00,000/-") - no name,
    no lender text of any kind.
  - MR 120/2015-2016 (56/1, acquisitionType "ಬಿಡುಗಡೆ" / release): the
    cell explicitly references "[MR 3760/2003-2004...]" - the exact
    reconciliation link reconcile.py needs, with no amount of its own.
"""

import re

from bank_mappings import match_bank

AMOUNT_RE = re.compile(r"(\d[\d,\.]*)\s*/-")
MR_REF_RE = re.compile(r"MR\.?\s*(\d+)\s*/\s*(\d{4}-\d{4})")

RELEASE_ACQUISITION_MARKER = "ಬಿಡುಗಡೆ"          # "release"
MORTGAGE_ACQUISITION_MARKER = "ಆಧಾರ"            # "Aadhara" (mortgage/pledge) - substring of ಆಧಾರ/ಭೋಗ್ಯ
LOAN_MUTATION_TYPE = "ಹಕ್ಕು ಮತ್ತು ಋಣ"           # "Right and Debt"


def _to_amount_number(raw: str) -> float | None:
    """Deterministic parse of a portal amount string. Handles the two
    grouping styles actually observed: comma-grouped ("1,00,000") and a
    dot used as a thousands separator in one narrative fragment
    ("40.000/-" meaning 40,000, not forty). Returns None (never a guess)
    if the digits don't parse cleanly."""
    digits = raw.strip()
    digits = re.sub(r"\s*/-\s*$", "", digits)  # strip the portal's rupee marker before parsing
    # dot-as-thousands-separator: a single '.' followed by exactly 3
    # digits and nothing else after it.
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", digits):
        digits = digits.replace(".", "")
    else:
        digits = digits.replace(",", "")
    if not digits.isdigit():
        return None
    return float(digits)


def extract_amounts(text: str) -> list[str]:
    """Raw amount substrings (e.g. "1,00,000/-"), in the order found,
    duplicates preserved (a repeated identical amount can be two real
    charges)."""
    return [m.group(0) for m in AMOUNT_RE.finditer(text or "")]


def extract_mr_refs(text: str) -> list[str]:
    """Normalized "MR <no>/<year>" references found anywhere in text -
    used by reconcile.py to link a release back to its charge."""
    return [f"MR {no}/{year}" for no, year in MR_REF_RE.findall(text or "")]


def _clauses(text: str) -> list[str]:
    """Splits the raw cell text into candidate same-context clauses: one
    per <br>-separated line (Sec. 25's worked example puts each distinct
    "BANK amount," charge on its own line, with a trailing comma that's
    just punctuation). Deliberately does NOT further split on commas -
    Indian rupee amounts use commas as digit-grouping separators
    ("1,00,000/-"), so splitting on every comma would tear a single
    amount in half."""
    return [line.strip().rstrip(",").strip() for line in (text or "").split("\n") if line.strip()]


def _pair_within_clauses(text: str) -> tuple[list[dict], list[str], bool]:
    """Attempts confident same-clause bank<->amount pairing. Returns
    (paired_charges, leftover_amount_raws, any_ambiguity_seen). A
    "paired" charge only ever comes from a clause containing exactly one
    bank alias and exactly one amount together - anything short of that
    is left for the caller to handle as unresolved."""
    paired = []
    leftover_amounts = list(extract_amounts(text))
    ambiguous = False

    for clause in _clauses(text):
        clause_amounts = extract_amounts(clause)
        bank_raw, bank_norm, score = match_bank(clause)
        if len(clause_amounts) == 1 and bank_raw:
            amt_raw = clause_amounts[0]
            paired.append({
                "bank": bank_norm,
                "bankRaw": bank_raw,
                "amount": _to_amount_number(amt_raw),
                "amountRaw": amt_raw,
                "bankNameMatchScore": score,
            })
            if amt_raw in leftover_amounts:
                leftover_amounts.remove(amt_raw)
        elif len(clause_amounts) > 1 or (bank_raw and len(clause_amounts) == 0 and False):
            # more than one amount crammed into a single clause with a
            # bank name present is exactly the "which amount is whose"
            # ambiguity Sec. 26 describes - don't pair any of them.
            if bank_raw and len(clause_amounts) > 1:
                ambiguous = True

    return paired, leftover_amounts, ambiguous


def extract_charges_from_row(party_cell_raw: str, narrative_text: str) -> dict:
    """Core zero-assumption extraction for one encumbrance-table row.

    Returns:
      {
        "charges": [...],                 # confidently paired bank+amount charges
        "unresolvedAmounts": [...],       # amounts found with no safe bank pairing
        "candidateBankTokens": [...],     # bank aliases seen but not safely paired
        "priorMrRefs": [...],             # "MR <no>/<year>" refs found anywhere
        "verificationRequired": bool,
        "reasonCode": str,
      }
    """
    paired, leftover_amounts, ambiguous_in_cell = _pair_within_clauses(party_cell_raw)

    all_bank_hits = []
    bank_raw, _, _ = match_bank(party_cell_raw)
    if bank_raw:
        all_bank_hits.append(bank_raw)

    prior_refs = extract_mr_refs(party_cell_raw) + extract_mr_refs(narrative_text)
    prior_refs = list(dict.fromkeys(prior_refs))  # de-dup, preserve order

    charges = list(paired)
    verification_required = False
    reason_code = "NONE"

    if leftover_amounts:
        # Amounts we could not safely tie to a specific bank in-clause.
        unpaired_has_bank_nearby = bool(all_bank_hits) and not paired
        for amt_raw in leftover_amounts:
            charges.append({
                "bank": None,
                "bankRaw": None,
                "amount": _to_amount_number(amt_raw),
                "amountRaw": amt_raw,
                "bankNameMatchScore": None,
            })
        verification_required = True
        if ambiguous_in_cell or (unpaired_has_bank_nearby and len(leftover_amounts) > 1):
            reason_code = "COLUMN_ALIGNMENT_AMBIGUOUS"
        elif not all_bank_hits:
            reason_code = "LENDER_NOT_STATED_IN_RECORD"
        else:
            reason_code = "COLUMN_ALIGNMENT_AMBIGUOUS"
    elif len(charges) > 1:
        reason_code = "MULTIPLE_CHARGES_IN_LEDGER"  # informational: cleanly paired, just >1

    return {
        "charges": charges,
        "candidateBankTokens": all_bank_hits,
        "priorMrRefs": prior_refs,
        "verificationRequired": verification_required,
        "reasonCode": reason_code,
    }


def classify_instrument(mutation_type: str, acquisition_type: str, has_evidence: bool) -> str:
    """Preview evidence takes precedence over the label (Sec. 10) - the
    label only decides which way to lean when evidence is absent."""
    if RELEASE_ACQUISITION_MARKER in (acquisition_type or ""):
        return "charge_release"
    if MORTGAGE_ACQUISITION_MARKER in (acquisition_type or "") and LOAN_MUTATION_TYPE in (mutation_type or ""):
        return "charge_creation" if has_evidence else "unknown"
    if has_evidence:
        # Sec. 10/14: other acquisition types can still carry charge
        # info - don't gate purely on the label.
        return "charge_creation"
    return "unknown"


def build_loan_event(mutation_row: dict, preview_data: dict) -> dict | None:
    """Combines one mutation-grid row with its fetched Preview data into
    a loan_history-shaped event (sequence/release-matching added later
    by reconcile.py). Returns None if this mutation shows no charge
    evidence at all AND its label gives no reason to expect any - i.e.
    we don't fabricate an empty loan_history entry for e.g. a partition
    (ಫೋಡಿ) mutation that turned out to have nothing to do with a loan."""
    charge_rows = preview_data.get("chargeRows", [])
    narrative = preview_data.get("narrativeText", "")

    combined_raw_texts = []
    all_charges, all_unresolved_tokens, all_prior_refs = [], [], []
    verification_required, reason_code = False, "NONE"

    for row in charge_rows:
        result = extract_charges_from_row(row["partyCellRaw"], narrative)
        combined_raw_texts.append(row["partyCellRaw"])
        all_charges.extend(result["charges"])
        all_unresolved_tokens.extend(result["candidateBankTokens"])
        all_prior_refs.extend(result["priorMrRefs"])
        if result["verificationRequired"]:
            verification_required = True
        if result["reasonCode"] != "NONE":
            # Propagate even when verificationRequired is False -
            # MULTIPLE_CHARGES_IN_LEDGER is informational (a cleanly
            # paired multi-charge row), not a verification blocker.
            reason_code = result["reasonCode"]

    has_evidence = bool(all_charges) or bool(all_prior_refs)
    mutation_type = mutation_row.get("mutationType", "")
    acquisition_type = mutation_row.get("acquisitionType", "")
    instrument_class = classify_instrument(mutation_type, acquisition_type, has_evidence)

    if instrument_class == "unknown" and not has_evidence:
        if LOAN_MUTATION_TYPE in mutation_type:
            # Type strongly suggested a loan/charge event but Preview
            # gave us nothing extractable - surface that explicitly
            # rather than silently dropping it (Sec. 13: don't hide
            # uncertainty). Must be checked BEFORE the no-evidence
            # early-return below, or this branch is unreachable.
            verification_required = True
            reason_code = "SOURCE_PARSE_FAILED" if reason_code == "NONE" else reason_code
        else:
            return None  # nothing to report - not a fabricated empty entry

    if instrument_class == "charge_release" and not all_prior_refs:
        verification_required = True
        reason_code = "CHARGE_RELEASE_NOT_MATCHED" if reason_code == "NONE" else reason_code

    return {
        "mrNo": mutation_row.get("mrNo"),
        "transactionYear": mutation_row.get("transactionYear"),
        "transactionNo": mutation_row.get("transactionNo"),
        "approvedDateRaw": mutation_row.get("approvedDate"),
        "acquisitionType": acquisition_type,
        "mutationType": mutation_type,
        "instrumentClass": instrument_class,
        "charges": all_charges,
        "priorMrRefs": all_prior_refs,
        "sourceVerification": {
            "rawSourceText": "\n---\n".join(combined_raw_texts),
            "narrativeText": narrative,
            "candidateBankTokensRaw": list(dict.fromkeys(all_unresolved_tokens)),
            "verificationRequired": verification_required,
            "reasonCode": reason_code,
            "partyCellRaw": combined_raw_texts[0] if combined_raw_texts else "",
            "areaCell": charge_rows[0]["areaCell"] if charge_rows else "",
            "sourceUrl": preview_data.get("sourceUrl"),
        },
    }
