"""anomaly_events → events 옮기기 (app/anomaly_ingest.py).

DB 는 메모리 가짜로 대신한다. 여기서 잡으려는 건 쿼리 문법이 아니라 "빠뜨리지
않는가, 두 번 만들지 않는가, 오래된 걸로 푸시를 울리지 않는가"다.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import anomaly_ingest

NOW = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FakeQuery:
    def __init__(self, db: dict, table: str):
        self.db, self.table = db, table
        self.filters, self.orders, self.limit_n, self.upsert_args = [], [], None, None

    def select(self, *_):
        return self

    def gt(self, column, value):
        self.filters.append(lambda row: row.get(column) is not None and row[column] > value)
        return self

    def in_(self, column, values):
        allowed = set(values)
        self.filters.append(lambda row: row.get(column) in allowed)
        return self

    def order(self, column, desc=False):
        self.orders.append((column, desc))
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def upsert(self, rows, on_conflict, ignore_duplicates):
        self.upsert_args = (rows, on_conflict, ignore_duplicates)
        return self

    def execute(self):
        table = self.db.setdefault(self.table, [])
        if self.upsert_args:
            rows, key, ignore = self.upsert_args
            assert ignore, "충돌은 무시해야 한다 — 덮어쓰면 사용자가 바꾼 상태가 날아간다"
            existing = {row[key] for row in table}
            inserted = [
                {**row, "id": f"evt-{row[key]}"} for row in rows if row[key] not in existing
            ]
            table.extend(inserted)
            return SimpleNamespace(data=inserted)
        rows = [row for row in table if all(f(row) for f in self.filters)]
        for column, desc in reversed(self.orders):
            rows.sort(key=lambda row: row.get(column), reverse=desc)
        return SimpleNamespace(data=rows[: self.limit_n] if self.limit_n else rows)


class FakeSupabase:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return FakeQuery(self.db, name)


def anomaly(n: int, created: datetime, video="video-1", start=10.0, end=41.0, score=0.9):
    return {
        "id": f"anomaly-{n}", "video_id": video, "created_at": iso(created),
        "start_time_sec": start, "end_time_sec": end, "anomaly_score": score, "threshold": 0.6,
        "clip_storage_path": f"u1/{video}/clip_{n}.mp4", "thumbnail_storage_path": f"u1/{video}/thumb_{n}.jpg",
    }


def base_db(recorded: datetime):
    return {
        "anomaly_events": [],
        "events": [],
        "videos": [
            {"id": "video-1", "recorded_started_at": iso(recorded), "store_uuid": "store-1",
             "camera_uuid": "cam-1", "camera_id": "urn:uuid:agent-01"},
            # 도메인 연결 전에 올라온 조각 — 매장·카메라를 모른다
            {"id": "video-orphan", "recorded_started_at": iso(recorded), "store_uuid": None,
             "camera_uuid": None, "camera_id": "urn:uuid:agent-77"},
        ],
        "cameras": [{"id": "cam-1", "store_id": "store-1", "agent_camera_id": "urn:uuid:agent-01", "name": "계산대"}],
        "stores": [{"id": "store-1", "clip_retention_days": 30}],
    }


@pytest.fixture
def published(monkeypatch):
    calls = []
    monkeypatch.setattr(
        anomaly_ingest, "publish_created_event",
        lambda sb, event, push=True: calls.append((event["anomaly_event_id"], push)) or 1,
    )
    monkeypatch.setattr(anomaly_ingest, "_unlinked", set())
    return calls


def use(monkeypatch, db):
    monkeypatch.setattr(anomaly_ingest, "supabase", FakeSupabase(db))


class TestIngest:
    def test_each_anomaly_becomes_one_event_and_is_announced(self, monkeypatch, published):
        db = base_db(NOW - timedelta(minutes=2))
        db["anomaly_events"] = [anomaly(1, NOW - timedelta(seconds=50)), anomaly(2, NOW - timedelta(seconds=40), start=50, end=58)]
        use(monkeypatch, db)

        _, created, has_more = anomaly_ingest.ingest_once(NOW - timedelta(minutes=5), NOW)

        assert created == 2 and has_more is False
        assert [e["anomaly_event_id"] for e in db["events"]] == ["anomaly-1", "anomaly-2"]
        assert published == [("anomaly-1", True), ("anomaly-2", True)]

    def test_rereading_does_not_create_twice(self, monkeypatch, published):
        """겹쳐 읽기와 재시작 따라잡기는 같은 행을 다시 본다."""
        db = base_db(NOW - timedelta(minutes=2))
        db["anomaly_events"] = [anomaly(1, NOW - timedelta(seconds=10))]
        use(monkeypatch, db)

        cursor, _, _ = anomaly_ingest.ingest_once(NOW - timedelta(minutes=5), NOW)
        _, created_again, _ = anomaly_ingest.ingest_once(cursor, NOW + timedelta(seconds=5))

        assert created_again == 0
        assert len(db["events"]) == 1
        assert published == [("anomaly-1", True)]

    def test_cursor_stays_behind_recent_rows(self, monkeypatch, published):
        """방금 생긴 행 직전 시각의 행이 늦게 보일 수 있다 — 커서를 SETTLE 만큼 뒤에 둔다."""
        db = base_db(NOW - timedelta(minutes=2))
        db["anomaly_events"] = [anomaly(1, NOW - timedelta(seconds=5))]
        use(monkeypatch, db)

        cursor, _, _ = anomaly_ingest.ingest_once(NOW - timedelta(minutes=5), NOW)
        assert cursor == NOW - anomaly_ingest.SETTLE

        # 커서보다 앞선 시각으로 늦게 커밋된 행도 다음 주기에 잡힌다
        db["anomaly_events"].append(anomaly(2, NOW - timedelta(seconds=8), start=50, end=58))
        _, created, _ = anomaly_ingest.ingest_once(cursor, NOW + timedelta(seconds=5))
        assert created == 1

    def test_unlinked_anomaly_is_skipped_but_does_not_block(self, monkeypatch, published):
        db = base_db(NOW - timedelta(minutes=2))
        db["anomaly_events"] = [
            anomaly(1, NOW - timedelta(seconds=50), video="video-orphan"),
            anomaly(2, NOW - timedelta(seconds=40)),
        ]
        use(monkeypatch, db)

        _, created, _ = anomaly_ingest.ingest_once(NOW - timedelta(minutes=5), NOW)

        assert created == 1
        assert [e["anomaly_event_id"] for e in db["events"]] == ["anomaly-2"]

    def test_old_segments_become_events_without_push(self, monkeypatch, published):
        """재시작 후 따라잡은 몇 시간 전 구간 — 목록에는 뜨지만 푸시로 깨우지 않는다."""
        recorded = NOW - timedelta(hours=3)
        db = base_db(recorded)
        db["anomaly_events"] = [anomaly(1, recorded + timedelta(minutes=2))]
        use(monkeypatch, db)

        anomaly_ingest.ingest_once(NOW - anomaly_ingest.CATCH_UP, NOW)

        assert len(db["events"]) == 1
        assert published == [("anomaly-1", False)]

    def test_catch_up_through_full_batches_finishes(self, monkeypatch, published):
        """묶음이 가득 차면 쉬지 않고 이어 읽는다. 같은 묶음만 맴돌지 않고 끝나야 한다."""
        monkeypatch.setattr(anomaly_ingest, "BATCH_SIZE", 2)
        db = base_db(NOW - timedelta(minutes=10))
        db["anomaly_events"] = [
            anomaly(n, NOW - timedelta(minutes=9) + timedelta(seconds=n), start=n, end=n + 5) for n in range(1, 6)
        ]
        use(monkeypatch, db)

        cursor, rounds, has_more = NOW - timedelta(hours=1), 0, True
        while has_more:
            cursor, _, has_more = anomaly_ingest.ingest_once(cursor, NOW)
            rounds += 1
            assert rounds <= 5, "따라잡기가 끝나지 않는다"

        assert sorted(e["anomaly_event_id"] for e in db["events"]) == [f"anomaly-{n}" for n in range(1, 6)]
        assert len(published) == 5
