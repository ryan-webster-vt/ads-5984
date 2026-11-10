# Offline test module: The checked-in golden team fixture matches the configured index.
from __future__ import annotations

import json

import config


# Verifies: team golden fixture is consistent.
def test_team_golden_fixture_is_consistent():
    payload = json.loads((config.FIXTURES_DIR / "team_golden.json").read_text())
    assert payload["opportunity_id"] == "330296"
    assert payload["index_version"] == config.INDEX_VERSION
    assert len(payload["baseline_team_ids"]) == payload["team_size"]
    assert len(set(payload["baseline_team_ids"])) == payload["team_size"]
    assert payload["baseline_pi_id"] in payload["baseline_team_ids"]
    assert all(isinstance(value, str) for value in payload["baseline_team_ids"])
