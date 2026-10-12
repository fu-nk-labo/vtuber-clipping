"""Deterministic adapters/validation tests. Fake models are NOT real inference QA."""
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clipbeta import local_ai as ai
from clipbeta.engine import load_captions


ROWS = [
    {"start_ms": 1000, "end_ms": 4000, "text": "I thought this was the easy level."},
    {"start_ms": 4500, "end_ms": 8000, "text": "It says final boss right there!"},
    {"start_ms": 9000, "end_ms": 12000, "text": "Oh. That explains everything."},
]


def candidate(cues=None, score=90):
    cues = cues or ai.transcript_cues(ROWS)
    return {"title": "The wrong level", "reason": "The setup is overturned by the boss reveal.",
            "start_cue_id": cues[0]["id"], "end_cue_id": cues[-1]["id"], "score": score,
            "evidence": [{"cue_id": c["id"], "quote": c["text"]} for c in (cues[0], cues[-1])]}


class FakeTransport:
    def __init__(self, response=None, model=None, show=None):
        self.calls = []
        self.model = model or {"name": "tiny-local:latest", "size": 1234, "digest": "synthetic-digest",
                               "details": {"format": "gguf"}}
        self.show = show or {"details": {"format": "gguf"}, "model_info": {"general.architecture": "qwen3"},
                             "capabilities": ["completion"], "thinking": {"values": [True, False]}}
        self.response = response or {"done": True, "done_reason": "stop",
                                     "response": json.dumps({"candidates": [candidate()]})}

    def request(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(payload), kwargs))
        if path == "/api/tags":
            return {"models": [self.model]}
        if path == "/api/show":
            return self.show
        if path == "/api/generate":
            return self.response
        raise AssertionError(path)


class FakeWhisper:
    def __init__(self, segments=None):
        self.model = self
        self.unloaded = False
        self.calls = []
        self.segments = segments or [SimpleNamespace(start=1, end=2, text=" Synthetic speech ")]

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return iter(self.segments), SimpleNamespace(language="en")

    def unload_model(self):
        self.unloaded = True


