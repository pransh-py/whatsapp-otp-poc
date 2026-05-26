// index.js is the entry point for the local WhatsApp sender service.
//
// Python comparison:
// This file plays the same role for the Node service that backend/app/main.py
// plays for the FastAPI backend.
//
// Responsibilities of this file:
// - load .env config
// - create the Express app
// - register HTTP endpoints
// - check API-key authorization
// - validate request body shape
// - call the WhatsApp client wrapper
// - translate service errors into HTTP responses
// - start listening on the configured port

// dotenv loads environment variables from a local .env file into process.env.
//
// For this service, expected values are documented in .env.example:
//   PORT=3001
//   SENDER_API_KEY=local-dev-key
//
// We keep secrets/config outside source code so local development and future
// deployments can use different values without editing JavaScript files.
require('dotenv').config();

// Express is the HTTP server framework.
//
// We use it to expose a tiny API:
// - GET /health
// - GET /status
// - POST /senders
// - GET /senders
// - GET /senders/:senderId/status
// - POST /senders/:senderId/logout
// - POST /send-otp
const express = require('express');

// Import multi-session sender manager functions.
//
// Official Express architecture concept:
// Route handlers should stay focused on HTTP concerns: request validation,
// status codes, and JSON responses. Domain/session logic should live in a
// separate module.
//
// Our project significance:
// senderManager.js owns the multi-session WhatsApp lifecycle. index.js exposes
// that lifecycle as APIs so an operator can register sender numbers, scan QR,
// inspect readiness, and remove sender sessions.
const {
  createSender,
  getManagerStatus,
  getSenderStatus,
  listSenders,
  logoutSender,
  normalizeSenderPhone,
  sendOtpFromSender,
} = require('./senderManager');

// Import phone helpers.
//
// The route accepts public E.164-style phone numbers like "+919876543210".
// Before calling whatsapp-web.js, we convert them to symbol-free digits like
// "919876543210".
const { isValidE164Phone, toWhatsAppLookupNumber } = require('./phone');

// Import OTP validation and message formatting.
//
// FastAPI generates the OTP. This service only verifies the incoming value has
// the expected 6-digit shape before sending it.
const { isValidOtp, buildOtpMessage } = require('./messageTemplate');

// Create the Express application object.
const app = express();

// Read service config from environment variables.
//
// PORT:
// The sender service runs separately from FastAPI. The planned default is 3001.
//
// SENDER_API_KEY:
// This is a simple local shared secret. FastAPI must send it in the
// X-SENDER-API-KEY header when calling POST /send-otp.
//
// This is not production-grade auth. It is enough for a local POC so that random
// local requests cannot trigger messages unless they know the shared key.
const port = Number(process.env.PORT || 3001);
const senderApiKey = process.env.SENDER_API_KEY || 'local-dev-key';

// Register JSON body parsing middleware.
//
// Without this, req.body would be undefined for JSON requests.
//
// The size limit is intentionally small because /send-otp needs only:
//   { "phone": "...", "otp": "..." }
app.use(express.json({ limit: '16kb' }));

// Health endpoint.
//
// Purpose:
// Confirms the Express service is running.
//
// Important:
// This does not prove WhatsApp is logged in or ready. It only proves the HTTP
// process is alive.
app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

// Status endpoint.
//
// Purpose:
// Shows the current WhatsApp client lifecycle state.
//
// This is useful during manual testing:
// - before QR scan, hasQr should become true
// - after scan, authenticated should become true
// - when fully usable, ready should become true
// - if session fails, lastError should show the reason
app.get('/status', (req, res) => {
  res.json(getManagerStatus());
});

// Multi-session senders list endpoint.
//
// Method:
//   GET /senders
//
// Official Express concept:
// app.get(path, handlers...) registers a route for HTTP GET requests. A GET
// route should be read-only and safe to call repeatedly.
//
// Our project significance:
// This route shows every sender session currently loaded in the Node process.
// It is the operator's overview page for multi-sender OTP delivery.
//
// Important limitation:
// listSenders() returns in-memory sessions only. LocalAuth files may exist on
// disk for older senders, but they are not loaded into memory until createSender
// is called for that sender phone.
app.get('/senders', requireSenderApiKey, (req, res) => {
  res.json({
    senders: listSenders(),
  });
});

// API-key middleware for protected sender endpoints.
//
// Why middleware:
// If we later add more protected endpoints, they can reuse this function.
//
// Header:
//   X-SENDER-API-KEY: local-dev-key
//
// Express lowercases header names internally, but req.get(...) handles normal
// HTTP header casing for us.
function requireSenderApiKey(req, res, next) {
  const providedApiKey = req.get('X-SENDER-API-KEY');

  if (!providedApiKey || providedApiKey !== senderApiKey) {
    return res.status(401).json({
      error: 'invalid_api_key',
      message: 'Missing or invalid X-SENDER-API-KEY header.',
    });
  }

  return next();
}

