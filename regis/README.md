Run from the conference repository root (Python 3.10+ and `pdflatex` required):

```sh
python3 regis/generate_acceptance_letters.py /path/to/reg.ods --dry-run
python3 regis/generate_acceptance_letters.py /path/to/reg.ods
```

The spreadsheet found for this task is in the neighboring repository:

```sh
python3 regis/generate_acceptance_letters.py ../parinchaipunya.github.io/regis/regis.ods
```

Alternatively, place the export at `regis/reg.ods` and omit the input argument.
The script uses Python's standard library; no spreadsheet package is required.

Edit `acceptance_letter_template.tex` to change the letter wording. It uses the
existing conference logos and includes `letters/signature.png` when available.
The spreadsheet itself and the generated letters are ignored by Git.

Only registrations whose `Registration type` starts with `Presenter` are used.
Each letter addresses the **Presenting author**, names their **Presentation
title**, and uses the department, institution, and country supplied on the
registration form. These affiliation fields should belong to the presenter.
The sheet has no acceptance-status column: running the script generates
acceptance letters for **all presenters**, unless restricted with `--row`.
It does not send any letters or download uploaded abstracts.

PDFs go to `acceptance_letters/`, named
`YYYYMMDD_HHMMSS_Presenting_Author.pdf`, using the **submission timestamp**
from the spreadsheet, not the generation time. Google Forms text timestamps
are interpreted as month/day/year. For example:
`20261014_182542_Faizan_Ali_Khan.pdf`.

Each normal run appends to `acceptance_letters/generation.log` (or to
`generation.log` inside a custom `--output-dir`). The log records the run time,
spreadsheet path, generated letters' presenting-author names, titles, and PDF
filenames, and the names of skipped non-presenters from the registration-name
column. Existing PDFs skipped on a repeat run are listed separately with their
author and title. Earlier runs remain in the log. `--dry-run` writes no log.

Useful options:

- `--row 3 --row 5`: generate only these spreadsheet rows (header is row 1).
- `--sheet Sheet1`: select a worksheet; otherwise the first is used.
- `--date 2026-10-19`: set the letter issue date; otherwise today's local date.
- `--overwrite`: regenerate existing PDFs; otherwise they are left unchanged.
- `--output-dir /path/to/folder`: change the destination folder.
- `--template /path/to/template.tex`: use another template with the same fields.

Spreadsheet text is escaped for LaTeX. A restricted set of inline mathematics
in `$...$` is preserved in titles, including `$q$` in the current export.
Invalid presenter records or duplicate filenames produce an error. LaTeX
auxiliary files are compiled in temporary directories and cleaned automatically.

### Email letters through Gmail

`send_acceptance_emails.py` uses the same spreadsheet, the email template
`email_abstract_acceptance.txt`, and the already generated PDFs. It runs on
Linux/macOS; preview and SMTP use only Python's standard library. It prefers **Email address
(as should be contacted with)** and falls back to **Email Address** only when
the contact cell is empty. Non-presenters are excluded. Select approved
presenters with `--row` if you do not intend to email every presenter.
If a contact cell contains multiple comma- or semicolon-separated addresses,
the same personalized email goes to those addresses together in its To field.

First generate letters, then create `.eml` previews to review in a mail client:

```sh
python3 regis/generate_acceptance_letters.py
python3 regis/send_acceptance_emails.py --sender your-address@gmail.com
```

Previews are saved under `acceptance_letters/email_previews/`. No emails are
sent without `--send`. Missing PDFs or invalid recipient addresses stop the
whole batch before sending starts. The From address is the authenticated Gmail
account; Reply-To defaults to `icnaao2026@kmutt.ac.th` and can be changed with
`--reply-to`.

#### Browser sign-in with OAuth (alternative to an app password)

Use `--auth oauth` to authorize through your browser and send with the Gmail
API. This option requires Google's Python libraries in addition to the standard
library. It uses the same recipient selection, matching PDFs, and send history
as SMTP, so switching methods does not resend letters already recorded as sent.

