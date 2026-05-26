from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import get_database
from app.otp_service import OtpService
from app.otp_store import OtpStore
from app.schemas import (
    HealthResponse,
    OtpRequest,
    OtpRequestResponse,
    OtpVerificationRequest,
    OtpVerificationResponse,
    SenderListResponse,
    SenderLogoutResponse,
    SenderStatusResponse,
    SetSenderRequest,
)
from app.senders.base import OtpSender
from app.senders.mock_sender import MockOtpSender
from app.senders.whatsapp_sender_admin import WhatsappSenderAdmin
from app.senders.whatsapp_http_sender import WhatsappHttpSender, WhatsappSenderError


# Official docs concept:
# FastAPI lifespan lets you define code that runs once before the app starts
# receiving requests and once after it finishes. The docs recommend lifespan for
# startup/shutdown logic and show @asynccontextmanager with yield.
# Docs: https://fastapi.tiangolo.com/advanced/events/
#
# Our project significance:
# MongoDB indexes should exist before users start requesting OTPs. Creating them
# during lifespan means index setup is part of app startup instead of being
# repeated inside every request.
#
# FastAPI lifespan runs startup/shutdown work for the app.
#
# We use it here to prepare MongoDB indexes before the app starts handling
# requests. This ensures the OTP collection has:
# - unique phone index
# - TTL expiry index
#
# asynccontextmanager lets us place startup code before yield and optional
# shutdown code after yield.
@asynccontextmanager
async def lifespan(app: FastAPI):
    database = get_database()
    otp_store = OtpStore(database)
    await otp_store.ensure_indexes()

    # Official docs concept:
    # In an async context manager, code before yield is startup/enter logic and
    # code after yield is shutdown/exit logic. FastAPI runs this around the app's
    # request-serving lifetime.
    #
    # Our project significance:
    # We currently only need startup work. If later we need to close the MongoDB
    # client or stop background jobs, that cleanup would go after yield.
    #
    # FastAPI serves requests while execution is paused at yield.
    # There is no shutdown cleanup needed yet, so nothing appears after yield.
    yield


# Official docs concept:
# FastAPI() creates the ASGI application object. Passing lifespan=... registers
# startup/shutdown lifecycle logic for that app.
#
# Our project significance:
# uvicorn will import this app object and serve these OTP endpoints.
#
# Create the FastAPI application and attach the lifespan hook.
#
# This app object is what uvicorn imports and serves.
app = FastAPI(lifespan=lifespan)


# Construct the OTP service for a request.
#
# Current mode:
# - OtpStore persists OTP records in MongoDB.
# - Sender is selected from settings.otp_sender_mode.
#
# Why this is a function instead of a global OtpService:
# It keeps dependency construction explicit and simple. When we add WhatsApp
# sender mode, this is the place where we can choose MockOtpSender or
# WhatsappHttpSender based on settings.
def get_otp_sender() -> OtpSender:
    # Official docs/Python concept:
    # A small factory function can hide object construction behind one stable
    # call site. This is a simple manual dependency-selection pattern. FastAPI
    # also has a formal dependency injection system with Depends(...), but this
    # project currently constructs services directly for clarity.
    #
    # Our project significance:
    # OtpService depends only on the OtpSender interface. It should not contain
    # if/else logic for WhatsApp vs mock. This function keeps delivery selection
    # at the application wiring layer.
    #
    # Supported modes:
    # - mock:
    #     Prints OTP to backend console. Best for backend-only Swagger testing.
    # - whatsapp:
    #     Calls the local Node whatsapp-web.js sender service over HTTP.
    #
    # Why strip/lower:
    # Environment variables are text typed by humans. Normalizing here makes
    # values like " WhatsApp " or "WHATSAPP" work as expected.
    sender_mode = settings.otp_sender_mode.strip().lower()

    if sender_mode == "mock":
        return MockOtpSender()

    if sender_mode == "whatsapp":
        return WhatsappHttpSender()

    # Fail fast for bad configuration.
    #
    # Project significance:
    # If someone sets OTP_SENDER_MODE=sms or mistypes "whatsapp", the backend
    # should refuse to start/use the service clearly instead of silently falling
    # back to mock or failing later in a confusing way.
    raise ValueError(
        "Unsupported OTP_SENDER_MODE. Expected 'mock' or 'whatsapp', "
        f"got {settings.otp_sender_mode!r}."
    )


