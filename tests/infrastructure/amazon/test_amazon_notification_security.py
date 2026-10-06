import pytest
from src.infrastructure.amazon.amazon_notification_security import AmazonNotificationSecurity

def test_init_sets_expected_topic_arn():
    topic_arn = "arn:aws:sns:us-east-1:123456789012:MyTopic"
    security = AmazonNotificationSecurity(expected_topic_arn=topic_arn)
    assert security.expected_topic_arn == topic_arn

def test_validate_sns_signature_returns_true():
    topic_arn = "arn:aws:sns:us-east-1:123456789012:MyTopic"
    security = AmazonNotificationSecurity(expected_topic_arn=topic_arn)

    body = '{"Type": "Notification", "Message": "Test message"}'
    signature = "EXAMPLE_SIGNATURE_BASE64=="

    result = security.validate_sns_signature(body, signature)
    assert result is True
