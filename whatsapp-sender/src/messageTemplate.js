// This file owns OTP validation and WhatsApp message formatting.
//
// Why this is separate from index.js:
// The HTTP API should not contain business-message formatting inline. If the OTP
// text changes later, we should change it in one obvious place without digging
// through route handlers.

// This POC uses a fixed 6-digit OTP format.
//
// Rule breakdown:
//   ^      start of string
//   \d{6} exactly six numeric digits
//   $      end of string
//
// We require a string instead of a number because OTPs are codes, not numbers.
// A code like "012345" is valid as a string, but would lose its leading zero if
// treated as a JavaScript number.
const OTP_PATTERN = /^\d{6}$/;

// Validate the OTP received by the sender service.
//
// Important architecture note:
// The FastAPI backend is still the authority for OTP generation, hashing,
// expiry, cooldown, attempts, and verification. This Node service only validates
// the shape of the OTP before sending it over WhatsApp.
//
// That means this function checks only:
// - is it a string?
// - is it exactly six digits?
function isValidOtp(otp) {
  return typeof otp === 'string' && OTP_PATTERN.test(otp);
}

// Build the actual WhatsApp message sent to the receiver.
//
// Keeping this as a function gives us one place to update wording later.
//
// Current message:
//   Your OTP is 123456. It expires in 5 minutes. Do not share this code.
//
// Why mention expiry:
// It matches the backend OTP behavior and sets the user's expectation.
//
// Why mention "Do not share":
// OTPs are sensitive login codes. Even in a POC, the message should model the
// safety wording we would want in a real authentication flow.
function buildOtpMessage(otp) {
  return `Your OTP is ${otp}. It expires in 5 minutes. Do not share this code.`;
}

// Export only the helpers needed by index.js.
module.exports = {
  isValidOtp,
  buildOtpMessage,
};
