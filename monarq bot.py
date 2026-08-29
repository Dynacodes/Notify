"""
MONARQ channel bot
------------------
- Posts the store advert to a channel every N hours (default 5).
- Deletes the previous advert so only one stays pinned to the top of the feed.
- Replies with the same advert to /start and to any message sent to the bot.

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
from telegram.error import BadRequest, TelegramError
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
CHANNEL_ID = os.environ["CHANNEL_ID"]           # "@yourchannel" or -100xxxxxxxxxx
INTERVAL_HOURS = float(os.getenv("INTERVAL_HOURS", "5"))
STATE_FILE = Path(os.getenv("STATE_FILE", "last_message.json"))

SHOP_URL = "https://themonarq.shop"
SUPPORT = "@monarqteam"

# --------------------------------------------------------------------------
# Premium (custom) emoji IDs.
# Replace the placeholder digits with real custom_emoji_id values from your
# own emoji pack. Leave a value as "" to fall back to the plain emoji.
# NOTE: only bots whose owner bought a username on Fragment may send these.
# --------------------------------------------------------------------------
EMOJI = {
    "crown":   ("5368324170671202286", "\U0001F451"),  # 👑
    "cart":    ("5372981976804366741", "\U0001F6D2"),  # 🛒
    "bullet":  ("5368324170671202286", "\u2022"),      # •
    "bolt":    ("5375464961822695044", "\u26A1"),      # ⚡
    "warning": ("5372981976804366741", "\u26A0"),      # ⚠
    "link":    ("5375464961822695044", "\U0001F517"),  # 🔗
}


def e(key: str, premium: bool) -> str:
    """Return a premium emoji entity, or the plain unicode emoji as fallback."""
    emoji_id, plain = EMOJI[key]
    if premium and emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{plain}</tg-emoji>'
    return plain


def build_ad(premium: bool = True) -> str:
    b = e("bullet", premium)
    return (
        f"{e('crown', premium)} <b>MONARQ — PREMIUM DIGITAL STORE</b>\n\n"
        f"{e('cart', premium)} Browse our full catalogue of premium digital products at "
        f"<a href=\"{SHOP_URL}\">themonarq.shop</a>:\n\n"
        f"{b} RDP &amp; VPS\n"
        f"{b} VPN &amp; Proxies\n"
        f"{b} Webmail &amp; SMTP\n"
        f"{b} Verified accounts\n"
        f"{b} and much more\n\n"
        f"{e('bolt', premium)} <b>Instant delivery.</b> Every order is delivered to you on "
        f"Telegram the moment payment is confirmed — no waiting, no manual processing.\n\n"
        f"{e('warning', premium)} <b>Beware of impersonators.</b> Our only official Telegram "
        f"account is {SUPPORT}. We never contact customers first from any other account, and "
        f"we accept payment through no other channel. Verify before you pay.\n\n"
        f"{e('link', premium)} <b>Store:</b> {SHOP_URL}\n"
        f"{e('link', premium)} <b>Official contact:</b> {SUPPORT}"
    )


KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("\U0001F6D2  Visit Store", url=SHOP_URL)],
        [InlineKeyboardButton("\U0001F4AC  Official Support", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
    ]
)


async def send_ad(bot, chat_id, reply_to: int | None = None):
    """Send the advert; retry without premium emoji if the bot may not use them."""
    kwargs = dict(
        chat_id=chat_id,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=KEYBOARD,
    )
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to
    try:
        return await bot.send_message(text=build_ad(premium=True), **kwargs)
    except BadRequest as exc:
        if "emoji" not in str(exc).lower():
            raise
        log.warning("Premium emoji rejected (%s) — sending plain version.", exc)
        return await bot.send_message(text=build_ad(premium=False), **kwargs)


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
    """Answer /start and any incoming message with the advert."""
    if not update.effective_message:
        return
    await send_ad(
        context.bot,
        update.effective_chat.id,
        reply_to=update.effective_message.message_id,
    )


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

    app.job_queue.run_repeating(
        rotate_post,
        interval=INTERVAL_HOURS * 3600,
        first=5,
        name="rotate_post",
    )

    log.info("Bot started — posting to %s every %s hours.", CHANNEL_ID, INTERVAL_HOURS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
