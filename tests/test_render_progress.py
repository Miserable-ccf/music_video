import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import workflow


class RenderProgressTest(unittest.TestCase):
    def test_gpu_failure_retries_cpu_and_keeps_cpu_for_second_output(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(workflow, "JOBS", Path(temporary)):
            workflow.save({"job_id": "fallback", "status": "DESIGN_SELECTED",
                           "lyrics_approved": True, "lyrics": "test.lrc",
                           "selected_design": {"id": "a"}, "outputs": {},
                           "audio_probe": {"format": {"duration": "10"}}})
            script = ("import sys; print('ARGS ' + ' '.join(sys.argv[1:]), flush=True); "
                      "print('100%', flush=True); "
                      "sys.exit(1 if 'gpu' in sys.argv else 0)")
            def command(data, output, codec):
                return [sys.executable, "-c", script, "--renderer", "gpu",
                        "--encoder", "h264_nvenc", "--audio-codec", codec]
            with patch.object(workflow, "render_command", side_effect=command), \
                 patch.object(workflow, "probe", return_value={"format": {"duration": "10"}}):
                workflow.command_run(argparse.Namespace(job_id="fallback", dry_run=False))
            directory = Path(temporary) / "fallback"
            log = (directory / "render.log").read_text()
            self.assertEqual(sum(line.startswith("ARGS --renderer gpu") for line in log.splitlines()), 1)
            self.assertEqual(sum(line.startswith("ARGS --renderer cpu") for line in log.splitlines()), 2)
            self.assertIn("--encoder libx264 --audio-codec flac", log)
            self.assertIn("--encoder libx264 --audio-codec aac", log)
            progress = json.loads((directory / "render-progress.json").read_text())
            self.assertEqual(progress["status"], "complete")
            self.assertEqual(progress["renderer"], "cpu")

    def test_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch.object(workflow, "JOBS", root):
                    workflow.save({"job_id": "progress", "status": "DESIGN_SELECTED",
                                   "lyrics_approved": True, "lyrics": "test.lrc",
                                   "selected_design": {"id": "a"}, "outputs": {},
                                   "audio_probe": {"format": {"duration": "10"}}})
                    script = "print('  25%', flush=True); print('  100%', flush=True)"
                    if fail:
                        script += "; raise SystemExit(1)"
                    with patch.object(workflow, "render_command",
                                      return_value=[sys.executable, "-c", script]), \
                         patch.object(workflow, "probe", return_value={"format": {"duration": "10"}}):
                        args = argparse.Namespace(job_id="progress", dry_run=False)
                        if fail:
                            with self.assertRaises(RuntimeError):
                                workflow.command_run(args)
                        else:
                            workflow.command_run(args)
                    progress = json.loads((root / "progress/render-progress.json").read_text())
                    self.assertEqual(progress["status"], "failed" if fail else "complete")
                    if fail:
                        self.assertLess(progress["percent"], 100)
                    else:
                        self.assertEqual(progress["percent"], 100)
                    self.assertIn("25%", (root / "progress/render.log").read_text())
