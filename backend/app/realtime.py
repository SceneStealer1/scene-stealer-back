"""SSE 팬아웃 (요구사항 4.2 — docs/api-contract.md 6절).

새 이벤트를 **만드는 쪽은 ai-worker** 이고 SSE 를 **흘리는 쪽은 backend** 다.
서로 다른 프로세스라 in-process pub/sub 만으로는 이어지지 않는다. 그래서
ai-worker 가 이벤트를 insert 한 뒤 backend 의 내부 엔드포인트를 두드리고
(routers/internal.py), backend 가 그걸 여기로 밀어 넣는다.

backend 가 죽어 있는 동안 발행된 알림은 SSE 로는 사라진다 — 그래도 행은 DB 에
남아 있고, 클라이언트는 재연결 후 GET /stores/:id/events 로 놓친 구간을 다시
읽는다. SSE 는 재생을 보장하지 않는다는 게 계약이다 (6절).

replicas: 1 전제다 (docker-compose.yml). 여러 replica 로 늘리면 구독자가
붙은 프로세스와 알림을 받은 프로세스가 달라져서 이 방식은 깨진다 — 그때는
Postgres LISTEN/NOTIFY 나 Redis 로 바꿔야 한다.
"""

import asyncio
import json
from typing import Any, AsyncIterator

# 구독자 큐가 이만큼 밀리면 그 구독자는 따라오지 못하는 것으로 본다. 큐를
# 무한정 키우면 느린 클라이언트 하나가 서버 메모리를 먹는다.
MAX_QUEUE_SIZE = 100

# 프록시·모바일 네트워크가 유휴 연결을 끊지 않도록 주는 주기적 신호.
PING_INTERVAL_SEC = 15


class EventBus:
    """매장별 팬아웃. 구독자는 각자 큐를 갖는다."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, store_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._subscribers.setdefault(store_id, set()).add(queue)
        return queue

    def unsubscribe(self, store_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(store_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._subscribers[store_id]

    def publish(self, store_id: str, name: str, data: dict[str, Any]) -> None:
        """비동기 컨텍스트가 아니어도 호출할 수 있게 put_nowait 로 던진다."""
        for queue in tuple(self._subscribers.get(store_id, ())):
            try:
                queue.put_nowait((name, data))
            except asyncio.QueueFull:
                # 따라오지 못하는 구독자는 버린다. 재연결하면 목록 조회로 복구된다.
                print(f"[backend] SSE 구독자가 밀려서 버립니다 store_id={store_id}")
                self.unsubscribe(store_id, queue)

    def subscriber_count(self, store_id: str) -> int:
        return len(self._subscribers.get(store_id, ()))


bus = EventBus()


def format_sse(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def event_stream(store_id: str) -> AsyncIterator[str]:
    """SSE 본문 제너레이터. 클라이언트가 끊으면 GeneratorExit 로 정리된다."""
    queue = bus.subscribe(store_id)
    try:
        # 연결 직후 한 번 — 클라이언트가 "붙었다"를 즉시 알 수 있게.
        yield format_sse("ready", {"storeId": store_id})
        while True:
            try:
                name, data = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL_SEC)
            except asyncio.TimeoutError:
                yield format_sse("ping", {})
                continue
            yield format_sse(name, data)
    finally:
        bus.unsubscribe(store_id, queue)
