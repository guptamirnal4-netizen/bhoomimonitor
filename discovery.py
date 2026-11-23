"""
discovery.py
-------------
State-wide district -> taluk -> hobli -> village enumeration, built on
top of mutation_crawler's cascade primitives. This is what a full
Karnataka crawl (Sec. 16) walks to generate the work list; the known
BAGALKOTE/MUDHOL/LOKAPURA/LOKAPURA test case is just one leaf of this
tree, never hardcoded as the only path.
"""

import logging

import mutation_crawler as mc
from bhoomi_session import BhoomiSession

log = logging.getLogger("bhoomi")


def list_districts(session: BhoomiSession):
    """Returns [(value, name), ...] for every district, plus the soup
    state needed to continue the cascade (a fresh page load)."""
    soup = mc.start(session)
    return mc.list_options(soup, "district"), soup


def list_taluks(session: BhoomiSession, soup, district_value: str):
    soup = mc.select_district(session, soup, district_value)
    return mc.list_options(soup, "taluk"), soup


def list_hoblis(session: BhoomiSession, soup, district_value: str, taluk_value: str):
    soup = mc.select_taluk(session, soup, district_value, taluk_value)
    return mc.list_options(soup, "hobli"), soup


def list_villages(session: BhoomiSession, soup, district_value: str, taluk_value: str, hobli_value: str):
    soup = mc.select_hobli(session, soup, district_value, taluk_value, hobli_value)
    return mc.list_options(soup, "village"), soup


def enumerate_all(session: BhoomiSession):
    """Generator yielding every (district_val, district_name, taluk_val,
    taluk_name, hobli_val, hobli_name, village_val, village_name) leaf,
    one fresh page load per district to avoid dragging a single session's
    ViewState across the entire state (keeps each branch independently
    resumable/retryable). Intended for building a work list, not for
    driving the crawl itself - callers checkpoint after each leaf they
    actually process, not after enumeration."""
    districts, _ = list_districts(session)
    for d_val, d_name in districts:
        soup = mc.start(session)
        taluks, soup = list_taluks(session, soup, d_val)
        for t_val, t_name in taluks:
            hoblis, hobli_soup = list_hoblis(session, soup, d_val, t_val)
            for h_val, h_name in hoblis:
                villages, _ = list_villages(session, hobli_soup, d_val, t_val, h_val)
                for v_val, v_name in villages:
                    yield {
                        "district_value": d_val, "district": d_name,
                        "taluk_value": t_val, "taluk": t_name,
                        "hobli_value": h_val, "hobli": h_name,
                        "village_value": v_val, "village": v_name,
                    }
