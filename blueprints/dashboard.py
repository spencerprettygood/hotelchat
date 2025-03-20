from flask import Blueprint, render_template, jsonify
from flask_login import login_required
import logging

# Set up logging
logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/')
@login_required
def dashboard():
    try:
        return render_template("dashboard.html")
    except Exception as e:
        logger.error(f"❌ Error rendering dashboard page: {e}")
        return jsonify({"error": "Failed to load dashboard page"}), 500
