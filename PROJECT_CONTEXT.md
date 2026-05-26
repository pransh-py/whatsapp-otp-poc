# WhatsApp OTP POC Context

This is a separate proof-of-concept app for WhatsApp OTP login without Meta APIs.

## Actual Background

The original task was to explore OTP login options for a FastAPI + MongoDB + Flutter hospital/medical app.

Research conclusion:

- OTP generation and verification can be implemented with open-source backend code.
- FastAPI can expose OTP request/verify endpoints.
- MongoDB can store OTP metadata and use TTL indexes for expiry.
- Python `secrets` can generate secure OTPs.
- Hashed OTP storage, resend cooldowns, and max attempt limits can be handled in our backend.
- Real SMS delivery to Indian phone numbers is not truly free for production.
- Open-source SMS tools like Gammu, Kannel, Jasmin, Android SMS gateway apps, or GoIP still need telecom infrastructure, SIM plans, GSM devices, SMPP routes, or DLT-compliant providers.
- Official WhatsApp OTP/business messaging also requires Meta WhatsApp Business APIs or a BSP and is paid.

Employer follow-up:

The employer asked whether OTP could be sent through WhatsApp without using Meta APIs, by checking whether a phone number is available on WhatsApp and sending through automation from an official WhatsApp channel.

POC conclusion:

- It is technically possible to attempt WhatsApp OTP delivery without Meta APIs using WhatsApp Web/app automation.
- This is unofficial and not production-safe.
- It can break when WhatsApp changes Web/app behavior.
- The sender account/number can be restricted, blocked, or banned.
- It may violate WhatsApp terms for unauthorized automation/non-personal use.
- For a medical app, this should be treated as a feasibility demo only, not a recommended production architecture.

Reason for this POC:

Build a small isolated demo to prove technical feasibility and document the risks clearly.

## Revised POC Goal

The immediate priority has changed. Flutter integration is deferred. The next goal is to prove whether the existing FastAPI OTP backend can deliver OTPs through a separate local WhatsApp Web automation service without using Meta APIs.

Revised flow:

1. Operator/API tester registers a sender through FastAPI `POST /set-sender`.
2. The Node WhatsApp sender service creates or loads that sender's whatsapp-web.js session.
3. Operator scans QR when required and waits for sender status `ready=true`.
4. User/API tester calls FastAPI `POST /otp/request` with `sender_phone` and receiver `phone`.
5. FastAPI generates a 6-digit OTP.
6. OTP metadata is stored temporarily in MongoDB.
7. A FastAPI sender adapter calls a separate local WhatsApp sender service.
8. The WhatsApp sender service uses WhatsApp Web automation from the selected test sender number.
9. The receiver gets the OTP message on WhatsApp if automation succeeds.
10. User/API tester calls FastAPI `POST /otp/verify`.
11. FastAPI verifies OTP, expiry, and attempts.
12. Flutter UI integration happens only after WhatsApp delivery feasibility is tested.

## Stack

- Frontend: Flutter
- Backend: FastAPI
- Database: MongoDB
- Initial OTP delivery: mock console sender
- Current next delivery target: separate Node.js service using unofficial WhatsApp Web automation

## Safety Constraints

- This is only a POC/research prototype.
- Do not connect this to any production backend.
- Do not commit credentials, QR sessions, phone numbers, OTPs, `.env`, or secret files.
- Use a test WhatsApp number only.
- Unofficial WhatsApp Web automation can break, violate WhatsApp terms, and get numbers blocked or banned.
- The WhatsApp sender service must use only a test WhatsApp number.
- QR/session files must never be committed.
- The POC must document whether automation is reliable enough for a demo and explicitly state that it is not production-safe.
- Production recommendation: official WhatsApp Business API/BSP, or an approved SMS route with required compliance.

## Backend Rules

- OTP length: 6 digits.
- OTP expiry: 5 minutes.
- Resend cooldown: 60 seconds.
- Max verify attempts: 5.
- Keep OTP business logic separate from FastAPI route functions.
- Keep delivery logic behind a sender abstraction.
- Prefer environment variables for config.

## Work Completed So Far

- Created a separate POC repository outside the company project.
- Initialized a FastAPI backend folder under `backend/`.
- Added Python dependencies in `backend/requirements.txt`.
- Created `schemas.py` with Pydantic request/response models.
- Created FastAPI routes:
  - `GET /health`
  - `POST /set-sender`
  - `GET /senders`
  - `GET /senders/{sender_id}/status`
  - `GET /senders/{sender_id}/qr`
  - `POST /senders/{sender_id}/logout`
  - `POST /otp/request`
  - `POST /otp/resend`
  - `POST /otp/verify`
- Created `config.py` for environment-based settings.
- Added `.env.example` for non-secret sample configuration.
- Created `db.py` for MongoDB connection setup using Motor.
- Created `otp_store.py` as the MongoDB data-access layer for OTP records.
- Wired `OtpStore.ensure_indexes()` into FastAPI startup using lifespan.
- Added sender abstraction files:
  - `senders/__init__.py`
  - `senders/base.py`
  - `senders/mock_sender.py`
- Created `otp_service.py` for OTP business logic:
  - generate OTP with Python `secrets`
  - hash OTP with a random salt
  - enforce resend cooldown
  - enforce expiry
  - enforce max verification attempts
  - compare OTP hashes securely with `hmac.compare_digest`
  - increment failed verification attempts
  - delete OTP after successful verification
  - call the mock sender abstraction
- Replaced placeholder route logic in `main.py` with real OTP service calls.
- Created a separate `whatsapp-sender/` Node.js service using whatsapp-web.js.
- Added QR login/session persistence with sender-specific LocalAuth client IDs.
- Added sender service endpoints:
  - `GET /health`
  - `GET /status`
  - `POST /senders`
  - `GET /senders`
  - `GET /senders/:senderId/status`
  - `GET /senders/:senderId/qr`
  - `POST /senders/:senderId/logout`
  - `POST /senders/normalize`
  - `POST /send-otp`
- Added FastAPI WhatsApp sender adapter:
  - `senders/whatsapp_http_sender.py`
  - `senders/whatsapp_sender_admin.py`
  - config for sender mode, sender service URL, API key, and timeout
- Updated OTP request/resend to include `sender_phone` so OTPs can be sent from a selected sender session.
- Started local MongoDB and validated backend startup.
- Tested `/health` successfully.
- Tested `/otp/request` with mock OTP generation.
- Tested `/otp/verify` failure behavior with invalid OTP input.
- Fixed MongoDB datetime normalization in OTP verification so naive UTC datetimes from Motor can be compared safely.
- Added `.gitignore` so `.env`, `.venv`, build outputs, and WhatsApp session files are not committed.
- Created and pushed the GitHub repository.

## Remaining Work

- Validate sender service manually:
  - QR login works with a test number
  - session persists across restarts
  - message can be sent to a test receiver number
  - failure is handled when WhatsApp is not authenticated
- Switch FastAPI delivery from `MockOtpSender` to WhatsApp adapter through config.
- Validate full backend flow:
  - `POST /set-sender` registers/loads a sender
  - sender reaches `ready=true`
  - `POST /otp/request` generates OTP using `sender_phone`
  - FastAPI stores OTP hash in MongoDB
  - FastAPI calls WhatsApp sender service
  - WhatsApp sender service sends OTP message
  - `POST /otp/verify` verifies the received OTP
- Document setup, testing result, limitations, and risks.
- Defer Flutter app screens and Flutter API integration until WhatsApp delivery feasibility is proven.
