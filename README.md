# SiteIntel — Phase 1

SiteIntel is a modular construction-intelligence web application. Phase 1
implements account registration, login/logout, CSRF-protected forms, and a
protected account dashboard using Flask, server-rendered HTML, CSS, JavaScript,
Flask-SQLAlchemy, and MySQL. The `pkl_files/`, `Notebooks/`, and `Datasets/`
directories are preserved for later phases; this phase does not load or change
their contents.

## Requirements

- Python 3.10+
- MySQL 8.x (or a compatible MySQL server)
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

Tests use an isolated in-memory SQLite database and cover persistence semantics,
password hashing, validation, duplicate registration, login/logout, CSRF, route
protection, and database-error handling. They do not create or modify MySQL
records. To verify the real MySQL integration, configure `.env`, run `db-init`
and `db-check`, then register through the browser and inspect the user row using
your MySQL client.

## Future modules

Authentication and dashboard are separate blueprints; database extensions and
configuration are centralized in `siteintel/`. Future reporting, extraction,
prediction, document retrieval, and assistant modules can be added as
independent packages and blueprints without changing the authentication model.
