# WhatsApp OTP POC: Technical Documentation

## Project Overview

WhatsApp OTP POC is a proof of concept for phone based OTP login. It tests whether WhatsApp can be used as a one time password delivery channel before that channel is built into a production login flow. The project has three parts: a FastAPI backend that owns all OTP business logic, MongoDB for storage, and a separate Node.js service that controls one or more WhatsApp Web sessions using the whatsapp-web.js library. A Flutter mobile app is planned but has not been built yet.

The backend and the WhatsApp sender are intentionally two separate processes. The backend never talks to WhatsApp directly, it calls the Node service over HTTP. This keeps OTP business rules independent of the delivery mechanism, so the delivery channel can be swapped between a console mock and real WhatsApp delivery without changing OTP logic.

## Architecture

Three components make up the system.

1. FastAPI backend, Python
   Owns OTP generation, hashing, storage, expiry, cooldown, attempt limits, and verification. Exposes a REST API used through Swagger UI today and eventually by a Flutter app.

2. MongoDB
   Stores one active OTP record per phone number. A unique index on phone enforces one record per number, and a TTL index on the expiry field lets MongoDB clean up expired records automatically in the background.

3. Node WhatsApp sender service, Express plus whatsapp-web.js
   Owns one or more WhatsApp Web sessions, one per sender phone number. Each session requires a QR code login and keeps its own persisted auth state on disk. The backend calls this service over HTTP to deliver OTP messages and to manage sender sessions.

## Request Flow

1. A client calls POST /otp/request on the FastAPI backend with a sender phone number and a receiver phone number.
2. The backend loads any existing OTP record for that receiver and enforces the resend cooldown.
3. The backend generates a random six digit OTP and a random salt, then stores only the salted hash in MongoDB. The raw code is never written to the database.
4. The backend calls the configured OtpSender implementation to deliver the OTP.
5. In WhatsApp mode, the backend makes an HTTP call to the Node service's POST /send-otp endpoint with the sender phone, receiver phone, and OTP.
6. The Node service looks up the WhatsApp Web session for that sender, confirms the receiver number is registered on WhatsApp, and sends the message.
7. The client later calls POST /otp/verify with the phone number and the code. The backend hashes the submitted code with the stored salt, compares it to the stored hash using a constant time comparison, and deletes the record on success so the code cannot be reused.

## Backend Details

### Configuration, app/config.py

Settings are loaded from environment variables through python-dotenv, with defaults that make the backend runnable on a fresh machine. The Settings dataclass is frozen so configuration values cannot change while the process is running.

- `MONGODB_URL`: MongoDB connection string
- `MONGODB_DB_NAME`: database name
- `OTP_EXPIRY_SECONDS`: how long a generated OTP stays valid, 300 seconds by default
- `OTP_RESEND_COOLDOWN_SECONDS`: minimum wait before a new OTP can be requested for the same phone, 60 seconds by default
- `OTP_MAX_VERIFY_ATTEMPTS`: number of wrong verification attempts allowed before the OTP is discarded, 5 by default
- `OTP_SENDER_MODE`: `mock` or `whatsapp`, selects the delivery implementation
- `WHATSAPP_SENDER_URL`: base URL of the Node sender service
- `WHATSAPP_SENDER_API_KEY`: shared key sent to the Node service
- `WHATSAPP_SENDER_TIMEOUT_SECONDS`: timeout for calls to the Node service

### Database Layer, app/db.py

Uses Motor, the asynchronous MongoDB driver, so database calls do not block the FastAPI event loop. One client is created when the module is imported and reused for the life of the process.

### OTP Store, app/otp_store.py

Wraps MongoDB operations behind a small class so the service layer never writes raw queries. Stores one document per phone number in the `otp_requests` collection, with fields for the OTP hash, salt, expiry time, last sent time, and verification attempt count. On startup, the store creates a unique index on `phone` and a TTL index on `expires_at`.

### OTP Service, app/otp_service.py

This is where the OTP business rules live.

OTP generation uses Python's `secrets` module rather than the standard `random` module, because `secrets` is designed for values that need to be unpredictable, such as authentication codes. Each OTP gets its own random salt, and the stored hash is SHA-256 of the salt and OTP combined. The raw OTP is never written to the database.

`request_otp`:
- Loads any existing OTP record for the phone and enforces the resend cooldown if one exists
- Generates a new OTP, salt, and hash
- Stores the hash, salt, and expiry in MongoDB, replacing any previous record for that phone
- Calls the configured sender to deliver the OTP
- If delivery fails, deletes the just stored record and re-raises the error, so a failed delivery does not leave a valid but undelivered OTP blocking a retry

`verify_otp`:
- Loads the stored record for the phone and rejects if none exists
- Rejects and deletes the record if the OTP has expired
- Rejects and deletes the record if the maximum verification attempts have already been used
- Hashes the submitted code with the stored salt and compares it to the stored hash using `hmac.compare_digest`, which avoids timing based leaks
- On a wrong code, increments the attempt counter in MongoDB and returns false
- On a correct code, deletes the record so the same OTP cannot be verified twice

### Sender Abstraction, app/senders/

`OtpSender` is an abstract base class with one method, `send_otp`. The OTP service depends only on this interface, not on a specific delivery mechanism.

Two implementations exist:
- `MockOtpSender` prints the OTP to the backend console. This is the default mode and is used for local testing without needing a live WhatsApp session.
- `WhatsappHttpSender` calls the Node sender service over HTTP. It builds the request, sets the shared API key header, applies a timeout, and converts HTTP or connection failures into one `WhatsappSenderError` type that the API layer can turn into a clean response.

