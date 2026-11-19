# SiteIntel — Authentication and Construction Reports

SiteIntel is a modular construction-intelligence web application built with
Flask, server-rendered HTML, CSS, JavaScript, Flask-SQLAlchemy, and MySQL. It
includes CSRF-protected account registration/login/logout, a protected
dashboard, and authenticated daily construction report uploads with local OCR.
The existing datasets remain available as source material and are not used by
the application.

## Requirements

- Python 3.10+
- MySQL 8.x (or a compatible MySQL server)
- Tesseract OCR
- PDFium through the `pypdfium2` Python dependency for PDF reports
- MySQL credentials with permission to create `siteintel_db`, or a database
  administrator who can create it and grant the application user access

## Install

From the project root:

```sh
python3.13 -m venv .venv-py313
source .venv-py313/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

For this project, use `.venv-py313` when running the application or tests.
On macOS, install the local OCR executable separately:

```sh
brew install tesseract
```

The Python `pytesseract` package calls the Tesseract executable; it does not
download or install the OCR engine itself.

Edit `.env` locally. Set a random `SECRET_KEY` of at least 32 characters and
the `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_HOST`, and `MYSQL_PORT` for your
MySQL installation. Do not commit `.env`.

Example secret generation:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

For local HTTP development, leave `SESSION_COOKIE_SECURE=false`. Set it to
`true` when the app is served over HTTPS.

## Initialize MySQL

The application database name is fixed as `siteintel_db`. Initialize the
database and `users` table without dropping existing data:

```sh
flask --app siteintel:create_app db-init
```

`db-init` connects to the MySQL server using the configured credentials,
executes `CREATE DATABASE IF NOT EXISTS siteintel_db`, then applies any
pending Alembic migrations. It does not delete or recreate the database. The
MySQL account needs database-creation privileges for this command.
Alternatively, an administrator can create the database and grant the
configured application user access before running the command.

To apply migrations to an existing database:

```sh
flask --app siteintel:create_app db upgrade
```

Migration history retains the original `ad5d0ec2fe8e` users-table revision.
The project foundation migration is a child revision that adds tables without
recreating or stamping over the existing users table.

Check connectivity:

```sh
flask --app siteintel:create_app db-check
```

## Start the application

From the repository root, with the virtual environment active and `.env`
configured:

```sh
flask --app siteintel:create_app run --host 127.0.0.1 --port 5001
```

Open <http://127.0.0.1:5001/>. The root redirects signed-out visitors to the
Register/Login page. The dashboard is at `/dashboard/` and requires an
authenticated session.

For development auto-reload:

```sh
flask --app siteintel:create_app run --debug --host 127.0.0.1 --port 5001
```

Do not use Flask's development server in production. Deploy behind a
production WSGI server and HTTPS, and set `SESSION_COOKIE_SECURE=true`.

## Authentication behavior

- Registration validates full name, email, password strength, and confirmation
  on both the client and server.
- Email is normalized to lowercase and unique in MySQL.
- Werkzeug scrypt password hashes are stored; raw passwords are never persisted.
- Login errors do not reveal whether an email address is registered.
- Flask-Login protects the dashboard and manages authenticated sessions.
- Flask-WTF CSRF protection applies to registration, login, and logout forms.
- Logout is a CSRF-protected POST request.

## Tests

Run the automated suite:

```sh
python -m unittest discover -s tests -v
```

Tests use an isolated in-memory SQLite database and cover authentication,
report API access controls, labeled-field extraction, and local Tesseract OCR.
They do not create or modify MySQL records.

## Daily construction report extraction

After login, create or open a project on the dashboard, then upload a
JPG/JPEG/PNG image or PDF in its workspace (maximum 10 MB, PDF maximum 20 pages).
An upload without a project through the existing API remains extraction only.
The browser previews the document and sends it to Flask with the CSRF token. The backend
verifies images, preserves the uploaded bytes and stores reports under the authenticated user's private
`instance/report_uploads/<user-id>/` directory with a random filename and
restrictive permissions. PDF pages are rendered locally with PDFium and passed
through the same local Tesseract OCR pipeline. It stores the extracted
text and labeled fields beside it. Project document previews require project access. The
current implementation does not provide report deletion or retention cleanup.

The extractor returns values written as `label: value` or `label=value`, and
also recognizes the 12 model inputs written as separator-less table rows
(for example `Rainfall 12 mm (forecast)` or `Weather risk Medium ...`). Numeric
rows need a recognized unit, which is converted to the model unit (cm/in to mm,
kg to tonnes, °F to °C, weeks to days); the matched line is kept as evidence.
Full OCR text is preserved for review. Printed text and clear tables work
best. Handwriting recognition is best-effort and depends on Tesseract. Reports
are stored per user; project members can review documents according to their access permissions.

API endpoints (all require an authenticated session and CSRF protection for
POST requests):

- `POST /api/reports/extract` — multipart upload (`image`, optional `project_id`);
  runs OCR, extracts labeled fields, and stores the report for its owner.
- `GET /api/reports/<report_id>` — retrieve the report data for its owner.

## ML predictions

The authenticated dashboard links to **ML Predictions** at `/dashboard/ml/`.
After a project report is reviewed and confirmed, it automatically produces
separate prediction records for each compatible model. Only owners and editors can upload or complete project predictions;
viewers can see the history. Results show input values and whether they came
from OCR or were entered manually. A missing, conflicting, invalid, or
unit-ambiguous field must be provided by an editor; no missing value is
fabricated. Re-running a prediction is explicit. Standalone manual forms and
the original endpoints remain available without project history.

Each project has its own workspace with overview, reports, predictions, and
history. After uploading, select a report to review its
extracted fields, correct any mistakes, and confirm the information. The
original OCR text is retained; confirmed reviews and updated predictions are
stored as separate history entries. The current prediction is tracked separately
from its history so confirming unchanged inputs or reverting to an earlier
snapshot does not create duplicates. Project viewers may review reports but
only owners and editors may confirm changes. Existing records and expenses
remain in the database and project API.

Automatic mapping recognizes the model feature names and aliases such as
`Precipitation`, `Cement content`, and `Curing age` as labeled OCR fields,
ignoring spaces, underscores, hyphens, and letter case. Values for `cement`,
`slag`, and `water` must explicitly include `kg/m3` or `kg/m³`; `age` must
include `day` or `days`. For example:

```text
cement: 350 kg/m3
curing age (days): 28
rain_mm: 12 mm
Material Availability: Medium
```

The other numeric feature names imply their displayed units; an optional unit
must match. Category values must match the saved encoder categories. Values
with unsupported units or conflicting repeated labels require manual review.
OCR can make mistakes, so review extracted inputs and model outputs before use.

Authenticated, CSRF-protected JSON endpoints:

- `POST /api/ml/cement-demand` — accepts `planned_pour_tonnes`,
  `site_rolling_mean_30`, `rain_mm`, and `avg_temp_c`; returns predicted
  consumption in tonnes.
- `POST /api/ml/concrete-strength` — accepts `cement`, `slag`, `water`, and
  `age`; returns predicted compressive strength in MPa.
- `POST /api/ml/delay-risk` — accepts `Material_Availability`,
  `Equipment_Availability`, `Weather_Risk`, and `Supplier_Delay`; returns
  class-1 probability, class, and a Yes/No label. The default classification
  threshold is `0.5`, configurable with the Flask `DELAY_RISK_THRESHOLD`
  setting. It is not an optimized threshold.

Model packages are loaded and cached by the centralized registry from the
root `pkl_files/` directory. The model pickle files are excluded from Git;
distribute them separately or through an appropriate model-artifact storage
mechanism so a fresh checkout can load the models.

The checked-in datasets remain available as source material. Prediction
responses are model outputs for the submitted values, not guarantees of
construction outcomes.

## Shared project data foundation

Projects are owned by a user. Owners have full project administration access;
invited project members have either `viewer` or `editor` access. Use
`get_accessible_project` from `siteintel.projects` to scope project retrieval
by the authenticated user's ID and requested permission; use
`get_owned_project` for owner-only administration. Project-owned
documents, construction records, expenses, and quotation items restrict
project deletion while data exists. Membership rows are removed with their
project.

The shared SQLAlchemy models are `Project`, `ProjectMember`, `Document`,
`ConstructionRecord`, `Expense`, and `QuotationItem`. Their schema is managed
through Flask-Migrate; run `flask db upgrade` after updating the application.
The prediction-history migration adds `document_predictions`, linked to the
existing project document and the user who created each prediction snapshot.
It preserves previous snapshots and enforces uniqueness for automatic runs.
Project-associated uploads create a `Document` row; existing unassociated
reports are not automatically attached to a project.

## Dashboard and project workspace

`/dashboard/` lists the user's projects (stats, search, status filter, sort)
and creates projects. Each project opens a workspace at
`/dashboard/projects/<id>/` with sections `ml-predictions`,
`document-assistant`, `expenses`, `ai-agent`, `communication`, `reports` and
`settings`. Access control is unchanged (inaccessible projects return 404).

Migration `6c1d4e7a9b20` adds `projects.location` and the `in_progress`
status. Apply it with `flask --app siteintel:create_app db upgrade` (back up
the database first).

Later-phase integration points are the placeholder pages' containers:
`#ai-agent-module` and `#communication-module` in
`templates/project/`.

