// qrcode-terminal converts QR payload text into a scannable terminal QR.
//
// Official docs concept:
// whatsapp-web.js emits a "qr" event when WhatsApp Web needs the user to link a
// device. The event gives QR data as text; qrcode-terminal renders that text in
// the terminal.
//
// Our project significance:
// Every sender phone number is a separate WhatsApp Web login. For multi-session
// support, each sender can independently require QR scan. Printing the sender
// phone beside the QR prevents confusion when multiple senders are being linked.
const qrcode = require('qrcode-terminal');

// Client is the main whatsapp-web.js object that controls one WhatsApp Web
// session. LocalAuth is the session persistence strategy.
//
// Official docs concept:
// - new Client(options) creates one WhatsApp Web client.
// - LocalAuth stores auth/session files locally.
// - LocalAuth supports a clientId option, which separates session folders.
//
// Our project significance:
// The old POC had one global Client, so all OTPs came from one WhatsApp account.
// Multi-sender support means one Client per sender phone. LocalAuth clientId is
// the key detail that keeps sender sessions separate on disk.
const { Client, LocalAuth } = require('whatsapp-web.js');

// Reuse the same phone helpers used by the HTTP routes.
//
// Official project concept:
// Validation should be consistent at every boundary. Even if index.js validates
// senderPhone before calling createSender(...), senderManager.js should protect
// itself because it owns the WhatsApp session lifecycle and can be called by
// future code paths.
//
// Our project significance:
// A sender phone is used as:
// - an API-facing sender identity
// - a Map key
// - a LocalAuth clientId
//
// So it must be normalized predictably and rejected if it is not E.164-style.
const { isValidE164Phone, toWhatsAppLookupNumber } = require('./phone');

// In-memory registry of all active sender sessions.
//
// Shape:
//   senderId -> {
//     senderId,
//     senderPhone,
//     client,
//     state
//   }
//
// Official JavaScript concept:
// Map is a key-value collection with stable lookup by key.
//
// Our project significance:
// OTP send requests will include a senderId. We use that senderId to find the
// correct WhatsApp Web client and send from that specific phone number.
//
// Important limitation:
// This Map is in-memory. If the Node process restarts, the LocalAuth files still
// exist, but the sender sessions must be loaded/registered again unless we later
// persist sender IDs in a database/config and auto-start them.
const senders = new Map();

// Create a coded Error object for predictable route-level error handling.
//
// Official JavaScript concept:
// Error objects can carry custom properties. Express route code can inspect
// error.code and map it to an HTTP response.
//
// Our project significance:
// Multi-session sender operations have expected failure modes:
// - invalid sender phone
// - sender not found
// - sender not ready
// - receiver not registered on WhatsApp
//
// Keeping error codes consistent here lets index.js return clean JSON instead
// of generic 500s.
function createCodedError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

// Convert public E.164-style phone number into a normalized sender id.
//
// Input:
//   +919876543210
//
// Output:
//   919876543210
//
// Why:
// - URLs are cleaner with digits.
// - Map keys are cleaner with digits.
// - LocalAuth clientId should be simple and filesystem-safe.
//
// Validation is expected to happen before this function is called. This function
// intentionally only removes the leading plus sign.
function normalizePhone(phone) {
  return toWhatsAppLookupNumber(phone);
}

// Validate and normalize sender phone into senderId.
//
// This is separate from normalizePhone(...) because createSender(...) should
// reject bad input with a clear coded error instead of silently creating an
// invalid LocalAuth clientId.
function normalizeSenderPhone(senderPhone) {
  if (!isValidE164Phone(senderPhone)) {
    throw createCodedError(
      'INVALID_SENDER_PHONE',
      'senderPhone must be an E.164-style string such as +919876543210.'
    );
  }

  return normalizePhone(senderPhone);
}

// Convert a senderId into the LocalAuth clientId stored on disk.
//
// LocalAuth creates its own session folder under dataPath using the clientId.
// Prefixing makes these folders visually distinct from any older single-session
// auth state that may already exist in .wwebjs_auth.
function toLocalAuthClientId(senderId) {
  return `sender_${senderId}`;
}

