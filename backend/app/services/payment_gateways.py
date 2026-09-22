"""Provider boundary. No provider errors or credentials are returned to the browser."""

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

from app.core.config import settings


class GatewayUnavailable(Exception):
    pass


class GatewayRejected(Exception):
    pass


def configured(provider, *, collection=True):
    if (
        collection and not settings.PAYMENTS_ENABLED
    ) or not settings.PAYMENT_INSTITUTION_ID:
        return False
    if provider == "stripe":
        return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRET)
    return bool(
        settings.MPESA_CONSUMER_KEY
        and settings.MPESA_CONSUMER_SECRET
        and settings.MPESA_SHORTCODE
        and settings.MPESA_PASSKEY
        and settings.PAYMENT_PUBLIC_URL.startswith("https://")
    )


def fingerprint(provider):
    values = [provider, settings.PAYMENT_MODE]
    if provider == "stripe":
        values += [settings.STRIPE_SECRET_KEY or ""]
    else:
        values += [settings.MPESA_CONSUMER_KEY or "", settings.MPESA_SHORTCODE or ""]
    return hashlib.sha256("|".join(values).encode()).hexdigest()


def callback_token(kind, identifier):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"payments:{kind}:{identifier}".encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_callback(kind, identifier, token):
    if not token or not hmac.compare_digest(callback_token(kind, identifier), token):
        raise HTTPException(400, "Invalid callback authentication")


def callback_url(kind, identifier):
    return (
        settings.PAYMENT_PUBLIC_URL.rstrip("/")
        + f"/api/v1/payments/webhooks/mpesa/{kind}/{identifier}?token="
        + callback_token(kind, identifier)
    )


