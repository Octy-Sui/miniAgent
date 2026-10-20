import asyncio
import os
import sys

import pytest

from miniagent.cli.console_approval import ConsoleApprovalHandler


@pytest.mark.parametrize(
    ("input_text", "expected"),
    [
        ("yes\nno\n", [True, False]),
        ("invalid\ny\n", [True]),
        ("", [False]),
        ("yes", [True]),
    ],
)
async def test_approval_reads_regular_file(tmp_path, monkeypatch, input_text, expected):
    approvals = tmp_path / "approvals.txt"
    approvals.write_text(input_text)
    with approvals.open() as source:
        monkeypatch.setattr(sys, "stdin", source)
        handler = ConsoleApprovalHandler()
        assert [
            await asyncio.wait_for(handler._confirm(), 2) for _ in expected
        ] == expected


async def test_approval_pipe_read_remains_cancellable(monkeypatch):
    reader, writer = os.pipe()
    with os.fdopen(reader) as source:
        monkeypatch.setattr(sys, "stdin", source)
        try:
            task = asyncio.create_task(ConsoleApprovalHandler()._confirm())
            await asyncio.sleep(0)
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            os.write(writer, b"yes\n")
            assert await ConsoleApprovalHandler()._confirm()
        finally:
            os.close(writer)
