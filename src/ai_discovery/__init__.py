"""AI-Discovery: Brownfield codebase analysis and SDLC document generation.

Load .env early so environment variables are available before any module imports.
This must happen before config.py, which imports model_defaults.py.
"""

from dotenv import load_dotenv

# Load .env FIRST, before any other imports
load_dotenv()

__version__ = "0.1.0"
