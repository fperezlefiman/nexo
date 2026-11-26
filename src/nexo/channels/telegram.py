"""Telegram long polling. One poller per bot token, offsets in Postgres so a restart loses nothing.

A chat id maps to exactly one profile (the allowlist). Several chats may share a bot; several
bots may live in the same process, one thread each (ADR 0018).
"""

import hashlib
import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

from ..adapters.opencode import OpenCodeRuntime
from ..db import connect, kv_get, kv_set
from ..dispatch import idempotency_key
from ..store import (
    conversation_status,
    enqueue_turn,
    get_channel_by_external,
    get_or_create_conversation,
    last_paused_turn,
    request_stop,
    reset_engine_session,
    set_model_override,
)

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4096
DEFAULT_TOKEN_ENV = "NEXO_TELEGRAM_TOKEN"


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Split on paragraph boundaries; never inside a code block if it can be avoided."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(block) > limit:
            parts.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        parts.append(current)
    return parts


def token_for(channel: dict) -> str | None:
    """A channel may name its own bot; without that it falls back to the shared one."""
    env = (channel.get("settings") or {}).get("token_env") or DEFAULT_TOKEN_ENV
    return os.environ.get(env) or os.environ.get(DEFAULT_TOKEN_ENV) or None


def _offset_key(token: str) -> str:
    return f"telegram.offset.{hashlib.sha256(token.encode()).hexdigest()[:12]}"


