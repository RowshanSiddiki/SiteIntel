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
- MySQL credentials with permission to create `siteintel_db`, or a database
  administrator who can create it and grant the application user access

## Install

From the project root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

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
executes `CREATE DATABASE IF NOT EXISTS siteintel_db`, then creates any
missing application tables through SQLAlchemy. It does not delete or recreate
the database. The MySQL account needs database-creation privileges for this
command. Alternatively, an administrator can create the database and grant
the configured application user access before running the command.

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

After login, use **Daily Construction Report** on the dashboard to upload one
JPG/JPEG/PNG image (maximum 10 MB). The browser previews the image and sends it
to Flask with the CSRF token. The backend verifies the actual image format,
re-encodes the image, stores it under the authenticated user's private
`instance/report_uploads/<user-id>/` directory with a random filename and
restrictive permissions, runs local Tesseract OCR, and stores the extracted
text and labeled fields beside it. There is no public file-serving route. The
current implementation does not provide report deletion or retention cleanup.

The extractor returns values written as `label: value` or `label=value` and
preserves the full OCR text for review. Printed text and clear tables work
best. Handwriting recognition is best-effort and depends on Tesseract. Reports
are stored per user and can only be retrieved by their owner.

API endpoints (all require an authenticated session and CSRF protection for
POST requests):

- `POST /api/reports/extract` — multipart upload (`image`); runs OCR, extracts
  labeled fields, and stores the report for its owner.
- `GET /api/reports/<report_id>` — retrieve the report data for its owner.

## Model integration preparation

The root `pkl_files/` directory is retained for these future artifacts:

- `cement_demand.pkl`
- `budget_overrun.pkl`
- `construction_delay.pkl`

No replacement models are currently present. The application has a centralized
registry with absolute artifact paths and a one-time load cache. Model-specific
predictors and prediction routes will be added only after the actual artifacts,
serialization libraries, and training feature definitions are available.

The checked-in datasets are retained as source material. The Flask application
does not import any ML libraries until the replacement model contracts are
available.

## Future modules

Authentication, dashboard, and reports are separate blueprints; database
extensions and configuration are centralized in `siteintel/`. Contract RAG
and the AI assistant are not part of this stage.
