import os

from backend.app import app

if __name__ == "__main__":
    debug = (os.getenv("FLASK_DEBUG", "0") == "1")
    try:
        db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI") or "")
        if db_uri.startswith("sqlite"):
            debug = True
    except Exception:
        pass
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug)
