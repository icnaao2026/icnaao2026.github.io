#!/usr/bin/env python3
"""Preview or send personalized acceptance emails with matching PDFs via Gmail.

Default: save .eml previews, without connecting to Gmail. Add --send to send.
SMTP uses a Google app password; --auth oauth uses browser sign-in and Gmail API.
OAuth dependencies are listed in requirements-gmail.txt. Linux/macOS supported.
"""

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import sys
from xml.etree import ElementTree as ET
from zipfile import BadZipFile

from generate_acceptance_letters import ROOT, submissions

OAUTH_SCOPES = ["https://www.googleapis.com/auth/gmail.send", "openid",
                "https://www.googleapis.com/auth/userinfo.email"]


class GmailAPIRejected(ValueError):
    """An explicit API rejection: the message was not accepted for sending."""


def load_oauth_service(credentials_file, token_file, sender):
    """Authorize sending and verify the signed-in account matches --sender."""
    try:
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import AuthorizedSession, Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise ValueError("Install OAuth dependencies first: python3 -m pip install "
                         "-r regis/requirements-gmail.txt") from exc
    credentials = None
    if token_file.exists():
        saved = json.loads(token_file.read_text(encoding="utf-8"))
        if not set(OAUTH_SCOPES).issubset(saved.get("scopes", [])):
            raise ValueError(f"The token at {token_file} lacks required permissions. "
                             "Use a new --token file and authorize again.")
        credentials = Credentials.from_authorized_user_info(saved, OAUTH_SCOPES)
    try:
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                if not credentials_file.is_file():
                    raise ValueError(f"Missing OAuth desktop credentials: {credentials_file}. "
                                     "See regis/README.md for the Google Cloud setup.")
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), OAUTH_SCOPES)
                credentials = flow.run_local_server(port=0, login_hint=sender, prompt="select_account")
        with AuthorizedSession(credentials) as session:
            response = session.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=30)
            if response.status_code != 200:
                raise ValueError("Could not verify the signed-in Google account.")
            actual_email = response.json().get("email", "")
        if actual_email.casefold() != sender.casefold():
            raise ValueError(f"Signed in as {actual_email}, but --sender is {sender}. "
                             "Use the matching sender or a new --token file for another account.")
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            os.chmod(token_file, 0o600)
            handle.write(credentials.to_json())
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
    except GoogleAuthError as exc:
        raise ValueError("Google authorization failed. Use a new --token file to sign in again; "
                         "Workspace accounts may require administrator approval.") from exc
    except HttpError as exc:
        raise ValueError(f"Google API setup failed (HTTP {exc.resp.status}); enable Gmail API "
                         "in the credentials' Cloud project and check account permissions.") from exc


class GmailAPITransport:
    def __init__(self, service):
        self.service = service

    def send_message(self, message, from_addr, to_addrs):
        # Gmail delivers to the MIME To/Cc/Bcc headers; these are built from the
        # same validated addresses as to_addrs by build_message().
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        try:
            result = self.service.users().messages().send(userId="me", body={"raw": raw}).execute(num_retries=0)
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if isinstance(status, int) and 400 <= status < 500:
                raise GmailAPIRejected(f"Gmail rejected this message (HTTP {status}); "
                                       "check account permissions and API configuration.") from exc
            raise ValueError("Gmail API delivery is uncertain; check Sent before retrying.") from exc
        if not result.get("id"):
            raise ValueError("Gmail returned no message ID; check Sent before retrying.")
        return {}


@contextmanager
def smtp_connection(sender, password):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=60) as smtp:
        smtp.login(sender, password)
        yield smtp


@contextmanager
def oauth_connection(credentials_file, token_file, sender):
    service = load_oauth_service(credentials_file, token_file, sender)
    try:
        yield GmailAPITransport(service)
    finally:
        service.close()