def get_otp_service() -> OtpService:
    # Official docs/FastAPI concept:
    # Path operation functions can call normal Python functions to create the
    # service objects they need. FastAPI's Depends system can formalize this, but
    # for this POC a direct factory keeps the flow easy to read.
    #
    # Our project significance:
    # Every OTP request gets an OtpService wired with:
    # - the MongoDB-backed OtpStore
    # - the configured OtpSender implementation
    #
    # The OTP business logic remains unchanged whether delivery is mock or
    # WhatsApp. Only this wiring chooses the sender.
    database = get_database()
    store = OtpStore(database)
    sender = get_otp_sender()
    return OtpService(store=store, sender=sender)


def sender_error_response(error: WhatsappSenderError) -> JSONResponse:
    # Convert a structured WhatsApp sender failure into a structured FastAPI
    # response.
    #
    # Official FastAPI concept:
    # JSONResponse lets a route return an explicit status code and JSON body
    # when the normal response_model success shape is not appropriate.
    #
    # Our project significance:
    # The Node sender service already knows specific operational failures:
    # - sender_not_found
    # - sender_not_ready
    # - number_not_registered
    # - invalid_receiver_phone
    #
    # FastAPI should preserve those details so Flutter/manual testers can tell
    # the difference between "OTP sent" and "this receiver has no WhatsApp".
    return JSONResponse(
        status_code=error.status_code,
        content={
            "sent": False,
            "error": error.error_code,
            "message": error.message,
        },
    )


def get_sender_admin() -> WhatsappSenderAdmin:
    # Construct the admin adapter that proxies FastAPI sender-management routes
    # to the Node whatsapp-web.js service.
    #
    # Project significance:
    # This keeps Swagger users on the FastAPI backend while preserving the
    # architecture boundary: Node still owns QR/auth/session lifecycle.
    return WhatsappSenderAdmin()


# Official docs concept:
# FastAPI decorators like @app.get(...) and @app.post(...) register path
# operations. response_model controls response validation/documentation.
# Docs: https://fastapi.tiangolo.com/tutorial/response-model/
#
# Our project significance:
# These decorators define the public API that Swagger UI shows for manual OTP
# validation today and that Flutter will call later.
#
# Basic health route.
#
# Used to confirm the FastAPI process is running. It does not check MongoDB or
# WhatsApp sender readiness.
@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


# Register/load a WhatsApp OTP sender session.
#
# Operator/admin endpoint:
# This is the FastAPI-facing API for "set the OTP sender phone number". Normal
# end users should not call this in the app flow.
#
# Flow:
# 1. FastAPI validates sender_phone.
# 2. FastAPI calls Node POST /senders.
# 3. Node creates or loads the per-sender whatsapp-web.js session.
# 4. FastAPI returns ready/auth/QR state.
#
# Important:
# QR authentication is asynchronous. If a QR is returned, scan it and poll
# GET /senders/{sender_id}/status until ready=true.
@app.post("/set-sender", response_model=SenderStatusResponse)
async def set_sender(
    request: SetSenderRequest,
) -> SenderStatusResponse | JSONResponse:
    admin = get_sender_admin()
    try:
        return await admin.set_sender(sender_phone=request.sender_phone)
    except WhatsappSenderError as error:
        return sender_error_response(error)


@app.get("/senders", response_model=SenderListResponse)
async def get_senders() -> SenderListResponse | JSONResponse:
    admin = get_sender_admin()
    try:
        senders = await admin.list_senders()
    except WhatsappSenderError as error:
        return sender_error_response(error)

    return SenderListResponse(senders=senders)


