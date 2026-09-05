"""Real-time image-directory monitoring with asyncio-safe event delivery.

``watchdog`` is used as a low-latency accelerator when it is available.  A
periodic snapshot reconciliation task always remains active so the application
also works on network filesystems, with an unavailable watchdog installation,
or after an operating-system watcher drops an event.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .config import IMAGE_SUFFIXES

try:  # The polling fallback deliberately keeps the application usable.
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except (ImportError, OSError):  # pragma: no cover - depends on host packages.
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment,misc]


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileStamp:
    """Cheap fingerprint used to coalesce duplicate filesystem events."""

    size: int
    mtime_ns: int
    ctime_ns: int
    inode: int


@dataclass(frozen=True, slots=True)
class ImageRecord:
    image_id: str
    path: Path
    stamp: FileStamp


@dataclass(frozen=True, slots=True)
class RawWatchEvent:
    kind: str
    generation: int
    src_path: Path | None = None
    dest_path: Path | None = None


@dataclass(frozen=True, slots=True)
class DirectoryChange:
    """A serialisable event delivered to connected SSE subscribers."""

    sequence: int
    kind: str
    directory_generation: int
    image_id: str | None = None
    old_image_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.kind,
            "sequence": self.sequence,
            "directory_generation": self.directory_generation,
        }
        if self.image_id is not None:
            payload["image_id"] = self.image_id
        if self.old_image_id is not None:
            payload["old_image_id"] = self.old_image_id
        payload.update(self.details)
        return payload


class _WatchdogEventHandler(FileSystemEventHandler):  # type: ignore[misc]
    """Tiny thread-side adapter; all real work stays on the asyncio loop."""

    def __init__(
        self,
        service: DirectorySyncService,
        generation: int,
    ) -> None:
        super().__init__()
        self._service = service
        self._generation = generation

    def on_created(self, event: Any) -> None:
        if not event.is_directory:
            self._service.submit_from_thread(
                RawWatchEvent("created", self._generation, Path(event.src_path))
            )

    def on_modified(self, event: Any) -> None:
        if not event.is_directory:
            self._service.submit_from_thread(
                RawWatchEvent("modified", self._generation, Path(event.src_path))
            )

    def on_deleted(self, event: Any) -> None:
        if not event.is_directory:
            self._service.submit_from_thread(
                RawWatchEvent("deleted", self._generation, Path(event.src_path))
            )

    def on_moved(self, event: Any) -> None:
        if not event.is_directory:
            self._service.submit_from_thread(
                RawWatchEvent(
                    "moved",
                    self._generation,
                    Path(event.src_path),
                    Path(event.dest_path),
                )
            )


class DirectorySyncService:
    """Watch one recursive image tree and fan changes out to SSE clients.

    Files are not announced until their size and timestamps have remained
    stable across multiple probes.  This prevents the browser from attempting
    to load an image while camera software is still writing it.
    """

    def __init__(
        self,
        *,
        debounce_seconds: float = 0.35,
        stability_interval: float = 0.25,
        stable_samples: int = 2,
        stability_timeout: float = 10.0,
        observer_reconcile_interval: float = 5.0,
        fallback_poll_interval: float = 1.0,
        raw_queue_size: int = 4096,
        subscriber_queue_size: int = 256,
    ) -> None:
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._stability_interval = max(0.05, stability_interval)
        self._stable_samples = max(2, stable_samples)
        self._stability_timeout = max(
            self._stability_interval * self._stable_samples,
            stability_timeout,
        )
        self._observer_reconcile_interval = max(1.0, observer_reconcile_interval)
        self._fallback_poll_interval = max(0.25, fallback_poll_interval)
        self._raw_queue_size = max(32, raw_queue_size)
        self._subscriber_queue_size = max(8, subscriber_queue_size)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._raw_queue: asyncio.Queue[RawWatchEvent] | None = None
        self._root: Path | None = None
        self._generation = 0
        self._running = False
        self._observer: Any | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._reconcile_task: asyncio.Task[None] | None = None
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_events: dict[str, RawWatchEvent] = {}
        self._known: dict[str, ImageRecord] = {}
        self._subscribers: dict[
            int, tuple[int, asyncio.Queue[DirectoryChange]]
        ] = {}
        self._next_subscriber_id = 0
        self._sequence = 0
        self._control_lock = asyncio.Lock()

    @property
    def current_sequence(self) -> int:
        return self._sequence

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def observer_active(self) -> bool:
        observer = self._observer
        if observer is None:
            return False
        try:
            return bool(observer.is_alive())
        except Exception:  # pragma: no cover - defensive third-party boundary.
            return False

    async def start(self, data_dir: str | Path, generation: int) -> None:
        async with self._control_lock:
            if self._running:
                return
            self._running = True
            self._loop = asyncio.get_running_loop()
            self._raw_queue = asyncio.Queue(maxsize=self._raw_queue_size)
            self._root = Path(data_dir).resolve()
            self._generation = generation
            initial = await asyncio.to_thread(self._scan_directory, self._root)
            self._known = initial or {}
            self._consumer_task = asyncio.create_task(
                self._consume_events(), name="annotation-directory-events"
            )
            self._reconcile_task = asyncio.create_task(
                self._reconcile_loop(), name="annotation-directory-reconcile"
            )
            await self._start_observer()

    async def stop(self) -> None:
        async with self._control_lock:
            if not self._running:
                return
            self._running = False
            await self._stop_observer()
            for task in list(self._pending_tasks.values()):
                task.cancel()
            background = [
                task
                for task in (self._consumer_task, self._reconcile_task)
                if task is not None
            ]
            for task in background:
                task.cancel()
            if self._pending_tasks or background:
                await asyncio.gather(
                    *self._pending_tasks.values(), *background,
                    return_exceptions=True,
                )
            self._pending_tasks.clear()
            self._pending_events.clear()
            self._consumer_task = None
            self._reconcile_task = None
            self._raw_queue = None
            self._loop = None

    async def switch_directory(
        self,
        data_dir: str | Path,
        generation: int,
    ) -> None:
        """Replace the watched tree and invalidate events from the old tree."""

        async with self._control_lock:
            old_generation = self._generation
            if self._running and generation != old_generation:
                self._publish(
                    "directory-changed",
                    generation=old_generation,
                    details={"current_directory_generation": generation},
                )

            # Change this before stopping Observer so late callbacks are stale.
            self._generation = generation
            await self._stop_observer()
            for task in list(self._pending_tasks.values()):
                task.cancel()
            if self._pending_tasks:
                await asyncio.gather(
                    *self._pending_tasks.values(), return_exceptions=True
                )
            self._pending_tasks.clear()
            self._pending_events.clear()
            self._drain_raw_queue()

            self._root = Path(data_dir).resolve()
            initial = await asyncio.to_thread(self._scan_directory, self._root)
            self._known = initial or {}
            if self._running:
                await self._start_observer()

    def subscribe(
        self, generation: int
    ) -> tuple[int, asyncio.Queue[DirectoryChange]]:
        self._next_subscriber_id += 1
        queue: asyncio.Queue[DirectoryChange] = asyncio.Queue(
            maxsize=self._subscriber_queue_size
        )
        token = self._next_subscriber_id
        self._subscribers[token] = (generation, queue)
        return token, queue

    def unsubscribe(self, token: int) -> None:
        self._subscribers.pop(token, None)

    def submit_from_thread(self, event: RawWatchEvent) -> None:
        """Safely bridge a watchdog Observer callback onto the event loop."""

        loop = self._loop
        if loop is None or loop.is_closed() or not self._running:
            return
        try:
            loop.call_soon_threadsafe(self._put_raw_event, event)
        except RuntimeError:
            # Shutdown may close the loop between the checks and the callback.
            return

    def _put_raw_event(self, event: RawWatchEvent) -> None:
        if not self._running or event.generation != self._generation:
            return
        if not self._event_has_supported_image(event):
            return
        queue = self._raw_queue
        if queue is None:
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            self._drain_raw_queue()
            self._publish("resync", details={"reason": "watch_queue_overflow"})
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - queue was just drained.
                pass

    def _drain_raw_queue(self) -> None:
        queue = self._raw_queue
        if queue is None:
            return
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _event_has_supported_image(self, event: RawWatchEvent) -> bool:
        return any(
            path is not None and path.suffix.lower() in IMAGE_SUFFIXES
            for path in (event.src_path, event.dest_path)
        )

    async def _consume_events(self) -> None:
        try:
            while self._running:
                queue = self._raw_queue
                if queue is None:
                    return
                event = await queue.get()
                if event.generation != self._generation:
                    continue
                key = self._event_key(event)
                if key is None:
                    continue
                previous = self._pending_events.get(key)
                if previous is not None:
                    event = self._merge_events(previous, event)
                self._pending_events[key] = event
                previous_task = self._pending_tasks.get(key)
                if previous_task is not None:
                    previous_task.cancel()
                task = asyncio.create_task(
                    self._debounced_process(key, event),
                    name="annotation-image-settle",
                )
                self._pending_tasks[key] = task
                task.add_done_callback(
                    lambda completed, event_key=key: self._pending_done(
                        event_key, completed
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Image directory event consumer failed")
            if self._running:
                self._publish("resync", details={"reason": "consumer_error"})

    def _pending_done(self, key: str, completed: asyncio.Task[None]) -> None:
        if self._pending_tasks.get(key) is completed:
            self._pending_tasks.pop(key, None)
            self._pending_events.pop(key, None)
        if not completed.cancelled():
            try:
                completed.exception()
            except (asyncio.CancelledError, Exception):
                # _debounced_process logs recoverable failures itself.
                pass

    def _event_key(self, event: RawWatchEvent) -> str | None:
        path = event.dest_path if event.kind == "moved" else event.src_path
        if path is None:
            return None
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    @staticmethod
    def _merge_events(previous: RawWatchEvent, current: RawWatchEvent) -> RawWatchEvent:
        # Preserve the most informative event across create/modify bursts.
        if previous.kind in {"created", "moved"} and current.kind == "modified":
            return previous
        if previous.kind == "deleted" and current.kind in {"created", "modified"}:
            return RawWatchEvent(
                "modified",
                current.generation,
                current.src_path,
                current.dest_path,
            )
        return current

    async def _debounced_process(self, key: str, event: RawWatchEvent) -> None:
        try:
            await asyncio.sleep(self._debounce_seconds)
            if not self._running or event.generation != self._generation:
                return
            await self._process_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Failed to process image event for %s", key)
            if event.generation == self._generation:
                self._publish("resync", details={"reason": "event_processing_error"})

    async def _process_event(self, event: RawWatchEvent) -> None:
        if event.kind == "deleted":
            if event.src_path is not None:
                self._handle_deleted(event.src_path)
            return
        if event.kind == "moved":
            await self._handle_moved(event)
            return
        if event.src_path is not None:
            await self._handle_created_or_modified(event.src_path)

    async def _handle_created_or_modified(self, path: Path) -> None:
        record = await self._wait_until_stable(path)
        if record is None:
            return
        key = self._id_key(record.image_id)
        previous = self._known.get(key)
        if previous is not None and previous.stamp == record.stamp:
            return
        self._known[key] = record
        self._publish(
            "created" if previous is None else "modified",
            image_id=record.image_id,
        )

    def _handle_deleted(self, path: Path) -> None:
        image_id = self._lexical_image_id(path)
        if image_id is None:
            return
        previous = self._known.pop(self._id_key(image_id), None)
        if previous is not None:
            self._publish("deleted", image_id=previous.image_id)

    async def _handle_moved(self, event: RawWatchEvent) -> None:
        if event.src_path is None or event.dest_path is None:
            return
        old_id = self._lexical_image_id(event.src_path)
        destination_supported = event.dest_path.suffix.lower() in IMAGE_SUFFIXES
        if not destination_supported:
            if old_id is not None:
                self._handle_deleted(event.src_path)
            return

        record = await self._wait_until_stable(event.dest_path)
        if record is None:
            if old_id is not None:
                self._handle_deleted(event.src_path)
            return
        new_key = self._id_key(record.image_id)
        if old_id is None:
            previous = self._known.get(new_key)
            if previous is None or previous.stamp != record.stamp:
                self._known[new_key] = record
                self._publish(
                    "created" if previous is None else "modified",
                    image_id=record.image_id,
                )
            return

        old_key = self._id_key(old_id)
        previous = self._known.pop(old_key, None)
        destination_previous = self._known.get(new_key)
        self._known[new_key] = record
        if old_key == new_key:
            if previous is None or previous.stamp != record.stamp:
                self._publish("modified", image_id=record.image_id)
        elif previous is not None:
            self._publish(
                "moved",
                image_id=record.image_id,
                old_image_id=previous.image_id,
            )
        elif destination_previous is None or destination_previous.stamp != record.stamp:
            self._publish("created", image_id=record.image_id)

    async def _wait_until_stable(self, path: Path) -> ImageRecord | None:
        deadline = asyncio.get_running_loop().time() + self._stability_timeout
        previous: ImageRecord | None = None
        equal_samples = 1
        while self._running and asyncio.get_running_loop().time() < deadline:
            generation = self._generation
            root = self._root
            if root is None:
                return None
            record = await asyncio.to_thread(self._read_record, path, root)
            if generation != self._generation or root != self._root:
                return None
            if record is None:
                return None
            if record.stamp.size <= 0:
                previous = None
                equal_samples = 1
            elif previous is not None and previous.stamp == record.stamp:
                equal_samples += 1
                if equal_samples >= self._stable_samples:
                    return record
            else:
                previous = record
                equal_samples = 1
            await asyncio.sleep(self._stability_interval)
        LOGGER.warning("Image did not become stable before timeout: %s", path)
        return None

    async def _reconcile_loop(self) -> None:
        try:
            while self._running:
                interval = (
                    self._observer_reconcile_interval
                    if self.observer_active
                    else self._fallback_poll_interval
                )
                await asyncio.sleep(interval)
                await self._reconcile_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Image directory reconciliation task failed")
            if self._running:
                self._publish("resync", details={"reason": "reconcile_error"})

    async def _reconcile_once(self) -> None:
        generation = self._generation
        root = self._root
        if root is None:
            return
        current = await asyncio.to_thread(self._scan_directory, root)
        if (
            current is None
            or generation != self._generation
            or root != self._root
            or not self._running
        ):
            return

        known = dict(self._known)
        removed = set(known) - set(current)
        added = set(current) - set(known)

        # When inode information is useful, retain move semantics in polling
        # fallback mode instead of degrading every rename to delete + create.
        removed_by_identity: dict[tuple[int, int, int], str] = {}
        for key in removed:
            record = known[key]
            if record.stamp.inode:
                removed_by_identity[
                    (record.stamp.inode, record.stamp.size, record.stamp.mtime_ns)
                ] = key
        matched_removed: set[str] = set()
        matched_added: set[str] = set()
        for key in added:
            record = current[key]
            identity = (
                record.stamp.inode,
                record.stamp.size,
                record.stamp.mtime_ns,
            )
            old_key = removed_by_identity.get(identity) if record.stamp.inode else None
            if old_key is None:
                continue
            matched_removed.add(old_key)
            matched_added.add(key)
            self._put_raw_event(
                RawWatchEvent(
                    "moved",
                    generation,
                    known[old_key].path,
                    record.path,
                )
            )

        for key in removed - matched_removed:
            self._put_raw_event(
                RawWatchEvent("deleted", generation, known[key].path)
            )
        for key in added - matched_added:
            self._put_raw_event(
                RawWatchEvent("created", generation, current[key].path)
            )
        for key in set(known) & set(current):
            if known[key].stamp != current[key].stamp:
                self._put_raw_event(
                    RawWatchEvent("modified", generation, current[key].path)
                )

    def _publish(
        self,
        kind: str,
        *,
        generation: int | None = None,
        image_id: str | None = None,
        old_image_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> DirectoryChange:
        self._sequence += 1
        target_generation = self._generation if generation is None else generation
        change = DirectoryChange(
            sequence=self._sequence,
            kind=kind,
            directory_generation=target_generation,
            image_id=image_id,
            old_image_id=old_image_id,
            details=details or {},
        )
        overflow_change: DirectoryChange | None = None
        for subscriber_generation, queue in list(self._subscribers.values()):
            if subscriber_generation != target_generation:
                continue
            try:
                queue.put_nowait(change)
            except asyncio.QueueFull:
                while True:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                if overflow_change is None:
                    self._sequence += 1
                    overflow_change = DirectoryChange(
                        sequence=self._sequence,
                        kind="resync",
                        directory_generation=target_generation,
                        details={"reason": "subscriber_queue_overflow"},
                    )
                queue.put_nowait(overflow_change)
        return change

    async def _start_observer(self) -> None:
        if not self._running or self._root is None:
            return
        if Observer is None:
            LOGGER.warning(
                "watchdog is unavailable; using periodic image-directory polling"
            )
            return

        root = self._root
        generation = self._generation

        def build_and_start() -> Any:
            observer = Observer()
            handler = _WatchdogEventHandler(self, generation)
            try:
                observer.schedule(handler, os.fspath(root), recursive=True)
                observer.start()
            except Exception:
                try:
                    observer.stop()
                except Exception:
                    pass
                try:
                    observer.join(timeout=2.0)
                except Exception:
                    pass
                raise
            return observer

        try:
            self._observer = await asyncio.to_thread(build_and_start)
        except Exception as exc:
            self._observer = None
            LOGGER.warning(
                "Unable to watch image directory %s (%s); using polling fallback",
                root,
                exc,
            )

    async def _stop_observer(self) -> None:
        observer = self._observer
        self._observer = None
        if observer is None:
            return

        def stop_and_join() -> None:
            try:
                observer.stop()
            finally:
                observer.join(timeout=5.0)
            if observer.is_alive():
                LOGGER.warning("Image directory observer did not stop within timeout")

        try:
            await asyncio.to_thread(stop_and_join)
        except Exception:
            LOGGER.exception("Failed while stopping image directory observer")

    @staticmethod
    def _stamp(stat_result: os.stat_result) -> FileStamp:
        return FileStamp(
            size=stat_result.st_size,
            mtime_ns=stat_result.st_mtime_ns,
            ctime_ns=stat_result.st_ctime_ns,
            inode=getattr(stat_result, "st_ino", 0) or 0,
        )

    @classmethod
    def _read_record(cls, path: Path, root: Path) -> ImageRecord | None:
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            return None
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(root)
            if not resolved.is_file():
                return None
            stat_result = resolved.stat()
        except (OSError, ValueError):
            return None
        image_id = PurePosixPath(*relative.parts).as_posix()
        return ImageRecord(image_id, resolved, cls._stamp(stat_result))

    @classmethod
    def _scan_directory(cls, root: Path) -> dict[str, ImageRecord] | None:
        records: dict[str, ImageRecord] = {}
        walk_failed = False

        def on_error(exc: OSError) -> None:
            nonlocal walk_failed
            walk_failed = True
            LOGGER.warning("Unable to scan part of image directory %s: %s", root, exc)

        try:
            for directory, _subdirectories, filenames in os.walk(
                root, onerror=on_error, followlinks=False
            ):
                base = Path(directory)
                for filename in filenames:
                    path = base / filename
                    if path.suffix.lower() not in IMAGE_SUFFIXES:
                        continue
                    record = cls._read_record(path, root)
                    if record is not None:
                        records[cls._id_key(record.image_id)] = record
        except OSError as exc:
            LOGGER.warning("Unable to scan image directory %s: %s", root, exc)
            return None
        # A partial network/permission scan must not generate a delete storm.
        return None if walk_failed else records

    def _lexical_image_id(self, path: Path) -> str | None:
        root = self._root
        if root is None or path.suffix.lower() not in IMAGE_SUFFIXES:
            return None
        try:
            absolute = Path(os.path.abspath(os.fspath(path)))
            relative = absolute.relative_to(root)
        except (OSError, ValueError):
            return None
        return PurePosixPath(*relative.parts).as_posix()

    @staticmethod
    def _id_key(image_id: str) -> str:
        return image_id.casefold() if os.name == "nt" else image_id
