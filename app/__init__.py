import os
from flask import Flask


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "cfo-dashboard-dev-key")
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB per upload

    from .routes import main
    app.register_blueprint(main)

    return app
