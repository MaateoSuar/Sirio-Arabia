from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import UserMixin, login_user, logout_user, current_user
from ..extensions import login_manager

bp = Blueprint("auth", __name__)

# Hardcoded user: sirio/sirio
class User(UserMixin):
    def __init__(self, id: int, username: str):
        self.id = id
        self.username = username

# In-memory credentials
USERS = {
    "sirio": {"password": "sirio", "id": 1},
}

@login_manager.user_loader
def load_user(user_id):
    try:
        uid = int(user_id)
    except Exception:
        return None
    # Only one user for now
    for uname, data in USERS.items():
        if data.get("id") == uid:
            return User(id=uid, username=uname)
    return None

@bp.before_app_request
def require_login_globally():
    # Allow auth endpoints and static files without login
    from flask import request
    endpoint = request.endpoint or ""
    if endpoint.startswith("auth.") or endpoint == "static":
        return None
    if not current_user.is_authenticated:
        next_url = request.url
        return redirect(url_for("auth.login", next=next_url))
    return None

@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username = (request.form.get("username") or request.form.get("email") or "").strip()
        password = (request.form.get("password") or "").strip()
        if username in USERS and USERS[username]["password"] == password:
            user = User(id=USERS[username]["id"], username=username)
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main.index"))
        # Failed auth: re-render login with error flag
        return render_template("login.html", error=True)

    return render_template("login.html", error=False)

@bp.route("/logout")
def logout():
    if current_user.is_authenticated:
        logout_user()
    return redirect(url_for("auth.login"))
