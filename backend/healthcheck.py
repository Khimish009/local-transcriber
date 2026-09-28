"""Container healthcheck for the API service (no curl in the slim image)."""

import json
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/api/v1/health"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=5) as response:
            if response.status != 200:
                print(f"unexpected status {response.status}", file=sys.stderr)
                return 1
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"health request failed: {exc}", file=sys.stderr)
        return 1

    # Redis is the only hard dependency of the API in Phase 0.
    if payload.get("redis", {}).get("status") != "ok":
        print("redis is not reachable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
