"""
MONARQ channel bot
------------------
- Posts the store advert to a channel every N hours (default 5).
- Deletes the previous advert so only one stays at the top of the feed.
- Replies with the same advert to /start and to any private message.
- Uses premium (custom) emoji only.

Requires: pip install "python-telegram-bot[job-queue]>=21.0"
Env vars: BOT_TOKEN, CHANNEL_ID, (optional) INTERVAL_HOURS, STATE_FILE
"""

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("monarq")

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]           # "@monarqupdates" or -100xxxxxxxxxx
INTERVAL_HOURS = float(os.getenv("INTERVAL_HOURS", "5"))
STATE_FILE = Path(os.getenv("STATE_FILE", "last_message.json"))

SHOP_URL = "https://themonarq.shop"
SUPPORT = "@monarqteam"

# --------------------------------------------------------------------------
# Premium (custom) emoji. Each entry is (custom_emoji_id, matching plain emoji).
# The plain emoji is the tag's inner text, which Telegram requires and shows to
# users without Premium. It must be a real emoji, never a text character.
# --------------------------------------------------------------------------
EMOJI = {
    "gem":       ("5226656353744862682", "\U0001F48E"),  # 💎
    "star":      ("5980995951160987855", "\u2B50"),      # ⭐
    "check":     ("5278622189556354905", "\u2705"),      # ✅
    "bolt":      ("5084974483685507801", "\u26A1"),      # ⚡
    "cross":     ("5042112436648281096", "\u274C"),      # ❌
    "globe":     ("5134452506935427991", "\U0001F30D"),  # 🌍
    "user":      ("5321304384838057247", "\U0001F464"),  # 👤
    "rocket":    ("5195033767969839232", "\U0001F680"),  # 🚀
    "sparkle":   ("5172739056592749710", "\u2728"),      # ✨
    "hourglass": ("5215327832040811010", "\u23F3"),      # ⏳
    "info":      ("5303138782004924588", "\u2139\uFE0F"),  # ℹ️
}


def pe(key: str) -> str:
    """Render a premium emoji entity."""
    emoji_id, plain = EMOJI[key]
    return f'<tg-emoji emoji-id="{emoji_id}">{plain}</tg-emoji>'


def build_ad() -> str:
    b = pe("check")
    return (
        f"{pe('gem')} <b>MONARQ — PREMIUM DIGITAL STORE</b>\n\n"
        f"{pe('star')} Browse full catalogue of premium digital products at "
        f'<a href="{SHOP_URL}">themonarq.shop</a>:\n\n'
        f"{b} RDP &amp; VPS\n"
        f"{b} VPN &amp; Proxies\n"
        f"{b} Webmail &amp; SMTP\n"
        f"{b} Verified accounts\n"
        f"{b} and much more\n\n"
        f"{pe('bolt')} <b>Instant orders.</b> Every order is delivered to you on Telegram "
        f"the moment payment is confirmed.\n\n"
        f"{pe('cross')} <b>Beware of impersonators.</b> My only official Telegram account is "
        f"{SUPPORT}. I will never contact you first from any other account, and monarqstore "
        f"accept payment through no other channel. Verify before you pay.\n\n"
        f"{pe('globe')} <b>Store:</b> {SHOP_URL}\n"
        f"{pe('user')} <b>Official contact:</b> {SUPPORT}"
    )


KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("\U0001F6D2  Visit Store", url=SHOP_URL)],
        [
            InlineKeyboardButton(
                "\U0001F4AC  Official Support", url=f"https://t.me/{SUPPORT.lstrip('@')}"
            )
        ],
    ]
)


async def send_ad(bot, chat_id, reply_to: int | None = None):
    kwargs = dict(
        chat_id=chat_id,
        text=build_ad(),
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=KEYBOARD,
    )
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to
    return await bot.send_message(**kwargs)


def load_last_id() -> int | None:
    try:
        return json.loads(STATE_FILE.read_text())["message_id"]
    except (OSError, ValueError, KeyError):
        return None


def save_last_id(message_id: int) -> None:
    try:
        STATE_FILE.write_text(json.dumps({"message_id": message_id}))
    except OSError as exc:
        log.warning("Could not persist state: %s", exc)


async def rotate_post(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post a fresh advert, then remove the previous one."""
    previous = load_last_id()
    try:
        message = await send_ad(context.bot, CHANNEL_ID)
    except TelegramError as exc:
        log.error("Failed to post advert: %s", exc)
        return
    save_last_id(message.message_id)
    log.info("Posted advert %s", message.message_id)

    if previous:
        try:
            await context.bot.delete_message(CHANNEL_ID, previous)
            log.info("Deleted previous advert %s", previous)
        except TelegramError as exc:
            log.warning("Could not delete advert %s: %s", previous, exc)


async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer /start and any incoming private message with the advert."""
    if not update.effective_message:
        return
    try:
        await send_ad(
            context.bot,
            update.effective_chat.id,
            reply_to=update.effective_message.message_id,
        )
    except TelegramError as exc:
        log.error("Could not reply to %s: %s", update.effective_chat.id, exc)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error: %s", context.error)


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal endpoint so Render's Web Service type sees an open port."""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"MONARQ bot is running")

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass  # keep Render's log stream readable


def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server listening on port %s", port)


def main() -> None:
    start_health_server()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", reply_to_user))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, reply_to_user))
    app.add_error_handler(on_error)

    app.job_queue.run_repeating(
        rotate_post,
        interval=INTERVAL_HOURS * 3600,
        first=5,
        name="rotate_post",
    )

    log.info("Bot started — posting to %s every %s hours.", CHANNEL_ID, INTERVAL_HOURS)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
