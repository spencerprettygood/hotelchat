# OpenAI Integration Diagnostic Tools

This document provides instructions for using the OpenAI diagnostic utilities to troubleshoot and verify the OpenAI integration in the HotelChat application.

## Quick Start

To test your OpenAI integration, run:

```bash
python openai_client_test.py
```

For a more comprehensive diagnostic with additional options:

```bash
python openai_diag_tool.py --prompt "Hello, how are you today?"
```

## Available Diagnostic Tools

### 1. OpenAI Client Test (`openai_client_test.py`)

A simple utility to verify OpenAI client initialization and basic API functionality.

#### Features:
- Verifies correct import paths for OpenAI v1.x API
- Tests both synchronous and asynchronous OpenAI clients
- Reports success/failure for each test

#### Usage:

```bash
python openai_client_test.py
```

#### Output:

The utility will output detailed logs about:
- OpenAI library version
- Import path verification
- Synchronous client test results
- Asynchronous client test results
- Overall success or failure

### 2. OpenAI Diagnostic Tool (`openai_diag_tool.py`)

A more comprehensive utility with additional features for diagnostics and integration.

#### Features:
- Command-line interface with configurable options
- Can be integrated as a Flask endpoint
- Tests API key validity
- Runs actual completion requests with timing information
- Provides detailed error diagnostics

#### CLI Usage:

```bash
# Basic usage
python openai_diag_tool.py

# With custom prompt
python openai_diag_tool.py --prompt "Generate a hotel recommendation"

# With specific model
python openai_diag_tool.py --model "gpt-3.5-turbo"

# With verbose output
python openai_diag_tool.py -v

# With custom API key (override environment variable)
python openai_diag_tool.py --api-key "your-api-key"
```

#### Flask Integration:

To use as a Flask endpoint:

1. Import the blueprint in your Flask application:
```python
from openai_diag_tool import create_flask_blueprint

app = Flask(__name__)
openai_diag_bp = create_flask_blueprint()
app.register_blueprint(openai_diag_bp)
```

2. Send POST requests to `/openai_diag` with a JSON body:
```json
{
  "prompt": "Tell me about hotel amenities",
  "api_key": "optional-custom-api-key" 
}
```

## Troubleshooting Common Issues

### API Key Problems

If the diagnostic reports "API key is invalid":
1. Check that your `OPENAI_API_KEY` environment variable is set correctly
2. Verify the key is active in your OpenAI dashboard
3. Try using the `--api-key` parameter to override the environment variable

### Import Path Issues

If you see "Import path verification failed":
1. Ensure you have the correct version of the OpenAI library installed:
   ```bash
   pip install -U 'openai>=1.3.0'
   ```
2. Verify your Python environment is using the correct packages

### Rate Limiting

If you encounter "Rate limit exceeded":
1. Wait a few minutes before trying again
2. Check your OpenAI usage dashboard for quota limits
3. Consider upgrading your OpenAI plan if this happens frequently

## Environment Setup

Both utilities rely on the `OPENAI_API_KEY` environment variable. You can set this:

1. In your system environment variables
2. In a `.env` file in the project root
3. Directly via command line parameter (for `openai_diag_tool.py`)

Example `.env` file:
