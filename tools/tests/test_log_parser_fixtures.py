import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.log_aggregator import JSONLogParser, TextLogParser, NginxLogParser, LogAggregator

FIXTURES = Path(__file__).parent / "fixtures" / "log_parser"

def lines(name):
    return (FIXTURES / name).read_text().splitlines()

def combined():
    return "\n".join(lines("application.jsonl") + lines("worker.log") + lines("nginx.log")) + "\n"

class IndependentLogFixtureTests(unittest.TestCase):
    def test_json_fields_and_numeric_timestamp(self):
        entry = JSONLogParser().parse(lines("application.jsonl")[0])
        self.assertEqual({k:entry[k] for k in ("timestamp", "level", "service", "message", "format")}, {"timestamp":1704067200,"level":"ERROR","service":"payments","message":"Declined charge","format":"json"})
        self.assertEqual(entry["fields"]["request_id"], "synthetic-7")
        self.assertEqual(entry["fields"]["attempt"], 2)

    def test_json_aliases_and_boolean_are_preserved(self):
        entry = JSONLogParser().parse(lines("application.jsonl")[1])
        self.assertEqual((entry["timestamp"],entry["level"],entry["service"],entry["message"],entry["format"]), (1704067203,"DEBUG","search","Cache warmed","json"))
        self.assertIs(entry["fields"]["cache_hit"], False)

    def test_text_timestamp_level_service_and_raw_message(self):
        raw = lines("worker.log")[0]
        entry = TextLogParser().parse(raw)
        self.assertEqual(entry, {"timestamp":1704067201,"level":"warn","service":"worker","message":raw,"fields":{"raw":raw},"format":"text"})

    def test_nginx_timezone_authenticated_user_and_key_fields(self):
        entry = NginxLogParser().parse(lines("nginx.log")[0])
        self.assertEqual((entry["timestamp"],entry["level"],entry["service"],entry["format"]), (1704067202,"error","nginx","nginx"))
        self.assertEqual(entry["fields"], {"remote_addr":"198.51.100.24","remote_user":"reader","request":"GET /health HTTP/1.1","status":503,"body_bytes":"27","referer":"-","user_agent":"fixture-agent/1.0"})
        self.assertEqual(entry["message"], "GET /health HTTP/1.1")

    def test_nginx_anonymous_success_and_client_error(self):
        for raw, timestamp, level, status in [(lines("nginx.log")[1],1704067204,"info",200),(lines("nginx.log")[2],1704067205,"warn",404)]:
            with self.subTest(status=status):
                entry = NginxLogParser().parse(raw)
                self.assertEqual((entry["timestamp"],entry["level"],entry["fields"]["status"],entry["fields"]["remote_user"]), (timestamp,level,status,"-"))

    def test_malformed_and_unsupported_inputs_do_not_crash_parsers(self):
        for raw in ['{"timestamp":', '[]', 'null', '"not an object"']:
            with self.subTest(raw=raw): self.assertIsNone(JSONLogParser().parse(raw))
        for raw in ["", "not a combined access log", '198.51.100.24 - - [invalid] "GET /" nope 1 "-" "agent"']:
            with self.subTest(raw=raw): self.assertIsNone(NginxLogParser().parse(raw))
        self.assertIsNone(TextLogParser().parse(" "))
        entry = TextLogParser().parse("unsupported legacy record")
        self.assertEqual((entry["timestamp"],entry["level"],entry["service"],entry["format"]), (None,"unknown",None,"text"))

    def test_json_iso_timestamp_is_usable_by_aggregation_and_summary(self):
        for stamp in ["2024-01-01T00:00:06Z", "2024-01-01T02:00:06+02:00", "2024-01-01T00:00:06"]:
            with self.subTest(stamp=stamp):
                raw = '{"timestamp":"' + stamp + '","level":"INFO","service":"orders","message":"ready"}'
                agg = LogAggregator()
                self.assertTrue(agg._parse_line(raw))
                self.assertEqual(agg.entries[0]["timestamp"], 1704067206)
                self.assertEqual(agg.entries[0]["fields"]["timestamp"], stamp)
                self.assertEqual(agg.get_summary()["time_range"]["start"], "2024-01-01T00:00:06+00:00")
                self.assertEqual(agg.get_summary()["by_hour"], {"2024-01-01T00:00":1})

    def test_invalid_json_timestamp_does_not_abort_aggregation(self):
        agg = LogAggregator()
        self.assertTrue(agg._parse_line('{"timestamp":"not-a-date","message":"legacy record"}'))
        self.assertIsNone(agg.entries[0]["timestamp"])
        self.assertEqual(agg.entries[0]["fields"]["timestamp"], "not-a-date")
        self.assertIsNone(agg.get_summary()["time_range"])

    def test_json_cli_accepts_iso_and_invalid_timestamp_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/"iso.jsonl"
            source.write_text('{"timestamp":"2024-01-01T00:00:06Z","service":"orders","message":"ready"}\n{"timestamp":"not-a-date","service":"orders","message":"legacy"}\n')
            report = Path(tmp)/"report.json"
            result = subprocess.run([sys.executable, str(ROOT/"tools/log_aggregator.py"), "--input", str(source), "--output", str(report)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(report.read_text())
            self.assertEqual(data["summary"]["total_entries"], 2)
            self.assertEqual(data["summary"]["time_range"]["start"], "2024-01-01T00:00:06+00:00")
            self.assertEqual([entry["timestamp"] for entry in data["entries"]], [1704067206, None])

    def test_cli_without_usable_timestamps_still_exports_report(self):
        for content, expected_count in [('{"timestamp":"not-a-date","message":"legacy"}\n', 1), ("", 0)]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)/"input.log"; source.write_text(content)
                report = Path(tmp)/"report.json"
                result = subprocess.run([sys.executable,str(ROOT/"tools/log_aggregator.py"),"--input",str(source),"--output",str(report)],capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(report.read_text())["summary"]["total_entries"],expected_count)
                self.assertIn("N/A to N/A",result.stdout)

    def test_aggregator_selects_nginx_before_generic_text(self):
        agg = LogAggregator()
        self.assertTrue(agg._parse_line(lines("nginx.log")[0]))
        self.assertEqual(agg.entries[0]["format"], "nginx")
        self.assertEqual(agg.get_summary()["by_service"], {"nginx":1})
        self.assertEqual(agg.get_summary()["by_level"], {"error":1})

    def test_process_file_and_gzip_preserve_all_six_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            normal = Path(tmp)/"archive.log"
            normal.write_text(combined())
            compressed = Path(tmp)/"archive.log.gz"
            with gzip.open(compressed,"wt") as out: out.write(combined())
            for filename in [normal,compressed]:
                with self.subTest(filename=filename.name):
                    agg=LogAggregator()
                    self.assertEqual(agg.process_file(str(filename)),6)
                    summary=agg.get_summary()
                    self.assertEqual(summary["by_service"], {"nginx":3,"payments":1,"worker":1,"search":1})
                    self.assertEqual(summary["by_level"], {"error":2,"warn":2,"debug":1,"info":1})
                    self.assertEqual(summary["time_range"]["start"], "2024-01-01T00:00:00+00:00")
                    self.assertEqual(summary["time_range"]["end"], "2024-01-01T00:00:05+00:00")

    def test_existing_json_cli_and_search_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"input.log";source.write_text(combined());report=Path(tmp)/"report.json"
            result=subprocess.run([sys.executable,str(ROOT/"tools/log_aggregator.py"),"--input",str(source),"--output",str(report),"--format","json","--search","/health"],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(report.read_text())
            self.assertEqual(data["summary"]["total_entries"],6)
            self.assertEqual(data["summary"]["by_service"]["nginx"],3)
            self.assertIn("GET /health HTTP/1.1",result.stdout)
            self.assertEqual([x["format"] for x in data["entries"]], ["json","json","text","nginx","nginx","nginx"])

if __name__ == "__main__": unittest.main()