def verify_stripe(body, signature):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "Stripe webhook is not configured")
    try:
        parts = [part.split("=", 1) for part in signature.split(",")]
        timestamps = [value for key, value in parts if key == "t"]
        signatures = [value for key, value in parts if key == "v1"]
        if len(timestamps) != 1 or abs(time.time() - int(timestamps[0])) > 300:
            raise ValueError
        expected = hmac.new(
            settings.STRIPE_WEBHOOK_SECRET.encode(),
            timestamps[0].encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        if not any(
            hmac.compare_digest(expected, candidate) for candidate in signatures
        ):
            raise ValueError
        payload = json.loads(body)
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("id"), str)
            or len(payload["id"]) > 150
        ):
            raise ValueError
        if payload.get("livemode") is not (
            settings.PAYMENT_MODE == "live"
        ) or payload.get("account"):
            raise ValueError
        return payload
    except (ValueError, TypeError, AttributeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Invalid Stripe webhook") from exc


def minor_amount(value):
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise HTTPException(400, "Invalid callback amount") from exc


class Gateways:
    def request(self, method, url, **kwargs):
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                response = client.request(method, url, **kwargs)
            if (
                method == "POST"
                and url
                in (
                    "https://api.stripe.com/v1/checkout/sessions",
                    "https://api.stripe.com/v1/refunds",
                )
                and response.status_code in (400, 401, 403, 404, 422)
            ):
                raise GatewayRejected
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (httpx.HTTPError, ValueError) as exc:
            # A timeout or API error does not prove that a payment request was not accepted.
            raise GatewayUnavailable from exc

    def stripe(self, method, path, **kwargs):
        headers = {
            "Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}",
            "Stripe-Version": "2025-02-24.acacia",
            **kwargs.pop("headers", {}),
        }
        return self.request(
            method, "https://api.stripe.com/v1/" + path, headers=headers, **kwargs
        )

    def mpesa(self, path, payload):
        base = (
            "https://api.safaricom.co.ke"
            if settings.PAYMENT_MODE == "live"
            else "https://sandbox.safaricom.co.ke"
        )
        token = self.request(
            "GET",
            base + "/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
        )
        if not isinstance(token.get("access_token"), str):
            raise GatewayUnavailable
        return self.request(
            "POST",
            base + path,
            headers={"Authorization": "Bearer " + token["access_token"]},
            json=payload,
        )

    def mpesa_auth(self):
        timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}".encode()
        ).decode()
        return {
            "BusinessShortCode": settings.MPESA_SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
        }

    def initiate(self, payment):
        if payment.provider == "stripe":
            data = self.stripe(
                "POST",
                "checkout/sessions",
                headers={"Idempotency-Key": "checkout-" + payment.id},
                data=payment.request_data,
            )
            if not isinstance(data.get("id"), str) or not data["id"].startswith("cs_"):
                raise GatewayUnavailable
            url = urlsplit(data.get("url") or "")
            if (
                url.scheme != "https"
                or url.hostname != "checkout.stripe.com"
                or url.username
                or url.password
            ):
                raise GatewayUnavailable
            return {"reference": data["id"], "checkout_url": data["url"]}
        data = self.mpesa(
            "/mpesa/stkpush/v1/processrequest",
            {**self.mpesa_auth(), **payment.request_data},
        )
        if str(data.get("ResponseCode")) != "0":
            raise GatewayRejected
        reference = data.get("CheckoutRequestID")
        if not isinstance(reference, str) or not 1 <= len(reference) <= 150:
            raise GatewayUnavailable
        return {"reference": reference, "checkout_url": None}

    def observe(self, payment):
        try:
            return self._observe(payment)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise GatewayUnavailable from exc

    def _observe(self, payment):
        if not payment.provider_reference:
            raise GatewayUnavailable
        if payment.provider == "mpesa":
            data = self.mpesa(
                "/mpesa/stkpushquery/v1/query",
                {**self.mpesa_auth(), "CheckoutRequestID": payment.provider_reference},
            )
            if data.get("CheckoutRequestID") != payment.provider_reference:
                raise GatewayUnavailable
            code = str(data.get("ResultCode", ""))
            if code == "0":
                return {
                    "status": "succeeded",
                    "transaction_reference": payment.transaction_reference,
                    "refunded_minor": payment.refunded_minor,
                }
            if code in {"1", "1032", "2001"}:
                return {"status": "failed", "refunded_minor": 0}
            return {"status": "pending", "refunded_minor": 0}
        data = self.stripe(
            "GET",
            "checkout/sessions/" + payment.provider_reference,
            params={"expand[]": "payment_intent.latest_charge"},
        )
        if (
            data.get("id") != payment.provider_reference
            or data.get("client_reference_id") != payment.id
            or data.get("metadata", {}).get("payment_id") != payment.id
            or data.get("amount_total") != payment.amount_minor
            or data.get("currency") != "kes"
            or data.get("livemode") is not (payment.mode == "live")
        ):
            raise GatewayUnavailable
        if data.get("payment_status") == "paid":
            intent = data.get("payment_intent") or {}
            charge = (
                intent.get("latest_charge") or {} if isinstance(intent, dict) else {}
            )
            if (
                not isinstance(intent, dict)
                or not isinstance(charge, dict)
                or intent.get("status") != "succeeded"
                or intent.get("amount_received") != payment.amount_minor
                or intent.get("currency") != "kes"
                or intent.get("metadata", {}).get("payment_id") != payment.id
                or charge.get("payment_intent") != intent.get("id")
                or charge.get("amount") != payment.amount_minor
                or charge.get("paid") is not True
                or charge.get("captured") is not True
            ):
                raise GatewayUnavailable
            refunded = charge.get("amount_refunded", 0)
            if type(refunded) is not int or not 0 <= refunded <= payment.amount_minor:
                raise GatewayUnavailable
            status = (
                "disputed"
                if charge.get("disputed")
                else "refunded"
                if refunded == payment.amount_minor
                else "partially_refunded"
                if refunded
                else "succeeded"
            )
            return {
                "status": status,
                "transaction_reference": intent["id"],
                "refunded_minor": refunded,
            }
        return {
            "status": "expired" if data.get("status") == "expired" else "pending",
            "refunded_minor": 0,
        }

    def refund(self, payment, refund):
        if payment.provider == "stripe":
            result = self.stripe(
                "POST",
                "refunds",
                headers={"Idempotency-Key": "refund-" + refund.id},
                data={
                    "payment_intent": payment.transaction_reference,
                    "amount": refund.amount_minor,
                    "metadata[refund_id]": refund.id,
                },
            )
            if (
                result.get("payment_intent") != payment.transaction_reference
                or result.get("amount") != refund.amount_minor
                or result.get("currency") != "kes"
                or not isinstance(result.get("id"), str)
            ):
                raise GatewayUnavailable
            return {
                "reference": result["id"],
                "status": result.get("status", "pending"),
            }
        result = self.mpesa(
            "/mpesa/reversal/v1/request",
            {
                "Initiator": settings.MPESA_INITIATOR,
                "SecurityCredential": settings.MPESA_SECURITY_CREDENTIAL,
                "CommandID": "TransactionReversal",
                "TransactionID": payment.transaction_reference,
                "Amount": refund.amount_minor // 100,
                "ReceiverParty": settings.MPESA_SHORTCODE,
                "RecieverIdentifierType": "11",
                "ResultURL": callback_url("reversals", refund.id),
                "QueueTimeOutURL": callback_url("reversal-timeouts", refund.id),
                "Remarks": "Order refund",
                "Occasion": refund.id,
            },
        )
        if str(result.get("ResponseCode")) != "0":
            raise GatewayRejected
        if not result.get("ConversationID") or not result.get(
            "OriginatorConversationID"
        ):
            raise GatewayUnavailable
        return {
            "reference": result["ConversationID"],
            "correlation_id": result["OriginatorConversationID"],
            "status": "pending",
        }

    def refund_status(self, payment, refund):
        if payment.provider != "stripe" or not refund.provider_reference:
            raise GatewayUnavailable
        result = self.stripe("GET", "refunds/" + refund.provider_reference)
        if (
            result.get("id") != refund.provider_reference
            or result.get("payment_intent") != payment.transaction_reference
            or result.get("amount") != refund.amount_minor
            or result.get("currency") != "kes"
        ):
            raise GatewayUnavailable
        return result.get("status", "pending")


def get_gateways():
    return Gateways()


class PaymentAccessLogFilter(logging.Filter):
    """Callback capability tokens must not appear in Uvicorn access logs."""

    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if isinstance(args[2], str) and args[2].startswith(
                "/api/v1/payments/webhooks/mpesa/"
            ):
                args[2] = args[2].split("?", 1)[0]
                record.args = tuple(args)
        return True
