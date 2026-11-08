# SiteIntel — Authentication and Construction Reports

SiteIntel is a modular construction-intelligence web application built with
Flask, server-rendered HTML, CSS, JavaScript, Flask-SQLAlchemy, and MySQL. It
includes CSRF-protected account registration/login/logout and a protected
dashboard. The dashboard also accepts daily report images and runs local OCR
to prepare structured report fields for five trained workforce, supply-chain, construction-cost,
civil-risk, and cement-demand models. The `pkl_files/`, `Notebooks/`, and
`Datasets/` contents are preserved.

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
report API access controls, extraction, readiness, and local Tesseract OCR.
They do not create or modify MySQL records. The three newly integrated models
are exercised against actual rows from their checked-in datasets and actual
pickle artifacts. Workforce and supply-chain prediction endpoints remain
blocked because those two existing artifacts are incompatible with their
required input/target constraints described below.

## Daily construction report extraction

After login, use **Daily Construction Report** on the dashboard to upload one
JPG/JPEG/PNG image (maximum 10 MB). The browser previews the image and sends it
to Flask with the CSRF token. The backend verifies the actual image format,
re-encodes the image, stores it under the authenticated user's private
`instance/report_uploads/<user-id>/` directory with a random filename and
restrictive permissions, runs local Tesseract OCR, and stores the extracted
text and structured information beside it. There is no public file-serving
route. The current implementation does not provide report deletion or
retention cleanup.

The OCR parser reads labeled values and aligned table rows. Numeric values are
typed using the model metadata; missing values remain `null`. Workforce date
values follow the sample's month/day/year convention and supply-chain dates
follow the sample's day-month-year convention; ISO dates are also accepted.
Printed text and clear tables work best. Handwriting support is best-effort and
depends on Tesseract being able to recognize it. Original OCR text is retained
and displayed so users can inspect what was read. A detected multi-record table
is not reduced to an arbitrary first row: readiness is blocked for the affected
model and the dashboard explains why. Unparseable date text is retained for
review, but no calendar features are derived from it.

API endpoints (all require an authenticated session and CSRF protection for
POST requests):

- `POST /api/reports/extract` — multipart upload (`image`); runs OCR, extracts
  fields once, and returns independent readiness for all five models.
- `GET /api/reports/<report_id>` — retrieve the report data for its owner.
- `POST /api/reports/<report_id>/predict/workforce` — requests workforce risk.
- `POST /api/reports/<report_id>/predict/supply-chain` — requests stockout
  classification.
- `POST /api/reports/<report_id>/predict/construction-estimate` — estimates
  `Total_Estimate`.
- `POST /api/reports/<report_id>/predict/civil-risk` — predicts `is_good` and,
  when supported, returns both class probabilities.
- `POST /api/reports/<report_id>/predict/cement-demand` — estimates
  `consumed_tonnes`.

The backend uses metadata feature order and numeric/categorical declarations.
The model pipelines handle legitimate remaining nulls; the API does not fill
missing fields with made-up values. Data readiness is assessed separately for
each model. A prediction additionally requires the corresponding model
artifact to pass compatibility checks.

### Workforce model inputs

The metadata's allowed feature columns, in its stored order, are:

`department`, `team`, `manager_id`, `manager_name`, `location`, `job_role`,
`employment_type`, `hire_date`, `tenure_band`, `work_date`,
`week_start_date`, `month_start_date`, `day_of_week`, `is_weekend`,
`is_holiday`, `shift_name`, `scheduled_start_time`, `scheduled_end_time`,
`scheduled_hours`, `schedule_type`, `attendance_status`, `actual_clock_in`,
`actual_clock_out`, `actual_hours_worked`, `minutes_late`,
`minutes_left_early`, `absence_type`, `excused_flag`,
`no_call_no_show_flag`, `partial_shift_flag`, `late_flag`, `absence_flag`,
`present_flag`, `regular_hours`, `overtime_hours`, `hourly_rate`,
`overtime_rate_multiplier`, `regular_labor_cost`, `overtime_labor_cost`,
`total_labor_cost`, `overtime_flag`, `high_overtime_flag`,
`coverage_gap_flag`, `replacement_needed_flag`,
`attendance_policy_exception_flag`, `data_quality_issue_flag`, and
`data_quality_issue_type`.

