"""
OpenAI Diagnostic Endpoint for HotelChat

This module defines a Flask blueprint that can be attached to the main application
to provide a diagnostic endpoint for testing OpenAI integration.
"""

from flask import Blueprint, request, jsonify, current_app
import logging
import time
import asyncio
from openai import AsyncOpenAI, OpenAI
from openai.types.error import RateLimitError, APIError, AuthenticationError
from openai.types.timeout_error import APITimeoutError

# Create a blueprint
openai_diag_bp = Blueprint('openai_diag', __name__)

# Configure logging
logger = logging.getLogger("openai_diag")

@openai_diag_bp.route('/openai_diag', methods=['POST'])
def openai_diagnostic_endpoint():
    """
    Endpoint to test OpenAI API integration.
    
    Expects a JSON payload with:
    {
        "prompt": "Your test prompt",
        "max_tokens": 50  # Optional, defaults to 50
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'prompt' not in data:
            return jsonify({
                "status": "error",
                "error": "Missing 'prompt' in request body"
            }), 400
        
        prompt = data['prompt']
        max_tokens = data.get('max_tokens', 50)
        
        # Use the application's OpenAI client
        from chat_server import openai_client, OPENAI_API_KEY
        
        if not OPENAI_API_KEY:
            return jsonify({
                "status": "error",
                "error": "OpenAI API key not configured"
            }), 500
        
        # Create a sync client for simplicity in this endpoint
        client = OpenAI(api_key=OPENAI_API_KEY, timeout=30.0)
        
        start_time = time.time()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.7
        )
        
        processing_time = (time.time() - start_time) * 1000  # Convert to ms
        
        result = {
            "status": "success",
            "response": response.choices[0].message.content,
            "processing_time_ms": round(processing_time, 2),
            "model": "gpt-3.5-turbo",
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
        }
        
        logger.info(f"OpenAI diagnostic test successful: {round(processing_time, 2)}ms, {response.usage.total_tokens} tokens")
        
        return jsonify(result)
        
    except RateLimitError as e:
        logger.error(f"OpenAI RateLimitError: {str(e)}")
        return jsonify({
            "status": "error",
            "error": "Rate limit exceeded",
            "details": str(e)
        }), 429
    except APITimeoutError as e:
        logger.error(f"OpenAI Timeout: {str(e)}")
        return jsonify({
            "status": "error",
            "error": "API request timed out",
            "details": str(e)
        }), 504
    except AuthenticationError as e:
        logger.error(f"OpenAI Authentication Error: {str(e)}")
        return jsonify({
            "status": "error",
            "error": "Authentication failed",
            "details": str(e)
        }), 401
    except APIError as e:
        logger.error(f"OpenAI API Error: {str(e)}")
        return jsonify({
            "status": "error",
            "error": "API error",
            "details": str(e)
        }), 500
    except Exception as e:
        logger.error(f"Unexpected error in OpenAI diagnostic endpoint: {str(e)}")
        return jsonify({
            "status": "error",
            "error": "Unexpected error",
            "details": str(e)
        }), 500
```

# Register this blueprint in chat_server.py with:
# from openai_diag_endpoint import openai_diag_bp
# app.register_blueprint(openai_diag_bp)
