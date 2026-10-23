from __future__ import annotations

from pydantic import BaseModel

from src.llm import _lenient_validate


class Payload(BaseModel):
    value: int


def test_lenient_validation_accepts_clean_json():
    assert _lenient_validate(Payload, '{"value": 3}').value == 3


def test_lenient_validation_recovers_from_vt_arc_stray_brace():
    malformed = '{\n{"value": 7}'
    assert _lenient_validate(Payload, malformed).value == 7
