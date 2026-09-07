from __future__ import annotations

import asyncio
from io import BytesIO
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from PIL import Image

from annotation_app.watcher import DirectorySyncService, RawWatchEvent


def image_bytes(image_format: str = "JPEG", color: str = "red") -> bytes:
    stream = BytesIO()
    Image.new("RGB", (48, 32), color).save(stream, format=image_format)
    return stream.getvalue()


def polling_service(**kwargs: object) -> DirectorySyncService:
    return DirectorySyncService(
        debounce_seconds=0.01,
        stability_interval=0.05,
        fallback_poll_interval=0.25,
        observer_reconcile_interval=1.0,
        **kwargs,
    )


async def wait_for_image(queue: asyncio.Queue, image_id: str, timeout: float = 3.0):
    async def read():
        while True:
            event = await queue.get()
            if event.image_id == image_id and event.kind in {"created", "modified", "moved"}:
                return event

    return await asyncio.wait_for(read(), timeout)


class WatcherReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name).resolve()
        self.observer_patch = patch("annotation_app.watcher.Observer", None)
        self.observer_patch.start()
        self.services: list[DirectorySyncService] = []

    async def asyncTearDown(self) -> None:
        for service in self.services:
            await service.stop()
        self.observer_patch.stop()
        self.temporary.cleanup()

    async def start_service(self, **kwargs: object) -> DirectorySyncService:
        service = polling_service(**kwargs)
        self.services.append(service)
        await service.start(self.directory, 1)
        return service

    async def test_existing_images_are_validated_and_paused_jpeg_is_hidden(self) -> None:
        complete = image_bytes()
        (self.directory / "ready.jpg").write_bytes(complete)
        pending = self.directory / "paused.jpg"
        pending.write_bytes(complete[:-20])
        (self.directory / "empty.jpg").write_bytes(b"")
        service = await self.start_service()
        _, events = service.subscribe(1)
        self.assertEqual(set(service.ready_snapshot(1)), {"ready.jpg"})
        self.assertTrue(service.is_ready("ready.jpg", 1))
        self.assertFalse(service.is_ready("paused.jpg", 1))

        # Several stable samples and a polling reconciliation must still not
        # mistake a camera's paused write for a complete photograph.
        await asyncio.sleep(0.65)
        self.assertNotIn("paused.jpg", service.ready_snapshot(1))
        self.assertTrue(events.empty())
        with pending.open("ab") as output:
            output.write(complete[-20:])
        await wait_for_image(events, "paused.jpg", timeout=1.2)
        self.assertTrue(service.is_ready("paused.jpg", 1))

    async def test_missed_events_discover_new_images_and_deletes_within_one_poll(self) -> None:
        service = await self.start_service()
        _, events = service.subscribe(1)
        picture = self.directory / "missed.jpg"
        started = asyncio.get_running_loop().time()
        picture.write_bytes(image_bytes())
        change = await wait_for_image(events, picture.name, timeout=1.2)
        self.assertEqual(change.kind, "created")
        self.assertLess(asyncio.get_running_loop().time() - started, 1.0)
        picture.unlink()
        deleted = await asyncio.wait_for(events.get(), timeout=1.0)
        self.assertEqual((deleted.kind, deleted.image_id), ("deleted", picture.name))
        self.assertEqual(service.ready_snapshot(1), {})

    async def test_repeated_reconciliation_does_not_cancel_slow_validation(self) -> None:
        service = await self.start_service()
        _, events = service.subscribe(1)
        validate = service._contents_complete

        def slow_validation(*args):
            time.sleep(0.55)
            return validate(*args)

        with patch.object(service, "_contents_complete", side_effect=slow_validation) as validator:
            picture = self.directory / "slow-decode.jpg"
            picture.write_bytes(image_bytes())
            await wait_for_image(events, picture.name, timeout=1.5)
            self.assertTrue(service.is_ready(picture.name, 1))
            self.assertEqual(validator.call_count, 1)

    async def test_duplicate_notifications_use_cached_validation_and_overwrite_is_hidden(self) -> None:
        picture = self.directory / "replace.jpg"
        picture.write_bytes(image_bytes())
        service = await self.start_service()
        _, events = service.subscribe(1)
        with patch.object(service, "_contents_complete", wraps=service._contents_complete) as validator:
            for _ in range(8):
                service._put_raw_event(RawWatchEvent("modified", 1, picture))
            await asyncio.sleep(0.2)
            self.assertEqual(validator.call_count, 0)
            self.assertTrue(events.empty())

            updated = image_bytes(color="blue")
            picture.write_bytes(updated[:-15])
            self.assertFalse(service.is_ready(picture.name, 1))
            service._put_raw_event(RawWatchEvent("modified", 1, picture))
            await asyncio.sleep(0.3)
            self.assertFalse(service.is_ready(picture.name, 1))
            self.assertTrue(events.empty())
            with picture.open("ab") as output:
                output.write(updated[-15:])
            changed = await wait_for_image(events, picture.name, timeout=1.2)
            self.assertEqual(changed.kind, "modified")
            self.assertTrue(service.is_ready(picture.name, 1))
            self.assertEqual(validator.call_count, 2)

    async def test_all_supported_formats_reject_incomplete_container_tails(self) -> None:
        formats = {"jpg": "JPEG", "png": "PNG", "gif": "GIF", "bmp": "BMP", "tif": "TIFF", "webp": "WEBP"}
        for suffix, image_format in formats.items():
            content = image_bytes(image_format)
            (self.directory / f"complete.{suffix}").write_bytes(content)
            (self.directory / f"truncated.{suffix}").write_bytes(content[:-1])
        service = await self.start_service()
        expected = {f"complete.{suffix}" for suffix in formats}
        self.assertEqual(set(service.ready_snapshot(1)), expected)
        await asyncio.sleep(0.35)
        self.assertEqual(set(service.ready_snapshot(1)), expected)

    async def test_directory_switch_discards_old_readiness_and_validates_existing_files(self) -> None:
        (self.directory / "old.jpg").write_bytes(image_bytes())
        service = await self.start_service()
        new_directory = self.directory / "new"
        new_directory.mkdir()
        (new_directory / "new.jpg").write_bytes(image_bytes())
        (new_directory / "incomplete.jpg").write_bytes(image_bytes()[:-2])
        await service.switch_directory(new_directory, 2)
        self.assertEqual(service.ready_snapshot(1), {})
        self.assertFalse(service.is_ready("old.jpg", 1))
        self.assertEqual(set(service.ready_snapshot(2)), {"new.jpg"})
        self.assertTrue(service.is_ready("new.jpg", 2))

    async def test_new_generation_cannot_reuse_old_image_during_observer_shutdown(self) -> None:
        (self.directory / "same.jpg").write_bytes(image_bytes())
        service = await self.start_service()
        new_directory = self.directory / "new"
        new_directory.mkdir()
        (new_directory / "same.jpg").write_bytes(image_bytes()[:-20])
        stop_observer = service._stop_observer

        async def inspect_during_shutdown():
            self.assertEqual(service.ready_snapshot(2), {})
            self.assertFalse(service.is_ready("same.jpg", 2))
            await stop_observer()

        with patch.object(service, "_stop_observer", side_effect=inspect_during_shutdown):
            await service.switch_directory(new_directory, 2)
        self.assertEqual(service.ready_snapshot(2), {})

    async def test_background_initial_scan_can_finish_after_start_returns(self) -> None:
        picture = self.directory / "existing.jpg"
        picture.write_bytes(image_bytes())
        service = polling_service()
        self.services.append(service)
        scan_started = asyncio.Event()
        release_scan = asyncio.Event()
        prime_directory = service._prime_directory

        async def delayed_prime() -> None:
            scan_started.set()
            await release_scan.wait()
            await prime_directory()

        with patch.object(service, "_prime_directory", side_effect=delayed_prime):
            await service.start(
                self.directory,
                1,
                wait_for_initial_scan=False,
            )
            await asyncio.wait_for(scan_started.wait(), timeout=1)
            self.assertFalse(service.initial_scan_complete)
            self.assertEqual(service.ready_snapshot(1), {})

            release_scan.set()
            deadline = asyncio.get_running_loop().time() + 2
            while not service.initial_scan_complete:
                if asyncio.get_running_loop().time() >= deadline:
                    self.fail("后台初始图片扫描未按时完成。")
                await asyncio.sleep(0.01)

        self.assertEqual(set(service.ready_snapshot(1)), {picture.name})

    async def test_stop_cancels_background_initial_scan(self) -> None:
        service = polling_service()
        self.services.append(service)
        scan_started = asyncio.Event()
        scan_cancelled = asyncio.Event()

        async def blocked_prime() -> None:
            scan_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                scan_cancelled.set()
                raise

        with patch.object(service, "_prime_directory", side_effect=blocked_prime):
            await service.start(
                self.directory,
                1,
                wait_for_initial_scan=False,
            )
            await asyncio.wait_for(scan_started.wait(), timeout=1)
            await asyncio.wait_for(service.stop(), timeout=1)

        self.assertTrue(scan_cancelled.is_set())
        self.assertFalse(service.initial_scan_complete)
        self.assertIsNone(service._prime_task)
        self.assertIsNone(service._reconcile_task)

    async def test_switch_cancels_old_background_scan_before_priming_new_directory(self) -> None:
        service = polling_service()
        self.services.append(service)
        old_scan_started = asyncio.Event()
        old_scan_cancelled = asyncio.Event()
        prime_directory = service._prime_directory
        new_directory = self.directory / "new"
        new_directory.mkdir()
        new_picture = new_directory / "new.jpg"
        new_picture.write_bytes(image_bytes())

        async def controlled_prime() -> None:
            if service.generation == 1:
                old_scan_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    old_scan_cancelled.set()
                    raise
            await prime_directory()

        with patch.object(service, "_prime_directory", side_effect=controlled_prime):
            await service.start(
                self.directory,
                1,
                wait_for_initial_scan=False,
            )
            await asyncio.wait_for(old_scan_started.wait(), timeout=1)
            await asyncio.wait_for(
                service.switch_directory(new_directory, 2),
                timeout=2,
            )

        self.assertTrue(old_scan_cancelled.is_set())
        self.assertTrue(service.initial_scan_complete)
        self.assertEqual(set(service.ready_snapshot(2)), {new_picture.name})

    async def test_switch_scan_failure_still_restores_reconciliation(self) -> None:
        service = await self.start_service()
        new_directory = self.directory / "new"
        new_directory.mkdir()

        async def fail_prime() -> None:
            raise RuntimeError("simulated initial scan failure")

        with (
            patch.object(service, "_prime_directory", side_effect=fail_prime),
            patch("annotation_app.watcher.LOGGER.exception") as log_exception,
        ):
            await service.switch_directory(new_directory, 2)
            await asyncio.sleep(0)

        self.assertFalse(service.initial_scan_complete)
        self.assertIsNotNone(service._reconcile_task)
        self.assertFalse(service._reconcile_task.done())
        log_exception.assert_called_once_with("Initial image directory scan failed")


if __name__ == "__main__":
    unittest.main()
