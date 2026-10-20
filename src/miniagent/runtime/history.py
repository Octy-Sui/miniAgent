from collections.abc import AsyncIterator
from uuid import UUID

from miniagent.runtime.events import RunEvent, RunEventType
from miniagent.runtime.store_contracts import CorruptStoredDataError, RuntimeStore


async def iter_run_events(
    store: RuntimeStore,
    run_id: UUID,
    *,
    through_sequence: int,
    after_sequence: int = 0,
    types: tuple[RunEventType, ...] | None = None,
) -> AsyncIterator[RunEvent]:
    if after_sequence < 0 or through_sequence < 0:
        raise ValueError("Event sequence bounds must be non-negative")
    cursor = after_sequence
    while cursor < through_sequence:
        page = await store.list_events(
            run_id,
            after_sequence=cursor,
            through_sequence=through_sequence,
            types=types,
            limit=16,
        )
        if not page:
            if types is None:
                raise CorruptStoredDataError("Missing event sequence before boundary")
            return
        for event in page:
            if (
                event.run_id != run_id
                or event.sequence <= cursor
                or event.sequence > through_sequence
                or (types is None and event.sequence != cursor + 1)
                or (types is not None and event.type not in types)
            ):
                raise CorruptStoredDataError("Invalid event sequence or identity")
            cursor = event.sequence
            yield event
        del page
