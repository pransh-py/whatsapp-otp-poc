from datetime import datetime, timezone
from typing import Any, Dict, Optional


# Official docs concept:
# Motor collection methods such as find_one, update_one, create_index, and
# delete_one are awaitable in the asyncio API.
# Docs: https://motor.readthedocs.io/en/stable/api-asyncio/
#
# Official docs concept:
# MongoDB TTL indexes automatically remove documents after a Date field reaches
# the configured expiration rule. For expireAfterSeconds=0, the indexed date is
# the expiration time. MongoDB removes expired documents in the background, not
# necessarily at the exact millisecond they expire.
# Docs: https://www.mongodb.com/docs/manual/core/index-ttl/
#
# Our project significance:
# This store keeps active OTP records only. It supports both:
# - manual expiry checks in OtpService for immediate correctness
# - MongoDB background TTL cleanup so old records do not accumulate
#
# OtpStore is the MongoDB persistence layer for OTP records.
#
# Why this class exists:
# OtpService should describe business rules, not raw MongoDB queries. This class
# hides the collection name and database operations behind small methods.
#
# Stored document shape:
# {
#   "phone": "...",
#   "otp_hash": "...",
#   "salt": "...",
#   "expires_at": datetime,
#   "last_sent_at": datetime,
#   "verify_attempts": int,
#   "created_at": datetime,
#   "updated_at": datetime
# }
class OtpStore:
    # Official docs concept:
    # MongoDB stores data in collections inside a database. Motor lets us select
    # a collection using database["collection_name"].
    #
    # Our project significance:
    # All OTP documents live in one collection named otp_requests, giving this
    # POC one obvious place to inspect/debug OTP state during manual testing.
    #
    # database is intentionally typed as Any because Motor's database object is
    # provided by app.db and behaves like a mapping of collection names.
    #
    # The OTP records live in the "otp_requests" collection.
    def __init__(self, database: Any) -> None:
        self.collection = database["otp_requests"]

    # Official docs concept:
    # MongoDB indexes improve lookup behavior and can enforce constraints such
    # as unique=True. TTL indexes use expireAfterSeconds for automatic expiry.
    #
    # Our project significance:
    # - phone unique index means one active OTP per phone
    # - expires_at TTL index means expired OTPs eventually disappear
    #
    # Create the database indexes needed by the OTP flow.
    #
    # phone unique index:
    #   One active OTP record per phone number. A new OTP request updates the
    #   existing record instead of creating duplicates.
    #
    # expires_at TTL index:
    #   MongoDB can automatically remove expired OTP records. expireAfterSeconds=0
    #   means the document expires at the datetime stored in expires_at.
    #
    # Note:
    # MongoDB TTL cleanup is not instant; it runs periodically. That is why
    # OtpService also checks expires_at manually during verification.
    async def ensure_indexes(self) -> None:
        await self.collection.create_index("phone", unique=True)
        await self.collection.create_index("expires_at", expireAfterSeconds=0)

    # Official docs concept:
    # find_one(filter) returns one matching document or None.
    #
    # Our project significance:
    # request_otp uses this to enforce resend cooldown. verify_otp uses this to
    # find the active OTP hash/salt/expiry for the phone.
    #
    # Fetch the current OTP record for one phone number.
    #
    # Returns:
    # - dict when a record exists
    # - None when no active record exists
    async def get_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        return await self.collection.find_one({"phone": phone})

    # Official docs concept:
    # update_one(filter, update, upsert=True) updates the first matching document
    # or inserts a new one when no match exists. MongoDB update operators include
    # $set and $setOnInsert.
    #
    # Our project significance:
    # A resend should replace the previous OTP for the same phone instead of
    # creating multiple valid OTPs. upsert gives us "create or replace current
    # OTP record" in one database operation.
    #
    # Insert or update the OTP record for a phone number.
    #
    # Why upsert:
    # The resend flow should replace the old OTP hash, salt, expiry, and attempt
    # count for the same phone. A unique phone index plus update_one(...,
    # upsert=True) keeps exactly one current OTP record per phone.
    #
    # Security note:
    # We store otp_hash and salt, not the raw OTP. If the database is inspected,
    # the active login code is not available in plaintext.
    async def upsert_otp(
        self,
        *,
        phone: str,
        otp_hash: str,
        salt: str,
        expires_at: datetime,
        last_sent_at: datetime,
    ) -> None:
        await self.collection.update_one(
            {"phone": phone},
            {
                # $set runs on both insert and update.
                #
                # verify_attempts resets to 0 because a new OTP should get its
                # own verification attempt budget.
                "$set": {
                    "phone": phone,
                    "otp_hash": otp_hash,
                    "salt": salt,
                    "expires_at": expires_at,
                    "last_sent_at": last_sent_at,
                    "verify_attempts": 0,
                    "updated_at": datetime.now(timezone.utc),
                },
                # $setOnInsert runs only when MongoDB creates the document for
                # the first time. Existing records keep their original created_at.
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )

    # Increase failed verification attempt count for a phone number.
    #
    # This is called only after a submitted OTP does not match the stored hash.
    # The service checks the max-attempts limit before each verification.
    async def increment_verify_attempts(self, phone: str) -> None:
        # Official docs concept:
        # MongoDB's $inc atomically increments numeric fields, and $set updates
        # fields to explicit values.
        #
        # Our project significance:
        # Failed OTP attempts must be counted consistently so the max-attempts
        # rule cannot be bypassed by repeated wrong submissions.
        await self.collection.update_one(
            {"phone": phone},
            {
                "$inc": {"verify_attempts": 1},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )

    # Official docs concept:
    # delete_one(filter) removes one matching document.
    #
    # Our project significance:
    # OTPs are single-use. Deleting on success prevents reuse; deleting on
    # expiry/max-attempts forces a fresh request.
    #
    # Delete the OTP record for a phone number.
    #
    # Used when:
    # - OTP is successfully verified
    # - OTP has expired
    # - max verification attempts are exceeded
    #
    # Deleting after success prevents OTP reuse.
    async def delete_by_phone(self, phone: str) -> None:
        await self.collection.delete_one({"phone": phone})
