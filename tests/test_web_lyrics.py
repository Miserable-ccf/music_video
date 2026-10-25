import argparse
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import web_app
from pipeline import workflow


class LyricsPreparationTest(unittest.TestCase):
    def make_job(self, root):
        audio = root / "song.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
            stream.writeframes(b"\0\0" * 8000)
        workflow.command_create(argparse.Namespace(
            audio=str(audio), title="歌", artist="歌手", lyrics=None, job_id="lyrics-test"))

    def test_network_lyrics_are_saved_as_editable_draft(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(workflow, "JOBS", root / "jobs"):
                self.make_job(root)
                with patch.object(web_app.lyric_fetcher, "fetch_lyrics", return_value=(
                        "[00:00.00]第一句\n[00:00.50]第二句\n", {"source": "lrclib"})):
                    web_app._prepare_lyrics("lyrics-test")
                data = workflow.load("lyrics-test")
                self.assertEqual(data["lyric_preparation"]["status"], "ready")
                self.assertEqual(data["lyric_preparation"]["source"], "lrclib")
                text = Path(data["lyric_preparation"]["draft"]).read_text(encoding="utf-8")
                self.assertIn("[status:draft_fetched]", text)

    def test_missing_network_lyrics_prompts_local_lrc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(workflow, "JOBS", root / "jobs"):
                self.make_job(root)
                with patch.object(web_app.lyric_fetcher, "fetch_lyrics", side_effect=RuntimeError("offline")):
                    web_app._prepare_lyrics("lyrics-test")
                data = workflow.load("lyrics-test")
                self.assertEqual(data["lyric_preparation"]["status"], "failed")
                self.assertIn("LRC", data["lyric_preparation"]["message"])


if __name__ == "__main__":
    unittest.main()
