from pydantic import BaseModel, Field


# Official docs concept:
# FastAPI uses Pydantic models to declare request bodies. Its docs say that when
# a path operation parameter is declared as a Pydantic model, FastAPI interprets
# it as the request body and gets validation, conversion, schema generation, and
# automatic docs.
# Docs: https://fastapi.tiangolo.com/tutorial/body/
#
# Official docs concept:
# Pydantic BaseModel is the base class for structured validation models. Field()
# can add validation constraints and JSON Schema metadata for individual fields.
# Docs: https://docs.pydantic.dev/latest/concepts/fields/
# FastAPI Field docs: https://fastapi.tiangolo.com/tutorial/body-fields/
#
# Our project significance:
# These models are the API contract shown in Swagger UI. They make invalid OTP
# request/verify payloads fail before OtpService runs, so the business layer can
# assume it received the expected shape.
#
# Pydantic models define the public request/response contract for FastAPI.
#
# FastAPI uses these models for:
# - request body validation
# - response serialization
# - Swagger/OpenAPI documentation
#
# Keeping schemas in their own file prevents route handlers in main.py from
# becoming mixed with validation model definitions.


# E.164-style phone format accepted by both the FastAPI backend and the Node
# WhatsApp sender service.
#
# Official docs concept:
# Pydantic Field(pattern=...) applies a regular-expression constraint during
# request validation and exposes that constraint in OpenAPI/Swagger.
#
# Our project significance:
# The WhatsApp sender expects international phone numbers like +919876543210.
# Validating that format in FastAPI prevents bad values such as 9840650578 from
# reaching the Node service and turning into a downstream 500.
E164_PHONE_PATTERN = r"^\+[1-9]\d{7,14}$"

# Six-digit OTP format shared by request verification and the sender service.
#
# Keeping it here means Swagger documents the exact OTP shape and invalid values
# such as "12345", "abcdef", or numeric JSON values are rejected before hashing.
OTP_PATTERN = r"^\d{6}$"


# Response model for GET /health.
#
# Official docs concept:
# FastAPI response_model tells FastAPI to validate, document, serialize, and
# filter response data according to the declared model.
# Docs: https://fastapi.tiangolo.com/tutorial/response-model/
#
# Our project significance:
# HealthResponse keeps /health predictable for manual checks and future uptime
# probes. The response shape stays { "status": "ok" }.
#
# The default status value means the route can simply return HealthResponse()
# and still produce:
#   { "status": "ok" }
class HealthResponse(BaseModel):
    status: str = "ok"


# Request body for POST /otp/request.
#
# Official docs concept:
# Field(..., pattern=...) declares a required Pydantic field with a regex
# constraint. FastAPI includes this constraint in the generated OpenAPI schema.
#
# Our project significance:
# This keeps clearly bad sender/receiver phone values out of request_otp before
# we generate, hash, store, or send anything.
#
# sender_phone:
#   The authenticated WhatsApp account that should send the OTP.
#   With whatsapp-web.js, this must already have a registered/ready sender
#   session in the Node sender service.
#
# phone:
#   The receiver/user phone number that should receive the OTP.
#
# Both values are intentionally strings, not numbers. Phone numbers are
# identifiers: they can contain a leading "+", may have leading zeros in some
# contexts, and should never be treated as arithmetic values.
class OtpRequest(BaseModel):
    sender_phone: str = Field(
        ...,
        pattern=E164_PHONE_PATTERN,
        examples=["+918637427358"],
    )
    phone: str = Field(
        ...,
        pattern=E164_PHONE_PATTERN,
        examples=["+919876543210"],
    )


# Response body for POST /otp/request.
#
# The message is returned as a string so the service can explain cooldown cases,
# success, or future delivery-specific results without changing the top-level
# response shape.
class OtpRequestResponse(BaseModel):
    message: str = "otp sent successfully"


# Request body for POST /set-sender.
#
# Operator/admin endpoint:
# This is not part of the normal patient/user OTP login flow. It lets an
# operator register or load a WhatsApp sender session through FastAPI Swagger,
# while the Node whatsapp-web.js service remains the owner of QR/auth state.
class SetSenderRequest(BaseModel):
    sender_phone: str = Field(
        ...,
        pattern=E164_PHONE_PATTERN,
        examples=["+918637427358"],
    )


# Response body for sender-management endpoints.
#
# Field naming:
# Node uses JavaScript-style names such as senderId/clientState. FastAPI exposes
# snake_case fields such as sender_id/client_state to keep the Python API
# consistent with sender_phone in request bodies.
#
# requires_qr:
#   True when the backend has a QR payload that the sender phone must scan.
#
# qr:
#   Raw whatsapp-web.js QR payload. A frontend can render this into a scannable
#   QR image. Swagger will display it as text for the POC.
class SenderStatusResponse(BaseModel):
    sender_id: str
    sender_phone: str
    authenticated: bool
    ready: bool
    requires_qr: bool
    client_state: str
    message: str
    qr: str | None = None
    last_error: str | None = None
    last_qr_at: str | None = None
    ready_at: str | None = None
    disconnected_at: str | None = None


class SenderListResponse(BaseModel):
    senders: list[SenderStatusResponse]


class SenderLogoutResponse(BaseModel):
    logged_out: bool
    sender_id: str


# Request body for POST /otp/verify.
#
# Official docs concept:
# Pydantic validates input data according to type annotations and Field
# constraints before route logic runs.
#
# Our project significance:
# The OTP route expects exactly a 6-character code string. This preserves leading
# zeros and prevents values like "123", "1234567", or numeric JSON values from
# flowing into the hash comparison path.
#
# phone identifies which stored OTP record to verify.
#
# otp is a string instead of an int because codes such as "012345" are valid
# six-digit OTPs and would be corrupted if converted to number 12345.
class OtpVerificationRequest(BaseModel):
    phone: str = Field(
        ...,
        pattern=E164_PHONE_PATTERN,
        examples=["+919876543210"],
    )
    otp: str = Field(
        ...,
        pattern=OTP_PATTERN,
        examples=["123456"],
    )


# Response body for POST /otp/verify.
#
# verified:
#   Machine-readable success/failure flag.
#
# message:
#   Human-readable reason, such as invalid OTP, expired OTP, or success.
class OtpVerificationResponse(BaseModel):
    verified: bool
    message: str
