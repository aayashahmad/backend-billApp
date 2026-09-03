import logging
import os
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.mailer import (
    MailNotConfigured,
    MailSendFailed,
    is_configured,
    send_password_reset_code,
    transport_name,
)
from app.models import PasswordResetCode, User
from app.schemas import (
    AccountUpdate,
    ChangePasswordRequest,
    SignupRequest,
    LoginRequest,
    AuthResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
    UserProfileOut,
)
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Long enough not to be guessable within the attempt limit, short enough to
# read off a phone screen and type.
CODE_DIGITS = 6
CODE_TTL_MINUTES = 15
MAX_CODE_ATTEMPTS = 5
# One live code per account at a time; asking again replaces the old one.
RESEND_COOLDOWN_SECONDS = 60

# The same generic answer for a registered and an unregistered number.
GENERIC_RESET_MESSAGE = (
    "If that number has an account, a reset code has been sent to the email "
    "address on it."
)


def _mask_email(email: str) -> str:
    """`bhatashu666@gmail.com` -> `bh•••••@gmail.com`, enough to recognise."""
    name, _, domain = (email or "").partition("@")
    if not domain:
        return ""
    head = name[:2] if len(name) > 2 else name[:1]
    return f"{head}{'•' * 5}@{domain}"


def _dev_otp_logging_allowed() -> bool:
    """
    Printing the code to the log is a development convenience only.

    Gated on a local sqlite database so it can never fire against the hosted
    Postgres, where the log is not a private place.
    """
    return (os.getenv("DATABASE_URL") or "").startswith("sqlite")


@router.post("/signup", response_model=AuthResponse, status_code=201)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(
        or_(User.email == req.email, User.phone == req.phone)
    ).first()
    if existing:
        raise HTTPException(status_code=400,
                            detail="Email or phone already registered")

    user = User(
        username=req.username,
        email=req.email,
        phone=req.phone,
        password_hash=hash_password(req.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, user.username)
    return AuthResponse(token=token, username=user.username)


@router.get("/me", response_model=UserProfileOut)
def get_profile(current_user: User = Depends(get_current_user)):
    """Profile of the signed-in shop owner, resolved from the bearer token."""
    return current_user


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PATTERN = re.compile(r"^\d{7,20}$")


@router.put("/me", response_model=UserProfileOut)
def update_account(
    payload: AccountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the signed-in owner's own name, email and phone.

    Email and phone are unique across accounts and double as login
    identifiers, so a clash has to be reported rather than silently
    overwriting somebody else's row.
    """
    username = (payload.username or "").strip()
    email = (payload.email or "").strip().lower()
    phone = (payload.phone or "").strip()

    if not username:
        raise HTTPException(status_code=422, detail="Name is required.")
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if not PHONE_PATTERN.match(phone):
        raise HTTPException(
            status_code=422, detail="Phone number must be 7 to 20 digits."
        )

    email_changed = email != (current_user.email or "").lower()
    phone_changed = phone != (current_user.phone or "")

    # Both are credentials, so prove it is really the owner at the keyboard
    # and not somebody who picked up an unlocked phone.
    if email_changed or phone_changed:
        if not payload.current_password:
            raise HTTPException(
                status_code=403,
                detail="Enter your current password to change your email or phone.",
            )
        if not verify_password(payload.current_password, current_user.password_hash):
            raise HTTPException(status_code=403, detail="That password is incorrect.")

    if email_changed:
        taken = (
            db.query(User)
            .filter(User.email == email, User.id != current_user.id)
            .first()
        )
        if taken:
            raise HTTPException(
                status_code=409, detail="That email is already used by another account."
            )

    if phone_changed:
        taken = (
            db.query(User)
            .filter(User.phone == phone, User.id != current_user.id)
            .first()
        )
        if taken:
            raise HTTPException(
                status_code=409,
                detail="That phone number is already used by another account.",
            )

    current_user.username = username
    current_user.email = email
    current_user.phone = phone

    if email_changed or phone_changed:
        # Any reset code in flight was sent to the old address, or names the
        # old number. Retire them so a change cannot be undone by a code the
        # previous holder still has.
        db.query(PasswordResetCode).filter(
            PasswordResetCode.user_id == current_user.id,
            PasswordResetCode.used_at.is_(None),
        ).update(
            {PasswordResetCode.used_at: datetime.utcnow()}, synchronize_session=False
        )

    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Change the password of the signed-in owner.

    Deliberately independent of email: an owner who still knows their
    password never has to wait on a message, so the routine case keeps
    working even if mail delivery is down. The current password is still
    required — a session left open on a counter must not be enough to lock
    the real owner out.
    """
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=403, detail="That password is incorrect.")

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=422, detail="Choose a password you have not used here before."
        )

    current_user.password_hash = hash_password(payload.new_password)

    # Any reset code still in flight was issued against the old password;
    # leaving it live would let whoever holds that email undo this change.
    db.query(PasswordResetCode).filter(
        PasswordResetCode.user_id == current_user.id,
        PasswordResetCode.used_at.is_(None),
    ).update({PasswordResetCode.used_at: datetime.utcnow()}, synchronize_session=False)

    db.commit()


