import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@dataclass
class ProviderEndpoint:
    url: str = ""
    message: dict = field(
        default_factory=lambda: {"role": "assistant", "content": "done"}
    )
    requests: list[dict] = field(default_factory=list)


@pytest.fixture
def provider_endpoint():
    endpoint = ProviderEndpoint()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            endpoint.requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            body = json.dumps(
                {
                    "id": "request",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls"
                            if endpoint.message.get("tool_calls")
                            else "stop",
                            "message": endpoint.message,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "completion_tokens_details": {"reasoning_tokens": 2},
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint.url = f"http://127.0.0.1:{server.server_port}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield endpoint
    server.shutdown()
    server.server_close()
    thread.join(2)
