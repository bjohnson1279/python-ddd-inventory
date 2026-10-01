import hmac
import hashlib
import base64

class WooCommerceWebhookSecurity:
    def __init__(self, api_secret: str):
        self.api_secret = api_secret.encode('utf-8')

    def validate_hmac(self, body: str, expected_hmac: str) -> bool:
        calculated_hash = hmac.new(
            self.api_secret, 
            body.encode('utf-8'), 
            hashlib.sha256
        ).digest()
        
        calculated_hmac_base64 = base64.b64encode(calculated_hash)
        expected_hmac_bytes = expected_hmac.encode('utf-8')

        return hmac.compare_digest(calculated_hmac_base64, expected_hmac_bytes)
