import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from siteintel.reports.handwriting_ocr import (
    HandwritingOcrError,
    _parse_analyze_result,
    confidence_by_field,
    extract_with_azure,
    low_confidence_fields,
)


class _Response:
    def __init__(self, *, status, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body


class HandwritingOcrTests(unittest.TestCase):
    def test_parses_handwriting_and_flags_low_confidence_fields(self):
        content = "rain_mm: 12 mm"
        result = _parse_analyze_result({
            "analyzeResult": {
                "content": content,
                "styles": [{"isHandwritten": True, "spans": [{"offset": 0, "length": len(content)}]}],
                "pages": [{
                    "lines": [{
                        "content": content,
                        "spans": [{"offset": 0, "length": len(content)}],
                    }],
                    "words": [{
                        "confidence": 0.61,
                        "spans": [{"offset": 0, "length": len(content)}],
                    }],
                }],
            },
        })

        self.assertEqual(result.text, content)
        self.assertEqual(result.provider, "azure_document_intelligence")
        self.assertTrue(result.handwriting_detected)
        confidences = confidence_by_field(
            {"rain_mm": "12 mm"},
            result.lines,
        )
        self.assertEqual(confidences, {"rain_mm": 0.61})
        self.assertEqual(low_confidence_fields(confidences), ["rain_mm"])

    def test_rejects_malformed_provider_response(self):
        with self.assertRaises(HandwritingOcrError):
            _parse_analyze_result({"status": "succeeded"})

    def test_submits_document_to_configured_read_model_without_logging_key(self):
        content = "Cement: 350 kg/m3"
        response_body = json.dumps({
            "status": "succeeded",
            "analyzeResult": {
                "content": content,
                "styles": [],
                "pages": [{
                    "lines": [{"content": content, "spans": [{"offset": 0, "length": len(content)}]}],
                    "words": [{"confidence": 0.96, "spans": [{"offset": 0, "length": len(content)}]}],
                }],
            },
        }).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.pdf"
            path.write_bytes(b"%PDF-1.7 test")
            app = Flask(__name__)
            app.config.update(
                HANDWRITING_OCR_ENDPOINT="https://ocr.example.test",
                HANDWRITING_OCR_KEY="test-key-not-for-logs",
                HANDWRITING_OCR_TIMEOUT_SECONDS=10,
            )
            with app.app_context():
                with patch("siteintel.reports.handwriting_ocr.urlopen") as open_url:
                    open_url.side_effect = [
                        _Response(
                            status=202,
                            headers={
                                "Operation-Location":
                                    "https://ocr.example.test/operations/123",
                            },
                        ),
                        _Response(status=200, body=response_body),
                    ]
                    result = extract_with_azure(path)

        self.assertEqual(result.text, content)
        self.assertEqual(open_url.call_count, 2)
        first_request = open_url.call_args_list[0].args[0]
        self.assertIn("prebuilt-read:analyze", first_request.full_url)
        self.assertEqual(first_request.data, b"%PDF-1.7 test")
        self.assertEqual(
            first_request.get_header("Ocp-apim-subscription-key"),
            "test-key-not-for-logs",
        )

    def test_rejects_operation_url_on_another_host(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.pdf"
            path.write_bytes(b"%PDF-1.7 test")
            app = Flask(__name__)
            app.config.update(
                HANDWRITING_OCR_ENDPOINT="https://ocr.example.test",
                HANDWRITING_OCR_KEY="test-key",
            )
            with app.app_context():
                with patch(
                    "siteintel.reports.handwriting_ocr.urlopen",
                    return_value=_Response(
                        status=202,
                        headers={"Operation-Location": "https://attacker.example/operation"},
                    ),
                ):
                    with self.assertRaises(HandwritingOcrError):
                        extract_with_azure(path)


if __name__ == "__main__":
    unittest.main()
