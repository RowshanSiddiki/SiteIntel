from flask import render_template
from flask_login import current_user, login_required

from siteintel.dashboard import dashboard_bp


@dashboard_bp.get("/")
@login_required
def index():
    return render_template("dashboard.html", user=current_user)
