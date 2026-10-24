"""Lets small UI add-ons load themselves: any static/ext_*.js file is added to the main page."""
from flask import Blueprint

import config

bp = Blueprint("ext", __name__)


@bp.app_context_processor
def inject_extra_scripts():
    static_dir = config.BASE_DIR / "static"
    return {"extra_scripts": sorted(p.name for p in static_dir.glob("ext_*.js"))}