@router.post("/login", response_model=AuthResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        or_(User.email == req.login, User.phone == req.login)
    ).first()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(user.id, user.username)
    return AuthResponse(token=token, username=user.username)


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Start a reset: emails a one-time code to the address on the account.

    The code goes to email rather than SMS because no SMS provider delivers
    OTPs free of charge, worldwide, in production — and every account already
    carries a verified-unique email address.
    """
    phone = (req.phone or "").strip()
    user = db.query(User).filter(User.phone == phone).first()

    # Unknown number: same response, no work done, nothing to enumerate.
    if not user:
        return ForgotPasswordResponse(message=GENERIC_RESET_MESSAGE)

    now = datetime.utcnow()

    # Rate limit per account, so this endpoint cannot be used to flood
    # someone's inbox — or to run up a bill on the sending account.
    latest = (
        db.query(PasswordResetCode)
        .filter(PasswordResetCode.user_id == user.id)
        .order_by(PasswordResetCode.created_at.desc())
        .first()
    )
    if latest and latest.created_at:
        age = (now - latest.created_at).total_seconds()
        if age < RESEND_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=(
                    "A code was just sent. Please wait "
                    f"{int(RESEND_COOLDOWN_SECONDS - age)} seconds before "
                    "asking for another."
                ),
            )

    # Any earlier code stops working the moment a new one is issued.
    db.query(PasswordResetCode).filter(
        PasswordResetCode.user_id == user.id,
        PasswordResetCode.used_at.is_(None),
    ).update({PasswordResetCode.used_at: now}, synchronize_session=False)

    # secrets, not random: this value guards an account.
    code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"

    db.add(
        PasswordResetCode(
            user_id=user.id,
            # Hashed with the same function as passwords — the plaintext code
            # is never written down anywhere.
            code_hash=hash_password(code),
            expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
            attempts=0,
        )
    )
    db.commit()

    if is_configured():
        try:
            send_password_reset_code(user.email, code, CODE_TTL_MINUTES)
        except Exception as exc:  # noqa: BLE001 — providers raise many types
            logger.exception(
                "Password reset email failed for user %s via %s",
                user.id,
                transport_name(),
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not send the reset email just now. "
                    "Please try again in a moment."
                ),
            ) from exc
    elif _dev_otp_logging_allowed():
        logger.warning("[dev] password reset code for %s: %s", user.phone, code)
    else:
        # Never pretend to have sent something in production.
        raise HTTPException(
            status_code=503,
            detail="Password reset email is not configured on this server.",
        )

    return ForgotPasswordResponse(
        message=GENERIC_RESET_MESSAGE,
        email_hint=_mask_email(user.email),
    )


@router.post("/reset-password", response_model=AuthResponse)
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Finish a reset: checks the code and sets the new password.

    Returns a token, so the owner lands in the app instead of being sent back
    to sign in with a password they have just typed twice.
    """
    phone = (req.phone or "").strip()
    code = (req.code or "").strip()

    user = db.query(User).filter(User.phone == phone).first()
    invalid = HTTPException(
        status_code=400, detail="That code is invalid or has expired."
    )
    if not user:
        raise invalid

    now = datetime.utcnow()
    record = (
        db.query(PasswordResetCode)
        .filter(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.used_at.is_(None),
        )
        .order_by(PasswordResetCode.created_at.desc())
        .first()
    )

    if not record or record.expires_at < now:
        raise invalid

    if record.attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many incorrect attempts. Ask for a new code.",
        )

    if not verify_password(code, record.code_hash):
        # Count the miss before returning, so guessing is bounded.
        record.attempts += 1
        db.commit()
        raise invalid

    user.password_hash = hash_password(req.new_password)
    # Burn the code: reuse would let anyone holding the email reset again.
    record.used_at = now
    db.commit()

    token = create_access_token(user.id, user.username)
    return AuthResponse(token=token, username=user.username)
