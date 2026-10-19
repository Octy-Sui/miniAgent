import json

from miniagent.dashboard.reads import SnapshotSlice
from miniagent.dashboard.redaction import redact_json
from miniagent.dashboard.views import FieldView, SourceTarget


def project_snapshot(source: SnapshotSlice) -> tuple[FieldView, ...]:
    fields = []
    missing = {
        issue.field_path
        for issue in source.snapshot.issues
        if issue.code == "field_not_recorded"
    }
    for index, message in enumerate(source.messages, start=source.start):
        raw = message.model_dump(mode="json", exclude_unset=True)
        for key in ("content", "tool_calls"):
            if ("conversation", "messages", index, key) in missing:
                raw.pop(key, None)
        fields.append(
            FieldView(
                f"message-{index}",
                f"消息 {index}（原始索引）",
                json.dumps(redact_json(raw), ensure_ascii=False, indent=2),
                f"Snapshot v{source.snapshot.version}",
                SourceTarget(
                    source.snapshot.run_id,
                    snapshot_version=source.snapshot.version,
                    start=index,
                    end=index + 1,
                ),
            )
        )
    return tuple(fields)