// Accept both JavaScript-style and API-style sender field names.
//
// Current manual API examples use sender_phone. Earlier local code used
// senderPhone. Supporting both keeps the service easy to test while we converge
// the FastAPI-facing contract later.
function readSenderPhone(body) {
  return body?.sender_phone || body?.senderPhone;
}

// Read the selected sender id from request body.
//
// The API accepts either:
// - sender_id / senderId: already-normalized digits, for example 919876543210
// - sender_phone / senderPhone: E.164 input, for example +919876543210
//
// sender_phone is normalized through the same validation helper used by
// POST /senders. This keeps manual API calls flexible without weakening the
// internal sender lookup key.
function readSenderId(body) {
  const senderId = body?.sender_id || body?.senderId;

  if (senderId !== undefined) {
    if (typeof senderId !== 'string' || !/^[1-9]\d{7,14}$/.test(senderId)) {
      const error = new Error(
        'senderId must be digits only, such as 919876543210.'
      );
      error.code = 'INVALID_SENDER_ID';
      throw error;
    }

    return senderId;
  }

  const senderPhone = readSenderPhone(body);

  if (senderPhone === undefined) {
    const error = new Error(
      'sender_id or sender_phone is required for multi-session sending.'
    );
    error.code = 'MISSING_SENDER';
    throw error;
  }

  return normalizeSenderPhone(senderPhone);
}

// Read receiver phone from request body.
//
// Existing FastAPI adapter sends "phone". Manual multi-session examples may use
// "receiver_phone" or "receiverPhone". Supporting all three keeps Sub Task 4
// compatible with current backend code and the planned explicit API shape.
function readReceiverPhone(body) {
  return body?.receiver_phone || body?.receiverPhone || body?.phone;
}

// Translate expected sender-manager errors into HTTP responses.
//
// Official Express concept:
// A route handler can catch domain errors and return the corresponding HTTP
// status with res.status(...).json(...). This keeps clients from receiving
// generic 500 responses for expected cases.
//
// Our project significance:
// Multi-session sender operations have normal operational failures:
// - user typed invalid sender phone
// - sender was not registered
// - sender session is not ready yet
//
// Returning clear status codes makes Swagger/manual testing much easier.
function handleSenderManagerError(error, res) {
  if (error.code === 'INVALID_SENDER_PHONE') {
    return res.status(400).json({
      error: 'invalid_sender_phone',
      message: error.message,
    });
  }

  if (error.code === 'INVALID_SENDER_ID') {
    return res.status(400).json({
      error: 'invalid_sender_id',
      message: error.message,
    });
  }

  if (error.code === 'MISSING_SENDER') {
    return res.status(400).json({
      error: 'missing_sender',
      message: error.message,
    });
  }

  if (error.code === 'SENDER_NOT_FOUND') {
    return res.status(404).json({
      error: 'sender_not_found',
      message: error.message,
    });
  }

  if (error.code === 'SENDER_NOT_READY') {
    return res.status(503).json({
      error: 'sender_not_ready',
      message: error.message,
    });
  }

  if (error.code === 'NUMBER_NOT_REGISTERED') {
    return res.status(404).json({
      error: 'number_not_registered',
      message: error.message,
    });
  }

  console.error('Unexpected sender manager error:', error);

  return res.status(500).json({
    error: 'sender_operation_failed',
    message: 'Sender operation failed.',
  });
}

// Register/create a WhatsApp sender session.
//
// Method:
//   POST /senders
//
// Header:
//   X-SENDER-API-KEY: local-dev-key
//
// Body:
//   {
//     "senderPhone": "+919876543210"
//   }
//
// Official whatsapp-web.js concept:
// One Client instance represents one WhatsApp Web session. LocalAuth with a
// distinct clientId persists that one session separately from other sessions.
//
// Our project significance:
// This route is the API version of "set/register OTP sender phone number".
// The sender is not usable just because the phone was submitted. The phone must
// be QR-authenticated and reach ready=true before it can send OTPs.
app.post('/senders', requireSenderApiKey, (req, res) => {
  const senderPhone = readSenderPhone(req.body);

  try {
    const status = createSender(senderPhone);

    return res.status(201).json(status);
  } catch (error) {
    return handleSenderManagerError(error, res);
  }
});

// Get status for one sender session.
//
// Method:
//   GET /senders/:senderId/status
//
// Example:
//   GET /senders/919876543210/status
//
// Official Express concept:
// Route params such as :senderId are exposed on req.params.
//
// Our project significance:
// A frontend/operator can poll this endpoint after POST /senders to know when
// the sender has moved from QR/authenticated to ready.
app.get('/senders/:senderId/status', requireSenderApiKey, (req, res) => {
  const { senderId } = req.params;
  const status = getSenderStatus(senderId);

  if (!status) {
    return res.status(404).json({
      error: 'sender_not_found',
      message: 'Sender session was not found.',
    });
  }

  return res.json(status);
});

