"""Tests for common.emails.send_templated_email — the shared outbound-email helper."""

from django.core import mail
from django.test import TestCase, override_settings

from .emails import send_templated_email


class SendTemplatedEmailReplyToTestCase(TestCase):
    @override_settings(REPLY_TO_EMAIL="support@example.com")
    def test_defaults_to_reply_to_email_setting(self):
        send_templated_email("welcome", {"user": None}, to="buyer@example.com")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].reply_to, ["support@example.com"])

    @override_settings(REPLY_TO_EMAIL="")
    def test_omits_header_when_reply_to_email_is_blank(self):
        send_templated_email("welcome", {"user": None}, to="buyer@example.com")

        self.assertEqual(mail.outbox[0].reply_to, [])

    def test_explicit_reply_to_overrides_default(self):
        send_templated_email("welcome", {"user": None}, to="buyer@example.com", reply_to="orders@example.com")

        self.assertEqual(mail.outbox[0].reply_to, ["orders@example.com"])
