"""Check that the local .env file can be read without exposing the API key."""

import os

from dotenv import load_dotenv


load_dotenv()

api_key = os.getenv("API_KEY", "")
sec_user_agent = os.getenv("SEC_USER_AGENT", "")

if not api_key:
    raise SystemExit("API_KEY is missing from .env.")

if not sec_user_agent:
    raise SystemExit("SEC_USER_AGENT is missing from .env.")

print(f"{api_key[:4]}...")
print(sec_user_agent)
