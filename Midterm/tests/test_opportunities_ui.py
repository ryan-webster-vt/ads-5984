from __future__ import annotations

from datetime import date

from models import Opportunity
from src.ui.opportunities import (
    filter_opportunities,
    paginate,
    records_to_frame,
    sort_opportunities,
)

TODAY = date(2026, 10, 22)


def _record(
    identifier: str,
    title: str,
    *,
    agency: str,
    doc_type: str,
    status: str,
    open_date: str,
    close_date: str | None,
    amount: float | None,
    cfda: list[str],
) -> Opportunity:
    return Opportunity(
        opportunity_id=identifier,
        title=title,
        agency=agency,
        doc_type=doc_type,
        status=status,
        open_date=open_date,
        close_date=close_date,
        award_ceiling=amount,
        cfda_numbers=cfda,
        detail_status="success",
    )


def _frame():
    return records_to_frame(
        [
            _record(
                "1",
                "Alpha Ecology",
                agency="NSF",
                doc_type="synopsis",
                status="posted",
                open_date="2026-09-01",
                close_date="2026-12-01",
                amount=500000,
                cfda=["01.001"],
            ),
            _record(
                "2",
                "Beta Climate",
                agency="NIH",
                doc_type="forecast",
                status="forecasted",
                open_date="2026-08-01",
                close_date=None,
                amount=None,
                cfda=["02.002"],
            ),
            _record(
                "3",
                "Gamma Data",
                agency="NSF",
                doc_type="synopsis",
                status="posted",
                open_date="2026-01-01",
                close_date="2026-09-01",
                amount=100000,
                cfda=["01.001"],
            ),
            _record(
                "4",
                "Delta Physics",
                agency="DOE",
                doc_type="synopsis",
                status="archived",
                open_date="2026-06-01",
                close_date="2026-12-31",
                amount=250000,
                cfda=["03.003"],
            ),
            _record(
                "5",
                "Epsilon Biology",
                agency="NSF",
                doc_type="forecast",
                status="forecasted",
                open_date="2026-10-01",
                close_date="2027-01-01",
                amount=750000,
                cfda=["01.002"],
            ),
        ]
    )


def test_default_filter_hides_archived_and_expired():
    result = filter_opportunities(_frame(), today=TODAY)
    assert set(result["id"]) == {"1", "2", "5"}
    expanded = filter_opportunities(_frame(), include_expired=True, today=TODAY)
    assert set(expanded["id"]) == {"1", "2", "3", "5"}


def test_filters_match_pandas_oracle():
    frame = _frame()
    assert set(filter_opportunities(frame, agencies=["NSF"], today=TODAY)["id"]) == {
        "1",
        "5",
    }
    assert set(filter_opportunities(frame, doc_types=["forecast"], today=TODAY)["id"]) == {
        "2",
        "5",
    }
    assert set(filter_opportunities(frame, cfda="01.0", today=TODAY)["id"]) == {
        "1",
        "5",
    }
    assert set(filter_opportunities(frame, title_query="clim", today=TODAY)["id"]) == {
        "2"
    }
    dated = filter_opportunities(
        frame,
        open_from=date(2026, 9, 1),
        today=TODAY,
    )
    assert set(dated["id"]) == {"1", "5"}
    amounts = filter_opportunities(frame, amount_min=400000, today=TODAY)
    assert set(amounts["id"]) == {"1", "5"}
    capped = filter_opportunities(frame, amount_max=600000, today=TODAY)
    assert set(capped["id"]) == {"1"}


def test_sort_amount_places_nulls_last():
    result = filter_opportunities(_frame(), include_expired=True, today=TODAY)
    ordered = sort_opportunities(result, column="Award amount", ascending=True)
    assert ordered["id"].tolist() == ["3", "1", "5", "2"]
    assert ordered["amount"].iloc[-1] != ordered["amount"].iloc[-1]


def test_sort_title_and_stable_tiebreak():
    ordered = sort_opportunities(
        filter_opportunities(_frame(), today=TODAY),
        column="Title",
        ascending=True,
    )
    assert ordered["title"].tolist() == ["Alpha Ecology", "Beta Climate", "Epsilon Biology"]


def test_pagination_page_counts_and_clamping():
    result = filter_opportunities(_frame(), include_expired=True, today=TODAY)
    first, pages = paginate(result, page=1, page_size=2)
    assert pages == 2
    assert len(first) == 2
    second, _ = paginate(result, page=99, page_size=2)
    assert len(second) == 2
    empty, empty_pages = paginate(result.iloc[0:0], page=5, page_size=2)
    assert empty.empty and empty_pages == 1