// Create the initial status object for one sender session.
//
// Each sender needs independent lifecycle state because sender A can be ready
// while sender B is still waiting for QR scan.
//
// State fields:
// - senderId:
//     Normalized digits, used by APIs and Map lookup.
// - senderPhone:
//     Human-facing E.164 phone, useful in API responses and logs.
// - ready:
//     True only after whatsapp-web.js emits "ready". Sending is allowed only
//     when this is true.
// - authenticated:
//     True after QR/session authentication succeeds.
// - hasQr:
//     True while a QR is available for the sender to scan.
// - lastError:
//     Stores the latest auth/disconnect error for troubleshooting.
// - clientState:
//     Small readable lifecycle label for status APIs.
function createInitialState(senderId, senderPhone) {
  return {
    senderId,
    senderPhone,
    ready: false,
    authenticated: false,
    hasQr: false,
    qr: null,
    lastError: null,
    clientState: 'initializing',
    createdAt: new Date().toISOString(),
    lastQrAt: null,
    readyAt: null,
    disconnectedAt: null,
  };
}

// Return a safe JSON-friendly status object for API responses.
//
// We return a copy of state instead of returning senderSession.state directly.
//
// Why:
// Other modules such as index.js should read status, not mutate internal state.
// The manager remains the owner of lifecycle state.
function buildSenderStatus(senderSession) {
  return { ...senderSession.state };
}

// Return process-level manager status.
//
// This powers GET /status in multi-session mode. It intentionally reports only
// sessions loaded in this Node process. Persisted LocalAuth folders are not
// auto-loaded until POST /senders is called for that sender phone.
function getManagerStatus() {
  const loadedSenders = listSenders();

  return {
    status: 'ok',
    mode: 'multi-session',
    loadedSenderCount: loadedSenders.length,
    readySenderCount: loadedSenders.filter((sender) => sender.ready).length,
    senders: loadedSenders,
  };
}

// Attach whatsapp-web.js lifecycle event handlers to one sender client.
//
// Official docs concept:
// whatsapp-web.js Client is an event emitter. Relevant events include:
// - qr
// - authenticated
// - ready
// - auth_failure
// - disconnected
//
// Our project significance:
// These events drive the sender status APIs. Without tracking them per sender,
// an operator cannot tell which sender needs QR scan or which sender is ready.
function attachClientEvents(senderSession) {
  const { client, state } = senderSession;

  // QR event.
  //
  // Emitted when this sender's WhatsApp Web session needs device linking.
  //
  // In multi-session mode, multiple senders may produce QR codes at different
  // times. The log includes senderPhone so the user knows which phone should
  // scan this QR.
  client.on('qr', (qr) => {
    state.ready = false;
    state.authenticated = false;
    state.hasQr = true;
    state.qr = qr;
    state.clientState = 'qr';
    state.lastError = null;
    state.lastQrAt = new Date().toISOString();
    state.readyAt = null;
    state.disconnectedAt = null;

    console.log(`Scan QR for sender ${state.senderPhone}:`);
    qrcode.generate(qr, { small: true });
  });

  // Authenticated event.
  //
  // Emitted when WhatsApp accepts the QR login or restores the LocalAuth session.
  //
  // Authenticated does not necessarily mean messages can be sent yet. We still
  // wait for the "ready" event before allowing send/check operations.
  client.on('authenticated', () => {
    state.authenticated = true;
    state.hasQr = false;
    state.qr = null;
    state.clientState = 'authenticated';
    state.lastError = null;

    console.log(`WhatsApp sender ${state.senderPhone} authenticated.`);
  });

  // Ready event.
  //
  // Emitted when whatsapp-web.js has fully initialized the session.
  //
  // Our project rule:
  // sendOtpFromSender and checkNumberFromSender require state.ready === true.
  client.on('ready', () => {
    state.ready = true;
    state.authenticated = true;
    state.hasQr = false;
    state.qr = null;
    state.clientState = 'ready';
    state.lastError = null;
    state.readyAt = new Date().toISOString();
    state.disconnectedAt = null;

    console.log(`WhatsApp sender ${state.senderPhone} is ready.`);
  });

  // Auth failure event.
  //
  // Emitted when authentication or session restore fails.
  //
  // Common causes:
  // - linked device removed from the phone
  // - saved LocalAuth session became invalid
  // - WhatsApp rejected the session
  //
  // In this state, the sender cannot be used for OTP delivery.
  client.on('auth_failure', (message) => {
    state.ready = false;
    state.authenticated = false;
    state.hasQr = false;
    state.qr = null;
    state.clientState = 'auth_failure';
    state.lastError = message;
    state.readyAt = null;

    console.error(`WhatsApp sender ${state.senderPhone} auth failure:`, message);
  });

  // Disconnected event.
  //
  // Emitted when the WhatsApp Web session disconnects.
  //
  // Our project significance:
  // If a sender disconnects, it must stop being eligible for OTP sends. The
  // status endpoint should show disconnected so the operator can restart or
  // relink the sender.
  client.on('disconnected', (reason) => {
    state.ready = false;
    state.hasQr = false;
    state.qr = null;
    state.clientState = 'disconnected';
    state.lastError = reason;
    state.readyAt = null;
    state.disconnectedAt = new Date().toISOString();

    console.warn(`WhatsApp sender ${state.senderPhone} disconnected:`, reason);
  });
}

