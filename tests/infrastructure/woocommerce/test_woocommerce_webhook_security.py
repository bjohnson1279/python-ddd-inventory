import pytest
import hmac
import hashlib
import base64
from src.infrastructure.woocommerce.woocommerce_webhook_security import WooCommerceWebhookSecurity

def test_validate_hmac_valid():
    secret = "woocommerce_secret_key"
    body = '{"id": 1001, "status": "processing"}'

    calculated_hash = hmac.new(
        secret.encode('utf-8'),
        body.encode('utf-8'),
        hashlib.sha256
    ).digest()
    valid_hmac = base64.b64encode(calculated_hash).decode('utf-8')

    security = WooCommerceWebhookSecurity(secret)
    assert security.validate_hmac(body, valid_hmac) is True

def test_validate_hmac_invalid():
    secret = "woocommerce_secret_key"
    body = '{"id": 1001, "status": "processing"}'
    invalid_hmac = "invalid_hmac_signature"

    security = WooCommerceWebhookSecurity(secret)
    assert security.validate_hmac(body, invalid_hmac) is False

def test_validate_hmac_different_body():
    secret = "woocommerce_secret_key"
    body1 = '{"id": 1001, "status": "processing"}'
    body2 = '{"id": 1002, "status": "completed"}'

    calculated_hash = hmac.new(
        secret.encode('utf-8'),
        body1.encode('utf-8'),
        hashlib.sha256
    ).digest()
    hmac_body1 = base64.b64encode(calculated_hash).decode('utf-8')

    security = WooCommerceWebhookSecurity(secret)
    assert security.validate_hmac(body2, hmac_body1) is False