One-time setup, following [Google's desktop OAuth guide](https://developers.google.com/workspace/gmail/api/quickstart/python):

1. Create or select a project in [Google Cloud Console](https://console.cloud.google.com/)
   and enable the **Gmail API**.
2. Configure the **Google Auth platform** app information/consent screen. For a
   personal Gmail account, use an **External** audience and add your sender
   account as a **test user** while the app is in Testing. A Workspace project
   may use Internal if it is limited to that organization.
3. Under **Clients**, create an OAuth client with application type **Desktop app**.
   Download its JSON and save it as `regis/gmail_credentials.json`.
4. Install the optional libraries in a local virtual environment:

   ```sh
   python3 -m venv regis/.venv
   regis/.venv/bin/python -m pip install -r regis/requirements-gmail.txt
   ```

Send one test letter to yourself:

```sh
regis/.venv/bin/python regis/send_acceptance_emails.py --auth oauth \
  --sender your-address@gmail.com --row 3 --test-to your-address@gmail.com --send
```

Sign in to the account matching `--sender` and grant permission when the browser
opens. Run the script on the same computer as your browser so the local OAuth
callback can complete. It requests Gmail **send** access and basic account-email
identity to verify the sender; it does not request mailbox-reading access.
Use the account's primary email for `--sender` (sending aliases are not supported
by this verification). Tokens are cached at `regis/gmail_token.json`, with local
file permissions restricted to the current user, and refreshed on later runs.
The credentials, token cache, and virtual environment are ignored by Git.

Then send the remaining letters:

```sh
regis/.venv/bin/python regis/send_acceptance_emails.py --auth oauth \
  --sender your-address@gmail.com --send
```

You can set other paths with `--credentials` and `--token`; keep custom token
files private too. If a cached token expires or lacks the required permissions,
use a new `--token regis/gmail_token_new.json` to sign in again. If Google blocks
authorization for a university/Workspace account, its administrator may need
to allow the OAuth app. OAuth does not bypass an organization's access policy.

#### SMTP with an app password

To send, enable 2-Step Verification and create a
[Google app password](https://support.google.com/accounts/answer/185833?hl=en).
App passwords may be unavailable for some accounts or restricted by a Workspace
administrator. The script connects to `smtp.gmail.com:465` using SSL, following
[Google's SMTP setup guidance](https://support.google.com/a/answer/176600?hl=en).
It prompts for the app password locally; do not put your regular Google password
or an app password in the repository. For unattended use, the script also reads
`GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` from the environment.

Send one sample to yourself first (row 3's letter, with its actual author/title):

```sh
python3 regis/send_acceptance_emails.py --sender your-address@gmail.com \
  --row 3 --test-to your-address@gmail.com --send
```

Send the batch to the authors:

```sh
python3 regis/send_acceptance_emails.py --sender your-address@gmail.com --send
```

Use `--row 3 --row 5` to limit the recipients, or pass another ODS path as the
positional argument. `--letters-dir` selects another PDF directory.

`acceptance_letters/email_send_history.jsonl` records names, titles, recipient
addresses, PDF filenames, Message-IDs, and delivery states. A `sent` entry means
Gmail accepted the message for sending; it does not guarantee inbox delivery.
Successful sends are skipped on later runs. If a connection breaks during a
send, that message remains `sending` and is also skipped: check Gmail's Sent
folder before retrying it with `--row N --resend --send`. An explicit SMTP
rejection is recorded as `failed` and can be retried normally. Delivery is
recorded as `partial` if Gmail accepts some contact addresses but rejects others;
these are skipped on reruns until you explicitly use `--resend`. Test sends are
logged separately and do not mark an author's letter as sent. Keep this history
file to preserve duplicate protection. A file lock prevents simultaneous sends
from sharing the same history file. Previews and history are ignored by Git.