// Create or return a sender session for a sender phone.
//
// This is the central entry point for registering a WhatsApp sender.
//
// Flow:
// 1. Normalize +91... into digits-only senderId.
// 2. If already registered in memory, return existing status.
// 3. Create per-sender lifecycle state.
// 4. Create one whatsapp-web.js Client for that sender.
// 5. Use LocalAuth clientId=senderId so auth files are isolated.
// 6. Attach lifecycle events.
// 7. Store session in the Map.
// 8. Start client.initialize().
// 9. Return initial status.
//
// Official docs concept:
// LocalAuth clientId identifies a particular local auth session.
//
// Our project significance:
// Without clientId, all senders would share one auth folder and overwrite each
// other. With clientId, sender sessions are separated under .wwebjs_auth.
function createSender(senderPhone) {
  const senderId = normalizeSenderPhone(senderPhone);

  if (senders.has(senderId)) {
    return buildSenderStatus(senders.get(senderId));
  }

  const state = createInitialState(senderId, senderPhone);

  const client = new Client({
    authStrategy: new LocalAuth({
      clientId: toLocalAuthClientId(senderId),
      dataPath: './.wwebjs_auth',
    }),
    puppeteer: {
      headless: true,
      timeout: 90_000,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-quic',
        '--disable-features=UseDnsHttpsSvcbAlpn',
      ],
    },
  });

  const senderSession = {
    senderId,
    senderPhone,
    client,
    state,
  };

  attachClientEvents(senderSession);

  senders.set(senderId, senderSession);

  client.initialize().catch((error) => {
    state.ready = false;
    state.authenticated = false;
    state.hasQr = false;
    state.qr = null;
    state.clientState = 'initialize_error';
    state.lastError = error.message || String(error);
    state.readyAt = null;

    console.error(
      `WhatsApp sender ${state.senderPhone} initialize error:`,
      error
    );
  });

  return buildSenderStatus(senderSession);
}

// Get the raw sender session from memory.
//
// This returns the internal object because send/check/logout functions need the
// actual whatsapp-web.js client.
//
// API routes should generally call getSenderStatus/listSenders instead of
// exposing this object directly.
function getSender(senderId) {
  return senders.get(senderId) || null;
}

// Return public status for a single sender.
//
// If the sender does not exist in memory, return null. The Express route will
// translate null into HTTP 404.
function getSenderStatus(senderId) {
  const senderSession = getSender(senderId);

  if (!senderSession) {
    return null;
  }

  return buildSenderStatus(senderSession);
}

