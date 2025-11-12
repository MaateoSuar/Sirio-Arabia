from flask import Flask
from dotenv import load_dotenv
import os

from .extensions import db, migrate
from .config import Config
from . import models


def create_app():
    load_dotenv()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config())

    db.init_app(app)
    migrate.init_app(app, db)

    # Register blueprints
    from .views.main import bp as main_bp
    app.register_blueprint(main_bp)

    return app


# WSGI entrypoint for Railway
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
