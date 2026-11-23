"""
Zero-assumption charge extraction, tested first against REAL Preview
fixtures captured from the live portal (Sec. 31: use fixtures from
existing scraped data wherever possible), then against synthetic
fixtures built from the extraction brief's own worked examples for
shapes the live test case didn't happen to contain (clean multi-bank
pairing, ambiguous column alignment, an unrecognized Kannada-script
lender token).
"""

import loan_parser
import preview_fetcher
from conftest import load_fixture

MR3760_ROW = {
    "mrNo": "3760", "transactionYear": "2003-2004", "transactionNo": "25023",
    "approvedDate": "23/02/2004", "mutationType": "ಹಕ್ಕು ಮತ್ತು ಋಣ", "acquisitionType": "ಆಧಾರ/ಭೋಗ್ಯ",
}
MR55_ROW = {
    "mrNo": "55", "transactionYear": "2005-2006", "transactionNo": "59",
    "approvedDate": "22/10/2005", "mutationType": "ಹಕ್ಕು ಮತ್ತು ಋಣ", "acquisitionType": "ಆಧಾರ/ಭೋಗ್ಯ",
}
MR47_ROW = {
    "mrNo": "47", "transactionYear": "2011-2012", "transactionNo": "51",
    "approvedDate": "19/11/2011", "mutationType": "ಹಕ್ಕು ಮತ್ತು ಋಣ", "acquisitionType": "ಆಧಾರ/ಭೋಗ್ಯ",
}
MR120_ROW = {
    "mrNo": "120", "transactionYear": "2015-2016", "transactionNo": "169",
    "approvedDate": "29/02/2016", "mutationType": "ಹಕ್ಕು ಮತ್ತು ಋಣ", "acquisitionType": "ಬಿಡುಗಡೆ",
}


def _build(fixture_name, row):
    html = load_fixture(fixture_name)
    preview_data = preview_fetcher.parse_preview_html(html, source_url=f"https://example/{fixture_name}")
    return loan_parser.build_loan_event(row, preview_data)


# ---------- Scenario 1/4: a real charge with an amount but no recognizable lender ----------
# (a court-ordered attachment, not a bank - the correct result is bank=None, not a guess)

def test_mr3760_court_order_charge_amount_extracted_bank_correctly_unresolved():
    event = _build("preview_mr3760_charge_creation_court_order.html", MR3760_ROW)
    assert event["instrumentClass"] == "charge_creation"
    assert len(event["charges"]) == 1
    charge = event["charges"][0]
    assert charge["amount"] == 12000.0
    assert charge["amountRaw"] == "12,000/-"
    assert charge["bank"] is None
    assert charge["bankRaw"] is None
    assert event["sourceVerification"]["verificationRequired"] is True
    assert event["sourceVerification"]["reasonCode"] == "LENDER_NOT_STATED_IN_RECORD"
    # raw source text must be preserved verbatim, never translated away
    assert "ಗುರುರಾಜ" in event["sourceVerification"]["rawSourceText"]


# ---------- Scenario 7: missing bank (amount-only cell, real fixtures) ----------

def test_mr55_amount_only_no_lender_text_at_all():
    event = _build("preview_mr55_amount_only_no_lender.html", MR55_ROW)
    assert event["charges"] == [{
        "bank": None, "bankRaw": None, "amount": 40000.0,
        "amountRaw": "40,000/-", "bankNameMatchScore": None,
    }]
    assert event["sourceVerification"]["reasonCode"] == "LENDER_NOT_STATED_IN_RECORD"


def test_mr47_amount_only_no_lender_text_at_all():
    event = _build("preview_mr47_amount_only_no_lender.html", MR47_ROW)
    assert event["charges"][0]["amount"] == 100000.0
    assert event["charges"][0]["bank"] is None


# ---------- Scenario 5: loan release, real fixture referencing a prior MR ----------

def test_mr120_release_references_prior_mr_no_amount_of_its_own():
    event = _build("preview_mr120_charge_release.html", MR120_ROW)
    assert event["instrumentClass"] == "charge_release"
    assert event["charges"] == []
    assert event["priorMrRefs"] == ["MR 3760/2003-2004"]
    # a clean, unambiguous single reference needs no verification flag
    assert event["sourceVerification"]["verificationRequired"] is False
    assert event["sourceVerification"]["reasonCode"] == "NONE"


# ---------- Scenario 2/3: multiple banks, multiple amounts, cleanly paired (Sec. 25) ----------

def _synthetic_preview(party_cell_html: str, header="ಕೋರ್ಟ್ ತಡೆಗೆ ಒಳಗಾದ ವಹಿವಾಟುದಾರರು"):
    html = f"""
    <html><body>
    <table><tr><td>ಜಿಲ್ಲೆ :</td><td>ಬಾಗಲಕೋಟ</td></tr></table>
    <table>
      <tr><td colspan="5">(ವಿಸ್ತೀರ್ಣ ಎಕರೆ ಮತ್ತು ಗುಂಟೆಗಳಲ್ಲಿ)</td></tr>
      <tr><td>ಬಾದಿತವಾದ ಸರ್ವೆ ಮತ್ತು ಹಿಸ್ಸಾ ನಂ.</td><td>{header}</td><td>ವಿಸ್ತೀರ್ಣ</td><td></td><td>ಷರಾ</td></tr>
      <tr><td>76/*/1</td><td>{party_cell_html}</td><td>0.00.00.00</td><td></td><td></td></tr>
    </table>
    </body></html>
    """
    return preview_fetcher.parse_preview_html(html, source_url="https://example/synthetic")


