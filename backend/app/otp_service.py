from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from app.config import settings
from app.otp_store import OtpStore
from app.senders.base import OtpSender


# Official docs concept:
# Python's secrets module is intended for generating cryptographically strong
# random numbers suitable for managing secrets such as tokens.
# Docs: https://docs.python.org/3/library/secrets.html
#
# Official docs concept:
# hashlib provides secure hash/message digest algorithms such as SHA-256 and
# hexdigest() returns a hex string representation.
# Docs: https://docs.python.org/3/library/hashlib.html
#
# Official docs concept:
# hmac.compare_digest is recommended when comparing externally supplied digests
# to reduce timing-attack risk compared with normal == comparison.
# Docs: https://docs.python.org/3/library/hmac.html
#
# Our project significance:
# OTPs are authentication secrets. This service generates them securely, stores
# only salted hashes, and compares submitted codes in a timing-safer way.
#
# OtpService is the business-logic layer for OTP authentication.
#
# It coordinates:
# - OTP generation
# - salting and hashing
# - cooldown enforcement
# - expiry enforcement
# - max verification attempts
# - delivery through an OtpSender abstraction
#
# It intentionally does not know whether OTPs are delivered by console print,
# WhatsApp Web, SMS, or another channel. That is handled by the sender object.
class OtpService:
    # The service receives its dependencies from main.py.
    #
    # store:
    #   Database access for OTP records.
    #
    # sender:
    #   Delivery mechanism. Today this can be MockOtpSender; later it can be the
    #   WhatsApp HTTP sender without changing OtpService logic.
    def __init__(self, *, store: OtpStore, sender: OtpSender) -> None:
        self.store = store
        self.sender = sender

    # Official docs concept:
    # secrets.randbelow(n) returns a random int in the range [0, n). It uses the
    # most secure randomness source provided by the operating system.
    #
    # Our project significance:
    # randbelow(1_000_000) gives 0 through 999999. Formatting with :06d turns it
    # into the exact six-character OTP shape expected by schemas and clients,
    # including leading-zero codes like "004219".
    #
    # Generate a random 6-digit OTP as a string.
    #
    # secrets.randbelow(...) is used instead of random.randint(...) because
    # secrets is designed for security-sensitive tokens.
    #
    # :06d pads with leading zeroes, so values like 42 become "000042".
    def _generate_otp(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    # Official docs concept:
    # secrets.token_hex(nbytes) returns a random text string in hexadecimal. Each
    # byte becomes two hex characters.
    #
    # Our project significance:
    # token_hex(16) creates a per-OTP salt. If two users receive the same OTP,
    # their stored hashes still differ because their salts differ.
    #
    # Generate a per-OTP salt.
    #
    # The salt makes identical OTPs produce different hashes in the database.
    # token_hex(16) gives 16 random bytes represented as 32 hex characters.
    def _generate_salt(self) -> str:
        return secrets.token_hex(16)

    # Official docs concept:
    # hashlib.sha256(data).hexdigest() computes a SHA-256 digest and returns it
    # as a hexadecimal string.
    #
    # Our project significance:
    # We never store the raw OTP in MongoDB. We store a deterministic hash of
    # salt + OTP so verify_otp can check future submissions without keeping the
    # plaintext login code.
    #
    # Hash the OTP with its salt.
    #
    # We store only this hash, not the raw OTP. During verification, we hash the
    # submitted OTP with the same salt and compare hashes.
    #
    # Format:
    #   "{salt}:{otp}"
    #
    # The colon is just a separator to avoid ambiguous concatenation.
    def _hash_otp(self, *, otp: str, salt: str) -> str:
        return hashlib.sha256(f"{salt}:{otp}".encode("utf-8")).hexdigest()

    # Official docs concept:
    # Python datetime supports naive datetimes and timezone-aware datetimes.
    # Arithmetic/comparison between mismatched naive/aware datetimes can fail.
    #
    # Our project significance:
    # OTP expiry and cooldown are time-based security rules. Normalizing to UTC
    # keeps comparisons consistent even if MongoDB returns a naive datetime.
    #
    # Normalize datetimes to timezone-aware UTC values.
    #
    # MongoDB/Motor can sometimes return naive datetimes depending on client
    # configuration. The rest of this service uses timezone-aware UTC datetimes.
    #
    # This helper prevents errors when subtracting/comparing naive and aware
    # datetimes.
    def _as_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    # Request a new OTP for a receiver phone number from a selected sender.
    #
    # Flow:
    # 1. Check existing record for resend cooldown.
    # 2. Generate raw OTP.
    # 3. Generate salt and hash OTP.
    # 4. Store only hash/salt/expiry metadata.
    # 5. Send raw OTP through the configured sender using sender_phone.
    # 6. Return a user-facing message.
    async def request_otp(self, *, sender_phone: str, phone: str) -> str:
        existing_otp = await self.store.get_by_phone(phone)
        now = datetime.now(timezone.utc)

        # Official docs concept:
        # datetime.now(timezone.utc) returns a timezone-aware UTC datetime.
        #
        # Our project significance:
        # Every stored time value uses the same clock convention. That makes
        # resend cooldown and expiry checks stable across requests.

        # If a record already exists, enforce resend cooldown before creating a
        # new OTP. This prevents repeated sends to the same phone too quickly.
        if existing_otp:
            last_sent_at = self._as_utc(existing_otp["last_sent_at"])
            seconds_since_last_send = (now - last_sent_at).total_seconds()

            if seconds_since_last_send < settings.otp_resend_cooldown_seconds:
                # Return the remaining cooldown as an integer number of seconds.
                # The API currently returns messages instead of raising HTTP
                # errors for cooldown cases.
                remaining_seconds = int(
                    settings.otp_resend_cooldown_seconds - seconds_since_last_send
                )

                return f"please wait {remaining_seconds} seconds before requesting another otp"

        # The raw OTP exists only in memory long enough to send it.
        # The database receives only otp_hash and salt.
        otp = self._generate_otp()
        salt = self._generate_salt()
        otp_hash = self._hash_otp(otp=otp, salt=salt)

        # Expiry is stored as an absolute UTC datetime. MongoDB TTL index can
        # clean it up, and verify_otp also checks this exact value manually.
        expires_at = now + timedelta(seconds=settings.otp_expiry_seconds)

        await self.store.upsert_otp(
            phone=phone,
            otp_hash=otp_hash,
            salt=salt,
            expires_at=expires_at,
            last_sent_at=now,
        )

        # Delivery happens after persistence.
        #
        # Why:
        # If the user receives the OTP, verify_otp needs the hash already stored.
        #
        # Failure behavior:
        # If delivery fails, delete the stored OTP and re-raise the sender
        # exception. Otherwise the database would contain a valid OTP that the
        # user never received, and cooldown could block them from retrying.
        try:
            await self.sender.send_otp(
                sender_phone=sender_phone,
                receiver_phone=phone,
                otp=otp,
            )
        except Exception:
            await self.store.delete_by_phone(phone)
            raise

        return "otp sent successfully"

    # Verify a submitted OTP for a phone number.
    #
    # Flow:
    # 1. Load stored OTP record.
    # 2. Reject if missing.
    # 3. Reject and delete if expired.
    # 4. Reject and delete if max attempts already reached.
    # 5. Hash submitted OTP with stored salt.
    # 6. Constant-time compare with stored hash.
    # 7. Increment attempts on failure.
    # 8. Delete record on success so OTP cannot be reused.
    async def verify_otp(self, *, phone: str, otp: str) -> tuple[bool, str]:
        existing_otp = await self.store.get_by_phone(phone)
        if not existing_otp:
            return False, "otp not found or expired"

        now = datetime.now(timezone.utc)
        expires_at = self._as_utc(existing_otp["expires_at"])

        # Even though MongoDB TTL eventually deletes expired records, we check
        # expiry here for immediate correctness and a clear API response.
        if now > expires_at:
            await self.store.delete_by_phone(phone)
            return False, "otp expired"

        # If the user has already used up the attempt budget, delete the OTP and
        # force a fresh request.
        verify_attempts = existing_otp.get("verify_attempts", 0)
        if verify_attempts >= settings.otp_max_verify_attempts:
            await self.store.delete_by_phone(phone)
            return False, "maximum verification attempts exceeded"

        # Hash the submitted OTP using the original salt from storage.
        # If the submitted OTP is correct, this hash will match otp_hash.
        submitted_hash = self._hash_otp(
            otp=otp,
            salt=existing_otp["salt"],
        )

        # Use hmac.compare_digest for constant-time comparison.
        #
        # This avoids leaking information through tiny timing differences in
        # string comparison. It is a good habit for comparing secrets or hashes.
        is_valid = hmac.compare_digest(submitted_hash, existing_otp["otp_hash"])

        if not is_valid:
            # Official docs concept:
            # The failed attempt is persisted with an atomic MongoDB increment in
            # OtpStore, not kept only in memory.
            #
            # Our project significance:
            # If the API process restarts, the attempt count remains in MongoDB
            # until the OTP expires, succeeds, or is deleted.
            await self.store.increment_verify_attempts(phone)
            return False, "invalid otp"

        # Delete after successful verification so the same OTP cannot be reused.
        await self.store.delete_by_phone(phone)
        return True, "otp verified successfully"
