from uuid import UUID

import pytest

from miniagent.cli.main import parse_run_id


@pytest.mark.parametrize(
    ("value",),
    [
        ("12345678-1234-4234-8234-123456789abc",),
        ("12345678123442348234123456789abc",),
        ("run_12345678-1234-4234-8234-123456789abc",),
        ("run_12345678123442348234123456789abc",),
        ("{12345678-1234-4234-8234-123456789abc}",),
        ("urn:uuid:12345678-1234-4234-8234-123456789abc",),
    ],
)
def test_complete_uuid_forms_identify_same_run(value):
    assert parse_run_id(value) == UUID("12345678-1234-4234-8234-123456789abc")