class ConfigTests(unittest.TestCase):
    def test_defaults_explicit_cpu_int8(self):
        c = ai.AnalysisConfig.from_value({"whisper_model_dir": "/local/model", "ollama_model": "tiny-local:latest"})
        self.assertEqual((c.device, c.compute_type, c.allow_cpu_fallback), ("cpu", "int8", False))
        self.assertEqual(c.max_context_chars, 2500)

    def test_small_context_budget_is_supported_and_bounded(self):
        c = ai.AnalysisConfig.from_value({"transcript_path": "x", "ollama_model": "local", "max_context_chars": 1000})
        self.assertEqual(c.max_context_chars, 1000)
        for size in (999, 6001):
            with self.assertRaises(ai.LocalAIError):
                ai.AnalysisConfig.from_value({"transcript_path": "x", "ollama_model": "local", "max_context_chars": size})

    def test_missing_local_input_and_unknown_fields_fail(self):
        for cfg in ({"ollama_model": "local"}, {"ollama_model": "local", "transcript_path": "x", "ollama_url": "https://example.com"}):
            with self.assertRaises(ai.LocalAIError):
                ai.AnalysisConfig.from_value(cfg)

    def test_remote_and_cloud_names_fail(self):
        for name in ("qwen:cloud", "https://example.com/model", "foo-http", "../model", "", []):
            with self.subTest(name=name), self.assertRaises(ai.LocalAIError):
                ai.AnalysisConfig.from_value({"transcript_path": "x.srt", "ollama_model": name})

    def test_invalid_device_compute_and_numeric_types(self):
        for field, value in (("device", "auto"), ("compute_type", "float16"), ("compute_type", []),
                             ("audio_chunk_seconds", True), ("max_candidates", 0),
                             ("context_window_seconds", 60), ("allow_cpu_fallback", "yes"),
                             ("llm_backend", "cloud")):
            with self.subTest(field=field, value=value), self.assertRaises(ai.LocalAIError):
                ai.AnalysisConfig.from_value({"transcript_path": "x.srt", "ollama_model": "local", field: value})

    def test_glossary_bounds(self):
        for glossary in ("name", [""], ["x" * 101], ["x"] * 101, ["x" * 100] * 21, ["bad\x00text"]):
            with self.subTest(glossary=glossary), self.assertRaises(ai.ValidationError):
                ai.AnalysisConfig.from_value({"transcript_path": "x.srt", "ollama_model": "local", "glossary": glossary})


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.cues = ai.transcript_cues(ROWS)

    def test_valid_candidate_uses_source_timestamps_and_stable_ids(self):
        result = ai.validate_candidate(candidate(self.cues), self.cues)
        self.assertEqual((result["start_ms"], result["end_ms"]), (1000, 12000))
        self.assertTrue(result["id"].startswith("candidate_"))
        self.assertEqual(result, ai.validate_candidate(candidate(self.cues), self.cues))

    def test_unknown_ids_reversed_order_and_extra_fields_rejected(self):
        for field, value in (("start_cue_id", "invented"), ("end_cue_id", []), ("start_ms", 0),
                             ("score", True), ("score", 101), ("title", "x" * 121)):
            item = candidate(self.cues)
            item[field] = value
            with self.subTest(field=field), self.assertRaises(ai.ValidationError):
                ai.validate_candidate(item, self.cues)
        item = candidate(self.cues)
        item["start_cue_id"], item["end_cue_id"] = item["end_cue_id"], item["start_cue_id"]
        with self.assertRaises(ai.LocalAIError):
            ai.validate_candidate(item, self.cues)

    def test_quote_must_match_entire_actual_cue(self):
        for quote in ("Invented quote", "I thought", "I thought this was the easy level. ", None):
            item = candidate(self.cues)
            item["evidence"][0]["quote"] = quote
            with self.subTest(quote=quote), self.assertRaises(ai.LocalAIError):
                ai.validate_candidate(item, self.cues)

    def test_evidence_requires_boundaries_and_no_duplicates(self):
        for evidence in ([], candidate()["evidence"][:1], candidate()["evidence"] * 2,
                         [{"cue_id": "not-in-context", "quote": "made up"}]):
            item = candidate()
            item["evidence"] = evidence
            with self.subTest(evidence=evidence), self.assertRaises(ai.LocalAIError):
                ai.validate_candidate(item, self.cues)

    def test_evidence_outside_range_rejected(self):
        item = candidate(self.cues)
        item["end_cue_id"] = self.cues[1]["id"]
        with self.assertRaises(ai.LocalAIError):
            ai.validate_candidate(item, self.cues)

    def test_sixty_second_limit_inclusive(self):
        rows = copy.deepcopy(ROWS)
        rows[-1]["end_ms"] = 61000
        cues = ai.transcript_cues(rows)
        self.assertEqual(ai.validate_candidate(candidate(cues), cues)["end_ms"], 61000)
        rows[-1]["end_ms"] += 1
        cues = ai.transcript_cues(rows)
        with self.assertRaises(ai.LocalAIError):
            ai.validate_candidate(candidate(cues), cues)

    def test_dynamic_schema_binds_both_boundaries_to_exact_evidence(self):
        schema = ai.candidate_schema(self.cues)
        variants = schema["properties"]["candidates"]["items"]["oneOf"]
        self.assertEqual(len(variants), 6)
        for variant in variants:
            props = variant["properties"]
            raw = {"title": "Selected title", "reason": "Selected contextual reason", "score": 85,
                   **{field: props[field]["const"] for field in ("start_cue_id", "end_cue_id", "evidence")}}
            ai.validate_candidate(raw, self.cues)
        self.assertLess(len(json.dumps(schema).encode()), 192 * 1024)

    def test_dynamic_schema_excludes_long_ranges_and_caps_cue_count(self):
        rows = copy.deepcopy(ROWS)
        rows[-1]["end_ms"] = 90000
        cues = ai.transcript_cues(rows)
        schema = ai.candidate_schema(cues)
        for variant in schema["properties"]["candidates"]["items"]["oneOf"]:
            self.assertNotEqual(variant["properties"]["end_cue_id"]["const"], cues[-1]["id"])
        with self.assertRaises(ai.LocalAIError):
            ai.candidate_schema(self.cues * 6)

    def test_regression_mid_sentence_trailing_i_cannot_be_an_endpoint(self):
        rows = [{"start_ms": 0, "end_ms": 9200, "text": "The diamond is still in my hand, I"},
                {"start_ms": 9200, "end_ms": 18540, "text": "have been holding it this whole time."}]
        cues = ai.transcript_cues(rows)
        self.assertIsNone(cues[0]["end_boundary"])
        self.assertIsNone(cues[1]["start_boundary"])
        variants = ai.candidate_schema(cues)["properties"]["candidates"]["items"]["oneOf"]
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]["properties"]["end_cue_id"]["const"], cues[1]["id"])
        bad = candidate(cues)
        bad["end_cue_id"] = cues[0]["id"]
        bad["evidence"] = bad["evidence"][:1]
        with self.assertRaisesRegex(ai.LocalAIError, "continuous speech"):
            ai.validate_candidate(bad, cues)
        self.assertEqual(ai.validate_candidate(candidate(cues), cues)["end_ms"], 18540)

    def test_japanese_terminators_and_closing_quotes(self):
        for text in ("見つけました。", "本当？", "やった！", "「ありました！」", '"It was here!"', "Really?”"):
            with self.subTest(text=text):
                self.assertTrue(ai._sentence_end(text))
        for text in ("まだ探して", "The diamond, I", "Well...", "そうかな…", "待って、"):
            with self.subTest(text=text):
                self.assertFalse(ai._sentence_end(text))
        rows = [{"start_ms": 0, "end_ms": 1000, "text": "ずっと探していたら"},
                {"start_ms": 1000, "end_ms": 2000, "text": "「手の中にありました！」"},
                {"start_ms": 2000, "end_ms": 3000, "text": "ずっと持っていたんですね。"}]
        cues = ai.transcript_cues(rows)
        self.assertIsNone(cues[0]["end_boundary"])
        self.assertEqual(cues[1]["end_boundary"], "sentence")
        self.assertEqual(cues[2]["start_boundary"], "sentence")
        self.assertEqual(len(ai.candidate_schema(cues)["properties"]["candidates"]["items"]["oneOf"]), 3)

    def test_real_pause_is_eligible_without_moving_timestamps(self):
        rows = [{"start_ms": 0, "end_ms": 1000, "text": "A phrase"},
                {"start_ms": 1800, "end_ms": 2400, "text": "another phrase"},
                {"start_ms": 2500, "end_ms": 3100, "text": "last words"}]
        cues = ai.transcript_cues(rows)
        self.assertEqual((cues[0]["end_boundary"], cues[1]["start_boundary"]), ("pause", "pause"))
        self.assertIsNone(cues[1]["end_boundary"])
        self.assertEqual(ai.validate_candidate(candidate(cues[1:]), cues)["start_ms"], 1800)
        rows[1]["start_ms"] -= 1
        cues = ai.transcript_cues(rows)
        self.assertIsNone(cues[0]["end_boundary"])
        self.assertIsNone(cues[1]["start_boundary"])

    def test_window_edges_never_create_fake_source_boundaries(self):
        rows = [{"start_ms": i * 1000, "end_ms": (i + 1) * 1000, "text": "continuous words"} for i in range(40)]
        cues = ai.transcript_cues(rows)
        windows = ai.context_windows(cues)
        self.assertGreater(len(windows), 1)
        for window in windows:
            self.assertEqual(ai.candidate_schema(window)["properties"]["candidates"]["maxItems"], 0)
            if window[0]["id"] != cues[0]["id"]:
                self.assertIsNone(window[0]["start_boundary"])
            if window[-1]["id"] != cues[-1]["id"]:
                self.assertIsNone(window[-1]["end_boundary"])

    def test_dedup_prefers_higher_score_and_sorts(self):
        low = ai.validate_candidate(candidate(score=80), self.cues)
        high = ai.validate_candidate(candidate(score=95), self.cues)
        near = dict(high, id="other", start_ms=1100, score=90)
        separate = dict(high, id="separate", start_ms=20000, end_ms=30000, score=85)
        self.assertEqual(ai.deduplicate_candidates([low, near, separate, high]), [high, separate])
        self.assertEqual(ai.deduplicate_candidates([low, separate, high], limit=1), [high])

    def test_context_windows_are_bounded_overlapping_and_cover_every_cue(self):
        rows = [{"start_ms": i * 10000, "end_ms": i * 10000 + 5000, "text": "dialogue" * 8} for i in range(50)]
        cues = ai.transcript_cues(rows)
        windows = ai.context_windows(cues, seconds=180, max_chars=4500)
        self.assertEqual({c["id"] for w in windows for c in w}, {c["id"] for c in cues})
        for window in windows:
            self.assertLessEqual(sum(len(json.dumps(c, ensure_ascii=False)) + 2 for c in window), 4500)
            self.assertLessEqual(window[-1]["end_ms"] - window[0]["start_ms"], 180000)
        self.assertTrue(set(c["id"] for c in windows[0]) & set(c["id"] for c in windows[1]))

    def test_context_character_budget_does_not_truncate_quotes(self):
        rows = [{"start_ms": i * 1000, "end_ms": i * 1000 + 500, "text": "あ" * 2000} for i in range(8)]
        windows = ai.context_windows(ai.transcript_cues(rows), max_chars=4500)
        self.assertTrue(all(c["text"] == "あ" * 2000 for w in windows for c in w))
        self.assertTrue(all(len(w) <= 2 for w in windows))

    def test_dense_context_does_not_slide_one_cue_at_a_time(self):
        rows = [{"start_ms": i * 100, "end_ms": i * 100 + 80, "text": "Very fast dialogue."} for i in range(100)]
        windows = ai.context_windows(ai.transcript_cues(rows), max_chars=2500)
        self.assertLess(len(windows), 20)
        self.assertEqual({c["id"] for w in windows for c in w}, {c["id"] for c in ai.transcript_cues(rows)})

    def test_json_rejects_duplicate_keys_nan_and_fences(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '```json\n{}\n```'):
            with self.subTest(raw=raw), self.assertRaises(ai.LocalAIError):
                ai._strict_json(raw)


