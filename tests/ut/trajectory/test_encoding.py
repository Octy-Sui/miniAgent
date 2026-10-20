import json

from miniagent.trajectory.export import iter_json_bytes


def test_json_encoding_counts_escaping():
    value = {"text": '汉🙂"\n\\', "number": "0.000020"}
    encoded = b"".join(iter_json_bytes(value))
    assert json.loads(encoded) == value
    assert len(encoded) == len(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    )