def email_address(value):
    """Require one bare address; reject lists, header injection, and SMTPUTF8."""
    value = value.strip()
    if not value or re.search(r"[\s,;<>\x00-\x1f\x7f]", value):
        raise ValueError(f"Expected a single email address: {value!r}")
    try:
        value.encode("ascii")
        address = Address(addr_spec=value)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid email address: {value!r}") from exc
    if not address.username or not address.domain or "." not in address.domain:
        raise ValueError(f"Invalid email address: {value!r}")
    return address.addr_spec


def email_content(template, record):
    first, separator, body = template.partition("\n")
    if not separator or not first.startswith("Subject:"):
        raise ValueError("Email template must start with a Subject: line.")
    fields = {"<PRESENTING AUTHOR>": record["name"],
              "<PRESENTATION TITLE>": record["title"]}
    if not all(token in body for token in fields):
        raise ValueError("Email template needs author and presentation-title placeholders.")
    body = re.sub(r"<PRESENTING AUTHOR>|<PRESENTATION TITLE>",
                  lambda match: fields[match[0]], body)
    subject = first.removeprefix("Subject:").strip()
    if not subject:
        raise ValueError("Email subject cannot be empty.")
    return subject, body.lstrip("\n")


def contact_addresses(value):
    """A registration may provide multiple comma/semicolon-separated contacts."""
    addresses = []
    for part in re.split(r"[,;]", value):
        address = email_address(part)
        if address.casefold() not in {item.casefold() for item in addresses}:
            addresses.append(address)
    return addresses


def build_message(record, template, sender, sender_name, pdf, recipient, reply_to):
    subject, body = email_content(template, record)
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = Address(display_name=sender_name, addr_spec=sender)
    message["To"] = tuple(Address(display_name=record["name"], addr_spec=address)
                          for address in contact_addresses(recipient))
    message["Reply-To"] = reply_to
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now().astimezone())
    message["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
    message.set_content(body)
    message.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf",
                           filename=pdf.name)
    return message


def history_key(record, recipient):
    return record["filename"] + "|" + ",".join(sorted(
        address.casefold() for address in contact_addresses(recipient)))


