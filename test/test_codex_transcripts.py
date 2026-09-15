from pathlib import Path
import json
import os
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from codex_transcripts import record_transcript, transcript_stage, TRANSCRIPT_ENV
from workbench import WorkbenchHandler


class TranscriptTests(unittest.TestCase):
    def test_stage_labels_cover_archived_and_legacy_invocations(self):
        for path, expected in (
            (".solve-run-abc/events.jsonl", "Solve"),
            (".review-run-abc/events.jsonl", "Review"),
            (".review-run-abc/repair-events.jsonl", "Review · Repair"),
            (".paper-review-run-abc/events.jsonl", "Paper review"),
            (".write-run-abc/events.jsonl", "Write"),
            (".literature-run-abc/events.jsonl", "Literature review"),
            (".triage-run-abc/events.jsonl", "Triage"),
            (".metadata-run-abc/events.jsonl", "Extract metadata"),
            (".run-abc/events.jsonl", "Analyze"),
            ("attempt-001/review-events.jsonl", "Review"),
            ("draft-001/review-events.jsonl", "Paper review"),
            ("attempt-001/events.jsonl", "Solve"),
            ("draft-001/events.jsonl", "Write"),
            ("analysis/events.jsonl", "Analyze"),
            ("unknown/events.jsonl", ""),
        ):
            with self.subTest(path=path):
                self.assertEqual(transcript_stage(Path(path)), expected)

    def test_failed_turn_is_archived_before_workspace_changes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / ".review-run-abc"
            workspace.mkdir()

            @record_transcript
            def invoke(**kwargs):
                (workspace / "events.jsonl").write_text('工具\n', encoding="utf-8")
                (workspace / "run.log").write_text("failure", encoding="utf-8")
                raise RuntimeError("failed")

            with patch.dict(os.environ, {TRANSCRIPT_ENV: str(root / "codex")}):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    invoke(workspace=workspace, prompt="Do the task")
            (workspace / "events.jsonl").unlink()
            folder = next((root / "codex").iterdir())
            self.assertEqual((folder / "events").read_text(encoding="utf-8"), '工具\n')
            self.assertEqual((folder / "prompt.txt").read_text(), "Do the task")
            self.assertEqual(json.loads((folder / "source.json").read_text())["stage"], "Review")
            handler = self.handler(root, {"log_path": str(root / "console.log"), "status": "failed"})
            handler._send_codex("id", "")
            self.assertEqual(handler.result["transcripts"][0]["label"], "Codex 1 · Review")

    def handler(self, root, run):
        handler = object.__new__(WorkbenchHandler)
        handler.server = SimpleNamespace(app=SimpleNamespace(
            store=SimpleNamespace(get_run=lambda _: run), allowed_roots=[root]
        ))
        handler.send_json = lambda value: setattr(handler, "result", value)
        return handler

    def test_legacy_paging_and_partial_live_event(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events.jsonl"
            first = json.dumps({"type": "item.completed", "item": {"text": "工具" * 50000}}, ensure_ascii=False).encode() + b"\n"
            events.write_bytes(first + b'{"type":')
            run = {"log_path": str(root / "managed" / "console.log"),
                   "status": "running", "outputs": [str(root / "attempt.md")]}
            handler = self.handler(root, run)
            handler._send_codex("id", "index=0")
            self.assertEqual(handler.result["text"].encode(), first)
            self.assertFalse(handler.result["complete"])
            handler._send_codex("id", f"index=0&offset={len(first)}")
            self.assertEqual(handler.result["text"], "")
            with events.open("ab") as output:
                output.write(b'"turn.completed"}\n')
            run["status"] = "succeeded"
            handler._send_codex("id", "index=0")
            self.assertFalse(handler.result["complete"])
            handler._send_codex("id", f"index=0&offset={len(first)}")
            self.assertTrue(handler.result["complete"])
            self.assertEqual(json.loads(handler.result["text"])["type"], "turn.completed")


if __name__ == "__main__":
    unittest.main()
