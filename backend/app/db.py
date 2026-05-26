from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings


# Official docs concept:
# Motor's AsyncIOMotorClient is the async MongoDB client for asyncio apps. It is
# the object used to access MongoDB databases and collections from async code.
# Docs: https://motor.readthedocs.io/en/stable/api-asyncio/
#
# Our project significance:
# FastAPI route handlers and OtpStore methods are async. Using Motor means our
# MongoDB calls can be awaited instead of blocking the event loop while the
# backend handles OTP request/verify calls.
#
# Create one async MongoDB client for the FastAPI process.
#
# Motor's AsyncIOMotorClient is designed to be reused. Creating it once at module
# import time avoids opening a new database connection for every API request.
client = AsyncIOMotorClient(settings.mongodb_url)

# Select the configured database from the shared MongoDB client.
#
# OtpStore receives this database object and then selects its collection from it.
database: AsyncIOMotorDatabase = client[settings.mongodb_db_name]


# Official docs concept:
# FastAPI apps often use dependency functions or small factory/accessor
# functions to retrieve shared resources. This project is not using FastAPI's
# Depends(...) yet, but this function gives us the same clean boundary.
#
# Our project significance:
# main.py can construct OtpStore without knowing the exact global variable name
# in db.py. Later, tests can replace this wiring more easily.
#
# Small accessor used by app.main.
#
# Keeping this behind a function makes dependency construction clearer and gives
# us an easy place to change database wiring later if tests or app lifespan need
# a different database.
def get_database() -> AsyncIOMotorDatabase:
    return database
