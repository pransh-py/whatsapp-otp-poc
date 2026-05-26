import httpx

from app.config import settings
from app.schemas import SenderStatusResponse
from app.senders.whatsapp_http_sender import WhatsappSenderError


# FastAPI-side admin adapter for the Node WhatsApp sender service.
#
# Responsibility:
# - call Node sender-management endpoints
# - hide X-SENDER-API-KEY from Swagger users
# - normalize JavaScript response field names into Python/FastAPI snake_case
#
# Non-responsibility:
# This class must not manage whatsapp-web.js Client objects, QR events, or
# LocalAuth directories. Those remain owned by whatsapp-sender/src/senderManager.js.
class WhatsappSenderAdmin:
    def __init__(self) -> None:
        self.base_url = settings.whatsapp_sender_url.rstrip("/")
        self.headers = {
            "X-SENDER-API-KEY": settings.whatsapp_sender_api_key,
        }
        self.timeout = httpx.Timeout(settings.whatsapp_sender_timeout_seconds)

    async def set_sender(self, *, sender_phone: str) -> SenderStatusResponse:
        data = await self._request_json(
            "POST",
            "/senders",
            json={"sender_phone": sender_phone},
        )
        status = self._to_sender_status(data)

        # QR may arrive slightly after sender creation. If the sender is not
        # ready yet, ask Node for its latest QR state so /set-sender can return
        # a QR immediately when one is available.
        if not status.ready:
            qr_status = await self.get_sender_qr(sender_id=status.sender_id)
            if qr_status.qr:
                return qr_status

        return status

    async def list_senders(self) -> list[SenderStatusResponse]:
        data = await self._request_json("GET", "/senders")
        return [self._to_sender_status(sender) for sender in data.get("senders", [])]

    async def get_sender_status(self, *, sender_id: str) -> SenderStatusResponse:
        data = await self._request_json("GET", f"/senders/{sender_id}/status")
        return self._to_sender_status(data)

    async def get_sender_qr(self, *, sender_id: str) -> SenderStatusResponse:
        # Node's QR endpoint intentionally returns a partial object focused on
        # QR fields. Fetch full status first, then overlay QR fields so FastAPI
        # does not accidentally report ready/authenticated as false for a sender
        # that is actually ready.
        status_data = await self._request_json("GET", f"/senders/{sender_id}/status")
        qr_data = await self._request_json("GET", f"/senders/{sender_id}/qr")
        merged_data = {
            **status_data,
            "hasQr": qr_data.get("hasQr", status_data.get("hasQr")),
            "qr": qr_data.get("qr", status_data.get("qr")),
            "lastQrAt": qr_data.get("lastQrAt", status_data.get("lastQrAt")),
            "clientState": qr_data.get(
                "clientState",
                status_data.get("clientState"),
            ),
        }
        return self._to_sender_status(merged_data)

    async def logout_sender(self, *, sender_id: str) -> dict:
        return await self._request_json("POST", f"/senders/{sender_id}/logout")

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    headers=self.headers,
                    json=json,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                try:
                    error_body = exc.response.json()
                except ValueError:
                    error_body = {}

                raise WhatsappSenderError(
                    status_code=exc.response.status_code,
                    error_code=error_body.get("error", "whatsapp_sender_error"),
                    message=error_body.get(
                        "message",
                        "WhatsApp sender returned an error.",
                    ),
                ) from exc
            except httpx.RequestError as exc:
                raise WhatsappSenderError(
                    status_code=503,
                    error_code="whatsapp_sender_unavailable",
                    message=f"Could not reach WhatsApp sender service: {exc}",
                ) from exc

    def _to_sender_status(self, data: dict) -> SenderStatusResponse:
        ready = bool(data.get("ready", False))
        authenticated = bool(data.get("authenticated", False))
        has_qr = bool(data.get("hasQr", False))
        qr = data.get("qr")
        client_state = data.get("clientState") or "unknown"

        return SenderStatusResponse(
            sender_id=str(data.get("senderId") or data.get("sender_id") or ""),
            sender_phone=str(data.get("senderPhone") or data.get("sender_phone") or ""),
            authenticated=authenticated,
            ready=ready,
            requires_qr=has_qr and bool(qr),
            client_state=client_state,
            qr=qr,
            last_error=data.get("lastError") or data.get("last_error"),
            last_qr_at=data.get("lastQrAt") or data.get("last_qr_at"),
            ready_at=data.get("readyAt") or data.get("ready_at"),
            disconnected_at=data.get("disconnectedAt")
            or data.get("disconnected_at"),
            message=self._build_message(
                ready=ready,
                authenticated=authenticated,
                requires_qr=has_qr and bool(qr),
                client_state=client_state,
                last_error=data.get("lastError") or data.get("last_error"),
            ),
        )

    def _build_message(
        self,
        *,
        ready: bool,
        authenticated: bool,
        requires_qr: bool,
        client_state: str,
        last_error: str | None,
    ) -> str:
        if ready:
            return "sender is already authenticated and ready"

        if requires_qr:
            return "scan the QR code and poll sender status"

        if authenticated:
            return "sender is authenticated, waiting for ready"

        if last_error:
            return f"sender is not ready: {last_error}"

        if client_state == "initializing":
            return "sender session is initializing, poll sender status"

        return "sender is not ready"
