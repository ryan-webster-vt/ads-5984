"""End-to-end Streamlit AppTest coverage for the Phase 3 UI."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

_APP_PATH = Path(__file__).resolve().parents[2] / "src" / "upset_profiler" / "ui" / "app.py"


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "ui.db"))
    app = AppTest.from_file(str(_APP_PATH), default_timeout=30)
    app.run()
    return app


@pytest.mark.e2e
def test_streamlit_app_renders_fixture_slate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path, monkeypatch)

    assert not app.exception
    assert app.title[0].value == "NCAA Upset Condition Profiler"
    assert len(app.dataframe) == 1
    assert [metric.value for metric in app.metric[:2]] == ["4", "Fixture (offline)"]
    assert len(app.multiselect[0].value) == 4
    assert [button.label for button in app.button] == ["Execute Search"]


@pytest.mark.e2e
def test_streamlit_app_runs_analysis_and_reuses_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path, monkeypatch)

    app.button(key="ui_execute").click().run()

    assert not app.exception
    assert app.session_state["ui_last_from_cache"] is False
    ranking = app.session_state["ui_last_ranking"]
    assert [score.game_id for score in ranking.scores] == ["401000001", "401000003"]
    assert any("upset watch" in item.value for item in app.success)

    app.button(key="ui_execute").click().run()

    assert not app.exception
    assert app.session_state["ui_last_from_cache"] is True
    assert [score.game_id for score in app.session_state["ui_last_ranking"].scores] == [
        "401000001",
        "401000003",
    ]


@pytest.mark.e2e
def test_streamlit_app_filters_can_show_empty_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path, monkeypatch)

    app.text_input(key="ui_filter_query").set_value("no-such-team").run()

    assert not app.exception
    assert any("No games match" in item.value for item in app.warning)
