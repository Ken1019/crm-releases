import os
import secrets

from flask import Flask

from . import db

VERSION = "0.1.0"


def _secret_key(instance_path):
    path = os.path.join(instance_path, "secret_key")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def create_app(test_config=None):
    data_dir = os.environ.get("GHMS_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    app = Flask(__name__, instance_path=os.path.abspath(data_dir))
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        DATABASE=os.path.join(app.instance_path, "ghms.sqlite3"),
        SECRET_KEY=_secret_key(app.instance_path),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    db.init_db(app.config["DATABASE"])
    app.teardown_appcontext(db.close_db)

    from . import auth, crud, views

    app.register_blueprint(auth.bp)
    app.register_blueprint(crud.bp)
    app.register_blueprint(views.bp)
    auth.install(app)

    from .entities import ENTITIES, GROUPS

    app.jinja_env.filters["reject_page"] = lambda args: {k: v for k, v in args.items() if k != "page"}

    @app.context_processor
    def inject():
        return {"ENTITIES": ENTITIES, "GROUPS": GROUPS, "VERSION": VERSION, "office_name": db.get_setting("office_name")}

    return app
