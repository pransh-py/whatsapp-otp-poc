import os
from dataclasses import dataclass

from dotenv import load_dotenv


# Official docs concept:
# python-dotenv's load_dotenv() reads key-value pairs from a .env file and adds
# them to the process environment so os.getenv(...) can read them.
#
# Our project significance:
# This OTP backend needs local-development config without hardcoding it:
# - MongoDB URL/database name
# - OTP expiry seconds
# - resend cooldown seconds
# - max verification attempts
# - later, WhatsApp sender URL/API key/mode
#
# Keeping these as environment-backed settings lets you switch between mock OTP
# printing and WhatsApp delivery without editing business logic.
#
# Load variables from a local .env file into process.env before Settings reads
# from os.getenv(...).
#
# Why this is here:
# During local development we do not want to hardcode MongoDB URLs, OTP expiry
# values, or future sender settings directly in Python files. python-dotenv lets
# the app read those values from backend/.env while still allowing normal
# environment variables to work in deployment.
load_dotenv()


# Official docs concept:
# Python's dataclasses module generates standard class methods from annotated
# fields. The frozen=True option makes assigning to fields after creation raise
# an error, which makes the instance behave like immutable config.
# Docs: https://docs.python.org/3/library/dataclasses.html
#
# Our project significance:
# Settings are loaded once when the backend process starts. OTP behavior should
# not silently mutate during a request because that would make cooldown, expiry,
# and sender selection unpredictable.
#
# Settings is the single place where backend runtime configuration is collected.
#
# frozen=True makes the dataclass immutable after creation. That is useful for
# config because the rest of the app should read settings, not mutate them at
# runtime.
@dataclass(frozen=True)
class Settings:
    # Official docs concept:
    # os.getenv(key, default) reads an environment variable and falls back to a
    # default when the key is missing.
    #
    # Our project significance:
    # These defaults make the repo runnable on a developer machine with minimal
    # setup, while still allowing a .env file to override values.

    # MongoDB connection string.
    #
    # Default points to a local MongoDB instance. The OTP store uses this through
    # app.db, which creates the async Motor client.
    mongodb_url: str = os.getenv("MONGODB_URL", "mongodb://localhost:27017")

    # Database name used for this POC.
    #
    # Keeping this configurable lets us use a different database for local
    # testing without changing code.
    mongodb_db_name: str = os.getenv("MONGODB_DB_NAME", "whatsapp_otp_poc")

    # How long an OTP remains valid after it is generated.
    #
    # The service stores expires_at in MongoDB and also checks expiry manually
    # during verification so the API can return a clear "otp expired" message.
    otp_expiry_seconds: int = int(os.getenv("OTP_EXPIRY_SECONDS", "300"))

    # Minimum wait time before the same phone number can request another OTP.
    #
    # This protects the sender from accidental spam and keeps the WhatsApp POC
    # from sending many messages quickly.
    otp_resend_cooldown_seconds: int = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "60"))

    # Maximum number of wrong verification attempts allowed for one OTP.
    #
    # After this limit, the OTP is deleted and the user must request a new one.
    otp_max_verify_attempts: int = int(os.getenv("OTP_MAX_VERIFY_ATTEMPTS", "5"))

    # Official docs concept:
    # Environment-backed settings let one codebase run in different modes
    # without editing source code.
    #
    # Our project significance:
    # OTP_SENDER_MODE chooses the delivery implementation:
    # - "mock" keeps the current backend behavior and prints OTPs to console
    # - "whatsapp" will call the local Node whatsapp-web.js sender service
    #
    # Defaulting to "mock" keeps local backend testing safe. A developer must
    # explicitly opt into real WhatsApp delivery.
    otp_sender_mode: str = os.getenv("OTP_SENDER_MODE", "mock")

    # Base URL for the local Node WhatsApp sender service.
    #
    # Our Node service listens on port 3001 by default and exposes:
    #   POST /send-otp
    #
    # FastAPI will combine this base URL with "/send-otp" in the HTTP sender
    # adapter.
    whatsapp_sender_url: str = os.getenv(
        "WHATSAPP_SENDER_URL",
        "http://127.0.0.1:3001",
    )

    # Shared local API key used when FastAPI calls the Node sender service.
    #
    # This must match whatsapp-sender/.env:
    #   SENDER_API_KEY=local-dev-key
    #
    # The Node service expects this value in:
    #   X-SENDER-API-KEY
    whatsapp_sender_api_key: str = os.getenv(
        "WHATSAPP_SENDER_API_KEY",
        "local-dev-key",
    )

    # Timeout for HTTP calls from FastAPI to the Node sender.
    #
    # Official docs concept:
    # HTTP clients should use timeouts so one slow/downstream service does not
    # hang the caller forever.
    #
    # Our project significance:
    # If the WhatsApp sender service is stopped, stuck, or slow, /otp/request
    # should fail in a bounded amount of time instead of waiting indefinitely.
    whatsapp_sender_timeout_seconds: float = float(
        os.getenv("WHATSAPP_SENDER_TIMEOUT_SECONDS", "10")
    )


# Create one shared settings object for the app to import.
#
# This keeps call sites simple:
#   from app.config import settings
settings = Settings()
