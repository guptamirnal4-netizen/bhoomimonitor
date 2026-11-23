"""district_registry.py - the schema.py <-> live-portal district name mapping."""

import pytest

import district_registry
from schema import DISTRICTS


def test_mapping_covers_exactly_the_canonical_31_districts():
    assert set(district_registry.DISTRICT_PORTAL_TEXT.keys()) == set(DISTRICTS)
    assert len(district_registry.DISTRICT_PORTAL_TEXT) == 31


def test_portal_text_known_district():
    assert district_registry.portal_text("Bagalkote") == "BAGALKOTE"
    assert district_registry.portal_text("Mysuru") == "MYSORE"  # real spelling divergence, not a typo


def test_portal_text_unknown_district_raises():
    with pytest.raises(KeyError):
        district_registry.portal_text("Not A Real District")


def test_ambiguous_mappings_are_explicitly_flagged_not_silently_assumed():
    assert "Bengaluru Urban" in district_registry.LOW_CONFIDENCE_MAPPINGS
    assert "Ramanagara" in district_registry.LOW_CONFIDENCE_MAPPINGS
