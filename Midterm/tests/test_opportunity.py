# Offline test module: Opportunity normalization and date/amount parsing.
from __future__ import annotations

from src.grants.client import load_opportunity_fixture
from src.grants.normalize import parse_amount, strip_html


# Verifies: real grants fixture normalizes.
def test_real_grants_fixture_normalizes():
    opportunity = load_opportunity_fixture()
    assert opportunity.opportunity_id == "330296"
    assert opportunity.title == "Long Term Research in Environmental Biology"
    assert opportunity.agency == "U.S. National Science Foundation"
    assert opportunity.status == "posted"
    assert opportunity.award_ceiling == 600000.0
    assert opportunity.award_floor is None
    assert "<p>" not in opportunity.description
    assert "evolutionary biology" in opportunity.description.lower()


# Verifies: amount and HTML helpers.
def test_amount_and_html_helpers():
    assert parse_amount("none") is None
    assert parse_amount("1,250,000") == 1250000.0
    assert parse_amount(500) == 500.0
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"
