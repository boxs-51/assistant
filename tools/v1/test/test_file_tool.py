import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.v1.file_tool as file_module
from tools.v1.file_tool import (
    DEFAULT_DANGER_PATTERNS,
    MAX_FILE_COUNT,
    MAX_QUERY_COUNT,
    MAX_REGEX_LINE_CHARS,
    MAX_TEXT_FILE_BYTES,
    FileTool,
    run,
)


class TestFileToolV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sample = self.root / "sample.txt"
        self.sample.write_text(
            "Line 1: Hello World\n"
            "Line 2: Python Code\n"
            "Line 3: File Tool Test\n"
            "Line 4: Hello Again",
            encoding="utf-8",
        )
        self.tool = FileTool()

    def tearDown(self):
        self.tmp.cleanup()

    def assert_ok(self, result, action):
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "file_tool")
        self.assertEqual(result["action"], action)
        self.assertEqual(result["meta"]["version"], "2.0.0")
        json.dumps(result)
        return result["data"]

    def assert_error(self, result, action, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["action"], action)
        self.assertEqual(result["error"]["code"], code)
        self.assertIsNone(result["data"])
        json.dumps(result)

    def test_read_full_and_line_pagination(self):
        data = self.assert_ok(self.tool.read(str(self.sample)), "read")
        self.assertIn("Hello World", data["content"])
        self.assertTrue(data["eof"])

        result = self.tool.read(str(self.sample), start_line=2, num_lines=2)
        data = self.assert_ok(result, "read")
        self.assertEqual(
            data["content"],
            "Line 2: Python Code\nLine 3: File Tool Test\n",
        )
        self.assertFalse(result["meta"]["truncated"])
        self.assertFalse(data["eof"])
        self.assertEqual(data["next_start_line"], 4)

    def test_read_max_chars_and_oversized_single_line(self):
        result = self.tool.read(str(self.sample), max_chars=25)
        data = self.assert_ok(result, "read")
        self.assertTrue(result["meta"]["truncated"])
        self.assertEqual(data["returned_line_count"], 1)
        self.assertEqual(data["next_start_line"], 2)

        long_file = self.root / "long.txt"
        long_file.write_text("x" * 100, encoding="utf-8")
        self.assert_error(
            self.tool.read(str(long_file), max_chars=10),
            "read",
            "OUTPUT_LIMIT_EXCEEDED",
        )

    def test_read_beyond_eof_and_invalid_arguments(self):
        data = self.assert_ok(self.tool.read(str(self.sample), start_line=999), "read")
        self.assertEqual(data["content"], "")
        self.assertTrue(data["eof"])
        for kwargs in (
            {"start_line": 0},
            {"start_line": True},
            {"num_lines": 0},
            {"num_lines": True},
            {"max_chars": 0},
            {"max_chars": True},
        ):
            with self.subTest(kwargs=kwargs):
                self.assert_error(
                    self.tool.read(str(self.sample), **kwargs),
                    "read",
                    "INVALID_ARGUMENT",
                )

    def test_read_missing_directory_encoding_decode_and_size_errors(self):
        self.assert_error(
            self.tool.read(str(self.root / "missing.txt")),
            "read",
            "FILE_NOT_FOUND",
        )
        self.assert_error(self.tool.read(str(self.root)), "read", "FILE_NOT_REGULAR")
        self.assert_error(
            self.tool.read(str(self.sample), encoding="definitely-not-a-codec"),
            "read",
            "FILE_ENCODING_INVALID",
        )

        bad = self.root / "bad.bin"
        bad.write_bytes(b"\xff")
        self.assert_error(self.tool.read(str(bad)), "read", "FILE_DECODE_ERROR")

        huge = self.root / "huge.txt"
        huge.write_bytes(b"x" * (MAX_TEXT_FILE_BYTES + 1))
        self.assert_error(self.tool.read(str(huge)), "read", "FILE_TOO_LARGE")

    def test_write_create_overwrite_no_change_and_empty_missing(self):
        target = self.root / "new.txt"
        data = self.assert_ok(self.tool.write(str(target), "abc"), "write")
        self.assertTrue(data["created"])
        self.assertTrue(data["changed"])
        self.assertEqual(target.read_text(encoding="utf-8"), "abc")

        data = self.assert_ok(self.tool.write(str(target), "abc"), "write")
        self.assertFalse(data["changed"])

        empty = self.root / "empty.txt"
        data = self.assert_ok(self.tool.write(str(empty), ""), "write")
        self.assertTrue(data["created"])
        self.assertTrue(empty.exists())
        self.assertEqual(empty.read_bytes(), b"")

    def test_append_and_empty_append_missing(self):
        data = self.assert_ok(
            self.tool.write(str(self.sample), "\nLine 5", mode="a"),
            "write",
        )
        self.assertTrue(data["changed"])
        self.assertTrue(self.sample.read_text(encoding="utf-8").endswith("Line 5"))

        empty = self.root / "append-empty.txt"
        data = self.assert_ok(self.tool.write(str(empty), "", mode="a"), "write")
        self.assertTrue(data["created"])
        self.assertEqual(empty.read_bytes(), b"")

    def test_existing_unreadable_snapshot_fails_closed(self):
        original = self.sample.read_bytes()
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            result = self.tool.write(str(self.sample), "replacement")
        self.assert_error(result, "write", "FILE_IO_ERROR")
        self.assertEqual(self.sample.read_bytes(), original)

    def test_invalid_existing_bytes_fail_closed_for_append(self):
        target = self.root / "bad.txt"
        target.write_bytes(b"\xffORIGINAL")
        original = target.read_bytes()
        result = self.tool.write(str(target), "tail", mode="a")
        self.assert_error(result, "write", "FILE_DECODE_ERROR")
        self.assertEqual(target.read_bytes(), original)

    def test_directory_and_symlink_mutation_rejected(self):
        self.assert_error(self.tool.write(str(self.root), "x"), "write", "FILE_NOT_REGULAR")
        link = self.root / "link.txt"
        try:
            link.symlink_to(self.sample)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        self.assert_error(
            self.tool.write(str(link), "x"),
            "write",
            "FILE_SYMLINK_MUTATION_BLOCKED",
        )
        data = self.assert_ok(self.tool.read(str(link)), "read")
        self.assertTrue(data["is_symlink"])

    def test_replace_failure_cleans_temp_file(self):
        target = self.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        with patch("tools.v1.file_tool.os.replace", side_effect=OSError("fail")):
            result = self.tool.write(str(target), "new")
        self.assert_error(result, "write", "FILE_IO_ERROR")
        leftovers = list(self.root.glob(f".{target.name}.tools-v1-*.tmp"))
        self.assertEqual(leftovers, [])

    @unittest.skipIf(os.name == "nt", "POSIX mode-bit assertion")
    def test_existing_mode_bits_preserved(self):
        target = self.root / "exec.sh"
        target.write_text("echo old\n", encoding="utf-8")
        target.chmod(0o751)
        self.assert_ok(self.tool.write(str(target), "echo new\n"), "write")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o751)

    def test_concurrent_change_is_detected(self):
        external = "EXTERNAL"

        def mutate(_info):
            self.sample.write_text(external, encoding="utf-8")
            return True

        tool = FileTool(confirm_callback=mutate)
        result = tool.write(str(self.sample), "ours")
        self.assert_error(result, "write", "FILE_CHANGED_DURING_OPERATION")
        self.assertEqual(self.sample.read_text(encoding="utf-8"), external)

    def test_confirmation_rejection_and_no_content_echo(self):
        secret = "SUPER_SECRET_TYPED_CONTENT"
        tool = FileTool(confirm_callback=lambda _info: False)
        before = self.sample.read_bytes()
        result = tool.write(str(self.sample), secret)
        self.assert_error(result, "write", "FILE_CHANGE_REJECTED")
        self.assertEqual(self.sample.read_bytes(), before)
        self.assertNotIn(secret, json.dumps(result))

    def test_danger_patterns_single_source_and_explicit_empty(self):
        self.assertIn(r"config/AGENT\.md$", DEFAULT_DANGER_PATTERNS)
        self.assertTrue(self.tool._is_dangerous_path("config/AGENT.md"))
        no_patterns = FileTool(danger_patterns=[])
        self.assertFalse(no_patterns._is_dangerous_path(".env"))

    def test_search_literal_regex_case_and_no_match(self):
        result = self.tool.search(str(self.sample), "Hello")
        data = self.assert_ok(result, "search")
        self.assertEqual(data["total_match_count"], 2)
        self.assertEqual(data["files"][0]["matches"][0]["line"], 1)

        result = self.tool.search(
            str(self.sample),
            r"line \d",
            use_regex=True,
            case_sensitive=False,
        )
        self.assertGreater(self.assert_ok(result, "search")["total_match_count"], 0)

        result = self.tool.search(str(self.sample), "hello", case_sensitive=True)
        self.assertEqual(self.assert_ok(result, "search")["total_match_count"], 0)

    def test_search_invalid_regex_and_limits(self):
        result = self.tool.search(str(self.sample), "[", use_regex=True)
        self.assert_error(result, "search", "FILE_REGEX_INVALID")
        self.assertEqual(result["error"]["details"], {"query_index": 0})

        for value in (0, -1, True, 501):
            with self.subTest(value=value):
                self.assert_error(
                    self.tool.search(
                        str(self.sample),
                        "Line",
                        max_results_per_file=value,
                    ),
                    "search",
                    "INVALID_ARGUMENT",
                )

    def test_search_report_truncation_does_not_corrupt_counts(self):
        result = self.tool.search(
            str(self.sample),
            "Line",
            max_results_per_file=2,
        )
        data = self.assert_ok(result, "search")
        self.assertEqual(data["total_match_count"], 4)
        self.assertEqual(data["returned_count"], 2)
        self.assertTrue(result["meta"]["truncated"])

    def test_search_duplicate_paths_and_file_count(self):
        self.assert_error(
            self.tool.search([str(self.sample), str(self.sample)], "Line"),
            "search",
            "INVALID_ARGUMENT",
        )
        too_many = [str(self.root / f"x{i}.txt") for i in range(MAX_FILE_COUNT + 1)]
        self.assert_error(
            self.tool.search(too_many, "x"),
            "search",
            "INVALID_ARGUMENT",
        )

    def test_search_samefile_alias_rejected_when_supported(self):
        alias = self.root / "alias.txt"
        try:
            alias.symlink_to(self.sample)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        self.assert_error(
            self.tool.search([str(self.sample), str(alias)], "Line"),
            "search",
            "INVALID_ARGUMENT",
        )

    def test_search_bounded_snippet_contains_match(self):
        target = self.root / "wide.txt"
        target.write_text("a" * 1500 + "NEEDLE" + "b" * 1500, encoding="utf-8")
        result = self.tool.search(str(target), "NEEDLE")
        data = self.assert_ok(result, "search")
        snippet = data["files"][0]["matches"][0]["text"]
        self.assertLessEqual(len(snippet), 2000)
        self.assertIn("NEEDLE", snippet)

    def test_replace_literal_regex_and_order(self):
        result = self.tool.replace(str(self.sample), "Hello", "Hi")
        data = self.assert_ok(result, "replace")
        self.assertEqual(data["total_replacements"], 2)
        self.assertIn("Hi World", self.sample.read_text(encoding="utf-8"))

        ordered = self.root / "ordered.txt"
        ordered.write_text("A", encoding="utf-8")
        result = self.tool.replace(
            str(ordered),
            ["A", "B"],
            ["B", "C"],
        )
        self.assert_ok(result, "replace")
        self.assertEqual(ordered.read_text(encoding="utf-8"), "C")

    def test_replace_report_limit_does_not_limit_mutation(self):
        target = self.root / "many.txt"
        target.write_text("Hello\nHello\nHello\n", encoding="utf-8")
        result = self.tool.replace(
            str(target),
            "Hello",
            "Hi",
            max_results_per_file=1,
        )
        data = self.assert_ok(result, "replace")
        self.assertEqual(data["total_replacements"], 3)
        self.assertEqual(data["reported_change_count"], 1)
        self.assertTrue(result["meta"]["truncated"])
        self.assertEqual(target.read_text(encoding="utf-8"), "Hi\nHi\nHi\n")

    def test_replace_mismatch_invalid_regex_and_preflight_are_zero_write(self):
        before = self.sample.read_bytes()
        self.assert_error(
            self.tool.replace(
                str(self.sample),
                ["A", "B"],
                ["X"],
            ),
            "replace",
            "INVALID_ARGUMENT",
        )
        self.assertEqual(self.sample.read_bytes(), before)

        self.assert_error(
            self.tool.replace(str(self.sample), "[", "x", use_regex=True),
            "replace",
            "FILE_REGEX_INVALID",
        )
        self.assertEqual(self.sample.read_bytes(), before)

        missing = self.root / "missing.txt"
        self.assert_error(
            self.tool.replace(
                [str(self.sample), str(missing)],
                "Line",
                "Changed",
            ),
            "replace",
            "FILE_NOT_FOUND",
        )
        self.assertEqual(self.sample.read_bytes(), before)

    def test_replace_all_confirmations_happen_before_commit(self):
        second = self.root / "second.txt"
        second.write_text("Hello", encoding="utf-8")
        before1 = self.sample.read_bytes()
        before2 = second.read_bytes()
        calls = []

        def confirm(info):
            calls.append(info["file_path"])
            return len(calls) < 2

        tool = FileTool(confirm_callback=confirm)
        result = tool.replace(
            [str(self.sample), str(second)],
            "Hello",
            "Hi",
        )
        self.assert_error(result, "replace", "FILE_CHANGE_REJECTED")
        self.assertEqual(self.sample.read_bytes(), before1)
        self.assertEqual(second.read_bytes(), before2)
        self.assertEqual(len(calls), 2)

    def test_replace_second_commit_failure_rolls_back_first(self):
        second = self.root / "second.txt"
        second.write_text("Hello second", encoding="utf-8")
        before1 = self.sample.read_bytes()
        before2 = second.read_bytes()
        original = self.tool._replace_bytes
        calls = {"n": 0}

        def fail_second(snapshot, final_bytes, *, preserve_mode):
            calls["n"] += 1
            if calls["n"] == 2:
                raise file_module._FileToolError("FILE_IO_ERROR", "injected failure")
            return original(snapshot, final_bytes, preserve_mode=preserve_mode)

        with patch.object(self.tool, "_replace_bytes", side_effect=fail_second):
            result = self.tool.replace(
                [str(self.sample), str(second)],
                "Hello",
                "Hi",
            )
        self.assert_error(result, "replace", "FILE_IO_ERROR")
        self.assertEqual(self.sample.read_bytes(), before1)
        self.assertEqual(second.read_bytes(), before2)
        self.assertIn(
            Path(os.path.abspath(self.sample)).as_posix(),
            result["error"]["details"]["rolled_back_paths"],
        )

    def test_rollback_does_not_overwrite_external_change(self):
        second = self.root / "second.txt"
        second.write_text("Hello second", encoding="utf-8")
        external = b"EXTERNAL AFTER FIRST COMMIT"
        first_canonical = Path(os.path.abspath(self.sample)).as_posix()
        original = self.tool._replace_bytes
        calls = {"n": 0}

        def fail_second_with_external_change(snapshot, final_bytes, *, preserve_mode):
            calls["n"] += 1
            if calls["n"] == 2:
                self.sample.write_bytes(external)
                raise file_module._FileToolError("FILE_IO_ERROR", "injected failure")
            return original(snapshot, final_bytes, preserve_mode=preserve_mode)

        with patch.object(
            self.tool,
            "_replace_bytes",
            side_effect=fail_second_with_external_change,
        ):
            result = self.tool.replace(
                [str(self.sample), str(second)],
                "Hello",
                "Hi",
            )
        self.assert_error(result, "replace", "FILE_IO_ERROR")
        self.assertEqual(self.sample.read_bytes(), external)
        self.assertIn(
            first_canonical,
            result["error"]["details"]["rollback_conflicts"],
        )

    def test_global_run_and_alias_return_toolresult(self):
        result = run(action="read", file_path=str(self.sample))
        self.assert_ok(result, "read")


    def test_write_size_limits_are_fail_closed(self):
        target = self.root / "small.txt"
        target.write_text("aaaa", encoding="utf-8")
        original = target.read_bytes()
        with patch.object(file_module, "MAX_TEXT_FILE_BYTES", 8), patch.object(
            file_module, "MAX_WRITE_CONTENT_BYTES", 8
        ):
            self.assert_error(
                self.tool.write(str(self.root / "too-big.txt"), "x" * 9),
                "write",
                "FILE_TOO_LARGE",
            )
            self.assert_error(
                self.tool.write(str(target), "12345", mode="a"),
                "write",
                "FILE_TOO_LARGE",
            )
        self.assertEqual(target.read_bytes(), original)

    def test_crlf_newlines_are_preserved_by_replace(self):
        target = self.root / "crlf.txt"
        target.write_bytes(b"A\r\nB\r\n")
        result = self.tool.replace(str(target), "B", "C")
        self.assert_ok(result, "replace")
        self.assertEqual(target.read_bytes(), b"A\r\nC\r\n")

    def test_search_query_count_and_aggregate_cap(self):
        too_many_queries = ["x"] * (MAX_QUERY_COUNT + 1)
        self.assert_error(
            self.tool.search(str(self.sample), too_many_queries),
            "search",
            "INVALID_ARGUMENT",
        )

        target = self.root / "aggregate.txt"
        target.write_text("x\nx\nx\nx\nx\n", encoding="utf-8")
        with patch.object(file_module, "MAX_TOTAL_RETURNED_MATCHES", 3):
            result = self.tool.search(
                str(target),
                "x",
                max_results_per_file=5,
            )
        data = self.assert_ok(result, "search")
        self.assertEqual(data["total_match_count"], 5)
        self.assertEqual(data["returned_count"], 3)
        self.assertTrue(result["meta"]["truncated"])

    def test_search_decode_failure_is_structured(self):
        target = self.root / "search-bad.bin"
        target.write_bytes(b"\xff")
        self.assert_error(
            self.tool.search(str(target), "x"),
            "search",
            "FILE_DECODE_ERROR",
        )

    def test_regex_replace_backreference_and_invalid_replacement(self):
        target = self.root / "regex.txt"
        target.write_text("abc123", encoding="utf-8")
        result = self.tool.replace(
            str(target),
            r"(\d+)",
            r"[\1]",
            use_regex=True,
        )
        self.assert_ok(result, "replace")
        self.assertEqual(target.read_text(encoding="utf-8"), "abc[123]")

        target.write_text("a", encoding="utf-8")
        before = target.read_bytes()
        result = self.tool.replace(
            str(target),
            r"(a)",
            r"\2",
            use_regex=True,
        )
        self.assert_error(result, "replace", "FILE_REGEX_INVALID")
        self.assertEqual(result["error"]["details"], {"query_index": 0})
        self.assertEqual(target.read_bytes(), before)

        target.write_text("no matching token", encoding="utf-8")
        before = target.read_bytes()
        result = self.tool.replace(
            str(target),
            r"(a)",
            r"\2",
            use_regex=True,
        )
        self.assert_error(result, "replace", "FILE_REGEX_INVALID")
        self.assertEqual(result["error"]["details"], {"query_index": 0})
        self.assertEqual(target.read_bytes(), before)

    def test_replace_no_match_and_result_size_overflow_are_zero_write(self):
        target = self.root / "nomatch.txt"
        target.write_text("abc", encoding="utf-8")
        result = self.tool.replace(str(target), "z", "x")
        data = self.assert_ok(result, "replace")
        self.assertEqual(data["changed_files"], 0)
        self.assertEqual(data["total_replacements"], 0)
        self.assertFalse(data["files"][0]["changed"])

        target.write_text("aaaa", encoding="utf-8")
        before = target.read_bytes()
        with patch.object(file_module, "MAX_TEXT_FILE_BYTES", 16):
            result = self.tool.replace(str(target), "a", "12345")
        self.assert_error(result, "replace", "FILE_TOO_LARGE")
        self.assertEqual(target.read_bytes(), before)

    def test_replace_symlink_member_causes_zero_mutation(self):
        first = self.root / "first.txt"
        first.write_text("Hello first", encoding="utf-8")
        link = self.root / "link-replace.txt"
        try:
            link.symlink_to(self.sample)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")

        before_first = first.read_bytes()
        before_target = self.sample.read_bytes()
        result = self.tool.replace(
            [str(first), str(link)],
            "Hello",
            "Hi",
        )
        self.assert_error(result, "replace", "FILE_SYMLINK_MUTATION_BLOCKED")
        self.assertEqual(first.read_bytes(), before_first)
        self.assertEqual(self.sample.read_bytes(), before_target)

    def test_transformed_line_hard_limit_is_enforced_before_large_materialization(self):
        target = self.root / "expanded.txt"
        target.write_text("aaaaa", encoding="utf-8")
        before = target.read_bytes()
        with patch.object(file_module, "MAX_REGEX_LINE_CHARS", 8):
            result = self.tool.replace(
                str(target),
                "a",
                "aa",
            )
        self.assert_error(result, "replace", "OUTPUT_LIMIT_EXCEEDED")
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(MAX_REGEX_LINE_CHARS, 1_000_000)

    def test_write_rejects_oversized_character_input_before_encoding(self):
        target = self.root / "preencode.txt"
        with patch.object(file_module, "MAX_WRITE_CONTENT_BYTES", 8), patch.object(
            self.tool,
            "_encode_text",
            side_effect=AssertionError("encode must not be reached"),
        ):
            result = self.tool.write(str(target), "x" * 9)
        self.assert_error(result, "write", "FILE_TOO_LARGE")
        self.assertFalse(target.exists())

    def test_aggregate_file_budget_is_fail_closed(self):
        first = self.root / "aggregate-a.txt"
        second = self.root / "aggregate-b.txt"
        first.write_text("aaaa", encoding="utf-8")
        second.write_text("bbbb", encoding="utf-8")
        before_first = first.read_bytes()
        before_second = second.read_bytes()

        with patch.object(file_module, "MAX_TOTAL_FILE_BYTES", 7):
            result = self.tool.search([str(first), str(second)], "a")
            self.assert_error(result, "search", "FILE_TOO_LARGE")

            result = self.tool.replace(
                [str(first), str(second)],
                ["a", "b"],
                ["x", "y"],
            )
            self.assert_error(result, "replace", "FILE_TOO_LARGE")

        self.assertEqual(first.read_bytes(), before_first)
        self.assertEqual(second.read_bytes(), before_second)

    def test_occurrence_budget_bounds_search_and_replace(self):
        target = self.root / "occurrences.txt"
        target.write_text("abcd", encoding="utf-8")
        before = target.read_bytes()

        with patch.object(file_module, "MAX_TOTAL_MATCH_OCCURRENCES", 3):
            result = self.tool.search(
                str(target),
                r"(?=)",
                use_regex=True,
            )
            self.assert_error(result, "search", "OUTPUT_LIMIT_EXCEEDED")

            result = self.tool.replace(
                str(target),
                r"(?=)",
                "x",
                use_regex=True,
            )
            self.assert_error(result, "replace", "OUTPUT_LIMIT_EXCEEDED")

        self.assertEqual(target.read_bytes(), before)

    def test_backreference_expansion_guard_is_conservative_and_fail_closed(self):
        target = self.root / "backref-budget.txt"
        target.write_text("abcdefghij", encoding="utf-8")
        before = target.read_bytes()

        with patch.object(file_module, "MAX_REGEX_LINE_CHARS", 15):
            result = self.tool.replace(
                str(target),
                r"(abcdefghij)",
                r"\1\1",
                use_regex=True,
            )

        self.assert_error(result, "replace", "OUTPUT_LIMIT_EXCEEDED")
        self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
