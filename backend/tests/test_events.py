import json

from app.api.events import frame, pending
from app.models import AuditRecord


def _rec(id_: int) -> AuditRecord:
    return AuditRecord(
        id=id_,
        ts="2026-10-04T10:00:00Z",
        check="jev",
        action="allow",
        reason="r",
        policy_version="t",
    )


ROWS = [_rec(i) for i in range(1, 8)]


def test_first_connect_gets_the_recent_backlog():
    assert [r.id for r in pending(ROWS, after_id=None, backlog=3)] == [5, 6, 7]


def test_reconnect_gets_only_newer_rows():
    assert [r.id for r in pending(ROWS, after_id=5, backlog=3)] == [6, 7]
    assert pending(ROWS, after_id=7, backlog=3) == []


def test_frame_is_one_sse_event_with_the_row_id():
    text = frame(_rec(42))
    assert text.startswith("id: 42\ndata: ") and text.endswith("\n\n")
    assert json.loads(text.split("data: ", 1)[1])["id"] == 42
