"""
Charge creation <-> release matching (Sec. 11), evidence-only - a release
is only ever tied to a specific prior charge when the source text itself
names it.
"""

import reconcile


def _creation(mr_no, year, txn_no, approved_date, amount=100000.0):
    return {
        "mrNo": mr_no, "transactionYear": year, "transactionNo": txn_no,
        "approvedDateRaw": approved_date, "mutationType": "ಹಕ್ಕು ಮತ್ತು ಋಣ",
        "acquisitionType": "ಆಧಾರ/ಭೋಗ್ಯ", "instrumentClass": "charge_creation",
        "charges": [{"bank": "Pra-Krupa Sahakari Bank (PKPS)", "bankRaw": "PKPS",
                     "amount": amount, "amountRaw": f"{amount:,.0f}/-", "bankNameMatchScore": 1.0}],
        "priorMrRefs": [],
        "sourceVerification": {"verificationRequired": False, "reasonCode": "NONE",
                                "rawSourceText": "PKPS", "sourceUrl": "https://example/creation"},
    }


def _release(mr_no, year, txn_no, approved_date, prior_refs):
    return {
        "mrNo": mr_no, "transactionYear": year, "transactionNo": txn_no,
        "approvedDateRaw": approved_date, "mutationType": "ಹಕ್ಕು ಮತ್ತು ಋಣ",
        "acquisitionType": "ಬಿಡುಗಡೆ", "instrumentClass": "charge_release",
        "charges": [], "priorMrRefs": prior_refs,
        "sourceVerification": {"verificationRequired": False, "reasonCode": "NONE",
                                "rawSourceText": " ".join(prior_refs), "sourceUrl": "https://example/release"},
    }


# ---------- Scenario 6: creation followed by a matched release ----------

def test_creation_followed_by_matched_release():
    events = [
        _creation("79", "2013-2014", "119", "27/12/2013"),
        _release("100", "2018-2019", "83", "17/10/2020", ["MR 79/2013-2014"]),
    ]
    out = reconcile.reconcile(events)
    assert [e["sequence"] for e in out] == [1, 2]

    creation = next(e for e in out if e["mrNo"] == "79")
    release = next(e for e in out if e["mrNo"] == "100")

    assert release["releasedMrNo"] == "79"
    assert release["releasedTransactionYear"] == "2013-2014"
    assert release["releasedChargeMatched"] is True
    assert creation["releaseStatus"] == "released"


def test_unreleased_creation_is_not_flagged_as_an_error():
    # a charge with no release anywhere yet is normal (a mortgage may
    # simply still be open) - it must not be marked verificationRequired
    # just for being unreleased.
    events = [_creation("79", "2013-2014", "119", "27/12/2013")]
    out = reconcile.reconcile(events)
    assert out[0]["releaseStatus"] == "unreleased_as_of_crawl"
    assert out[0]["sourceVerification"]["verificationRequired"] is False


# ---------- Scenario 11 (reconciliation variant): release ref not found locally ----------

def test_release_referencing_an_mr_outside_this_record_is_not_falsely_confirmed():
    # the referenced MR (200/2009-2010) never appears in this record's
    # own charge_creation events - can't confirm it, must not fabricate confirmation.
    events = [_release("55", "2015-2016", "10", "01/01/2016", ["MR 200/2009-2010"])]
    out = reconcile.reconcile(events)
    release = out[0]
    assert release["releasedMrNo"] == "200"          # best-available info from the reference itself
    assert release["releasedTransactionYear"] == "2009-2010"
    assert release["releasedChargeMatched"] is False  # NOT independently confirmed
    assert release["sourceVerification"]["reasonCode"] == "CHARGE_RELEASE_NOT_MATCHED"
    assert release["sourceVerification"]["verificationRequired"] is True


def test_release_with_multiple_local_matches_is_ambiguous_not_guessed():
    events = [
        _creation("82", "2008-2009", "40", "10/05/2008"),
        _creation("100", "2009-2010", "12", "20/06/2009"),
        _release("120", "2015-2016", "9", "01/01/2016", ["MR 82/2008-2009", "MR 100/2009-2010"]),
    ]
    out = reconcile.reconcile(events)
    release = next(e for e in out if e["mrNo"] == "120")
    assert release["releasedMrNo"] is None, "must not guess which of the two matching MRs is the real target"
    assert release["releasedChargeMatched"] is False
    assert release["sourceVerification"]["reasonCode"] == "RELEASE_MATCH_AMBIGUOUS"


def test_chronological_ordering_from_approved_date():
    events = [
        _creation("100", "2018-2019", "5", "10/05/2019"),
        _creation("79", "2013-2014", "119", "27/12/2013"),
    ]
    out = reconcile.reconcile(events)
    assert [e["mrNo"] for e in out] == ["79", "100"]
    assert [e["sequence"] for e in out] == [1, 2]
