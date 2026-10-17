"""Explicit authentication callback; caller owns bounded process, TTY and session."""
from __future__ import annotations

from getpass import GetPassWarning, getpass
import warnings


def prompt_secret(prompt: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        return getpass(prompt)


async def authenticate(sdk) -> dict:
    from telethon.errors import SessionPasswordNeededError

    if not await sdk.is_user_authorized():
        phone = prompt_secret("Telegram phone (hidden): ")
        sent = await sdk.send_code_request(phone)
        code = prompt_secret("Telegram login code (hidden): ")
        try:
            await sdk.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            await sdk.sign_in(password=prompt_secret("Telegram 2FA password (hidden): "))
        if not await sdk.is_user_authorized():
            raise RuntimeError("Telegram authorization was not confirmed")
    return {"authorized": True}


def client(session, api_id: int, api_hash: str):
    from telethon import TelegramClient

    return TelegramClient(session, api_id, api_hash, request_retries=0, connection_retries=0,
                          auto_reconnect=False, flood_sleep_threshold=0, receive_updates=False)
