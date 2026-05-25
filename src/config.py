"""Configuration loaded from environment."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", PROJECT_ROOT / "output"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", PROJECT_ROOT / ".cache"))

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-nano-2026-03-17")

# gemini-3.1-flash-lite-preview is better
# but its not reliable as compared to gemini-2.5-flash
# still going to use gemini-3.1 anyway cause goteem
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview") 


# External APIs
NWS_API_BASE = "https://api.weather.gov"
IEM_API_BASE = "https://mesonet.agron.iastate.edu"
NEXRAD_S3_BUCKET = "noaa-nexrad-level2"

PORT = 8000
