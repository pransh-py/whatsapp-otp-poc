// This file contains phone-number helper functions for the WhatsApp sender API.
//
// Why this is separate from index.js:
// index.js should focus on HTTP concerns:
// - reading request bodies
// - checking headers
// - choosing HTTP status codes
// - returning JSON responses
//
// Phone-number parsing and validation is a separate concern. Keeping it here
// makes the route code easier to read and makes the phone rules reusable if we
// later add another endpoint.

// This regular expression accepts a simple E.164-style international phone number.
//
// E.164 numbers usually look like:
//   +919876543210
//   +14155552671
//
// Rule breakdown:
//   ^       start of string
//   \+      literal plus sign; we require callers to send international format
//   [1-9]   first country-code digit cannot be 0
//   \d{7,14}
//           remaining digits; total length becomes 8 to 15 digits after "+"
//   $       end of string
//
// Why this is intentionally simple:
// Real phone-number validation is country-specific and more complex. For this
// POC, we only need to reject clearly invalid inputs before calling
// whatsapp-web.js. WhatsApp itself will still tell us whether the number is
// actually registered through client.getNumberId(...).
const E164_PHONE_PATTERN = /^\+[1-9]\d{7,14}$/;

// Validate the public API phone input.
//
// The Express endpoint will receive JSON like:
//   { "phone": "+919876543210", "otp": "123456" }
//
// We accept only strings so numbers are not accidentally transformed by JSON,
// JavaScript, or clients. Phone numbers are identifiers, not arithmetic values.
// For example, leading zeros can matter in some national formats, and very long
// numbers should not be treated as numeric data.
function isValidE164Phone(phone) {
  return typeof phone === 'string' && E164_PHONE_PATTERN.test(phone);
}

// Convert a public API phone number into the format whatsapp-web.js expects for
// number lookup.
//
// Our API accepts:
//   +919876543210
//
// whatsapp-web.js Client#getNumberId(number) accepts a symbol-free number string
// and automatically appends "@c.us" when needed, according to its docs.
//
// So we remove only the leading "+", producing:
//   919876543210
//
// We do not remove spaces, dashes, or brackets here because the validator should
// already reject those. Keeping this strict prevents surprising cleanup behavior
// where bad user input silently becomes something else.
function toWhatsAppLookupNumber(phone) {
  return phone.replace(/^\+/, '');
}

// Export the small public surface needed by index.js.
//
// index.js will use:
// - isValidE164Phone(...) before accepting a send request
// - toWhatsAppLookupNumber(...) before calling sendOtp(...)
module.exports = {
  isValidE164Phone,
  toWhatsAppLookupNumber,
};