class OllamaTests(unittest.TestCase):
    def test_requires_tags_and_show_before_any_transcript(self):
        transport = FakeTransport()
        result = ai.OllamaClient(transport).generate("tiny-local:latest", ai.transcript_cues(ROWS), ["VTuber"])
        self.assertIn("candidates", result)
        self.assertEqual([c[1] for c in transport.calls], ["/api/tags", "/api/show", "/api/generate"])
        payload = transport.calls[-1][2]
        self.assertEqual(payload["format"], ai.candidate_schema(ai.transcript_cues(ROWS)))
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(payload["options"]["num_predict"], 1024)
        self.assertEqual(payload["options"]["num_thread"], 4)
        self.assertEqual(json.loads(payload["prompt"])["glossary"], ["VTuber"])

    def test_missing_model_never_generates_or_downloads(self):
        transport = FakeTransport()
        with self.assertRaises(ai.LocalAIError):
            ai.OllamaClient(transport).generate("absent", ai.transcript_cues(ROWS), [])
        self.assertEqual([c[1] for c in transport.calls], ["/api/tags"])

    def test_remote_alias_rejected_at_tags(self):
        for field in ("remote_host", "remote_model"):
            transport = FakeTransport()
            transport.model[field] = "remote"
            with self.subTest(field=field), self.assertRaises(ai.LocalAIError):
                ai.OllamaClient(transport).generate("tiny-local:latest", ai.transcript_cues(ROWS), [])
            self.assertEqual([c[1] for c in transport.calls], ["/api/tags"])

    def test_remote_alias_rejected_at_show(self):
        transport = FakeTransport()
        transport.show["remote_host"] = "https://example.com"
        with self.assertRaises(ai.LocalAIError):
            ai.OllamaClient(transport).generate("tiny-local:latest", ai.transcript_cues(ROWS), [])
        self.assertNotIn("/api/generate", [c[1] for c in transport.calls])

    def test_missing_weights_or_unsupported_thinking_rejected(self):
        transport = FakeTransport()
        transport.model["size"] = 0
        with self.assertRaises(ai.LocalAIError):
            ai.OllamaClient(transport).require_installed("tiny-local:latest")
        transport = FakeTransport()
        transport.show["thinking"] = {"values": ["low", "high"]}
        with self.assertRaises(ai.LocalAIError):
            ai.OllamaClient(transport).require_installed("tiny-local:latest")

    def test_invalid_incomplete_or_remote_response_rejected(self):
        for response in ({"done": False, "response": "{}"}, {"done": True, "response": "{bad}"},
                         {"done": True, "done_reason": "length", "response": "{}"},
                         {"done": True, "remote_model": "foo", "response": "{}"}):
            with self.subTest(response=response), self.assertRaises(ai.LocalAIError):
                ai.OllamaClient(FakeTransport(response=response)).generate("tiny-local:latest", ai.transcript_cues(ROWS), [])

    def test_invalid_candidates_discarded_no_rule_fallback(self):
        valid = candidate()
        invalid = candidate()
        invalid["evidence"][0]["quote"] = "hallucination"
        client = Mock()
        client.generate.return_value = {"candidates": [valid, invalid]}
        config = ai.AnalysisConfig(ollama_model="local", transcript_path="x")
        result, metadata = ai.rank_transcript(ROWS, config, client=client)
        self.assertEqual(len(result), 1)
        self.assertEqual(metadata["rejected_candidates"], 1)
        client.generate.return_value = {"candidates": [invalid]}
        self.assertEqual(ai.rank_transcript(ROWS, config, client=client)[0], [])

    def test_short_unpunctuated_source_remains_usable_with_explicit_warning(self):
        rows = [{"start_ms": 0, "end_ms": 1000, "text": "one continuous"},
                {"start_ms": 1000, "end_ms": 2200, "text": "short statement"}]
        client = Mock()
        client.generate.return_value = {"candidates": [candidate(ai.transcript_cues(rows))]}
        result, metadata = ai.rank_transcript(rows, ai.AnalysisConfig(ollama_model="local"), client=client)
        self.assertEqual(len(result), 1)
        self.assertTrue(any("without sentence punctuation" in w for w in metadata["warnings"]))
        self.assertEqual((result[0]["start_ms"], result[0]["end_ms"]), (0, 2200))

    def test_long_continuous_source_does_not_invent_a_cut_or_run_model(self):
        rows = [{"start_ms": 0, "end_ms": 31000, "text": "continuous sentence"},
                {"start_ms": 31000, "end_ms": 62000, "text": "still going without a break"}]
        client = Mock()
        result, metadata = ai.rank_transcript(rows, ai.AnalysisConfig(ollama_model="local"), client=client)
        self.assertEqual(result, [])
        self.assertEqual(metadata["skipped_boundary_windows"], 1)
        self.assertTrue(metadata["warnings"])
        client.generate.assert_not_called()

    def test_pause_based_candidates_are_labelled_as_heuristic(self):
        rows = [{"start_ms": 0, "end_ms": 1000, "text": "opening statement"},
                {"start_ms": 1800, "end_ms": 2400, "text": "second statement"},
                {"start_ms": 2500, "end_ms": 3100, "text": "the final words"}]
        cues = ai.transcript_cues(rows)
        client = Mock()
        client.generate.return_value = {"candidates": [candidate(cues[1:])]}
        result, metadata = ai.rank_transcript(rows, ai.AnalysisConfig(ollama_model="local"), client=client)
        self.assertEqual(len(result), 1)
        self.assertTrue(any("800ms" in w for w in metadata["warnings"]))


