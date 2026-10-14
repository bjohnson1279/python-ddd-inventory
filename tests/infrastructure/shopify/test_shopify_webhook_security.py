import pytest
import hmac
import hashlib
import base64
from src.infrastructure.shopify.shopify_webhook_security import ShopifyWebhookSecurity

def test_validate_hmac_valid():
    secret = "my_secret_key"
    body = '{"id": 12345, "title": "Test Product"}'
    
    # Calculate what the valid hmac should be
    calculated_hash = hmac.new(
        secret.encode('utf-8'), 
        body.encode('utf-8'), 
        hashlib.sha256
    ).digest()
    valid_hmac = base64.b64encode(calculated_hash).decode('utf-8')
    
    security = ShopifyWebhookSecurity(secret)
    assert security.validate_hmac(body, valid_hmac) is True

def test_validate_hmac_invalid():
    secret = "my_secret_key"
    body = '{"id": 12345, "title": "Test Product"}'
    invalid_hmac = "invalid_hmac_string_here"
    
    security = ShopifyWebhookSecurity(secret)
    assert security.validate_hmac(body, invalid_hmac) is False

def test_validate_hmac_different_body():
    secret = "my_secret_key"
    body1 = '{"id": 12345}'
    body2 = '{"id": 67890}'
    
    calculated_hash = hmac.new(
        secret.encode('utf-8'), 
        body1.encode('utf-8'), 
        hashlib.sha256
    ).digest()
    hmac_body1 = base64.b64encode(calculated_hash).decode('utf-8')
    
    security = ShopifyWebhookSecurity(secret)
    assert security.validate_hmac(body2, hmac_body1) is False