Which implementation is used is decided in one place, the `get_otp_sender` function in `main.py`, based on the `OTP_SENDER_MODE` setting. The service logic itself never branches on delivery type.

### Sender Administration, app/senders/whatsapp_sender_admin.py

A separate adapter, `WhatsappSenderAdmin`, proxies sender management calls, registering a sender, checking status, fetching a QR code, and logging out, from FastAPI to the Node service. This lets an operator manage WhatsApp sender sessions through the FastAPI Swagger UI instead of calling the Node service directly, while the Node service remains the sole owner of session state.

### API Routes, app/main.py

- `GET /health`: confirms the FastAPI process is running
- `POST /set-sender`: registers or loads a WhatsApp sender session by phone number
- `GET /senders`: lists all sender sessions known to the Node service
- `GET /senders/{sender_id}/status`: returns the lifecycle status of one sender
- `GET /senders/{sender_id}/qr`: returns the current QR code for a sender that still needs to be linked
- `POST /senders/{sender_id}/logout`: logs out and removes a sender session
- `POST /otp/request`: requests a new OTP for a phone number
- `POST /otp/resend`: same behavior as `/otp/request`, exposed as a separate route so resend intent is explicit for callers
- `POST /otp/verify`: verifies a submitted OTP

All request and response shapes are declared as Pydantic models in `app/schemas.py`, giving FastAPI automatic validation and Swagger documentation. Phone numbers must match an E.164 style pattern and OTP codes must be exactly six digits.

## WhatsApp Sender Service Details, Node.js

This service is deliberately separate from the FastAPI backend. It is the only part of the system that talks to WhatsApp, using the whatsapp-web.js library, which drives a real WhatsApp Web session through a headless browser.

### Multi Session Design, src/senderManager.js

The service supports more than one WhatsApp sender account at the same time. Each sender phone number gets:
- its own whatsapp-web.js Client instance
- its own LocalAuth session, isolated by a clientId derived from the sender phone, so session files on disk do not collide between senders
- its own lifecycle state, tracked in an in-memory Map keyed by a normalized sender id

Lifecycle events tracked per sender:
- `qr`: WhatsApp needs the account to be linked by scanning a QR code
- `authenticated`: the QR login or restored session was accepted
- `ready`: the session is fully initialized and can send messages
- `auth_failure`: authentication failed, for example the linked device was removed on the phone
- `disconnected`: the session dropped and can no longer send

Sending is only allowed when a sender's state is ready. Before sending, the service also checks that the receiver number is actually registered on WhatsApp and returns a clear error if not, instead of attempting a send that would fail silently.

Sessions are in-memory only. If the Node process restarts, previously linked senders must be re-registered through `POST /senders`, even though their saved auth files remain on disk.

### API Routes, src/index.js

- `GET /health`: confirms the Express process is running
- `GET /status`: reports process level sender counts and the full session list
- `POST /senders`: creates or loads a sender session for a phone number
- `GET /senders`: lists all sender sessions currently in memory
- `GET /senders/{senderId}/status`: status for one sender
- `GET /senders/{senderId}/qr`: current QR payload for one sender
- `POST /senders/{senderId}/logout`: logs out and removes a sender session
- `POST /senders/normalize`: converts a phone number into the sender id format used by the other routes, for manual testing
- `POST /send-otp`: sends an OTP message through a selected sender to a receiver phone number

Every route except `/health` requires an `X-SENDER-API-KEY` header matching the service's configured key. This is a simple shared secret intended for local development, not a production grade auth scheme.

### Supporting Modules

- `src/phone.js` validates that phone numbers are E.164 style and converts them into the plain digit format whatsapp-web.js expects
- `src/messageTemplate.js` validates the six digit OTP shape and builds the exact WhatsApp message text sent to the receiver

## Security Considerations

- OTP codes are never stored in plaintext, only a salted SHA-256 hash is written to MongoDB
- Each OTP has its own random salt, so two identical codes stored at different times produce different hashes
- Verification uses `hmac.compare_digest` for constant time comparison, which reduces the risk of leaking information through response timing
- OTPs are single use, the record is deleted immediately after a successful verification
- A resend cooldown prevents a phone number from being sent new OTPs too quickly
- A maximum verification attempt limit forces a fresh OTP request after repeated wrong guesses, instead of allowing unlimited guessing against one code
- Calls between the FastAPI backend and the Node sender service are protected by a shared API key header
- Phone numbers and OTP codes are validated against strict patterns before they reach business logic, blocking malformed input early

## Current Status

Completed:
- FastAPI backend with all OTP and sender management routes implemented
- MongoDB backed OTP storage with proper indexing
- Full OTP business logic: generation, hashing, cooldown, expiry, attempt limits, single use verification
- Mock sender for backend only testing
- Node WhatsApp sender service with multi-session support
- FastAPI to Node integration for both OTP delivery and sender administration

Not yet completed:
- Full end to end validation of the WhatsApp delivery path with a real linked sender and a real receiver
- The Flutter mobile app has not been started

## Running the Project

Backend:
1. `cd backend`
2. Create and activate a Python virtual environment
3. `pip install -r requirements.txt`
4. `uvicorn app.main:app --reload`

WhatsApp sender service:
1. `cd whatsapp-sender`
2. `npm install`
3. `npm start`

The backend defaults to mock sender mode, which prints OTPs to the console instead of sending real WhatsApp messages. Switching `OTP_SENDER_MODE` to `whatsapp` in `backend/.env` requires the Node sender service to be running and a sender phone number to already be linked through `POST /set-sender`.