class LoopbackTransportTests(unittest.TestCase):
    def connection(self, status=200, raw=b'{"models":[]}'):
        sock = Mock()
        response = Mock(status=status)
        response.read.return_value = raw
        connection = Mock(sock=sock)
        connection.getresponse.return_value = response
        return connection

    def test_fixed_loopback_ignores_proxy_environment(self):
        connection = self.connection()
        with patch.dict(os.environ, {"HTTP_PROXY": "http://example.com:9999", "OLLAMA_HOST": "https://example.com"}), \
                patch.object(ai.http.client, "HTTPConnection", return_value=connection) as factory:
            self.assertEqual(ai.LoopbackTransport().request("GET", "/api/tags"), {"models": []})
        factory.assert_called_once_with("127.0.0.1", 11434, timeout=300)
        self.assertEqual(connection.request.call_args.args[:2], ("GET", "/api/tags"))

    def test_redirects_are_errors_not_followed(self):
        connection = self.connection(status=302)
        with patch.object(ai.http.client, "HTTPConnection", return_value=connection) as factory, \
                self.assertRaisesRegex(ai.LocalAIError, "redirects"):
            ai.LoopbackTransport().request("GET", "/api/tags")
        factory.assert_called_once()
        self.assertEqual(connection.request.call_count, 1)

    def test_arbitrary_endpoint_or_server_not_allowed(self):
        with self.assertRaises(ai.LocalAIError):
            ai.LoopbackTransport("https://example.com")
        for path in ("https://example.com", "/api/pull", "/api/tags?url=evil", "//evil"):
            with self.subTest(path=path), self.assertRaises(ai.LocalAIError):
                ai.LoopbackTransport().request("GET", path)

    def test_oversized_response_rejected(self):
        connection = self.connection(raw=b" " * (ai.MAX_RESPONSE_BYTES + 1))
        with patch.object(ai.http.client, "HTTPConnection", return_value=connection), self.assertRaises(ai.LocalAIError):
            ai.LoopbackTransport().request("GET", "/api/tags")

    def test_cancel_interrupts_blocked_response_socket(self):
        started, stopped, cancel = threading.Event(), threading.Event(), threading.Event()
        connection = self.connection()
        socket_reference = connection.sock
        socket_reference.shutdown.side_effect = lambda _: stopped.set()

        def response():
            started.set()
            cancel.set()
            # Simulate http.client detaching the connection socket for Connection:close.
            connection.sock = None
            stopped.wait(2)
            raise OSError("closed")

        connection.getresponse.side_effect = response
        before = time.monotonic()
        with patch.object(ai.http.client, "HTTPConnection", return_value=connection), self.assertRaises(ai.AnalysisCancelled):
            ai.LoopbackTransport().request("GET", "/api/tags", cancelled=cancel.is_set)
        self.assertTrue(started.is_set())
        self.assertTrue(stopped.is_set())
        self.assertLess(time.monotonic() - before, 1)


