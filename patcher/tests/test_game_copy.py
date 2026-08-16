from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from sierra_patcher import game_copy, proc
from sierra_patcher.game_copy import (
    COPY_STATE_FILENAME,
    copy_live_game,
    inspect_copy_destination,
    paths_overlap,
)


class GameCopyTests(unittest.TestCase):
    def test_windows_paths_block_same_and_nested_destinations(self) -> None:
        self.assertTrue(paths_overlap(r"D:\EscapeFromTarkov", r"d:\escapefromtarkov"))
        self.assertTrue(paths_overlap(r"D:\EscapeFromTarkov", r"D:\EscapeFromTarkov\SPT"))
        self.assertTrue(paths_overlap(r"D:\Games", r"D:\Games\EscapeFromTarkov"))
        self.assertFalse(paths_overlap(r"D:\EscapeFromTarkov", r"D:\SPT\3.11.4"))
        self.assertFalse(paths_overlap(r"D:\EscapeFromTarkov", r"E:\EscapeFromTarkov"))

    def test_new_empty_and_nonempty_destination_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Live"
            destination = root / "SPT"
            source.mkdir()
            (source / "EscapeFromTarkov.exe").write_bytes(b"exe")

            self.assertEqual(
                inspect_copy_destination(source, destination, "1.0").reason,
                "new",
            )
            destination.mkdir()
            self.assertEqual(
                inspect_copy_destination(source, destination, "1.0").reason,
                "empty",
            )
            (destination / "unrelated.txt").write_text("keep", encoding="utf-8")
            status = inspect_copy_destination(source, destination, "1.0")
            self.assertFalse(status.ready)
            self.assertEqual(status.reason, "not_empty")

    def test_cancelled_copy_is_resumable_and_finishes_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Live"
            destination = root / "SPT"
            source.mkdir()
            (source / "EscapeFromTarkov.exe").write_bytes(b"exe")
            payload = b"x" * (5 * 1024 * 1024)
            (source / "EscapeFromTarkov_Data.bin").write_bytes(payload)

            cancel_event = threading.Event()

            def cancel_after_first_chunk(_phase, current, _total, _message):
                if current >= 4 * 1024 * 1024:
                    cancel_event.set()

            with self.assertRaises(proc.Cancelled):
                copy_live_game(
                    source,
                    destination,
                    source_version="1.0",
                    on_progress=cancel_after_first_chunk,
                    cancel_event=cancel_event,
                )

            self.assertTrue((destination / COPY_STATE_FILENAME).is_file())
            status = inspect_copy_destination(source, destination, "1.0")
            self.assertTrue(status.ready)
            self.assertTrue(status.resumable)
            mismatched = inspect_copy_destination(source, destination, "2.0")
            self.assertFalse(mismatched.ready)
            self.assertEqual(mismatched.reason, "state_mismatch")

            cancel_event.clear()
            with mock.patch.object(
                game_copy.shutil,
                "disk_usage",
                return_value=mock.Mock(free=1024 * 1024),
            ):
                copy_live_game(
                    source,
                    destination,
                    source_version="1.0",
                    cancel_event=cancel_event,
                )

            self.assertFalse((destination / COPY_STATE_FILENAME).exists())
            self.assertEqual((destination / "EscapeFromTarkov_Data.bin").read_bytes(), payload)

    @unittest.skipUnless(os.name == "nt", "Windows long-path behavior")
    def test_copy_supports_long_windows_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Live"
            destination = root / "SPT"
            source.mkdir()
            (source / "EscapeFromTarkov.exe").write_bytes(b"exe")
            relative = Path(*(["deep-folder-name" * 4] * 5)) / "payload.bin"
            source_file = source / relative
            os.makedirs(game_copy._io_path(source_file.parent), exist_ok=True)
            with open(game_copy._io_path(source_file), "wb") as stream:
                stream.write(b"payload")

            copy_live_game(source, destination, source_version="1.0")

            with open(game_copy._io_path(destination / relative), "rb") as stream:
                self.assertEqual(stream.read(), b"payload")

if __name__ == "__main__":
    unittest.main()
