from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_user, logout_user, current_user
from sqlalchemy import func

from ..extensions import login_manager
from ..models import AppUser

bp = Blueprint("auth", __name__)

@login_manager.user_loader
def load_user(user_id):
    try:
        uid = int(user_id)
    except Exception:
        return None
    try:
        return AppUser.query.get(uid)
    except Exception:
        return None

@bp.before_app_request
def require_login_globally():
    # Allow auth endpoints and static files without login
    from flask import request
    endpoint = request.endpoint or ""
    if endpoint.startswith("auth.") or endpoint == "static":
        return None
    try:
        if (request.path or "").strip().lower() == "/favicon.ico":
            return None
    except Exception:
        pass
    if not current_user.is_authenticated:
        next_url = request.url
        return redirect(url_for("auth.login", next=next_url))
    return None

@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username_raw = (request.form.get("username") or request.form.get("email") or "").strip()
        password = (request.form.get("password") or "").strip()

        uname = (username_raw or "").strip().upper()
        u = None
        try:
            if uname:
                u = AppUser.query.filter(func.upper(AppUser.username) == uname).first()
        except Exception:
            u = None
        if u is not None and u.check_password(password):
            login_user(u)
            next_page = (request.args.get("next") or "").strip()
            safe_next = ""
            try:
                if next_page.startswith("/") and ("//" not in next_page):
                    if next_page.lower().startswith("/favicon"):
                        safe_next = ""
                    else:
                        safe_next = next_page
            except Exception:
                safe_next = ""
            return redirect(safe_next or url_for("main.index"))

        return render_template("login.html", error=True)

    return render_template("login.html", error=False)

@bp.route("/logout")
def logout():
    if current_user.is_authenticated:
        logout_user()
    return redirect(url_for("auth.login"))
