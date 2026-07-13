"""
qr/exceptions.py

Domain exceptions for the QR code subsystem.

These are raised by QRService and caught by the customer-facing views to
produce appropriate HTTP error responses (404 / 410).

Requirements: 14.3, 14.4
"""


class QRCodeInvalid(Exception):
    """
    Raised by QRService.validate_qr when the supplied token does not
    correspond to an active QRCode record.

    This covers two scenarios:
      1. The token is completely unknown (no matching QRCode row).
      2. The token was previously valid but has since been deactivated
         because the Branch_Manager regenerated the QR code for that table
         (Requirement 14.3).

    The customer-facing view should surface an informative error message and
    prompt the customer to request a new code from staff (Requirement 14.4).
    """

    def __init__(self, message: str = "QR code is invalid or has been deactivated."):
        super().__init__(message)
        self.message = message
