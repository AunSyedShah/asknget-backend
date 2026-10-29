"""WhatsApp verification, started by the user.

The user sends *us* a WhatsApp message (a QR or a button opens WhatsApp with the text already typed), so there is no
code to type and nothing for us to send first: Twilio tells our webhook which number the message came from, and that
number is the proof. A reply inside the user's 24-hour window is free on Meta's side, and we only reply when a message
carries a valid token, so strangers writing to the number cost nothing.
"""

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from urllib.parse import quote
from xml.sax.saxutils import escape

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from ninja.errors import HttpError

from core.validators import _normalize_phone

from .models import User, WhatsappChallenge
from .services import client_ip, mask_target

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I, so a token is never misread
TOKEN_LENGTH = 8
TOKEN_RE = re.compile(rf"\b[{ALPHABET}]{{{TOKEN_LENGTH}}}\b")
PER_PERSON_PER_HOUR = 5
PER_IP_PER_HOUR = 20


def _hash(token: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"whatsapp:{token}".encode(), hashlib.sha256).hexdigest()


def start(*, request, user: User | None, purpose: str, phone: str | None) -> tuple[WhatsappChallenge, str, str]:
    if not settings.TWILIO_WHATSAPP_NUMBER:
        raise HttpError(503, "WhatsApp verification is not configured")
    if user is not None and user.phone and phone and phone != user.phone:
        raise HttpError(422, "That is not the phone number on your account")
    expected = phone or (user.phone if user is not None and user.phone else "")

    since = timezone.now() - timedelta(hours=1)
    recent = WhatsappChallenge.objects.filter(created_at__gte=since)
    ip = client_ip(request)
    who = recent.filter(user=user) if user is not None else recent.filter(expected_phone=expected) if expected else None
    if (who is not None and who.count() >= PER_PERSON_PER_HOUR) or (ip and recent.filter(ip=ip).count() >= PER_IP_PER_HOUR):
        raise HttpError(429, "Too many verification attempts. Try again later.")

    token = "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LENGTH))
    challenge = WhatsappChallenge.objects.create(
        user=user, purpose=purpose, expected_phone=expected, token_hash=_hash(token), ip=ip,
        expires_at=timezone.now() + timedelta(minutes=settings.WHATSAPP_CHALLENGE_TTL_MINUTES),
    )
    message = f"Verify your OTP for asknget: {token}"
    return challenge, f"https://wa.me/{settings.TWILIO_WHATSAPP_NUMBER}?text={quote(message)}", message


def state(challenge: WhatsappChallenge) -> str:
    if challenge.failure:
        return "failed"
    if challenge.verified_at:
        return "verified"
    return "expired" if challenge.expires_at < timezone.now() else "pending"


def masked(challenge: WhatsappChallenge) -> str:
    return mask_target(challenge.verified_phone) if challenge.verified_phone else ""


# --------------------------------------------------------------------------- #
# Twilio webhook
# --------------------------------------------------------------------------- #


def valid_signature(request) -> bool:
    """Twilio signs the full URL it called plus the sorted form fields (HMAC-SHA1 with the auth token)."""
    token = settings.TWILIO_AUTH_TOKEN
    given = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
    if not token or not given:
        return False
    url = settings.TWILIO_WEBHOOK_URL or request.build_absolute_uri()
    signed = url + "".join(key + value for key in sorted(request.POST) for value in sorted(request.POST.getlist(key)))
    expected = base64.b64encode(hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, given)


def twiml(text: str = "") -> HttpResponse:
    body = f"<Message>{escape(text)}</Message>" if text else ""
    return HttpResponse(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', content_type="text/xml")


def receive(request) -> HttpResponse:
    if not settings.TWILIO_AUTH_TOKEN:
        return HttpResponse("WhatsApp verification is not configured", status=503)
    if not valid_signature(request):
        return HttpResponse("Invalid signature", status=403)
    try:
        sender = _normalize_phone(re.sub(r"^whatsapp:", "", request.POST.get("From", "")).strip())
    except ValueError:
        return twiml()
    for token in dict.fromkeys(TOKEN_RE.findall(request.POST.get("Body", "").upper())):
        reply = _verify(sender, token)
        if reply is not None:
            return twiml(reply)
    return twiml()   # not one of ours: no reply, so no charge


def _verify(sender: str, token: str) -> str | None:
    with transaction.atomic():
        challenge = (WhatsappChallenge.objects.select_for_update(of=("self",)).select_related("user")
                     .filter(token_hash=_hash(token)).first())
        if challenge is None:
            return None
        if challenge.verified_at:
            return "Already verified. You can go back to asknget."
        if challenge.failure or challenge.expires_at < timezone.now():
            return "This verification has expired. Start again in asknget."
        user = challenge.user
        expected = challenge.expected_phone or (user.phone if user is not None and user.phone else "")
        if expected and sender != expected:
            return _fail(challenge, "phone_mismatch", "This is not the number on your asknget account. Start again from the right phone.")
        if user is not None:
            if not user.phone:
                if User.objects.filter(phone=sender).exclude(pk=user.pk).exists():
                    return _fail(challenge, "phone_in_use", "That number belongs to another asknget account.")
                user.phone = sender
            if challenge.purpose == WhatsappChallenge.Purpose.SIGNUP_PHONE:
                user.phone_verified_at = timezone.now()
            user.save(update_fields=["phone", "phone_verified_at", "updated_at"])
        challenge.verified_phone = sender
        challenge.verified_at = timezone.now()
        challenge.save(update_fields=["verified_phone", "verified_at", "updated_at"])
    return "Verified. You can go back to asknget."


def _fail(challenge: WhatsappChallenge, reason: str, reply: str) -> str:
    challenge.failure = reason
    challenge.save(update_fields=["failure", "updated_at"])
    return reply