@app.get("/senders/{sender_id}/status", response_model=SenderStatusResponse)
async def get_sender_status(sender_id: str) -> SenderStatusResponse | JSONResponse:
    admin = get_sender_admin()
    try:
        return await admin.get_sender_status(sender_id=sender_id)
    except WhatsappSenderError as error:
        return sender_error_response(error)


@app.get("/senders/{sender_id}/qr", response_model=SenderStatusResponse)
async def get_sender_qr(sender_id: str) -> SenderStatusResponse | JSONResponse:
    admin = get_sender_admin()
    try:
        return await admin.get_sender_qr(sender_id=sender_id)
    except WhatsappSenderError as error:
        return sender_error_response(error)


@app.post("/senders/{sender_id}/logout", response_model=SenderLogoutResponse)
async def logout_sender(sender_id: str) -> SenderLogoutResponse | JSONResponse:
    admin = get_sender_admin()
    try:
        data = await admin.logout_sender(sender_id=sender_id)
    except WhatsappSenderError as error:
        return sender_error_response(error)

    return SenderLogoutResponse(
        logged_out=bool(data.get("loggedOut", False)),
        sender_id=str(data.get("senderId", sender_id)),
    )


# Request an OTP for a phone number.
#
# Official docs concept:
# FastAPI sees request: OtpRequest as a Pydantic body model. It parses JSON into
# OtpRequest before calling this function.
# Docs: https://fastapi.tiangolo.com/tutorial/body/
#
# Our project significance:
# This endpoint is the start of the login flow. It delegates all OTP rules to
# OtpService so the route stays thin and easy to connect to Flutter later.
#
# FastAPI validates the request body with OtpRequest before this function runs.
#
# The service handles:
# - cooldown
# - OTP generation
# - hashing/storage
# - delivery through the configured sender
@app.post("/otp/request", response_model=OtpRequestResponse)
async def request_otp(request: OtpRequest) -> OtpRequestResponse | JSONResponse:
    service = get_otp_service()
    try:
        message = await service.request_otp(
            sender_phone=request.sender_phone,
            phone=request.phone,
        )
    except WhatsappSenderError as error:
        return sender_error_response(error)

    return OtpRequestResponse(message=message)


# Re-send an OTP for a phone number.
#
# Employer requirement:
# Expose a dedicated "re-send OTP" API instead of making clients call
# /otp/request again and rely on undocumented behavior.
#
# Official API design concept:
# Two endpoints can share the same service method when the business rule is the
# same. The public route name communicates user intent, while OtpService remains
# the single place that enforces:
# - resend cooldown
# - new OTP generation
# - replacing old OTP hash/salt/expiry
# - delivery through the selected sender
#
# Our project significance:
# request_otp already acts like a resend when an OTP record exists because
# OtpStore.upsert_otp replaces the previous OTP. This route makes that behavior
# explicit for the employer and future Flutter client.
@app.post("/otp/resend", response_model=OtpRequestResponse)
async def resend_otp(request: OtpRequest) -> OtpRequestResponse | JSONResponse:
    service = get_otp_service()
    try:
        message = await service.request_otp(
            sender_phone=request.sender_phone,
            phone=request.phone,
        )
    except WhatsappSenderError as error:
        return sender_error_response(error)

    return OtpRequestResponse(message=message)


# Verify a submitted OTP.
#
# Official docs concept:
# A response_model makes FastAPI serialize the returned Pydantic model to the
# documented JSON shape and helps Swagger/OpenAPI show clients what to expect.
#
# Our project significance:
# Flutter can rely on:
#   { "verified": true/false, "message": "..." }
# when deciding whether to let the user proceed.
#
# FastAPI validates the request body with OtpVerificationRequest first.
#
# The service returns:
# - verified boolean for machine-readable result
# - message string for user/developer feedback
@app.post("/otp/verify", response_model=OtpVerificationResponse)
async def verify_otp(request: OtpVerificationRequest) -> OtpVerificationResponse:
    service = get_otp_service()
    verified, message = await service.verify_otp(
        phone=request.phone,
        otp=request.otp,
    )
    return OtpVerificationResponse(
        verified=verified,
        message=message,
    )
