"""Notification provider abstraction.

Authentication and Gmail mailbox access are separate concerns. Google login
ID token/session is never reused for Gmail send permissions.
"""
from __future__ import annotations

from .config import config


class EmailProvider:
    name = "base"
    # Vocabulary the UI can render: DISABLED / NOT IMPLEMENTED / CONFIGURED.
    state = "DISABLED"

    def configured(self) -> bool:
        return False

    def send_alarm(self, recipients: list[str], subject: str,
                   body: str) -> None:
        raise NotImplementedError


class DisabledEmailProvider(EmailProvider):
    name = "disabled"
    state = "DISABLED"

    def send_alarm(self, recipients: list[str], subject: str,
                   body: str) -> None:
        # Intentional no-op. Do not pretend delivery happened elsewhere.
        return None


class GmailEmailProvider(EmailProvider):
    name = "gmail"
    # OAuth consent and token storage are not implemented, so this provider cannot
    # deliver: reporting it as configured would tell an operator that alarm mail
    # works when it does not.
    state = "NOT IMPLEMENTED"

    def configured(self) -> bool:
        return False

    def send_alarm(self, recipients: list[str], subject: str,
                   body: str) -> None:
        raise RuntimeError(
            "Gmail notification provider is configured placeholder only; "
            "OAuth consent/token storage is not implemented yet."
        )


def get_email_provider() -> EmailProvider:
    if config.EMAIL_PROVIDER == "gmail":
        return GmailEmailProvider()
    return DisabledEmailProvider()
