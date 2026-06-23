# WhatsApp OTP POC

Separate proof-of-concept for OTP login using Flutter, FastAPI, MongoDB, and a swappable OTP delivery layer.


## Current Status

Completed:

- FastAPI backend scaffold.
- Pydantic schemas for health, sender management, OTP request/resend, and OTP verification.
- Working routes:
  - `GET /health`
  - `POST /set-sender`
  - `GET /senders`
  - `GET /senders/{sender_id}/status`
  - `GET /senders/{sender_id}/qr`
  - `POST /senders/{sender_id}/logout`
  - `POST /otp/request`
  - `POST /otp/resend`
  - `POST /otp/verify`
- Environment-based backend config.
- MongoDB connection helper.
- MongoDB OTP store layer with index setup and OTP record methods.
- FastAPI startup wiring for MongoDB OTP indexes.
- Sender abstraction with a mock console OTP sender.
- OTP service with:
  - 6-digit OTP generation using Python `secrets`
  - salted SHA-256 OTP hashing
  - MongoDB OTP storage
  - OTP expiry handling
  - resend cooldown handling
  - max verification attempt handling
  - secure hash comparison
  - failed attempt tracking
  - one-time OTP deletion after successful verification
- API routes connected to the OTP service.
- Local backend validation with MongoDB and Swagger/manual API calls.
- Separate Node.js WhatsApp sender service using whatsapp-web.js.
- Multi-session WhatsApp sender management:
  - one sender session per sender phone number
  - sender-specific QR login and LocalAuth persistence
  - sender status, QR lookup, list, logout, and selected-sender OTP delivery
- FastAPI WhatsApp sender adapter and admin proxy routes.
- Git ignore rules for secrets, virtualenv, build artifacts, and WhatsApp session files.

Current priority:

- Manually validate the full FastAPI-to-WhatsApp flow with a test sender number.
- Document reliability, failure modes, and the production risk clearly before Flutter.

Not completed yet:

- Full end-to-end validation with a real QR-linked sender session and receiver.
- Flutter app.

## Project Structure

```text
whatsapp_otp_poc/
  backend/
    app/
      config.py
      db.py
      main.py
      otp_service.py
      otp_store.py
      schemas.py
      senders/
        __init__.py
        base.py
        mock_sender.py
    requirements.txt
    .env.example
  whatsapp-sender/
    src/
      index.js
      senderManager.js
      phone.js
      messageTemplate.js
    MANUAL_TESTING.md
    package.json
  PROJECT_CONTEXT.md
  README.md
```

## Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

Request OTP:

```bash
curl -X POST http://127.0.0.1:8000/otp/request \
  -H "Content-Type: application/json" \
  -d '{"sender_phone":"+918637427358","phone":"+919876543210"}'
```

The mock sender prints the generated OTP in the backend console:

```text
[MOCK OTP] sender_phone=+918637427358 receiver_phone=+919876543210 otp=123456
```

Resend OTP:

```bash
curl -X POST http://127.0.0.1:8000/otp/resend \
  -H "Content-Type: application/json" \
  -d '{"sender_phone":"+918637427358","phone":"+919876543210"}'
```

Verify OTP:

```bash
curl -X POST http://127.0.0.1:8000/otp/verify \
  -H "Content-Type: application/json" \
  -d '{"phone":"+919876543210","otp":"123456"}'
```

Expected successful response:

```json
{"verified":true,"message":"otp verified successfully"}
```

## Config

Sample config is documented in `backend/.env.example`.

```env
MONGODB_URL=mongodb://localhost:27017
MONGODB_DB_NAME=whatsapp_otp_poc
OTP_EXPIRY_SECONDS=300
OTP_RESEND_COOLDOWN_SECONDS=60
OTP_MAX_VERIFY_ATTEMPTS=5
OTP_SENDER_MODE=mock
WHATSAPP_SENDER_URL=http://127.0.0.1:3001
WHATSAPP_SENDER_API_KEY=local-dev-key
WHATSAPP_SENDER_TIMEOUT_SECONDS=10
```

Do not commit a real `.env` file.

## WhatsApp Sender Setup

The Node sender service is in `whatsapp-sender/`.

```bash
cd whatsapp-sender
npm install
npm start
```

The service listens on `http://127.0.0.1:3001` by default.

Register a sender session through FastAPI:

```bash
curl -X POST http://127.0.0.1:8000/set-sender \
  -H "Content-Type: application/json" \
  -d '{"sender_phone":"+918637427358"}'
```

If the sender needs login, scan the QR from the Node terminal or call:

```bash
curl http://127.0.0.1:8000/senders/918637427358/qr
```

Poll status until `ready` is `true`:

```bash
curl http://127.0.0.1:8000/senders/918637427358/status
```

For detailed direct Node-service tests, see `whatsapp-sender/MANUAL_TESTING.md`.

## Next Steps

1. Run the Node sender service.
2. Register a test sender number with `POST /set-sender`.
3. Scan the QR and confirm the sender reaches `ready: true`.
4. Set `OTP_SENDER_MODE=whatsapp` in `backend/.env`.
5. Validate `POST /otp/request`, WhatsApp delivery, and `POST /otp/verify`.
6. Document setup, limitations, failure modes, and account-ban/platform-term risks.
7. Defer Flutter until WhatsApp delivery feasibility is tested.
