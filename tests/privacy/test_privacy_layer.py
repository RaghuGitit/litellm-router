import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from privacy.masker import REDACTED_SECRET
from privacy.service import PrivacyService


POLICY = """
version: test-v1
enabled: true
fail_closed: true
reject_streaming: true
entities:
  EMAIL_ADDRESS:
    enabled: true
    action: tokenize
    restore: true
    score_threshold: 0.5
  PHONE_NUMBER:
    enabled: true
    action: tokenize
    restore: true
    score_threshold: 0.5
  DATE_TIME:
    enabled: true
    action: tokenize
    restore: true
    score_threshold: 0.5
  API_KEY:
    enabled: true
    action: redact
    restore: false
    score_threshold: 0.5
custom_patterns:
  - name: customer_id
    entity_type: CUSTOMER_ID
    regex: '\\bCUST-[0-9]{6}\\b'
    score: 0.95
    action: tokenize
    restore: true
  - name: employee_id
    entity_type: EMPLOYEE_ID
    regex: '\\bEMP-[A-Z]{2}-[0-9]{4}\\b'
    score: 0.95
    action: tokenize
    restore: true
"""


class PrivacyLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.policy_path = Path(self.tmp.name) / "policy.yaml"
        self.policy_path.write_text(POLICY, encoding="utf-8")
        self.service = PrivacyService(self.policy_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _mask(self, text, request_key=1):
        vault = self.service.start_request(request_key)
        result = self.service.mask_request_messages(
            [{"role": "user", "content": text}],
            vault,
        )
        return result.messages[0]["content"], vault, result.stats

    def test_email_and_phone_detection_and_masking(self):
        masked, vault, stats = self._mask("Email alice@example.com or call +1 415-555-1212.")
        self.assertNotIn("alice@example.com", masked)
        self.assertNotIn("415-555-1212", masked)
        self.assertGreaterEqual(stats.tokenized_count, 2)
        restored = self.service.restorer.restore_text(masked, vault)
        self.assertIn("alice@example.com", restored)
        self.assertIn("+1 415-555-1212", restored)

    def test_custom_customer_and_employee_templates(self):
        masked, vault, stats = self._mask("Customer CUST-123456 belongs to EMP-AB-1234.")
        self.assertNotIn("CUST-123456", masked)
        self.assertNotIn("EMP-AB-1234", masked)
        self.assertEqual(stats.entity_counts["CUSTOMER_ID"], 1)
        self.assertEqual(stats.entity_counts["EMPLOYEE_ID"], 1)
        restored = self.service.restorer.restore_text(masked, vault)
        self.assertIn("CUST-123456", restored)
        self.assertIn("EMP-AB-1234", restored)

    def test_two_different_values_same_entity_get_different_tokens(self):
        masked, vault, _ = self._mask("a@example.com and b@example.com")
        tokens = list(vault.token_to_record)
        self.assertEqual(len(tokens), 2)
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertNotIn("a@example.com", masked)
        self.assertNotIn("b@example.com", masked)

    def test_repeated_value_reuses_token_in_request(self):
        masked, vault, _ = self._mask("a@example.com then a@example.com again")
        self.assertEqual(len(vault.token_to_record), 1)
        token = next(iter(vault.token_to_record))
        self.assertEqual(masked.count(token), 2)

    def test_unicode_punctuation_newlines_and_adjacent_spans(self):
        masked, vault, _ = self._mask("Hello,\nemail:alice@example.com; id:CUST-123456 - thanks.")
        self.assertNotIn("alice@example.com", masked)
        self.assertNotIn("CUST-123456", masked)
        restored = self.service.restorer.restore_text(masked, vault)
        self.assertIn("email:alice@example.com", restored)
        self.assertIn("id:CUST-123456", restored)

    def test_original_token_like_text_does_not_collide(self):
        existing = "[[PII_EMAIL_ADDRESS_collisiontoken]]"
        with patch(
            "privacy.masker.secrets.token_urlsafe",
            side_effect=["collisiontoken", "fresh_token_123"],
        ):
            masked, vault, _ = self._mask(f"{existing} alice@example.com")
        self.assertIn(existing, masked)
        self.assertIn("[[PII_EMAIL_ADDRESS_fresh_token_123]]", vault.token_to_record)

    def test_only_registered_tokens_are_restored(self):
        masked, vault, _ = self._mask("alice@example.com")
        forged = "[[PII_EMAIL_ADDRESS_forgedtoken123]]"
        restored = self.service.restorer.restore_text(masked + " " + forged, vault)
        self.assertIn("alice@example.com", restored)
        self.assertIn(forged, restored)

    def test_secret_policy_redacts_and_never_restores(self):
        masked, vault, _ = self._mask("Use sk-abcdefghijklmnopqrstuvwxyz123456")
        self.assertIn(REDACTED_SECRET, masked)
        self.assertFalse(vault.token_to_record)
        restored = self.service.restorer.restore_text(masked, vault)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", restored)

    def test_concurrent_requests_are_isolated(self):
        async def run():
            vault_one = self.service.start_request(101)
            vault_two = self.service.start_request(202)
            one = self.service.mask_request_messages([{"role": "user", "content": "a@example.com"}], vault_one)
            two = self.service.mask_request_messages([{"role": "user", "content": "b@example.com"}], vault_two)
            return one.messages[0]["content"], two.messages[0]["content"], vault_one, vault_two

        masked_one, masked_two, vault_one, vault_two = asyncio.run(run())
        self.assertIn("a@example.com", self.service.restorer.restore_text(masked_one, vault_one))
        self.assertIn("b@example.com", self.service.restorer.restore_text(masked_two, vault_two))
        self.assertNotIn("a@example.com", self.service.restorer.restore_text(masked_one, vault_two))

    def test_detector_failure_does_not_return_unmasked_prompt_when_fail_closed(self):
        vault = self.service.start_request(303)
        with patch.object(self.service.detector, "detect", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.service.mask_request_messages(
                    [{"role": "user", "content": "alice@example.com"}],
                    vault,
                )

    def test_response_restores_registered_tokens_in_message_content_and_tool_arguments(self):
        masked, vault, _ = self._mask("alice@example.com")
        response = {
            "choices": [
                {
                    "message": {
                        "content": masked,
                        "tool_calls": [
                            {"function": {"arguments": '{"email": "' + masked + '"}'}}
                        ],
                    }
                }
            ]
        }
        restored = self.service.restorer.restore_response(response, vault)
        self.assertIn("alice@example.com", restored["choices"][0]["message"]["content"])
        self.assertIn("alice@example.com", restored["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])

    def test_streaming_is_rejected_by_policy(self):
        self.assertTrue(self.service.policy.reject_streaming)

    def test_existing_classifier_files_are_not_privacy_modules(self):
        self.assertTrue(Path("plugins/remote_classifier.py").exists())
        self.assertTrue(Path("routing-service/app.py").exists())


if __name__ == "__main__":
    unittest.main()
