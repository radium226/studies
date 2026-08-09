import asyncio

from video_analyzer.kernel import StopToken


def test_is_stop_requested_false_initially() -> None:
    assert not StopToken().is_stop_requested


def test_request_stop_sets_is_stop_requested() -> None:
    stop_token = StopToken()
    stop_token.request_stop()
    assert stop_token.is_stop_requested


def test_request_stop_is_idempotent() -> None:
    stop_token = StopToken()
    stop_token.request_stop()
    stop_token.request_stop()
    assert stop_token.is_stop_requested


def test_wait_returns_once_stop_requested() -> None:
    async def scenario() -> None:
        stop_token = StopToken()
        waiter = asyncio.ensure_future(stop_token.wait())
        await asyncio.sleep(0)
        assert not waiter.done()

        stop_token.request_stop()
        await asyncio.wait_for(waiter, timeout=1.0)
        assert waiter.done()

    asyncio.run(scenario())
