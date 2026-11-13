"""Integration check against a disposable, empty M2 PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from snapshot_state import PostgresSnapshotStore


store = PostgresSnapshotStore(os.environ["SNAPSHOT_DATABASE_URL"])
store.initialize()
assert store.load() is None, "Use an empty test database"
urls = os.environ["TEST_API_URLS"].split(",")
cache = {"gold_bar_data": [{"bullion_sell": "100"}], "source_type": "New Website"}
checked_at = datetime.now(timezone.utc).isoformat()

with ThreadPoolExecutor(max_workers=2) as pool:
    commits = list(pool.map(lambda _: store.commit(cache, checked_at), range(2)))
assert sum(commit.changed for commit in commits) == 1
assert {commit.revision for commit in commits} == {1}


def assert_readers(revision, price):
    etag = f'W/"gold-board-{revision}"'
    for url in urls:
        with urlopen(url + "/ready", timeout=5) as response:
            assert json.load(response)["has_gold_data"]
        with urlopen(url + "/api/board", timeout=5) as response:
            board = json.load(response)
            assert (board["revision"], board["latest"]["bullion_sell"]) == (revision, price)
            assert response.headers["ETag"] == etag
        with urlopen(url + "/api/latest", timeout=5) as response:
            assert json.load(response)["data"]["bullion_sell"] == price
        try:
            urlopen(Request(url + "/api/board", headers={"If-None-Match": etag}), timeout=5)
        except HTTPError as error:
            assert error.code == 304 and error.read() == b""
        else:
            raise AssertionError("Expected 304")


assert_readers(1, "100")
cache["gold_bar_data"][0]["bullion_sell"] = "101"
assert store.commit(cache, datetime.now(timezone.utc).isoformat()).revision == 2
assert not store.commit(cache, checked_at).changed
assert_readers(2, "101")
print("Two API processes agree on revisions 1 and 2; duplicate commits and 304 checks passed.")