def read_history(handle):
    handle.seek(0)
    history = {}
    for number, line in enumerate(handle, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if not entry["status"].startswith("test_"):
                history[entry["key"]] = entry["status"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Invalid send history at line {number}; check it before sending.") from exc
    return history


def write_history(handle, status, record, recipient, message_id):
    entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds"),
             "key": history_key(record, recipient), "status": status,
             "row": record["row"], "name": record["name"], "title": record["title"],
             "recipient": recipient, "pdf": record["filename"], "message_id": message_id}
    handle.seek(0, 2)
    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def send_batch(pending, sender, password, history_file, resend=False, test_to=None,
               transport_factory=None):
    """Lock history for this run; never automatically retry an ambiguous send."""
    with history_file.open("a+", encoding="utf-8") as log:
        fcntl.flock(log, fcntl.LOCK_EX | fcntl.LOCK_NB)
        history = read_history(log)
        eligible = []
        for record, recipient, message in pending:
            status = history.get(history_key(record, recipient))
            if status in {"sent", "sending", "partial"} and not resend and not test_to:
                reason = {"sent": "already sent", "sending": "previous delivery uncertain; check Gmail",
                          "partial": "some addresses refused; check Gmail"}[status]
                print(f"Skipped ({reason}): {record['name']}")
                continue
            eligible.append((record, recipient, message))
        if not eligible:
            print("No unsent emails remain.")
            return
        connection = transport_factory() if transport_factory else smtp_connection(sender, password)
        with connection as smtp:
            for record, recipient, message in eligible:
                prefix = "test_" if test_to else ""
                write_history(log, prefix + "sending", record, recipient, message["Message-ID"])
                try:
                    refused = smtp.send_message(message, from_addr=sender,
                                                to_addrs=contact_addresses(recipient))
                    if refused:
                        write_history(log, prefix + "partial", record, recipient, message["Message-ID"])
                        raise ValueError(f"Partial delivery for {record['name']}; refused addresses: "
                                         + ", ".join(refused) + ". Check Gmail before retrying.")
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError,
                        smtplib.SMTPSenderRefused, GmailAPIRejected):
                    write_history(log, prefix + "failed", record, recipient, message["Message-ID"])
                    raise
                # Connection loss after DATA is ambiguous: keep 'sending' so a
                # rerun cannot silently duplicate a message Gmail may have accepted.
                write_history(log, prefix + "sent", record, recipient, message["Message-ID"])
                print(f"Sent{' test' if test_to else ''}: {record['name']} -> {recipient}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ods", type=Path, nargs="?", default=ROOT / "regis/reg.ods")
    parser.add_argument("--sender", default=os.environ.get("GMAIL_ADDRESS"),
                        help="Gmail/Google Workspace address, or set GMAIL_ADDRESS")
    parser.add_argument("--auth", choices=("smtp", "oauth"), default="smtp",
                        help="smtp: app password (default); oauth: browser sign-in and Gmail API")
    parser.add_argument("--credentials", type=Path, default=ROOT / "regis/gmail_credentials.json",
                        help="Downloaded Google OAuth Desktop app credentials JSON")
    parser.add_argument("--token", type=Path, default=ROOT / "regis/gmail_token.json",
                        help="Local OAuth token cache; never commit this file")
    parser.add_argument("--sender-name", default="Parin Chaipunya — ICNAAO 2026")
    parser.add_argument("--reply-to", default="icnaao2026@kmutt.ac.th")
    parser.add_argument("--letters-dir", type=Path, default=ROOT / "acceptance_letters")
    parser.add_argument("--template", type=Path,
                        default=Path(__file__).with_name("email_abstract_acceptance.txt"))
    parser.add_argument("--sheet")
    parser.add_argument("--row", type=int, action="append", default=[],
                        help="Only include this spreadsheet row; repeatable")
    parser.add_argument("--send", action="store_true", help="Send through Gmail instead of saving previews")
    parser.add_argument("--resend", action="store_true", help="Send even if delivery is already recorded")
    parser.add_argument("--test-to", help="Send one selected letter to this test address; requires --row")
    args = parser.parse_args()
    try:
        sender = email_address(args.sender or "")
        reply_to = email_address(args.reply_to)
        test_to = email_address(args.test_to) if args.test_to else None
        if test_to and len(args.row) != 1:
            raise ValueError("Use exactly one --row with --test-to.")
        records, skipped = submissions(args.ods, args.sheet, set(args.row))
        template = args.template.read_text(encoding="utf-8")
        pending = []
        # Validate every selected recipient and attachment before any email is sent.
        for record in records:
            recipient = test_to or ", ".join(contact_addresses(
                record.get("email") or record.get("form_email") or ""))
            pdf = args.letters_dir / record["filename"]
            if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
                raise ValueError(f"Row {record['row']}: missing or invalid PDF: {pdf}; generate it first.")
            message = build_message(record, template, sender, args.sender_name, pdf, recipient, reply_to)
            pending.append((record, recipient, message))
        print(f"{len(pending)} email(s); {len(skipped)} non-presenter(s) excluded.")
        if not pending:
            return 0
        if not args.send:
            preview_dir = args.letters_dir / "email_previews"
            preview_dir.mkdir(parents=True, exist_ok=True)
            for record, recipient, message in pending:
                path = preview_dir / Path(record["filename"]).with_suffix(".eml")
                path.write_bytes(message.as_bytes())
                print(f"Preview: {record['name']} -> {recipient}: {path}")
            print("No emails sent. Add --send to send through Gmail.")
            return 0
        password, transport_factory = None, None
        if args.auth == "oauth":
            transport_factory = lambda: oauth_connection(args.credentials, args.token, sender)
        else:
            password = os.environ.get("GMAIL_APP_PASSWORD") or getpass.getpass("Google app password: ")
            password = "".join(password.split())
            if not password:
                raise ValueError("A Google app password is required; alternatively use --auth oauth.")
        send_batch(pending, sender, password, args.letters_dir / "email_send_history.jsonl",
                   resend=args.resend, test_to=test_to, transport_factory=transport_factory)
        return 0
    except (OSError, ValueError, BadZipFile, ET.ParseError, KeyError,
            smtplib.SMTPException, EOFError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
