import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path
import smtplib
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import send_acceptance_emails as emails


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.record = {"row": 3, "name": "Example Author", "title": "A title & its applications",
                       "filename": "20261014_182542_Example_Author.pdf"}
        self.template = "Subject: Abstract accepted\n\nDear <PRESENTING AUTHOR>,\n<PRESENTATION TITLE>\n"
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name) / self.record["filename"]
        self.pdf.write_bytes(b"%PDF-1.4\nTest attachment")
        self.history = Path(self.temp.name) / "history.jsonl"

    def pending(self):
        message = emails.build_message(self.record, self.template, "sender@gmail.com",
                                       "Conference", self.pdf, "author@example.org",
                                       "conference@example.org")
        return [(self.record, "author@example.org", message)]

    def test_message_is_personalized_with_correct_pdf(self):
        message = BytesParser(policy=policy.default).parsebytes(self.pending()[0][2].as_bytes())
        self.assertEqual(str(message["To"]), "Example Author <author@example.org>")
        self.assertEqual(message["Reply-To"], "conference@example.org")
        body = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("Dear Example Author", body)
        self.assertIn("A title & its applications", body)
        attachment, = message.iter_attachments()
        self.assertEqual(attachment.get_filename(), self.pdf.name)
        self.assertEqual(attachment.get_payload(decode=True), self.pdf.read_bytes())

    def test_rejects_multiple_addresses_and_header_injection(self):
        for address in ("one@example.org,two@example.org", "a@example.org\nBcc: b@example.org",
                        "Name <a@example.org>", "", "not-an-address"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                emails.email_address(address)

    def test_multiple_contacts_share_one_email(self):
        addresses = emails.contact_addresses("one@example.org, two@example.org; one@example.org")
        self.assertEqual(addresses, ["one@example.org", "two@example.org"])
        message = emails.build_message(self.record, self.template, "sender@gmail.com",
                                       "Conference", self.pdf, ", ".join(addresses),
                                       "conference@example.org")
        self.assertEqual([item.addr_spec for item in message["To"].addresses], addresses)
        self.assertEqual(emails.history_key(self.record, "one@example.org,two@example.org"),
                         emails.history_key(self.record, "two@example.org;one@example.org"))

    def test_partial_delivery_is_not_automatically_retried(self):
        smtp = MagicMock()
        smtp.send_message.return_value = {"refused@example.org": (550, b"Unknown recipient")}
        pending = self.pending()
        record, _, _ = pending[0]
        recipient = "author@example.org, refused@example.org"
        message = emails.build_message(record, self.template, "sender@gmail.com", "Conference",
                                       self.pdf, recipient, "conference@example.org")
        with patch.object(emails.smtplib, "SMTP_SSL") as connection:
            connection.return_value.__enter__.return_value = smtp
            with self.assertRaisesRegex(ValueError, "Partial delivery"):
                emails.send_batch([(record, recipient, message)], "sender@gmail.com", "secret", self.history)
            emails.send_batch([(record, recipient, message)], "sender@gmail.com", "secret", self.history)
        smtp.send_message.assert_called_once()

    def test_sent_messages_are_skipped_on_rerun(self):
        smtp = MagicMock()
        smtp.send_message.return_value = {}
        with patch.object(emails.smtplib, "SMTP_SSL") as connection:
            connection.return_value.__enter__.return_value = smtp
            emails.send_batch(self.pending(), "sender@gmail.com", "test-secret", self.history)
            emails.send_batch(self.pending(), "sender@gmail.com", "test-secret", self.history)
        smtp.send_message.assert_called_once()
        with self.history.open() as log:
            self.assertEqual(emails.read_history(log)[emails.history_key(self.record, "author@example.org")], "sent")
        self.assertNotIn("test-secret", self.history.read_text())

    def test_uncertain_delivery_is_not_automatically_retried(self):
        smtp = MagicMock()
        smtp.send_message.side_effect = smtplib.SMTPServerDisconnected("Lost connection")
        with patch.object(emails.smtplib, "SMTP_SSL") as connection:
            connection.return_value.__enter__.return_value = smtp
            with self.assertRaises(smtplib.SMTPServerDisconnected):
                emails.send_batch(self.pending(), "sender@gmail.com", "test-secret", self.history)
            emails.send_batch(self.pending(), "sender@gmail.com", "test-secret", self.history)
        smtp.send_message.assert_called_once()

    def test_test_sends_do_not_replace_real_delivery_history(self):
        with self.history.open("w+") as log:
            emails.write_history(log, "sent", self.record, "author@example.org", "<actual>")
            emails.write_history(log, "test_sent", self.record, "author@example.org", "<test>")
            self.assertEqual(emails.read_history(log)[emails.history_key(self.record, "author@example.org")], "sent")

    def test_oauth_sends_identical_mime_and_skips_previous_delivery(self):
        service = MagicMock()
        request = service.users().messages().send.return_value
        request.execute.return_value = {"id": "gmail-message-id"}
        pending = self.pending()
        factory = lambda: emails.oauth_connection(Path("credentials"), Path("token"), "sender@gmail.com")
        with patch.object(emails, "load_oauth_service", return_value=service) as loader, \
                patch.object(emails.smtplib, "SMTP_SSL") as smtp:
            emails.send_batch(pending, "sender@gmail.com", None, self.history, transport_factory=factory)
            emails.send_batch(pending, "sender@gmail.com", None, self.history, transport_factory=factory)
        loader.assert_called_once()
        smtp.assert_not_called()
        request.execute.assert_called_once_with(num_retries=0)
        call = service.users().messages().send.call_args
        self.assertEqual(call.kwargs["userId"], "me")
        raw = base64.urlsafe_b64decode(call.kwargs["body"]["raw"])
        message = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual(message["To"].addresses[0].addr_spec, "author@example.org")
        attachment, = message.iter_attachments()
        self.assertEqual(attachment.get_payload(decode=True), self.pdf.read_bytes())
        service.close.assert_called_once()

    def test_oauth_rejection_and_uncertain_response_have_distinct_history(self):
        for code, expected in ((403, "failed"), (500, "sending")):
            with self.subTest(http_status=code):
                service = MagicMock()
                error = Exception("API failure")
                error.resp = type("Response", (), {"status": code})()
                service.users().messages().send.return_value.execute.side_effect = error
                history = Path(self.temp.name) / f"history-{code}.jsonl"
                factory = lambda: emails.oauth_connection(Path("credentials"), Path("token"), "sender@gmail.com")
                with patch.object(emails, "load_oauth_service", return_value=service):
                    with self.assertRaises(ValueError):
                        emails.send_batch(self.pending(), "sender@gmail.com", None, history,
                                          transport_factory=factory)
                with history.open() as log:
                    self.assertEqual(emails.read_history(log)[emails.history_key(self.record, "author@example.org")],
                                     expected)

    def test_oauth_does_not_prompt_for_app_password(self):
        template = Path(self.temp.name) / "email.txt"
        template.write_text(self.template)
        argv = ["send_acceptance_emails.py", "--sender", "sender@gmail.com", "--auth", "oauth",
                "--send", "--letters-dir", self.temp.name, "--template", str(template)]
        record = self.record | {"email": "author@example.org"}
        with patch.object(emails, "submissions", return_value=([record], [])), \
                patch.object(emails.sys, "argv", argv), \
                patch.object(emails.getpass, "getpass") as password_prompt, \
                patch.object(emails, "send_batch") as batch:
            self.assertEqual(emails.main(), 0)
        password_prompt.assert_not_called()
        self.assertIsNotNone(batch.call_args.kwargs["transport_factory"])

    def test_preview_mode_never_connects_to_gmail(self):
        with patch.object(emails, "submissions", return_value=([self.record | {"email": "author@example.org"}], [])), \
                patch.object(emails.smtplib, "SMTP_SSL") as connection, \
                patch.object(emails.sys, "argv", ["send_acceptance_emails.py", "--sender", "sender@gmail.com",
                                                  "--letters-dir", self.temp.name]):
            template = Path(self.temp.name) / "email.txt"
            template.write_text(self.template)
            with patch.object(emails.sys, "argv", emails.sys.argv + ["--template", str(template)]):
                self.assertEqual(emails.main(), 0)
        connection.assert_not_called()
        self.assertTrue((Path(self.temp.name) / "email_previews" / self.pdf.with_suffix(".eml").name).exists())
        self.assertFalse(self.history.exists())


if __name__ == "__main__":
    unittest.main()
