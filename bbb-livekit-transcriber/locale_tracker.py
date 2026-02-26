"""Tracks per-user speech locale by subscribing to BBB Redis events.

Listens on from-akka-apps-redis-channel for UserSpeechLocaleChangedEvtMsg,
which akka-bbb-apps broadcasts whenever a user sets their caption language
in the BBB UI (via the SET_SPEECH_LOCALE GraphQL mutation).
"""

import asyncio
import json
import logging
import re
import redis.asyncio as redis

logger = logging.getLogger(__name__)

FROM_AKKA_CHANNEL = "from-akka-apps-redis-channel"
LOCALE_CHANGED_EVENT = "UserSpeechLocaleChangedEvtMsg"
MEETING_ENDED_EVENT = "MeetingEndedEvtMsg"

_LOCALE_RE = re.compile(r'^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{2,8})*$')
_ISO639_RE = re.compile(r'^[a-z]{2,8}$')
_RECONNECT_DELAY = 5  # seconds between Redis reconnect attempts


class LocaleTracker:
    """Maintains a per-user locale map for one meeting, updated in real time."""

    def __init__(self, meeting_id: str, default_locale: str = "en-US"):
        self._meeting_id = meeting_id
        self._default_locale = default_locale
        self._locales: dict[str, str] = {}  # userId -> BCP-47 locale e.g. "en-US"
        self._task: asyncio.Task | None = None
        self._redis: redis.Redis | None = None
        self._meeting_ended = asyncio.Event()

    def get_locale(self, user_id: str) -> str:
        """Return the user's current locale, or the configured default."""
        return self._locales.get(user_id, self._default_locale)

    def set_locale(self, user_id: str, locale: str):
        self._locales[user_id] = locale
        logger.info("Locale set: meeting=%s user=%s locale=%s", self._meeting_id, user_id, locale)

    def remove(self, user_id: str):
        self._locales.pop(user_id, None)

    async def start(self, redis_host: str, redis_port: int):
        self._redis = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self._task = asyncio.create_task(self._subscribe())
        logger.info(
            "LocaleTracker started for meeting %s (default: %s)",
            self._meeting_id, self._default_locale,
        )

    async def wait_for_meeting_end(self):
        """Wait until a MeetingEndedEvtMsg is received for this meeting."""
        await self._meeting_ended.wait()

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        if self._redis:
            await self._redis.aclose()

    async def _subscribe(self):
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(FROM_AKKA_CHANNEL)
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    self._handle_message(message["data"])
                return  # listen() returned normally — connection closed cleanly
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "Redis subscription error for meeting %s, reconnecting in %ds: %s",
                    self._meeting_id, _RECONNECT_DELAY, exc,
                )
            finally:
                try:
                    await pubsub.unsubscribe(FROM_AKKA_CHANNEL)
                    await pubsub.aclose()
                except Exception:
                    pass

            try:
                await asyncio.sleep(_RECONNECT_DELAY)
            except asyncio.CancelledError:
                return

    def _handle_message(self, data: str):
        try:
            payload = json.loads(data)
            msg_name = payload.get("envelope", {}).get("name")

            if msg_name == LOCALE_CHANGED_EVENT:
                header = payload["core"]["header"]
                body = payload["core"]["body"]
                if header.get("meetingId") != self._meeting_id:
                    return
                user_id = header.get("userId", "")
                locale = body.get("locale", "")
                if user_id and locale:
                    if not _LOCALE_RE.match(locale):
                        logger.warning(
                            "Ignoring invalid locale %r for user %s in meeting %s",
                            locale, user_id, self._meeting_id,
                        )
                        return
                    self.set_locale(user_id, locale)

            elif msg_name == MEETING_ENDED_EVENT:
                body = payload["core"]["body"]
                if body.get("meetingId") == self._meeting_id:
                    logger.info("Meeting ended: %s", self._meeting_id)
                    self._meeting_ended.set()

        except (KeyError, json.JSONDecodeError, TypeError) as e:
            logger.debug("Ignoring malformed Redis message: %s", e)


def bcp47_to_iso639(locale: str) -> str:
    """Convert a BCP-47 locale (e.g. 'en-US') to ISO 639-1 code (e.g. 'en').

    Both faster-whisper and the OpenAI audio API accept ISO 639-1 language codes.
    """
    if not locale:
        return ""
    lang = locale.split("-")[0].lower()
    if not _ISO639_RE.match(lang):
        logger.warning(
            "Invalid locale %r produces invalid ISO 639-1 code %r, ignoring",
            locale, lang,
        )
        return ""
    return lang
