# Add this near the top of your imports
from openai_diag_endpoint import openai_diag_bp

# Add this after your Flask app initialization
app.register_blueprint(openai_diag_bp)
