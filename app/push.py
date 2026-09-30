"""
Push notifications to the shop owner's own phone.

Uses Expo's push service, which is free and needs no Firebase account of our
own — the token the app registers is all it takes. Failures are logged and
swallowed everywhere: a notification that did not arrive must never fail the
job that was trying to send it, because that job is also sending email and
updating balances.

Built on urllib rather than requests, matching the mailer: this service runs
on a free tier where every dependency is one more thing that can fail to
install at deploy time, and the whole need here is one JSON POST.
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
TIMEOUT_SECONDS = 10


def is_expo_token(token: str) -> bool:
    """Expo tokens look like ExponentPushToken[xxxxxxxx]."""
    return bool(token) and token.startswith(("ExponentPushToken[", "ExpoPushToken["))


def send_push(token: str, title: str, body: str, data: dict = None) -> bool:
    """Returns whether Expo accepted the message. Never raises."""
    if not is_expo_token(token):
        logger.warning("Refusing to push to a token that is not an Expo token")
        return False

    payload = {
        "to": token,
        "title": title,
        "body": body,
        "sound": "default",
        "priority": "high",
    }
    if data:
        payload["data"] = data

    request = urllib.request.Request(
        EXPO_PUSH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — network, JSON and HTTP errors alike
        logger.exception("Could not send push notification")
        return False

    # Expo answers 200 with a per-message status inside the body, so an HTTP
    # success is not proof the message was accepted.
    status = (body.get("data") or {}).get("status")
    if status != "ok":
        logger.warning("Expo rejected the push: %s", str(body)[:200])
        return False
    return True