class LlamaCppTests(unittest.TestCase):
    def test_fixed_llama_port_and_whitelist(self):
        connection = LoopbackTransportTests().connection(raw=b'{"data":[]}')
        with patch.object(ai.http.client, "HTTPConnection", return_value=connection) as factory:
            ai.LoopbackTransport("llama_cpp").request("GET", "/v1/models")
        factory.assert_called_once_with("127.0.0.1", 8080, timeout=300)
        with self.assertRaises(ai.LocalAIError):
            ai.LoopbackTransport("llama_cpp").request("POST", "/models")

    def test_existing_local_single_model_required_and_structured_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / "test.gguf"
            model.write_bytes(b"synthetic-test-not-a-model")
            transport = Mock()
            transport.request.side_effect = [
                {"data": [{"id": "clipbeta-local"}]}, {"model_path": str(model)},
                {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"candidates": []})}}]},
            ]
            result = ai.LlamaCppClient(transport).generate("clipbeta-local", ai.transcript_cues(ROWS), [])
            self.assertEqual(result, {"candidates": []})
            payload = transport.request.call_args.args[2]
            self.assertEqual(payload["response_format"]["type"], "json_object")
            self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
            self.assertEqual(payload["max_tokens"], 1024)

    def test_router_and_remote_or_missing_paths_rejected(self):
        for models, props in (([{"id": "a"}, {"id": "b"}], {}),
                              ([{"id": "a"}], {"model_path": "https://example.com/model.gguf"}),
                              ([{"id": "a"}], {"model_path": "/not-existing.gguf"})):
            transport = Mock()
            transport.request.side_effect = [{"data": models}, props]
            with self.subTest(props=props), self.assertRaises(ai.LocalAIError):
                ai.LlamaCppClient(transport).require_installed("a")


class ASRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.model_dir = self.base / "whisper"
        self.model_dir.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (self.model_dir / name).write_text("synthetic test file")
        self.config = ai.AnalysisConfig(whisper_model_dir=str(self.model_dir), ollama_model="local", language="en",
                                        audio_chunk_seconds=30, glossary=("Miko", "Suisei"))

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def extract(media, start, length, path, cancelled):
        path.write_bytes(b"synthetic fake audio")

    def test_missing_tokenizer_blocks_runtime_import_and_download_fallback(self):
        (self.model_dir / "tokenizer.json").unlink()
        with patch.object(ai.importlib, "import_module") as runtime, self.assertRaisesRegex(ai.LocalAIError, "tokenizer.json"):
            ai._load_whisper_model(self.config)
        runtime.assert_not_called()

    def test_local_vocabulary_required_and_both_supported_formats_allowed(self):
        (self.model_dir / "vocabulary.txt").unlink()
        with self.assertRaisesRegex(ai.LocalAIError, "vocabulary"):
            ai.validate_whisper_directory(self.model_dir)
        (self.model_dir / "vocabulary.json").write_text("[]")
        self.assertEqual(ai.validate_whisper_directory(self.model_dir), self.model_dir)

    def test_model_names_are_not_downloadable_directory_inputs(self):
        with patch.object(ai.importlib, "import_module") as runtime, self.assertRaises(ai.LocalAIError):
            ai._load_whisper_model(ai.replace(self.config, whisper_model_dir="https://example.com/model"))
        runtime.assert_not_called()

    def test_runtime_load_is_local_files_only_and_explicit_compute(self):
        factory = Mock(return_value=FakeWhisper())
        with patch.object(ai.importlib, "import_module", return_value=SimpleNamespace(WhisperModel=factory)) as imports:
            ai._load_whisper_model(self.config)
        factory.assert_called_once_with(str(self.model_dir), device="cpu", compute_type="int8", local_files_only=True, num_workers=1, cpu_threads=4)
        imports.assert_called_once_with("faster_whisper")

    def test_long_cues_split_only_at_real_word_timestamps(self):
        terms = [" A synthetic", " story about", " the mystery", " of a missing", " diamond", " and a funny", " reveal."]
        words = [SimpleNamespace(start=i, end=i + .8, word=term) for i, term in enumerate(terms)]
        segment = SimpleNamespace(start=0, end=7, text="".join(terms), words=words)
        rows = ai._segment_rows(segment, 30000, 30000)
        self.assertTrue(all(len(r["text"]) <= 36 for r in rows))
        self.assertGreater(len(rows), 1)
        self.assertEqual(" ".join(r["text"] for r in rows), segment.text.strip())
        self.assertTrue(all(r["start_ms"] in {30000 + round(w.start * 1000) for w in words} for r in rows))
        self.assertTrue(all(r["end_ms"] in {30000 + round(w.end * 1000) for w in words} for r in rows))

    def test_word_timed_caption_split_preserves_sentence_ending(self):
        terms = [" A short sentence.", " Then a second", " sentence continues", " long enough to split."]
        words = [SimpleNamespace(start=i, end=i + .8, word=term) for i, term in enumerate(terms)]
        rows = ai._segment_rows(SimpleNamespace(start=0, end=4, text="".join(terms), words=words), 10000, 30000)
        self.assertEqual(rows[0], {"start_ms": 10000, "end_ms": 10800, "text": "A short sentence."})
        self.assertTrue(all(len(r["text"]) <= 36 for r in rows))

    def test_tiny_japanese_suffix_merge_preserves_exact_text_and_times(self):
        rows = [{"start_ms": 18740, "end_ms": 26940,
                 "text": "残念それさっき私が捨てた靴下です でも待って靴下の中から小さな猫が出てき"},
                {"start_ms": 26940, "end_ms": 27240, "text": "ました"},
                {"start_ms": 27840, "end_ms": 31740, "text": "かわいい、宝物はこの子だったんですね"}]
        original = copy.deepcopy(rows)
        merged = ai.merge_short_asr_cues(rows)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0], {"start_ms": 18740, "end_ms": 27240,
                                     "text": rows[0]["text"] + rows[1]["text"]})
        self.assertEqual("".join(r["text"] for r in merged), "".join(r["text"] for r in rows))
        self.assertEqual(merged[-1], rows[-1])
        self.assertEqual(rows, original)
        self.assertLessEqual(len(ai.wrap_caption(merged[0]["text"]).splitlines()), 3)

    def test_tiny_suffix_does_not_merge_across_pause_overlap_or_size_limit(self):
        for previous, start, end in (("あ" * 58, 1000, 1300), ("猫が出てき", 1051, 1351),
                                     ("猫が出てき", 999, 1299), ("猫が出てき", 1000, 1500),
                                     ("一行\n二行\n三行\n四行", 1000, 1300)):
            rows = [{"start_ms": 0, "end_ms": 1000, "text": previous},
                    {"start_ms": start, "end_ms": end, "text": "ました"}]
            with self.subTest(previous=previous, start=start, end=end):
                self.assertEqual(ai.merge_short_asr_cues(rows), rows)

    def test_tiny_suffix_can_merge_at_fifty_ms_gap_without_inventing_times(self):
        rows = [{"start_ms": 70, "end_ms": 1000, "text": "猫が出てき"},
                {"start_ms": 1050, "end_ms": 1300, "text": "ました"}]
        self.assertEqual(ai.merge_short_asr_cues(rows), [{"start_ms": 70, "end_ms": 1300, "text": "猫が出てきました"}])

    def test_short_fresh_sentence_and_ambiguous_ascii_word_join_stay_separate(self):
        for previous, current in (("猫でした。", "はい"), ("Hello", "world")):
            rows = [{"start_ms": 0, "end_ms": 1000, "text": previous},
                    {"start_ms": 1000, "end_ms": 1300, "text": current}]
            with self.subTest(previous=previous):
                self.assertEqual(ai.merge_short_asr_cues(rows), rows)

    def test_real_asr_path_applies_tiny_suffix_merge(self):
        model = FakeWhisper([SimpleNamespace(start=0, end=1, text="猫が出てき"),
                             SimpleNamespace(start=1, end=1.3, text="ました")])
        with patch.object(ai, "_load_whisper_model", return_value=model), \
                patch.object(ai, "_extract_audio", side_effect=self.extract):
            rows, _ = ai.transcribe_media("fake", 5000, self.config)
        self.assertEqual(rows, [{"start_ms": 0, "end_ms": 1300, "text": "猫が出てきました"}])

    def test_long_cue_missing_or_mismatched_words_rejected_not_interpolated(self):
        segment = SimpleNamespace(start=0, end=7, text="Long dialogue " * 7)
        with self.assertRaisesRegex(ai.LocalAIError, "word timestamps"):
            ai._segment_rows(segment, 0, 30000)
        segment.words = [SimpleNamespace(start=0, end=7, word="wrong")]
        with self.assertRaisesRegex(ai.LocalAIError, "alignment"):
            ai._segment_rows(segment, 0, 30000)

    def test_chunk_absolute_timing_glossary_and_model_release(self):
        model = FakeWhisper()
        events = []
        with patch.object(ai, "_load_whisper_model", return_value=model), \
                patch.object(ai, "_extract_audio", side_effect=self.extract) as extract:
            rows, metadata = ai.transcribe_media("fake.mp4", 65000, self.config, events.append)
        self.assertEqual([row["start_ms"] for row in rows], [1000, 31000, 61000])
        self.assertEqual([call.args[1:3] for call in extract.call_args_list], [(0, 30000), (30000, 30000), (60000, 5000)])
        self.assertTrue(all(c[1]["initial_prompt"] == "Miko, Suisei" for c in model.calls))
        self.assertTrue(all(c[1]["vad_filter"] is False and c[1]["word_timestamps"] is True for c in model.calls))
        self.assertTrue(model.unloaded)
        self.assertEqual(metadata["device_used"], "cpu")
        self.assertTrue(any(event["stage"] == "asr_released" for event in events))
        self.assertTrue(all(not Path(c[0]).exists() for c in model.calls))

    def test_cuda_fallback_requires_opt_in_and_reports_used_device(self):
        model = FakeWhisper()
        cuda = ai.replace(self.config, device="cuda", compute_type="float16")
        with patch.object(ai, "_load_whisper_model", side_effect=ai._ASRRuntimeError("CUDA missing")) as load, \
                self.assertRaises(ai.LocalAIError):
            ai.transcribe_media("fake", 5000, cuda)
        self.assertEqual(load.call_count, 1)
        events = []
        with patch.object(ai, "_load_whisper_model", side_effect=[ai._ASRRuntimeError("CUDA missing"), model]), \
                patch.object(ai, "_extract_audio", side_effect=self.extract):
            rows, metadata = ai.transcribe_media("fake", 5000, ai.replace(cuda, allow_cpu_fallback=True), events.append)
        self.assertTrue(rows)
        self.assertEqual(metadata["device_requested"], "cuda")
        self.assertEqual(metadata["device_used"], "cpu")
        self.assertTrue(metadata["warnings"])
        self.assertTrue(any(e["stage"] == "cpu_fallback" for e in events))

    def test_cancel_between_segments_unloads_and_does_not_cpu_fallback(self):
        model = FakeWhisper()
        cancel = threading.Event()

        def segments():
            yield SimpleNamespace(start=0, end=1, text="first")
            cancel.set()
            yield SimpleNamespace(start=1, end=2, text="second")

        model.transcribe = Mock(return_value=(segments(), None))
        with patch.object(ai, "_load_whisper_model", return_value=model) as load, \
                patch.object(ai, "_extract_audio", side_effect=self.extract), self.assertRaises(ai.AnalysisCancelled):
            ai.transcribe_media("fake", 5000, ai.replace(self.config, device="cuda", allow_cpu_fallback=True), cancelled=cancel.is_set)
        self.assertTrue(model.unloaded)
        self.assertEqual(load.call_count, 1)

    def test_invalid_asr_timestamps_do_not_become_candidates(self):
        for start, end in ((float("nan"), 1), (0, 100), (-1, 2), (2, 1)):
            model = FakeWhisper([SimpleNamespace(start=start, end=end, text="bad")])
            with self.subTest(start=start, end=end), patch.object(ai, "_load_whisper_model", return_value=model), \
                    patch.object(ai, "_extract_audio", side_effect=self.extract), self.assertRaises(ai.LocalAIError):
                ai.transcribe_media("fake", 5000, self.config)
            self.assertTrue(model.unloaded)


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "captions.json"
        self.source.write_text(json.dumps(ROWS))
        self.config = {"transcript_path": str(self.source), "ollama_model": "tiny-local:latest"}
        self.media = {"path": "fake.mp4", "duration_ms": 15000, "has_audio": True}

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_import_persists_engine_compatible_transcript_and_ranked_results(self):
        events = []
        with patch.object(ai, "probe_media", return_value=self.media), \
                patch.object(ai, "_client_for", return_value=ai.OllamaClient(FakeTransport())), \
                patch.object(ai, "transcribe_media") as asr, \
                patch.object(ai, "merge_short_asr_cues") as merge:
            result = ai.analyze("fake.mp4", self.base / "result", self.config, events.append)
        asr.assert_not_called()
        merge.assert_not_called()
        self.assertEqual(load_captions(result["transcript_path"]), ROWS)
        self.assertEqual(json.loads(Path(result["candidates_path"]).read_text()), result["candidates"])
        self.assertEqual(result["metadata"]["asr"]["mode"], "imported-transcript")
        self.assertTrue(result["metadata"]["review_required"])
        self.assertEqual(events[-1], {"stage": "complete", "progress": 1.0, "message": "Found 1 evidence-grounded candidates for review"})

    def test_existing_output_is_not_overwritten_and_preflight_does_not_run(self):
        out = self.base / "result"
        out.mkdir()
        existing = out / "candidates.json"
        existing.write_text("keep me")
        with patch.object(ai, "probe_media") as probe, self.assertRaises(ai.LocalAIError):
            ai.analyze("fake", out, self.config)
        probe.assert_not_called()
        self.assertEqual(existing.read_text(), "keep me")

    def test_completed_transcript_survives_ranking_failure_no_success_manifest(self):
        transport = FakeTransport(response={"done": True, "response": "invalid"})
        out = self.base / "result"
        with patch.object(ai, "probe_media", return_value=self.media), \
                patch.object(ai, "_client_for", return_value=ai.OllamaClient(transport)), self.assertRaises(ai.LocalAIError):
            ai.analyze("fake", out, self.config)
        self.assertEqual(load_captions(out / "transcript.json"), ROWS)
        self.assertFalse((out / "analysis.json").exists())
        self.assertFalse((out / "candidates.json").exists())

    def test_cancelled_before_start_has_no_writes(self):
        out = self.base / "result"
        with patch.object(ai, "probe_media") as probe, self.assertRaises(ai.AnalysisCancelled):
            ai.analyze("fake", out, self.config, cancelled=lambda: True)
        probe.assert_not_called()
        self.assertFalse(out.exists())

    def test_model_is_released_before_ranking(self):
        model = FakeWhisper([SimpleNamespace(start=r["start_ms"] / 1000, end=r["end_ms"] / 1000, text=r["text"]) for r in ROWS])
        client = ai.OllamaClient(FakeTransport())
        generate = client.generate

        def checked(*args, **kwargs):
            self.assertTrue(model.unloaded)
            return generate(*args, **kwargs)

        client.generate = checked
        with patch.object(ai, "probe_media", return_value=self.media), patch.object(ai, "_client_for", return_value=client), \
                patch.object(ai, "_load_whisper_model", return_value=model), \
                patch.object(ai, "_extract_audio", side_effect=ASRTests.extract):
            result = ai.analyze("fake", self.base / "result", {"whisper_model_dir": "fake", "ollama_model": "tiny-local:latest"})
        self.assertEqual(result["transcript"], ROWS)
        self.assertEqual(result["metadata"]["asr"]["mode"], "faster-whisper-local")

    def test_out_of_duration_import_rejected(self):
        with patch.object(ai, "probe_media", return_value=dict(self.media, duration_ms=5000)), \
                patch.object(ai, "_client_for", return_value=ai.OllamaClient(FakeTransport())), self.assertRaises(ai.LocalAIError):
            ai.analyze("fake", self.base / "result", self.config)
        self.assertFalse((self.base / "result").exists())