## Expenses & Bills (Phase 3)

The project workspace displays analytics derived from indexed project PDFs
and generated Daily Construction Reports. The existing document pipeline
extracts identifiable monetary figures into source-linked observations.
Editors must verify or ignore each figure without entering financial data;
review decisions can be revoked or revisited, and only confirmed figures
appear in calculations. Existing manual expenses are
preserved but excluded. Viewers cannot review figures.

The extractor is deliberately conservative: it requires explicit BDT, USD,
EUR, GBP or INR currency codes (or unambiguous ৳, ₹, € or £ symbols) and
recognizable cost, total, payment, budget or outstanding labels. Missing dates
and amounts are not guessed. Dates are taken from unambiguous ISO or
day-month-name document dates or the generated report date. OCR and
document formats vary, and extracted figures may be incomplete or incorrect.
Reviewers should check each source and ignore estimates or duplicate bills.
Quotation totals and payment schedules are not expenses. Verified invoice
totals and direct expenses count once; actual payments, delivery charges and
line items are displayed separately rather than added to invoice totals.
Unknown categories remain unclassified. Material components from quotations
are labeled as quoted, not actual spending.
Repeated processing of an unchanged document preserves review decisions;
changed figures require a new review. Repeated totals and delivery charges in
one document are deduplicated. Cross-document deduplication requires an
explicit matching document type, number and vendor; other possible duplicates
still require human review. Amounts in different currencies are never added
together.

