"""Opportunity browser: filters, sorting, pagination, counts, and selection."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

import config
from models import Opportunity, OpportunityDatabase
from src.grants.store import ARCHIVED_STATUSES, OPEN_STATUSES, is_expired, is_visible_open

FRAME_COLUMNS = [
    "ID",
    "Title",
    "Agency",
    "Type",
    "Status",
    "Open date",
    "Close date",
    "Award",
    "CFDA",
]


def _award_value(record: Opportunity) -> float | None:
    if record.award_ceiling is not None:
        return record.award_ceiling
    return record.estimated_funding


def records_to_frame(records: list[Opportunity]) -> pd.DataFrame:
    rows = [
        {
            "id": record.opportunity_id,
            "title": record.title,
            "agency": record.agency,
            "agency_code": record.agency_code,
            "doc_type": record.doc_type,
            "status": record.status,
            "open_date": record.open_date,
            "close_date": record.close_date,
            "amount": _award_value(record),
            "cfda": ", ".join(record.cfda_numbers),
            "detail_status": record.detail_status,
            "number": record.opportunity_number,
        }
        for record in records
    ]
    frame = pd.DataFrame(rows, columns=[
        "id",
        "title",
        "agency",
        "agency_code",
        "doc_type",
        "status",
        "open_date",
        "close_date",
        "amount",
        "cfda",
        "detail_status",
        "number",
    ])
    for column in ("open_date", "close_date"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    return frame


def filter_opportunities(
    frame: pd.DataFrame,
    *,
    statuses: list[str] | None = None,
    agencies: list[str] | None = None,
    doc_types: list[str] | None = None,
    cfda: str = "",
    open_from: date | None = None,
    open_to: date | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    title_query: str = "",
    include_expired: bool = False,
    today: date | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    today = today or date.today()
    today_ts = pd.Timestamp(today)
    result = frame[frame["status"].isin(OPEN_STATUSES)].copy()
    if not include_expired:
        result = result[
            result["close_date"].isna() | (result["close_date"] >= today_ts)
        ]
    if statuses:
        result = result[result["status"].isin(statuses)]
    if agencies:
        result = result[result["agency"].isin(agencies)]
    if doc_types:
        result = result[result["doc_type"].isin(doc_types)]
    if cfda.strip():
        result = result[
            result["cfda"].str.contains(cfda.strip(), case=False, regex=False, na=False)
        ]
    if open_from is not None:
        result = result[result["open_date"].notna()]
        result = result[result["open_date"] >= pd.Timestamp(open_from)]
    if open_to is not None:
        result = result[result["open_date"].notna()]
        result = result[result["open_date"] <= pd.Timestamp(open_to)]
    if amount_min is not None:
        result = result[result["amount"].notna() & (result["amount"] >= amount_min)]
    if amount_max is not None:
        result = result[result["amount"].notna() & (result["amount"] <= amount_max)]
    if title_query.strip():
        result = result[
            result["title"].str.contains(
                title_query.strip(), case=False, regex=False, na=False
            )
        ]
    return result


def sort_opportunities(
    frame: pd.DataFrame,
    *,
    column: str = "open_date",
    ascending: bool = False,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    column_map = {
        "Open date": "open_date",
        "Close date": "close_date",
        "Award amount": "amount",
        "Title": "title",
        "Agency": "agency",
        "Status": "status",
    }
    key = column_map.get(column, column)
    return frame.sort_values(
        by=[key, "id"],
        ascending=[ascending, True],
        na_position="last",
        kind="mergesort",
    )


def paginate(
    frame: pd.DataFrame, *, page: int, page_size: int
) -> tuple[pd.DataFrame, int]:
    if frame.empty:
        return frame.copy(), 1
    page_size = max(1, page_size)
    page_count = max(1, (len(frame) + page_size - 1) // page_size)
    page = min(max(1, page), page_count)
    start = (page - 1) * page_size
    return frame.iloc[start : start + page_size].copy(), page_count


def _format_update(timestamp: str | None) -> str:
    if not timestamp:
        return "never"
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    display = pd.DataFrame(
        {
            "Title": frame["title"],
            "Agency": frame["agency"],
            "Type": frame["doc_type"],
            "Status": frame["status"],
            "Open date": frame["open_date"].dt.strftime("%Y-%m-%d").fillna("Unknown"),
            "Close date": frame["close_date"].dt.strftime("%Y-%m-%d").fillna("Unknown"),
            "Award": frame["amount"].map(
                lambda value: "Unknown" if pd.isna(value) else f"${value:,.0f}"
            ),
        }
    )
    return display.reset_index(drop=True)


def render_opportunity_browser(database: OpportunityDatabase) -> Opportunity | None:
    records = database.opportunities
    today = date.today()
    feed_open_count = sum(1 for record in records if record.status in OPEN_STATUSES)
    open_count = sum(1 for record in records if is_visible_open(record, today=today))
    expired_count = sum(
        1
        for record in records
        if record.status in OPEN_STATUSES and is_expired(record, today=today)
    )
    archived_count = sum(1 for record in records if record.status in ARCHIVED_STATUSES)
    summary = database.last_refresh

    st.header("Browse open opportunities")
    cols = st.columns(4)
    cols[0].metric("Open opportunities", open_count)
    cols[1].metric("Archived", archived_count)
    cols[2].metric("Expired (hidden)", expired_count)
    cols[3].metric("Last update", _format_update(summary.completed_at if summary else None))
    if summary is not None:
        st.caption(
            f"Grants.gov reported {feed_open_count} open records. "
            f"{expired_count} records with an explicit past close date are hidden."
        )

    if summary is None:
        st.info(
            "No live refresh has run yet. The saved fixture opportunity is shown for "
            "offline use. Click **Refresh opportunities** to harvest Grants.gov."
        )
    elif summary.status == "failed":
        st.error(
            "The last refresh failed. The previous good opportunity snapshot was "
            "preserved." + (f" {summary.errors[0]}" if summary.errors else "")
        )
    elif summary.status == "partial":
        st.warning(
            "The last refresh was partial. "
            f"{summary.detail_failed} detail fetches failed and incomplete feeds are "
            "never archived."
        )
    elif summary.archived_count:
        st.caption(f"Archived {summary.archived_count} opportunity records this refresh.")

    frame = records_to_frame(records)
    if frame.empty:
        st.info("No opportunities are stored yet. Refresh to harvest Grants.gov.")
        return None

    with st.expander("Filters", expanded=True):
        col_a, col_b, col_c = st.columns(3)
        statuses = col_a.multiselect(
            "Status",
            options=sorted(OPEN_STATUSES),
            default=sorted(OPEN_STATUSES),
        )
        agency_options = sorted(
            {record.agency for record in records if record.agency}
        )
        agencies = col_b.multiselect("Agency", options=agency_options)
        doc_type_options = sorted(
            {record.doc_type for record in records if record.doc_type}
        )
        doc_types = col_c.multiselect("Document type", options=doc_type_options)

        col_d, col_e, col_f = st.columns(3)
        cfda = col_d.text_input("CFDA number contains")
        title_query = col_e.text_input("Title contains")
        include_expired = col_f.checkbox("Show expired close dates", value=False)

        col_g, col_h, col_i, col_j = st.columns(4)
        open_from = col_g.date_input("Open date from", value=None)
        open_to = col_h.date_input("Open date to", value=None)
        amount_min = col_i.number_input(
            "Minimum award (0 = any)",
            min_value=0.0,
            value=0.0,
            step=10000.0,
        )
        amount_max = col_j.number_input(
            "Maximum award (0 = any)",
            min_value=0.0,
            value=0.0,
            step=10000.0,
        )

    filtered = filter_opportunities(
        frame,
        statuses=statuses,
        agencies=agencies,
        doc_types=doc_types,
        cfda=cfda,
        open_from=open_from,
        open_to=open_to,
        amount_min=amount_min or None,
        amount_max=amount_max or None,
        title_query=title_query,
        include_expired=include_expired,
        today=today,
    )

    if amount_min or amount_max:
        unknown_pool = filter_opportunities(
            frame,
            statuses=statuses,
            agencies=agencies,
            doc_types=doc_types,
            cfda=cfda,
            open_from=open_from,
            open_to=open_to,
            title_query=title_query,
            include_expired=include_expired,
            today=today,
        )
        unknown_amounts = int(unknown_pool["amount"].isna().sum())
        if unknown_amounts:
            st.warning(
                f"{unknown_amounts} matching records have unknown award amounts "
                "because detail fetches are incomplete or amounts are absent."
            )

    col_sort, col_order, col_size = st.columns(3)
    sort_column = col_sort.selectbox(
        "Sort by",
        options=["Open date", "Close date", "Award amount", "Title", "Agency", "Status"],
    )
    ascending = col_order.checkbox("Ascending", value=False)
    page_size = col_size.selectbox(
        "Rows per page",
        options=list(config.OPPORTUNITY_PAGE_SIZE_OPTIONS),
        index=list(config.OPPORTUNITY_PAGE_SIZE_OPTIONS).index(
            config.OPPORTUNITY_PAGE_SIZE
        ),
    )

    ordered = sort_opportunities(filtered, column=sort_column, ascending=ascending)
    page = st.number_input("Page", min_value=1, value=1, step=1)
    page_frame, page_count = paginate(
        ordered, page=int(page), page_size=int(page_size)
    )
    st.caption(
        f"{len(filtered)} matching open opportunities | page {min(int(page), page_count)} "
        f"of {page_count} | showing {len(page_frame)}"
    )

    if page_frame.empty:
        st.info("No opportunities match the current filters.")
        return None

    st.dataframe(_display_frame(page_frame), width="stretch", hide_index=True)

    ids = page_frame["id"].tolist()
    by_id = {record.opportunity_id: record for record in records}
    selected_id = st.selectbox(
        "Select an opportunity",
        options=ids,
        format_func=lambda value: (
            f"{by_id[value].title} — {by_id[value].agency or 'Unknown agency'} "
            f"({by_id[value].status})"
        ),
    )
    if st.button("Build team for selected opportunity", type="primary"):
        return by_id.get(selected_id)
    return None