class ProcessTests(unittest.TestCase):
    def test_configured_native_tool_paths_are_used(self):
        with tempfile.TemporaryDirectory() as d:
            media=Path(d)/"video.mp4";media.write_bytes(b"fixture")
            with patch.dict(os.environ,{"CLIPBETA_FFMPEG_PATH":"C:/Tools/ffmpeg.exe","CLIPBETA_FFPROBE_PATH":"C:/Tools/ffprobe.exe"}), patch.object(ai,"resolve_executable",side_effect=lambda name,path:path) as resolve, patch.object(ai,"_run_command",return_value=b'{"format":{"duration":"1"},"streams":[{"codec_type":"video"}]}') as run:
                ai.probe_media(media)
                self.assertEqual(run.call_args[0][0][0],"C:/Tools/ffprobe.exe")
                ai._extract_audio(media,0,1000,Path(d)/"audio.wav",None)
                self.assertEqual(run.call_args[0][0][0],"C:/Tools/ffmpeg.exe")

    def test_cancel_terminates_running_media_command(self):
        before = time.monotonic()
        with self.assertRaises(ai.AnalysisCancelled):
            ai._run_command([sys.executable, "-c", "import time; time.sleep(30)"],
                            cancelled=lambda: time.monotonic() - before > .15)
        self.assertLess(time.monotonic() - before, 3)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_real_ffmpeg_probe_and_pcm_extraction_only(self):
        # This is media plumbing QA, not real speech/model quality verification.
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mp4"
            ai._run_command(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=160x90:d=1",
                             "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "mpeg4",
                             "-c:a", "aac", "-shortest", str(source)])
            info = ai.probe_media(source)
            self.assertTrue(info["has_audio"])
            self.assertAlmostEqual(info["duration_ms"], 1000, delta=100)
            audio = Path(temp) / "chunk.wav"
            ai._extract_audio(source, 200, 500, audio, None)
            import wave
            with wave.open(str(audio)) as handle:
                self.assertEqual(handle.getframerate(), 16000)
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getnframes(), 8000)


if __name__ == "__main__":
    unittest.main()
