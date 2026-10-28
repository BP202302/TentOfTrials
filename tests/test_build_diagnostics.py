"""Exercise real diagnostic generation with temporary paths and a fake pack process."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diagnostic_build_under_test", ROOT / "build.py")
build = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = build
spec.loader.exec_module(build)

class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.diag = self.root / "diagnostic"
        self.results = [("ok", True, 1.23456, "compile output", "bin/ok"),
                        ("bad", False, 0.125, "failure output", None)]
        for target, value in [("ROOT", self.root), ("DIAGNOSTIC_DIR", self.diag)]:
            mock = patch.object(build, target, value)
            mock.start(); self.addCleanup(mock.stop)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__(); self.addCleanup(self.output.__exit__, None, None, None)

    def metadata(self):
        return json.loads((self.diag / "build-abcdef12.json").read_text())

    def generate(self, pack=None, binary=True):
        with patch.object(build, "current_commit_id", return_value="abcdef12"), \
             patch.object(build, "get_encryptly_bin", return_value=Path("/fake/encryptly") if binary else None), \
             patch.object(build, "collect_system_info", return_value="synthetic system"), \
             patch.object(build.Path, "home", return_value=self.root), \
             patch.object(build.subprocess, "run", side_effect=pack) as run:
            result = build.generate_logd(self.results)
            return result, run.call_count

    def test_mixed_module_report_preserves_results(self):
        report = build.build_diagnostic_report(self.results, "abcdef12", ["diagnostic/build-abcdef12.logd"], "fixture-password")
        self.assertEqual((report["total_modules"], report["passed"], report["failed"]), (2, 1, 1))
        self.assertEqual(report["commit"], "abcdef12")
        self.assertEqual(report["modules"], [
            {"name":"ok", "status":"PASS", "elapsed_seconds":1.235, "artifact":"bin/ok", "output":"compile output"},
            {"name":"bad", "status":"FAIL", "elapsed_seconds":0.125, "artifact":None, "output":"failure output"}])
        self.assertEqual(report["diagnostic_logd"], "diagnostic/build-abcdef12.logd")
        self.assertFalse(report["chunked"])
        self.assertIsNone(report["chunk_size_bytes"])

    def test_empty_results_do_not_invent_modules(self):
        report = build.build_diagnostic_report([], "abcdef12")
        self.assertEqual((report["total_modules"], report["passed"], report["failed"]), (0, 0, 0))
        self.assertEqual(report["modules"], [])
        self.assertIsNone(report["diagnostic_logd"])
        self.assertIsNone(report["decrypt_command"])

    def test_missing_encryptly_writes_failure_metadata_without_process(self):
        result, calls = self.generate(binary=False)
        report = self.metadata()
        self.assertFalse(result); self.assertEqual(calls, 0)
        self.assertIn("encryptly binary not found", report["diagnostic_logd_error"])
        self.assertIsNone(report["diagnostic_logd"])
        self.assertIsNone(report["password"])
        self.assertIsNone(report["decrypt_command"])

    def test_pack_failure_removes_partial_archive_and_cleans_workspace(self):
        def failed(args, **kwargs):
            Path(args[2]).write_bytes(b"partial")
            return subprocess.CompletedProcess(args, 1, "", "fixture pack failure")
        result, calls = self.generate(failed)
        self.assertFalse(result); self.assertEqual(calls, 1)
        report = self.metadata()
        self.assertEqual(report["diagnostic_logd_error"], "fixture pack failure")
        self.assertIsNone(report["diagnostic_logd"])
        self.assertIsNone(report["decrypt_command"])
        self.assertFalse((self.diag / "build-abcdef12.logd").exists())
        self.assertFalse((self.root / ".cache/tent-of-trials/logd-workspace").exists())

    def pack(self, args, **kwargs):
        # Inspect the actual staged bundle rather than reproducing its builder.
        safe = Path(args[args.index("--include") + 1]) / "safe"
        self.assertEqual((safe / "system-info.txt").read_text(), "synthetic system")
        self.assertIn("passed: 1", (safe / "build-summary.txt").read_text())
        log = (safe / "build.log").read_text()
        self.assertIn("compile output", log); self.assertIn("failure output", log)
        Path(args[2]).write_bytes(b"0123456789")
        return subprocess.CompletedProcess(args, 0, "fixture-password\n", "")

    def test_success_writes_single_archive_and_decrypt_reference(self):
        result, calls = self.generate(self.pack)
        self.assertTrue(result); self.assertEqual(calls, 1)
        report = self.metadata()
        self.assertEqual(report["diagnostic_logd"], "diagnostic/build-abcdef12.logd")
        self.assertIsNone(report["diagnostic_logd_error"])
        self.assertEqual(report["password"], "fixture-password")
        self.assertIn("diagnostic/build-abcdef12.logd", report["decrypt_command"])
        self.assertFalse((self.root / ".cache/tent-of-trials/logd-workspace").exists())

    def test_chunked_generation_lists_existing_parts_in_reassembly_order(self):
        splitter = build.split_diagnostic_logd
        with patch.object(build, "split_diagnostic_logd", side_effect=lambda path: splitter(path, chunk_size=4)):
            result, calls = self.generate(self.pack)
        self.assertTrue(result); self.assertEqual(calls, 1)
        report = self.metadata()
        paths = ["diagnostic/build-abcdef12-part%03d.logd" % i for i in (1, 2, 3)]
        self.assertEqual(report["diagnostic_logd"], paths)
        self.assertTrue(report["chunked"])
        self.assertEqual(report["chunk_size_bytes"], build.DIAGNOSTIC_CHUNK_SIZE)
        self.assertEqual(b"".join((self.root / path).read_bytes() for path in paths), b"0123456789")
        self.assertFalse((self.diag / "build-abcdef12.logd").exists())
        self.assertIn("diagnostic/build-abcdef12.logd", report["decrypt_command"])

    def test_split_boundary_preserves_original_at_or_below_limit(self):
        self.diag.mkdir()
        for size in (0, 3, 4):
            with self.subTest(size=size):
                path = self.diag / "boundary.logd"; path.write_bytes(b"x" * size)
                self.assertEqual(build.split_diagnostic_logd(path, chunk_size=4), [path])
                self.assertEqual(path.read_bytes(), b"x" * size)

    def test_split_multiple_chunks_are_bounded_and_lossless(self):
        self.diag.mkdir(); path = self.diag / "boundary.logd"
        original = bytes(range(256)) * 2; path.write_bytes(original)
        paths = build.split_diagnostic_logd(path, chunk_size=17)
        self.assertFalse(path.exists())
        self.assertTrue(all(0 < x.stat().st_size <= 17 for x in paths))
        self.assertEqual(b"".join(x.read_bytes() for x in paths), original)

if __name__ == "__main__":
    unittest.main()
