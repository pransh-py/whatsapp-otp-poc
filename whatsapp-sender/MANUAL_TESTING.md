# WhatsApp Sender Manual Testing

Run from `whatsapp-sender/`:

```powershell
npm start
```

The service listens on `http://127.0.0.1:3001` by default.

## Health

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:3001/health -Method Get
```

Expected:

```json
{"status":"ok"}
```

## Multi-Session Status

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:3001/status -Method Get | ConvertTo-Json -Depth 5
```

Expected before registering senders:

```json
{
  "status": "ok",
  "mode": "multi-session",
  "loadedSenderCount": 0,
  "readySenderCount": 0,
  "senders": []
}
```

## Normalize Sender Phone

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:3001/senders/normalize `
  -Method Post `
  -Headers @{ 'X-SENDER-API-KEY' = 'local-dev-key' } `
  -ContentType 'application/json' `
  -Body '{"sender_phone":"+919876543210"}' |
  ConvertTo-Json -Depth 5
```

Expected:

```json
{
  "senderPhone": "+919876543210",
  "senderId": "919876543210"
}
```

## Register Sender Session

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:3001/senders `
  -Method Post `
  -Headers @{ 'X-SENDER-API-KEY' = 'local-dev-key' } `
  -ContentType 'application/json' `
  -Body '{"sender_phone":"+919876543210"}' |
  ConvertTo-Json -Depth 5
```

If this sender has no valid saved WhatsApp Web session, the terminal prints a QR
code. Scan it from WhatsApp linked devices. Session data is stored under
`.wwebjs_auth/` using a sender-specific LocalAuth client id.

## Poll Sender Status

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:3001/senders/919876543210/status `
  -Method Get `
  -Headers @{ 'X-SENDER-API-KEY' = 'local-dev-key' } |
  ConvertTo-Json -Depth 5
```

The sender is usable only when `ready` is `true`.

## Get Sender QR Payload

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:3001/senders/919876543210/qr `
  -Method Get `
  -Headers @{ 'X-SENDER-API-KEY' = 'local-dev-key' } |
  ConvertTo-Json -Depth 5
```

## List Loaded Senders

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:3001/senders `
  -Method Get `
  -Headers @{ 'X-SENDER-API-KEY' = 'local-dev-key' } |
  ConvertTo-Json -Depth 5
```

## Logout Sender

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:3001/senders/919876543210/logout `
  -Method Post `
  -Headers @{ 'X-SENDER-API-KEY' = 'local-dev-key' } |
  ConvertTo-Json -Depth 5
```

## Send OTP From Selected Sender

Use this only after the selected sender status has `ready: true`.

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:3001/send-otp `
  -Method Post `
  -Headers @{ 'X-SENDER-API-KEY' = 'local-dev-key' } `
  -ContentType 'application/json' `
  -Body '{"sender_id":"919876543210","phone":"+918765432109","otp":"123456"}' |
  ConvertTo-Json -Depth 5
```

Also accepted:

```json
{
  "sender_phone": "+919876543210",
  "receiver_phone": "+918765432109",
  "otp": "123456"
}
```

Expected success:

```json
{
  "sent": true,
  "senderId": "919876543210",
  "to": "918765432109@c.us",
  "messageId": "..."
}
```

Expected operational errors:

- `400 invalid_receiver_phone`
- `400 invalid_otp`
- `400 missing_sender`
- `404 sender_not_found`
- `404 number_not_registered`
- `503 sender_not_ready`