// Get QR state for one sender session.
//
// Method:
//   GET /senders/:senderId/qr
//
// This endpoint is useful when the service is tested from an API client instead
// of only watching the Node terminal. The same QR payload is also printed in the
// terminal as a scannable code.
app.get('/senders/:senderId/qr', requireSenderApiKey, (req, res) => {
  const { senderId } = req.params;
  const status = getSenderStatus(senderId);

  if (!status) {
    return res.status(404).json({
      error: 'sender_not_found',
      message: 'Sender session was not found.',
    });
  }

  return res.json({
    senderId: status.senderId,
    senderPhone: status.senderPhone,
    hasQr: status.hasQr,
    qr: status.qr,
    lastQrAt: status.lastQrAt,
    clientState: status.clientState,
  });
});

// Logout/remove one sender session.
//
// Method:
//   POST /senders/:senderId/logout
//
// Official whatsapp-web.js concept:
// client.logout() logs out of the WhatsApp Web session, and client.destroy()
// closes browser/client resources.
//
// Our project significance:
// If the wrong phone was linked, or a sender account should stop sending OTPs,
// the operator needs an API to remove it from the active sender list.
app.post('/senders/:senderId/logout', requireSenderApiKey, async (req, res) => {
  const { senderId } = req.params;

  try {
    const loggedOut = await logoutSender(senderId);

    if (!loggedOut) {
      return res.status(404).json({
        error: 'sender_not_found',
        message: 'Sender session was not found.',
      });
    }

    return res.json({
      loggedOut: true,
      senderId,
    });
  } catch (error) {
    return handleSenderManagerError(error, res);
  }
});

// Normalize a sender phone into the senderId used by URL-based APIs.
//
// Method:
//   POST /senders/normalize
//
// Body:
//   {
//     "senderPhone": "+919876543210"
//   }
//
// Why this route exists:
// It is optional, but useful during manual Swagger testing. It lets a tester see
// exactly which senderId should be used in /senders/:senderId/status and, later,
// /send-otp.
app.post('/senders/normalize', requireSenderApiKey, (req, res) => {
  const senderPhone = readSenderPhone(req.body);

  try {
    const senderId = normalizeSenderPhone(senderPhone);

    return res.json({
      senderPhone,
      senderId,
    });
  } catch (error) {
    return handleSenderManagerError(error, res);
  }
});

// Send OTP endpoint.
//
// Expected request:
//   POST /send-otp
//   X-SENDER-API-KEY: local-dev-key
//   Content-Type: application/json
//
//   {
//     "sender_id": "919876543210",
//     "phone": "+918765432109",
//     "otp": "123456"
//   }
//
// Also accepted:
// - sender_phone / senderPhone instead of sender_id
// - receiver_phone / receiverPhone instead of phone
//
// Response examples:
// - 200 when sent
// - 400 for missing sender or invalid sender/phone/OTP shape
// - 401 for missing/wrong API key
// - 404 when sender is not registered in this process
// - 404 when number is not registered on WhatsApp
// - 503 when selected sender WhatsApp session is not ready
// - 500 for unexpected errors
app.post('/send-otp', requireSenderApiKey, async (req, res) => {
  const otp = req.body?.otp;
  const receiverPhone = readReceiverPhone(req.body);

  let senderId;

  try {
    senderId = readSenderId(req.body);
  } catch (error) {
    return handleSenderManagerError(error, res);
  }

  // Validate receiver phone before doing any WhatsApp work.
  //
  // This protects the WhatsApp client from obviously bad input and gives the
  // caller a clear 400 response.
  if (!isValidE164Phone(receiverPhone)) {
    return res.status(400).json({
      error: 'invalid_receiver_phone',
      message:
        'receiver phone must be an E.164-style string such as +919876543210.',
    });
  }

  // Validate OTP shape.
  //
  // The backend should send the OTP as a string, not a number, to preserve
  // leading zeros.
  if (!isValidOtp(otp)) {
    return res.status(400).json({
      error: 'invalid_otp',
      message: 'otp must be a 6-digit string.',
    });
  }

  // Convert public API phone format to whatsapp-web.js lookup format.
  const receiverDigits = toWhatsAppLookupNumber(receiverPhone);

  // Build the exact message body in one place.
  const message = buildOtpMessage(otp);

  try {
    const result = await sendOtpFromSender(senderId, receiverDigits, message);

    return res.json({
      sent: true,
      senderId: result.senderId,
      to: result.to,
      messageId: result.messageId,
    });
  } catch (error) {
    return handleSenderManagerError(error, res);
  }
});

// Start the HTTP server first.
//
// Why before startClient():
// During startup, WhatsApp login may take time or wait for QR scan. Starting the
// HTTP server immediately lets us call /health and /status while auth is in
// progress.
app.listen(port, () => {
  console.log(`WhatsApp sender service listening on port ${port}.`);
});