def test_multiple_banks_multiple_amounts_cleanly_paired_not_collapsed():
    # Sec. 25's own worked example text.
    party_cell = "PKPS 40,000/-,<br/>PKPS 60,000/-,<br/>Karnataka Vikas Grameena Bank 3,06,000/-"
    preview_data = _synthetic_preview(party_cell)
    row = {**MR3760_ROW, "mrNo": "100", "transactionYear": "2018-2019"}
    event = loan_parser.build_loan_event(row, preview_data)

    assert len(event["charges"]) == 3, "every distinct charge must be preserved, never collapsed into one"
    amounts = sorted(c["amount"] for c in event["charges"])
    assert amounts == [40000.0, 60000.0, 306000.0]
    assert all(c["bank"] is not None for c in event["charges"]), "every charge should be cleanly paired here"
    banks = sorted(c["bank"] for c in event["charges"])
    assert banks == ["Karnataka Vikas Grameena Bank", "Pra-Krupa Sahakari Bank (PKPS)", "Pra-Krupa Sahakari Bank (PKPS)"]
    for c in event["charges"]:
        assert c["bankNameMatchScore"] == 1.0
    # cleanly paired -> informational only, not a verification blocker
    assert event["sourceVerification"]["reasonCode"] == "MULTIPLE_CHARGES_IN_LEDGER"
    assert event["sourceVerification"]["verificationRequired"] is False


# ---------- Scenario 10/11: multiple MR refs + ambiguous column alignment (Sec. 26) ----------

def test_ambiguous_column_alignment_never_invents_pairing():
    # Sec. 26's own worked example: MR refs + a bank + an unrecognized
    # numeric code + three bare amounts with no clear per-amount pairing.
    party_cell = (
        "MR 162/2007-2008<br/>MR 85/2007-2008<br/>1478<br/>PKPS<br/>"
        "80,000/-<br/>6,70,000/-<br/>1,00,000/-"
    )
    preview_data = _synthetic_preview(party_cell)
    row = {**MR3760_ROW, "mrNo": "162", "transactionYear": "2007-2008"}
    event = loan_parser.build_loan_event(row, preview_data)

    assert event["priorMrRefs"] == ["MR 162/2007-2008", "MR 85/2007-2008"]
    assert len(event["charges"]) == 3, "all three amounts must be preserved, none silently dropped"
    for c in event["charges"]:
        assert c["bank"] is None, "must NOT guess which amount belongs to PKPS"
        assert c["bankRaw"] is None
    amounts = sorted(c["amount"] for c in event["charges"])
    assert amounts == [80000.0, 100000.0, 670000.0]
    assert event["sourceVerification"]["verificationRequired"] is True
    assert event["sourceVerification"]["reasonCode"] == "COLUMN_ALIGNMENT_AMBIGUOUS"
    # PKPS was seen in the cell - surfaced for a human reviewer, but never asserted as a match
    assert "PKPS" in event["sourceVerification"]["candidateBankTokensRaw"]


# ---------- Scenario 9: an unrecognized Kannada-script lender token must not be guessed ----------

def test_unrecognized_kannada_lender_token_stays_unresolved():
    # "ಸಹಕಾರ ಸಂಘ" (cooperative society) with no confirmed mapping in
    # bank_mappings.py - must NOT be invented as a normalized bank name.
    party_cell = "ರಾಮಪ್ಪ ಗೌಡ<br/>ಸಹಕಾರ ಸಂಘ 25,000/-"
    preview_data = _synthetic_preview(party_cell)
    event = loan_parser.build_loan_event(MR3760_ROW, preview_data)

    assert len(event["charges"]) == 1
    assert event["charges"][0]["amount"] == 25000.0
    assert event["charges"][0]["bank"] is None
    assert event["charges"][0]["bankRaw"] is None
    assert event["sourceVerification"]["reasonCode"] == "LENDER_NOT_STATED_IN_RECORD"


# ---------- Scenario 8: missing amount entirely (name only, no charge evidence) ----------

def test_missing_amount_entirely_no_charge_fabricated():
    party_cell = "ಸೋಮಶೇಖರ ಪಾಟೀಲ"  # a name with no amount/lender/MR-ref anywhere
    preview_data = _synthetic_preview(party_cell)
    row = {**MR3760_ROW, "mutationType": "ಫೋಡಿ", "acquisitionType": "ವಿಭಜನೆ"}  # an unrelated mutation type
    event = loan_parser.build_loan_event(row, preview_data)
    assert event is None, "no evidence + unrelated type must not fabricate a loan_history entry"


def test_missing_amount_but_loan_shaped_type_flags_instead_of_silently_dropping():
    party_cell = "ಸೋಮಶೇಖರ ಪಾಟೀಲ"
    preview_data = _synthetic_preview(party_cell)
    event = loan_parser.build_loan_event(MR3760_ROW, preview_data)  # mutationType IS ಹಕ್ಕು ಮತ್ತು ಋಣ
    assert event is not None, "a loan-shaped mutation type with zero evidence must be surfaced, not dropped"
    assert event["instrumentClass"] == "unknown"
    assert event["sourceVerification"]["verificationRequired"] is True
    assert event["sourceVerification"]["reasonCode"] == "SOURCE_PARSE_FAILED"
