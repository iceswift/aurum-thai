"""Compare the public read API contract of two environments."""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def get(base, path):
    with urlopen(api_request(base, path), timeout=10) as response:
        return json.load(response), response.headers.get("ETag")


def shape(value):
    if isinstance(value, dict):
        return {key: type(item).__name__ for key, item in value.items()}
    return type(value).__name__


def api_request(base, path, **headers):
    return Request(base.rstrip("/") + path, headers={
        "User-Agent": "AurumThai-ContractCheck/1.0",
        "Accept": "application/json",
        **headers,
    })


def check_etag(base):
    board, tag = get(base, "/api/board")
    assert board["status"] == "ok" and board["latest"] and board["history"], base
    assert tag == f'W/"gold-board-{board["revision"]}"', base
    request = api_request(base, "/api/board", **{"If-None-Match": tag})
    try:
        with urlopen(request, timeout=10) as response:
            updated = json.load(response)
            assert updated["revision"] != board["revision"], base
    except HTTPError as error:
        assert error.code == 304 and error.read() == b"" and error.headers["ETag"] == tag, base
        return
    raise AssertionError(f"{base}: snapshot changed during ETag check; rerun")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production")
    parser.add_argument("staging")
    args = parser.parse_args()
    for path in ("/health", "/ready", "/api/board", "/api/latest", "/api/gold",
                 "/api/history", "/api/percent_jewelry", "/api/shops"):
        production, _ = get(args.production, path)
        staging, _ = get(args.staging, path)
        assert shape(production) == shape(staging), (path, shape(production), shape(staging))
        if path == "/api/board":
            assert shape(production["latest"]) == shape(staging["latest"]), path
        print(f"{path}: contract matches")
    assert get(args.production, "/ready")[0]["has_gold_data"]
    assert get(args.staging, "/ready")[0]["has_gold_data"]
    assert get(args.staging, "/health")[0]["firebase_enabled"] is False
    for base in (args.production, args.staging):
        check_etag(base)
        print(f"{base}: matching ETag returned empty 304")
