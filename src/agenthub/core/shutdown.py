"""Let a worker finish its current cycle after SIGTERM."""

import signal
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event


@contextmanager
def stop_requested() -> Iterator[Event]:
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    previous = signal.signal(signal.SIGTERM, request_stop)
    try:
        yield stop
    finally:
        signal.signal(signal.SIGTERM, previous)
