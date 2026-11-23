"""
reconcile.py
-------------
Turns a flat list of per-MR loan events (from loan_parser.build_loan_event,
one land record's worth) into an ordered loan_history with
charge_creation <-> charge_release relationships resolved from the
priorMrRefs each event's Preview text actually contained - never by
matching on date proximity, name similarity, or any other inference the
source doesn't state (Sec. 11).

Verified live: MR 120/2015-2016 (acquisitionType "ಬಿಡುಗಡೆ") explicitly
contains "[MR 3760/2003-2004...]" in its Preview text, tying it back to
the MR 3760/2003-2004 charge_creation event from the same survey/hissa -
that's the only kind of evidence this module is allowed to act on.
"""

import re
from datetime import datetime

MR_REF_RE = re.compile(r"^MR\s*(\d+)\s*/\s*(\d{4}-\d{4})$")


def _sort_key(event: dict):
    """Chronological key: approvedDateRaw (DD/MM/YYYY) first, falling
    back to transactionYear + transactionNo so events without a parseable
    date still sort deterministically instead of raising."""
    raw = event.get("approvedDateRaw")
    if raw:
        try:
            return (0, datetime.strptime(raw, "%d/%m/%Y"))
        except ValueError:
            pass
    year = (event.get("transactionYear") or "0000-0000").split("-")[0]
    try:
        year_num = int(year)
    except ValueError:
        year_num = 0
    try:
        txn_num = int(event.get("transactionNo") or 0)
    except ValueError:
        txn_num = 0
    return (1, year_num, txn_num)


def reconcile(loan_events: list[dict]) -> list[dict]:
    """Returns a new list: sorted chronologically, sequence-numbered,
    with charge_release events carrying releasedMrNo/releasedTransaction
    Year/releasedChargeMatched, and charge_creation events carrying a
    releaseStatus computed from whether any release in this same list
    references them."""
    ordered = sorted(loan_events, key=_sort_key)

    creation_by_mr = {
        e["mrNo"]: e for e in ordered if e.get("instrumentClass") == "charge_creation"
    }
    referenced_mrs = set()

    out = []
    for i, event in enumerate(ordered, start=1):
        event = dict(event)  # don't mutate caller's dict
        event["sequence"] = i

        if event.get("instrumentClass") == "charge_release":
            prior_refs = event.get("priorMrRefs", [])
            local_matches = []
            for ref in prior_refs:
                m = MR_REF_RE.match(ref)
                if m and m.group(1) in creation_by_mr:
                    local_matches.append((m.group(1), m.group(2)))

            sv = dict(event.get("sourceVerification", {}))
            if len(local_matches) == 1:
                mr_no, year = local_matches[0]
                event["releasedMrNo"] = mr_no
                event["releasedTransactionYear"] = year
                event["releasedChargeMatched"] = True
                referenced_mrs.add(mr_no)
            elif len(local_matches) > 1:
                event["releasedMrNo"] = None
                event["releasedTransactionYear"] = None
                event["releasedChargeMatched"] = False
                sv["verificationRequired"] = True
                sv["reasonCode"] = "RELEASE_MATCH_AMBIGUOUS"
            elif len(prior_refs) == 1:
                # Not found among this record's own charge_creation
                # events (may belong to a different hissa/record outside
                # this crawl unit) - keep the reference as the best
                # available info but don't claim it's confirmed.
                m = MR_REF_RE.match(prior_refs[0])
                event["releasedMrNo"] = m.group(1) if m else None
                event["releasedTransactionYear"] = m.group(2) if m else None
                event["releasedChargeMatched"] = False
                if sv.get("reasonCode", "NONE") == "NONE":
                    sv["verificationRequired"] = True
                    sv["reasonCode"] = "CHARGE_RELEASE_NOT_MATCHED"
            else:
                event["releasedMrNo"] = None
                event["releasedTransactionYear"] = None
                event["releasedChargeMatched"] = False
                if sv.get("reasonCode", "NONE") == "NONE":
                    sv["verificationRequired"] = True
                    sv["reasonCode"] = (
                        "RELEASE_MATCH_AMBIGUOUS" if len(prior_refs) > 1
                        else "CHARGE_RELEASE_NOT_MATCHED"
                    )
            event["sourceVerification"] = sv

        out.append(event)

    for event in out:
        if event.get("instrumentClass") == "charge_creation":
            event["releaseStatus"] = (
                "released" if event["mrNo"] in referenced_mrs else "unreleased_as_of_crawl"
            )

    return out
