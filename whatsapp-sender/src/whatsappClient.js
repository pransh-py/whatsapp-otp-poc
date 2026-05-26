// qrcode-terminal is not part of whatsapp-web.js.
// whatsapp-web.js gives us QR data as a string through the "qr" event.
// This package turns that QR string into a scannable QR code in the terminal.
const qrcode = require('qrcode-terminal');

// Client is the main WhatsApp Web controller.
// LocalAuth is the session persistence strategy from whatsapp-web.js.
// Docs: Client options include authStrategy.
// Docs: LocalAuth stores auth/session data in a local directory.
const { Client, LocalAuth } = require('whatsapp-web.js');

// This object is our own service state.
// whatsapp-web.js emits events, but it does not automatically give our Express API
// a clean JSON status object. We maintain one here so /status can later report:
// - whether QR login is needed
// - whether auth succeeded
// - whether WhatsApp is ready to send
// - latest error/disconnect reason
const state = {
  ready: false,
  authenticated: false,
  hasQr: false,
  lastError: null,
  clientState: 'initializing',
};

// Create one WhatsApp Web client instance for this local sender service.
//
// Docs: new Client(options)
// The Client controls a WhatsApp Web session through Puppeteer.
//
// authStrategy:
// Docs say Client's authStrategy decides how sessions are saved/restored.
// Without LocalAuth, the session would not persist cleanly and QR login may be
// required more often.
//
// LocalAuth:
// Docs describe LocalAuth as "local directory-based authentication".
// dataPath changes where session files are saved.
// We use ./.wwebjs_auth so session files stay inside whatsapp-sender/.
// This folder must be ignored by git because it contains local login/session data.
//
// puppeteer.headless:
// whatsapp-web.js uses Puppeteer under the hood.
// headless: true means Chromium runs in the background without opening a visible browser.
const client = new Client({
  authStrategy: new LocalAuth({
    dataPath: './.wwebjs_auth',
  }),
  puppeteer: {
    headless: true,
  },
});

// QR event.
//
// Docs: Client emits "qr" when a QR code is received.
// This happens when WhatsApp Web needs a phone to link this browser session.
//
// In our flow:
// 1. Start Node service.
// 2. whatsapp-web.js opens WhatsApp Web.
// 3. If no saved LocalAuth session exists, WhatsApp Web asks for QR login.
// 4. This event fires.
// 5. We print the QR in terminal.
// 6. You scan it from WhatsApp -> Linked devices -> Link a device.
client.on('qr', (qr) => {
  state.ready = false;
  state.authenticated = false;
  state.hasQr = true;
  state.clientState = 'qr';
  state.lastError = null;

  console.log('Scan this QR code with WhatsApp:');
  qrcode.generate(qr, { small: true });
});

// Authenticated event.
//
// Docs: Client emits "authenticated" when authentication succeeds.
//
// Important:
// authenticated means WhatsApp accepted the login/session.
// It does not necessarily mean the client is ready to send messages yet.
// For sending, we wait for the "ready" event.
client.on('authenticated', () => {
  state.authenticated = true;
  state.hasQr = false;
  state.clientState = 'authenticated';
  state.lastError = null;

  console.log('WhatsApp authenticated.');
});

// Ready event.
//
// Docs: Client emits "ready" when the client has initialized and is ready
// to receive messages.
//
// This is the state we care about before sending OTPs.
// If ready is false, /send-otp should later return HTTP 503.
client.on('ready', () => {
  state.ready = true;
  state.authenticated = true;
  state.hasQr = false;
  state.clientState = 'ready';
  state.lastError = null;

  console.log('WhatsApp client is ready.');
});

// Authentication failure event.
//
// Docs: Client emits "auth_failure" when session restore/authentication fails.
//
// Common reasons:
// - saved .wwebjs_auth session became invalid
// - linked device was removed from the phone
// - WhatsApp rejected the session
//
// In this state, sending is not possible.
client.on('auth_failure', (message) => {
  state.ready = false;
  state.authenticated = false;
  state.hasQr = false;
  state.clientState = 'auth_failure';
  state.lastError = message;

  console.error('WhatsApp auth failure:', message);
});

// Disconnected event.
//
// Docs: Client emits "disconnected" when the client disconnects.
// The event gives a reason, usually a WhatsApp state or "LOGOUT".
//
// Once disconnected, we mark ready false so the API does not pretend
// OTP sending is available.
client.on('disconnected', (reason) => {
  state.ready = false;
  state.hasQr = false;
  state.clientState = 'disconnected';
  state.lastError = reason;

  console.warn('WhatsApp disconnected:', reason);
});

// Start WhatsApp Web.
//
// Docs examples call client.initialize() after registering event handlers.
// We wrap it in a function so index.js controls startup.
//
// Later index.js will do:
// startClient();
function startClient() {
  client.initialize();
}

// Return current WhatsApp client status.
//
// We return a copy using { ...state } so callers cannot accidentally mutate
// the original state object.
//
// Later GET /status will return this JSON.
function getStatus() {
  return { ...state };
}

// Send an OTP WhatsApp message.
//
// phoneDigits must be an international number without "+".
// Example:
//   "919876543210"
//
// message is the already-formatted OTP text.
// Example:
//   "Your OTP is 123456. It expires in 5 minutes. Do not share this code."
async function sendOtp(phoneDigits, message) {
  // Guard: do not call WhatsApp methods until "ready" has fired.
  // Later Express will translate this error code to HTTP 503.
  if (!state.ready) {
    const error = new Error('WhatsApp client is not ready');
    error.code = 'CLIENT_NOT_READY';
    throw error;
  }

  // Docs: client.getNumberId(number)
  // It gets the registered WhatsApp ID for a number.
  // Docs say it returns an Object or null.
  // It returns null if the number is not registered on WhatsApp.
  //
  // We use this before sendMessage so we can return a clean 404 later
  // instead of failing with a lower-level send error.
  const numberId = await client.getNumberId(phoneDigits);

  if (!numberId) {
    const error = new Error('Phone number is not registered on WhatsApp');
    error.code = 'NUMBER_NOT_REGISTERED';
    throw error;
  }

  // Docs: client.sendMessage(chatId, content[, options])
  // It sends a message to a specific chatId.
  // It returns a Promise containing the Message that was just sent.
  //
  // numberId._serialized is the WhatsApp chat id.
  // Example:
  //   "919876543210@c.us"
  const sentMessage = await client.sendMessage(numberId._serialized, message);

  // Return minimal metadata for our HTTP API response.
  // The optional chaining protects us if whatsapp-web.js returns a message shape
  // without id._serialized in some edge case.
  return {
    to: numberId._serialized,
    messageId: sentMessage.id?._serialized || null,
  };
}

// Export only the functions index.js needs.
// This keeps the WhatsApp implementation hidden behind a small local API.
module.exports = {
  startClient,
  getStatus,
  sendOtp,
};
