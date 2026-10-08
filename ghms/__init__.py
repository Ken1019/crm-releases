import os
import secrets

from flask import Flask, g

from . import db

VERSION = "1.0.0"


def _secret_key(instance_path):
    path = os.path.join(instance_path, "secret_key")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def create_app(test_config=None):
    from .runtime import data_dir as _data_dir

    data_dir = _data_dir()
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

    from . import absences, auth, billing, crud, shift, updater, views

    app.register_blueprint(auth.bp)
    app.register_blueprint(crud.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(billing.bp)
    app.register_blueprint(shift.bp)
    app.register_blueprint(absences.bp)
    app.register_blueprint(updater.bp)
    auth.install(app)

    from .entities import ENTITIES, GROUP_ICONS, GROUPS

    app.jinja_env.filters["reject_page"] = lambda args: {k: v for k, v in args.items() if k != "page"}

    from .hubs import current_hub, hub_url, visible_hubs

    @app.context_processor
    def hubs():
        return {"hubs": visible_hubs() if getattr(g, "user", None) else [], "cur_hub": current_hub(), "hub_url": hub_url}

    @app.context_processor
    def inject():
        return {"ENTITIES": ENTITIES, "GROUPS": GROUPS, "GROUP_ICONS": GROUP_ICONS, "VERSION": VERSION, "office_name": db.get_setting("office_name")}

    return app
