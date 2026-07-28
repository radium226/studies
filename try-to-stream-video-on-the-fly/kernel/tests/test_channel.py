import asyncio

from video_analyzer.kernel.channel import Channel


def test_try_recv_takes_queued_items_without_suspending() -> None:
    async def scenario() -> None:
        channel: Channel[int] = Channel(name="items")
        for item in range(3):
            await channel.send(item)

        assert [channel.try_recv() for _ in range(3)] == [0, 1, 2]

    asyncio.run(scenario())


def test_try_recv_returns_none_when_empty_or_closed() -> None:
    async def scenario() -> None:
        channel: Channel[int] = Channel(name="items")
        assert channel.try_recv() is None

        await channel.send(1)
        await channel.close()
        # Items queued before the close are still drainable; only once it runs
        # dry does a closed channel report nothing left.
        assert channel.try_recv() == 1
        assert channel.try_recv() is None

    asyncio.run(scenario())


def test_close_is_idempotent() -> None:
    """Every producer closes its output channel in a `finally`, so a second
    close (happy path plus unwind) must not raise."""

    async def scenario() -> None:
        channel: Channel[int] = Channel(name="items")
        await channel.close()
        await channel.close()

    asyncio.run(scenario())
