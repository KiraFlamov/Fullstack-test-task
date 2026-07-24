import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

API_URL = os.getenv("API_URL")

CANDIDATE_ID = os.getenv("CANDIDATE_ID")

HEADERS = {
    "X-Candidate-Id": CANDIDATE_ID
}