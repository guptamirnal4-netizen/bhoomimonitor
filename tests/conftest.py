"""
conftest.py
------------
Shared test fixtures. No live Bhoomi requests anywhere in this suite
(Sec. 31) - real Preview pages are used as static HTML fixtures
(tests/fixtures/*.html), captured once during Phase 1-2 inspection of
the live portal, not regenerated per test run.

FakeCollection stands in for pymongo.collection.Collection so
checkpoint_store.py/persistence.py's idempotency logic (replace_one
upsert keyed on a natural key) can be tested without a running MongoDB.
"""

import os

import pytest


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


class FakeCursor(list):
    """A plain list that also supports the chainable pymongo.Cursor
    methods this project's aggregate modules actually use (.sort()/
    .skip()/.limit()) - lets FakeCollection.find() stand in for a real
    cursor without pulling in a Mongo-compatible test double library.

    NOTE: unlike real pymongo (where skip/limit are query modifiers
    applied at execution time regardless of call order), this mutates
    the list eagerly at each call - correct as long as callers chain
    them in the same "sort -> skip -> limit" order this codebase always
    uses, not a general-purpose reordering-safe emulation."""

    def sort(self, key, direction=1):
        self.sort_key = key
        super().sort(key=lambda d: d.get(key), reverse=(direction < 0))
        return self

    def skip(self, n):
        del self[:n]
        return self

    def limit(self, n):
        del self[n:]
        return self


class FakeCollection:
    """Minimal in-memory stand-in for the handful of pymongo.Collection
    methods this project's non-Mongo tests actually use. Enough to test
    upsert idempotency and query filters (including simple $gte/$lte/
    $ne/$in operators - aggregate.py's time-window queries) without a
    running MongoDB."""

    def __init__(self):
        self.docs: dict = {}  # id-ish key -> doc, insertion order preserved
        self._auto_id = 0

    def create_index(self, *a, **k):
        return "fake_index"

    @staticmethod
    def _value_matches(actual, expected):
        if isinstance(expected, dict):
            for op, val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= val):
                    return False
                if op == "$gt" and not (actual is not None and actual > val):
                    return False
                if op == "$lt" and not (actual is not None and actual < val):
                    return False
                if op == "$ne" and actual == val:
                    return False
                if op == "$in" and actual not in val:
                    return False
            return True
        return actual == expected

    def _match(self, doc, filt):
        return all(self._value_matches(doc.get(k), v) for k, v in filt.items())

    def find_one(self, filt):
        for doc in self.docs.values():
            if self._match(doc, filt):
                return dict(doc)
        return None

    def replace_one(self, filt, doc, upsert=False):
        for key, existing in self.docs.items():
            if self._match(existing, filt):
                self.docs[key] = dict(doc)
                return
        if upsert:
            key = doc.get("_id")
            if key is None:
                self._auto_id += 1
                key = f"auto-{self._auto_id}"
            self.docs[key] = dict(doc)

    def insert_one(self, doc):
        self._auto_id += 1
        self.docs[f"auto-{self._auto_id}"] = dict(doc)

    def count_documents(self, filt):
        return sum(1 for doc in self.docs.values() if self._match(doc, filt))

    def find(self, filt=None):
        filt = filt or {}
        return FakeCursor(dict(d) for d in self.docs.values() if self._match(d, filt))

    def distinct(self, field, filt=None):
        filt = filt or {}
        seen = []
        for d in self.docs.values():
            if self._match(d, filt) and field in d and d[field] not in seen:
                seen.append(d[field])
        return seen


@pytest.fixture
def fake_collection():
    return FakeCollection()
