from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from siteintel.auth import auth_bp
from siteintel.auth.models import User
from siteintel.auth.validation import (
    validate_email,
    validate_full_name,
    validate_password,
)
from siteintel.extensions import db


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "GET":
        return render_template("auth.html", active_tab="register")

    full_name = request.form.get("full_name", "")
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    password_confirmation = request.form.get("password_confirmation", "")

    errors = {
        message
        for message in (
            validate_full_name(full_name),
            validate_email(email),
            validate_password(password),
            (
                "Passwords do not match."
                if password != password_confirmation
                else None
            ),
        )
        if message
    }
    if errors:
        for message in sorted(errors):
            flash(message, "error")
        return render_template(
            "auth.html",
            active_tab="register",
            full_name=full_name.strip(),
            email=email,
        ), 400

    try:
        user = User(
            full_name=full_name.strip(),
            email=email,
            password_hash=generate_password_hash(password, method="scrypt"),
        )
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("An account with this email already exists.", "error")
        return render_template(
            "auth.html",
            active_tab="register",
            full_name=full_name.strip(),
            email=email,
        ), 409
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to create a user account")
        flash("We could not create your account right now. Please try again.", "error")
        return render_template(
            "auth.html",
            active_tab="register",
            full_name=full_name.strip(),
            email=email,
        ), 500

    flash("Your account was created. You can now log in.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "GET":
        return render_template("auth.html", active_tab="login")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    email_error = validate_email(email)
    password_error = (
        "Enter a valid password."
        if not password or len(password) > 128
        else None
    )
    if email_error or password_error:
        flash(email_error or password_error or "Invalid login details.", "error")
        return render_template("auth.html", active_tab="login", email=email), 400

    try:
        user = db.session.execute(
            db.select(User).where(User.email == email)
        ).scalar_one_or_none()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to look up a login account")
        flash("We could not log you in right now. Please try again.", "error")
        return render_template("auth.html", active_tab="login", email=email), 500

    if user is None or not check_password_hash(user.password_hash, password):
        flash("Invalid email or password.", "error")
        return render_template("auth.html", active_tab="login", email=email), 401

    session.clear()
    login_user(user, remember=False, fresh=True)
    session.permanent = True
    flash("You are now logged in.", "success")
    return redirect(url_for("dashboard.index"))


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
