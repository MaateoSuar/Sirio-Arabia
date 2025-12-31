from flask import Flask
from dotenv import load_dotenv
import os

from .extensions import db, migrate, login_manager
from .config import Config
from . import models
from flask_migrate import upgrade
from sqlalchemy import text


def create_app():
    load_dotenv()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config())

    db.init_app(app)
    migrate.init_app(app, db)
    # Auth
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    # Ensure DB schema exists in production (Railway)
    with app.app_context():
        did_upgrade = False
        if os.getenv("AUTO_MIGRATE") == "1":
            try:
                # If there are multiple heads (divergent migrations), apply them all.
                upgrade(revision="heads")
                did_upgrade = True
            except Exception:
                # Rely on logs in platform to inspect failure
                did_upgrade = False
                # On Postgres, don't continue booting with a potentially incompatible schema.
                try:
                    if db.engine.dialect.name != "sqlite":
                        raise
                except Exception:
                    raise
        # If migrations are not enabled or failed, fall back to create_all
        if not did_upgrade:
            try:
                db.create_all()
            except Exception:
                # As último recurso, dejar que la app arranque y ver logs
                pass

        # SQLite: create_all no agrega columnas nuevas; asegurar compatibilidad mínima
        try:
            if db.engine.dialect.name == "sqlite":
                cols = db.session.execute(text("PRAGMA table_info(client_company_link)"))
                col_names = {row[1] for row in cols.fetchall()}  # row[1] = name
                if "plazo_pago_dias" not in col_names:
                    db.session.execute(text("ALTER TABLE client_company_link ADD COLUMN plazo_pago_dias INTEGER"))
                    db.session.commit()
        except Exception:
            # No impedir el arranque si el check/alter falla
            try:
                db.session.rollback()
            except Exception:
                pass

    # Register blueprints
    from .views.auth import bp as auth_bp
    app.register_blueprint(auth_bp)
    from .views.main import bp as main_bp
    app.register_blueprint(main_bp)

    return app


# WSGI entrypoint for Railway
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