Metadata numeric columns are `is_weekend`, `is_holiday`, `scheduled_hours`,
`actual_hours_worked`, `minutes_late`, `minutes_left_early`, `excused_flag`,
`no_call_no_show_flag`, `partial_shift_flag`, `late_flag`, `absence_flag`,
`present_flag`, `regular_hours`, `overtime_hours`, `hourly_rate`,
`overtime_rate_multiplier`, `regular_labor_cost`, `overtime_labor_cost`,
`total_labor_cost`, `overtime_flag`, `high_overtime_flag`, `coverage_gap_flag`,
`replacement_needed_flag`, `attendance_policy_exception_flag`, and
`data_quality_issue_flag`. The remaining allowed columns are categorical.

No target/leakage columns or employee identifiers are extracted or sent. The
actual workforce pickle nevertheless requires `attendance_record_id` as one
of its 49 pipeline inputs, in addition to the 48 permitted features. Because
that is an identifier forbidden by the model-input rules, the workforce
prediction button remains disabled until a compatible model artifact is
provided.

### Supply-chain model inputs

The metadata input order is `SKU_ID`, `Warehouse_ID`, `Supplier_ID`, `Region`,
`Units_Sold`, `Inventory_Level`, `Supplier_Lead_Time_Days`, `Reorder_Point`,
`Order_Quantity`, `Unit_Cost`, `Unit_Price`, `Promotion_Flag`, `date_year`,
`date_month`, `date_day`, and `date_dayofweek`. The first four are categorical;
the remaining twelve are numeric. Raw `Date`, `Stockout_Flag`, and
`Demand_Forecast` are never passed to the model. The report date is converted
to the four metadata calendar columns.

The checked-in supply-chain model has only class `0` in its `classes_`, and the
dataset target contains 91,250 zeros and no `1` examples. It cannot identify a
stockout class, so the stockout prediction button remains disabled rather than
displaying a misleading result. Replace it with a compatible artifact trained
on both target classes before enabling that prediction. The app does not
retrain or alter either pickle file.

### Construction-cost model inputs

The metadata feature order is `Material_Cost`, `Labor_Cost`, `Profit_Rate`,
`Discount_or_Markup`, `Policy_Reason`. The first four are numeric and the last
is categorical. `Total_Estimate` is excluded. Readiness requires all five
values to be read from the report; no costs or pricing policy are inferred.
The model returns the predicted number without a currency symbol because the
dataset does not document a currency or unit.

**Important model limitation:** every row in `construction_estimates.csv`
exactly follows
`(Material_Cost + Labor_Cost) * (1 + Profit_Rate / 100) + Discount_or_Markup`.
The model therefore learns a deterministic calculation from components that
directly define the target. Its output is not an independent cost estimate.

### Civil-risk model inputs

The model's `feature_columns` metadata order is `Certificates`, `Years of
Experience`, `age`, `Time Arrival Strafe`, `Project Cost`, `Project Proximity`,
`Violation Risk Index`, `Company PCAB Score`, `Weekly Overtime Hours`, and
`Salary Bracket`. All ten inputs are numeric. `is_good` and `Project ID` are
not inputs. Readiness requires all ten values to be explicitly present in the
report. Prediction is a model-based project assessment, not a definitive
judgment about a person or contractor. Since the model includes age and
experience, do not use its output for automated employment or other
consequential decisions.

### Cement-demand model inputs

The metadata feature order is `site_id`, `cement_type`,
`planned_pour_tonnes`, `rain_mm`, `avg_temp_c`, `year`, `month`,
`week_of_year`, `day_of_week`, `quarter`, `is_weekend`, `site_demand_lag_1`,
`site_demand_lag_7`, `site_demand_lag_14`, `site_rolling_mean_7`,
`site_rolling_mean_30`, `date_year`, `date_month`, `date_day`, and
`date_dayofweek`. The fitted pipeline identifies `site_id` and `cement_type`
as categorical and the remaining inputs as numeric; the metadata itself does
not list type groups. `date_dayofweek` uses Monday=0 through Sunday=6. The raw
`date` and target `consumed_tonnes` are never passed to the model. The report
date is transformed into the four `date_*` calendar features. No weather,
lag, rolling-history, or other values are generated; readiness requires every
input feature to be extracted or date-derived. Missing historical and weather
information therefore commonly leaves this prediction disabled.

The checked-in cement dataset has 31,980 rows, and `consumed_tonnes` exactly
equals `planned_pour_tonnes` in 19,195 rows. This is a material limitation of
the training data; no model-quality metric is documented here. Predictions
are estimates for the supplied date and inputs, not guaranteed future demand.

The serialized models were produced with scikit-learn 1.6.1. The dependency is
pinned to that version to avoid incompatible unpickling; use the commands in
**Install** to prepare the environment.

## Future modules

Authentication, dashboard, and reports are separate blueprints; database
extensions and configuration are centralized in `siteintel/`. Contract RAG,
the AI assistant, and prediction history are not part of this stage.