The dashboard offers date, category and project filters, category and trend
charts, source links, and individual budgets when available. Figures without
dates do not appear in date-filtered views. AI insights require the existing
LLM settings to point to a numeric local loopback address (such as
`127.0.0.1`); proxies and redirects are disabled for these calls. No project
data is sent to a remote language model by this feature. Generated insights require review.
Use Document Assistant for PDFs and Report Builder for image-based source
reports; generated reports are indexed by the same RAG pipeline.

Back up the database and run `flask --app siteintel:create_app db upgrade`.
Then run `flask --app siteintel:create_app finance-backfill` once to extract
reviewable figures from previously indexed documents. Newly processed or
reprocessed documents are handled automatically.

## Document Assistant (Phase 2)

Project-scoped document Q&A at `/dashboard/projects/<id>/document-assistant/`.
Users upload PDFs (agreement, quotation, specification, BOQ, other), which are
extracted per page (scanned pages fall back to the existing Tesseract OCR,
capped at `DOCUMENT_MAX_OCR_PAGES`), cleaned, chunked (about 1000 characters,
150 overlap, never crossing a page), embedded and stored in `document_chunks`.

- **Storage:** the existing `documents` table, with a `category` column that is
  NULL for Phase 1 daily reports. Report code only reads `category IS NULL`
  rows. Files live in `instance/project_documents/<project_id>/<uuid>.pdf`.
- **Retrieval:** local sparse hashed-term embeddings plus BM25 ranking, with no
  external service or new dependency. It is lexical, so it does not match
  synonyms. Only the requesting project's completed documents are searched.
- **Answers:** if `LLM_MODEL` and `LLM_API_KEY` are set, any OpenAI-compatible
  chat endpoint (`LLM_BASE_URL`, default OpenAI) writes the answer from the
  retrieved passages. Citations come from the retrieved chunks, never the model
  text. Without an LLM, or if it fails, the top passages are shown with
  citations. Unsupported questions return the fixed "could not find sufficient
  information" message.
- **Settings (env):** `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`,
  `LLM_TIMEOUT_SECONDS`, `DOCUMENT_MAX_BYTES` (25 MB), `DOCUMENT_MAX_PAGES`
  (300), `DOCUMENT_MAX_OCR_PAGES` (20), `DOCUMENT_UPLOAD_ROOT`.
- **Phase 4 interface** (`siteintel.documents.qa`; callers must authorise the
  project first): `search_project_documents(project_id, question)`,
  `answer_project_question(project_id, question)` and
  `get_document_sources(project_id, citations)`.
- **Migration:** `7d2e8f31b4c5` is additive (5 nullable `documents` columns and
  the `document_chunks` table). Back up, then run
  `flask --app siteintel:create_app db upgrade`.

Limitations: PDF only, processing runs inside the upload request, and the
"enough evidence" threshold is a heuristic.

## Future modules

Authentication, dashboard, and reports are separate blueprints; database
extensions and configuration are centralized in `siteintel/`. Contract RAG
and the AI assistant are not part of this stage.