def _with_attachment(bot: "TelegramBot", message: dict, attachment: dict, caption: str) -> str | None:
    """Describe the attachment in the turn's text: the model gets a path, not a binary."""
    dest_dir = os.environ.get("NEXO_MEDIA_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "nexo", "media")
    path = bot.download(attachment["file_id"], dest_dir)
    if not path:
        return f"{caption}\n\n[adjunto no descargado: {attachment['kind']} {attachment['name']}]".strip()
    kb = max(1, attachment["size"] // 1024)
    hint = ("usá vision_analyze con esa ruta" if attachment["mime"].startswith("image/")
            else "leelo con las tools que tengas")
    note = f"[adjunto: {path} · {attachment['mime']} · {kb} KB · {hint}]"
    return f"{caption}\n\n{note}".strip()


def attachment_of(message: dict) -> dict | None:
    """The first attachment in a message, normalised. Photos arrive as a list of sizes."""
    for key in ("document", "photo", "video", "audio", "voice", "animation", "sticker"):
        value = message.get(key)
        if not value:
            continue
        if key == "photo":
            value = value[-1]  # the largest size
        return {
            "kind": key,
            "file_id": value.get("file_id"),
            "name": value.get("file_name") or f"{key}-{value.get('file_id', '')[:8]}",
            "mime": value.get("mime_type") or "application/octet-stream",
            "size": value.get("file_size") or 0,
        }
    return None


class TelegramBot:
    """One bot token: the HTTP side, no polling state."""

    def __init__(self, token: str, poll_timeout: int = 30):
        self.token = token
        self.poll_timeout = poll_timeout

    def _call(self, method: str, **params):
        url = API.format(token=self.token, method=method)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        # Long polling returns at `timeout`; give the socket room so it is not a URLError.
        try:
            with urllib.request.urlopen(url, timeout=self.poll_timeout + 45) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # The body carries the only useful part (error_code + description: 429 retry_after,
            # 403 bot was kicked, 400 chat not found). Dropping it left "HTTPError" in the logs
            # and cost an hour of guessing on 2026-11-25.
            body = exc.read().decode("utf-8", "ignore")[:300]
            raise RuntimeError(f"{method} {exc.code}: {body}") from None

    def send(self, chat_id: str, text: str, silent: bool = True,
             parse_mode: str | None = None) -> int | None:
        """Silent by default: this bot is an operations channel, not a chat that should buzz.

        The operator asked for it on 2026-11-25 (the notifications were the noise). Passing
        `silent=False` is the exception, not the rule. `parse_mode` ("Markdown") is opt-in:
        plain text must stay plain, since unmatched markers in arbitrary output would break it.
        """
        message_id = None
        for chunk in split_message(text):
            try:
                payload = self._call("sendMessage", chat_id=chat_id, text=chunk,
                                     disable_web_page_preview="true",
                                     disable_notification="true" if silent else "false",
                                     **({"parse_mode": parse_mode} if parse_mode else {}))
                message_id = ((payload.get("result") or {}).get("message_id")) or message_id
            except Exception as exc:  # a failed send must not kill the poller
                print(f"send failed: {type(exc).__name__}: {exc}", flush=True)
        return message_id

    def edit(self, chat_id: str, message_id: int, text: str) -> None:
        try:
            self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                       disable_web_page_preview="true")
        except Exception:
            pass  # "message is not modified" and rate limits are not worth a retry here

    def download(self, file_id: str, dest_dir: str) -> str | None:
        """Fetch an attachment the user sent. Returns the local path, or None if it failed."""
        try:
            info = self._call("getFile", file_id=file_id).get("result") or {}
            remote = info.get("file_path")
            if not remote:
                return None
            os.makedirs(dest_dir, exist_ok=True)
            name = os.path.basename(remote)
            dest = os.path.join(dest_dir, f"{file_id[:12]}-{name}")
            url = f"https://api.telegram.org/file/bot{self.token}/{remote}"
            urllib.request.urlretrieve(url, dest)
            return dest
        except Exception as exc:
            print(f"download failed: {type(exc).__name__}: {exc}", flush=True)
            return None

    def delete(self, chat_id: str, message_id: int) -> bool:
        """True when Telegram confirmed. No retries: the caller decides the fallback."""
        try:
            self._call("deleteMessage", chat_id=chat_id, message_id=message_id)
            return True
        except Exception as exc:
            print(f"delete failed: {exc}", flush=True)
            return False

    def typing(self, chat_id: str) -> None:
        try:
            self._call("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:
            pass


class TelegramChannel:
    """Poller: one thread per distinct bot token found in the channels table."""

    def __init__(self, runtime: OpenCodeRuntime, poll_timeout: int = 30):
        self.runtime = runtime
        self.poll_timeout = poll_timeout

    def run(self) -> None:
        tokens = self._tokens()
        if not tokens:
            raise SystemExit(f"no telegram token: set {DEFAULT_TOKEN_ENV} or channels.settings.token_env")
        for token in tokens:
            threading.Thread(target=self._loop, args=(token,), daemon=True).start()
        print(f"telegram channel up ({len(tokens)} bot)", flush=True)
        while True:  # keep the process alive; the pollers are daemons
            time.sleep(3600)

    def _tokens(self) -> list[str]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT settings FROM channels WHERE kind = 'telegram' AND enabled")
            rows = cur.fetchall()
        found = []
        for row in rows:
            token = token_for(row)
            if token and token not in found:
                found.append(token)
        if not found:  # nothing configured yet: still answer on the shared bot
            shared = os.environ.get(DEFAULT_TOKEN_ENV)
            return [shared] if shared else []
        return found

    def _loop(self, token: str) -> None:
        bot = TelegramBot(token, self.poll_timeout)
        while True:
            offset = self._offset(token)
            try:
                payload = bot._call("getUpdates", offset=offset, timeout=self.poll_timeout,
                                    allowed_updates='["message"]')
            except Exception:
                # Type name alone once hid a real bug (a TypeError in the request path):
                # a poller that fails must say why, not just that it failed.
                traceback.print_exc()
                time.sleep(1)
                continue
            for update in payload.get("result", []):
                self._handle_update(bot, update)
                self._set_offset(token, update["update_id"] + 1)

    def _handle_update(self, bot: TelegramBot, update: dict) -> None:
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id") or "")
        text = (message.get("text") or "").strip()
        attachment = attachment_of(message)
        if not chat_id or (not text and not attachment):
            return
        channel = get_channel_by_external(chat_id)
        if not channel:  # allowlist: unknown chats are dropped before dispatch
            print(f"ignored chat {chat_id}", flush=True)
            return

        conv = get_or_create_conversation(channel["profile_id"], channel["id"], title=chat_id)
        if text.startswith("/"):
            self._command(bot, channel, conv, chat_id, text)
            return

        if attachment:
            text = _with_attachment(bot, message, attachment, text) or text
        bot.typing(chat_id)
        # Placeholder first: the turn is born knowing which message to delete. Enqueueing first let a
        # worker finish before set_placeholder ran, leaving the ⏳ orphaned (2026-11-25).
        placeholder = bot.send(chat_id, "⏳ 0 min", silent=True)
        turn = enqueue_turn(conv["id"], text, idempotency_key("telegram", chat_id, str(update["update_id"])),
                            placeholder_message_id=placeholder)
        if turn is None:  # duplicate update
            if placeholder:
                bot.delete(chat_id, placeholder)
            return

    def _command(self, bot: TelegramBot, channel: dict, conv: dict, chat_id: str, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.lower()

        if cmd == "/stop":
            stopped = request_stop(conv["id"])
            session = self._session_for(channel)
            if session:
                try:
                    self.runtime.interrupt(session)
                except Exception as exc:
                    print(f"interrupt failed: {type(exc).__name__}", flush=True)
            bot.send(chat_id, "⏸ pausado" if stopped else "no hay turno en curso")
            return

        if cmd == "/resume":
            paused = last_paused_turn(conv["id"])
            if not paused:
                bot.send(chat_id, "no hay ningún turno pausado")
                return
            turn = enqueue_turn(conv["id"], "Continuá desde donde quedaste.", f"resume:{paused['id']}",
                                kind="resume", partial=paused.get("partial_text"))
            if turn:
                bot.send(chat_id, "▶️ retomando")
            return

        if cmd == "/new":
            reset_engine_session(conv["id"])
            bot.send(chat_id, "🧹 sesión nueva: el próximo turno arranca sin el contexto acumulado "
                               "del motor. El historial queda intacto.")
            return

        if cmd in ("/model", "/models"):
            entry = arg.strip()
            if entry == "clear":
                set_model_override(conv["id"], None)
                reset_engine_session(conv["id"])  # the session is bound to the old model
                entry = ""
            if entry:
                set_model_override(conv["id"], entry)
                reset_engine_session(conv["id"])
                bot.send(chat_id, f"modelo: {entry}")
                return
            bot.send(chat_id, f"modelo: {conv.get('model_override') or channel['default_model']}")
            return

        if cmd == "/status":
            status = conversation_status(conv["id"])
            bot.send(
                chat_id,
                f"turnos: {status.get('turns', 0)} · pausados: {status.get('paused', 0)}\n"
                f"modelo: {status.get('model_override') or channel['default_model']}\n"
                f"sesión motor: {status.get('engine_session_id') or '—'}",
            )
            return

        bot.send(chat_id, "commands: /stop /resume /new /model [provider/modelo|clear] /status")

    def _session_for(self, channel: dict) -> str | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT engine_session_id FROM conversations WHERE channel_id = %s ORDER BY created_at LIMIT 1",
                (channel["id"],),
            )
            row = cur.fetchone()
        return (row or {}).get("engine_session_id")

    def _offset(self, token: str) -> int:
        with connect() as conn:
            return int(kv_get(conn, _offset_key(token), "0"))

    def _set_offset(self, token: str, value: int) -> None:
        with connect() as conn:
            kv_set(conn, _offset_key(token), value)
