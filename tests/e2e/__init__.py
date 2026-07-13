"""
tests/e2e/ — End-to-end test suite for the Restaurant Management Platform.

E2E tests validate complete user workflows from QR scan through order completion,
including real-time WebSocket notifications.

Tests use:
  - channels.testing.WebsocketCommunicator for WebSocket assertions
  - rest_framework.test.APIClient for HTTP API calls
  - @pytest.mark.django_db(transaction=True) for Channels compatibility
  - InMemoryChannelLayer (configured in config/settings/testing.py)

Requirements: 10.1, 14.2–14.10, 17.1, 17.2
"""