// Return public status for all loaded sender sessions.
//
// This powers GET /senders.
//
// Note:
// This lists only sessions currently loaded in memory. A future version can load
// configured sender IDs from MongoDB or a config file on startup.
function listSenders() {
  return Array.from(senders.values()).map(buildSenderStatus);
}

// Logout and remove one sender session from memory.
//
// Flow:
// 1. Find sender.
// 2. If missing, return false.
// 3. Call client.logout() to log out the WhatsApp Web session.
// 4. Call client.destroy() to close browser/client resources.
// 5. Delete sender from Map.
//
// Project significance:
// Operators need a way to remove a sender session if the wrong phone was linked
// or the sender account should no longer be used for OTPs.
async function logoutSender(senderId) {
  const senderSession = getSender(senderId);

  if (!senderSession) {
    return false;
  }

  try {
    await senderSession.client.logout();
  } finally {
    await senderSession.client.destroy();
  }

  senders.delete(senderId);

  return true;
}

// Check whether a receiver number exists on WhatsApp using a selected sender.
//
// Why senderId is required:
// getNumberId(...) is a method on a specific WhatsApp client. In multi-session
// mode, the API must choose which authenticated sender session performs the
// lookup.
//
// Errors thrown here are intentionally coded:
// - SENDER_NOT_FOUND maps to HTTP 404
// - SENDER_NOT_READY maps to HTTP 503
//
// The Express routes will translate these error codes into structured API
// responses.
async function checkNumberFromSender(senderId, phoneDigits) {
  const senderSession = getSender(senderId);

  if (!senderSession) {
    throw createCodedError('SENDER_NOT_FOUND', 'Sender session was not found');
  }

  if (!senderSession.state.ready) {
    throw createCodedError(
      'SENDER_NOT_READY',
      'Sender WhatsApp session is not ready'
    );
  }

  const numberId = await senderSession.client.getNumberId(phoneDigits);

  return {
    hasWhatsapp: Boolean(numberId),
    whatsappId: numberId?._serialized || null,
  };
}

// Send an OTP message from a selected sender session.
//
// This replaces the old single-session sendOtp(...) behavior.
//
// Flow:
// 1. Find selected sender.
// 2. Ensure sender is ready.
// 3. Check whether receiver number exists on WhatsApp.
// 4. If not registered, throw NUMBER_NOT_REGISTERED.
// 5. Send message using senderSession.client.sendMessage(...).
// 6. Return message metadata.
//
// Project significance:
// This is the core of Option B. The senderId decides which WhatsApp account
// sends the OTP.
async function sendOtpFromSender(senderId, phoneDigits, message) {
  const senderSession = getSender(senderId);

  if (!senderSession) {
    throw createCodedError('SENDER_NOT_FOUND', 'Sender session was not found');
  }

  if (!senderSession.state.ready) {
    throw createCodedError(
      'SENDER_NOT_READY',
      'Sender WhatsApp session is not ready'
    );
  }

  const numberCheck = await checkNumberFromSender(senderId, phoneDigits);

  if (!numberCheck.hasWhatsapp) {
    throw createCodedError(
      'NUMBER_NOT_REGISTERED',
      'Phone number is not registered on WhatsApp'
    );
  }

  const sentMessage = await senderSession.client.sendMessage(
    numberCheck.whatsappId,
    message
  );

  return {
    senderId,
    to: numberCheck.whatsappId,
    messageId: sentMessage.id?._serialized || null,
  };
}

// Export the manager API used by index.js.
//
// index.js should own HTTP request/response handling.
// senderManager.js should own WhatsApp sender session lifecycle and operations.
module.exports = {
  createSender,
  getSender,
  getSenderStatus,
  listSenders,
  getManagerStatus,
  logoutSender,
  checkNumberFromSender,
  sendOtpFromSender,
  normalizeSenderPhone,
};
