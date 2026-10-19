"""Stdlib-only exec gate; the inherited read end never permits itself."""

import math
import os
import select
import sys
from time import monotonic


def main() -> None:
    descriptor = int(sys.argv[1])
    deadline = float(sys.argv[2])
    error_fd = int(sys.argv[3])
    os.set_inheritable(error_fd, False)
    remaining = deadline - monotonic()
    if (
        remaining <= 0
        or not select.select(
            [descriptor], [], [], remaining if math.isfinite(remaining) else None
        )[0]
    ):
        raise SystemExit(125)
    permitted = os.read(descriptor, 1) == b"1"
    os.close(descriptor)
    if not permitted or monotonic() >= deadline:
        raise SystemExit(125)
    try:
        os.execvpe(sys.argv[4], sys.argv[4:], dict(os.environ))
    except OSError as exc:
        os.write(error_fd, (exc.errno or 5).to_bytes(4, "big"))
        raise SystemExit(127) from None


if __name__ == "__main__":
    main()
