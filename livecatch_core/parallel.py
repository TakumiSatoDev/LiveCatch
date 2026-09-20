"""Bounded, ordered prefetch that does not drain a live iterable before yielding.

A producer owns next(source), separately from the consumer. A semaphore slot is
released AFTER the consumer resumes (i.e. after the fragment has been appended),
so completed-but-uncommitted files count towards the bound too.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from queue import Empty, Queue
from threading import Event, Semaphore, Thread
from typing import Callable, Iterable, Iterator, TypeVar

T = TypeVar("T")
R = TypeVar("R")


class OrderedPrefetch:
    def __init__(self, workers: int, window: int, cancel: Event | None = None):
        if workers < 1 or window < workers:
            raise ValueError("Require 1 <= workers <= window")
        self.workers, self.window = workers, window
        self.cancel = cancel if cancel is not None else Event()
        self.closed = Event()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lc-fragment")
        self.producer: Thread | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            self.cancel.set()
        self.shutdown()

    def map(self, fn: Callable[[T], R], source: Iterable[T]) -> Iterator[R]:
        if self.producer is not None:
            raise RuntimeError("OrderedPrefetch is single-use")
        slots = Semaphore(self.window)
        ready: Queue[Future[R] | None] = Queue()

        def stopped() -> bool:
            return self.closed.is_set() or self.cancel.is_set()

        def produce() -> None:
            try:
                iterator = iter(source)
                while not stopped():
                    if not slots.acquire(timeout=0.05):
                        continue
                    try:
                        item = next(iterator)
                    except StopIteration:
                        slots.release()
                        break
                    if stopped():
                        slots.release()
                        break
                    ready.put(self.pool.submit(fn, item))
            except BaseException as exc:
                failed: Future[R] = Future()
                failed.set_exception(exc)
                ready.put(failed)
            finally:
                ready.put(None)

        self.producer = Thread(target=produce, daemon=True, name="lc-fragment-source")
        self.producer.start()
        try:
            while True:
                try:
                    future = ready.get(timeout=0.05)
                except Empty:
                    if stopped():
                        break
                    continue
                if future is None:
                    break
                # Drain already scheduled fragments on cooperative stop, in order.
                try:
                    yield future.result()
                finally:
                    slots.release()
        finally:
            self.closed.set()

    def shutdown(self, wait: bool = True, **_) -> None:
        self.closed.set()
        self.pool.shutdown(wait=wait, cancel_futures=True)
        if self.producer is not None and wait:
            # next() may be waiting on a live edge; it MUST NOT block the consumer.
            # The isolated worker process owns the producer and its network stack.
            self.producer.join(timeout=0.2)
