"""Redis message publisher for BBB caption messages.

Publishes UpdateTranscriptPubMsg and TranscriptionProviderErrorMsg
to the to-akka-apps-redis-channel, matching the exact JSON format
expected by ReceivedJsonMsgHandlerActor in akka-bbb-apps.
"""

import json
import time
import logging
import redis.asyncio as redis

logger = logging.getLogger(__name__)

CHANNEL = "to-akka-apps-redis-channel"


class BBBRedisPublisher:
    def __init__(self, host: str = "127.0.0.1", port: int = 6379):
        self._client = redis.Redis(host=host, port=port, decode_responses=True)

    async def close(self):
        await self._client.aclose()

    async def _publish(self, payload: dict):
        message = json.dumps(payload)
        await self._client.publish(CHANNEL, message)
        logger.debug("Published %s to %s", payload["envelope"]["name"], CHANNEL)

    async def publish_transcript_update(
        self,
        meeting_id: str,
        user_id: str,
        transcript_id: str,
        start: int,
        end: int,
        text: str,
        transcript: str,
        locale: str,
        is_final: bool,
    ):
        """Publish an UpdateTranscriptPubMsg to akka-bbb-apps.

        This matches the message format defined in AudioCaptionsMsgs.scala
        and the envelope structure used by bbb-graphql-actions/src/index.ts.
        """
        event_name = "UpdateTranscriptPubMsg"
        payload = {
            "envelope": {
                "name": event_name,
                "routing": {
                    "meetingId": meeting_id,
                    "userId": user_id,
                },
                "timestamp": int(time.time() * 1000),
            },
            "core": {
                "header": {
                    "name": event_name,
                    "meetingId": meeting_id,
                    "userId": user_id,
                },
                "body": {
                    "transcriptId": transcript_id,
                    "start": start,
                    "end": end,
                    "text": text,
                    "transcript": transcript,
                    "locale": locale,
                    "result": is_final,
                },
            },
        }
        await self._publish(payload)

    async def publish_transcription_error(
        self,
        meeting_id: str,
        user_id: str,
        error_code: str,
        error_message: str,
    ):
        """Publish a TranscriptionProviderErrorMsg to akka-bbb-apps."""
        event_name = "TranscriptionProviderErrorMsg"
        payload = {
            "envelope": {
                "name": event_name,
                "routing": {
                    "meetingId": meeting_id,
                    "userId": user_id,
                },
                "timestamp": int(time.time() * 1000),
            },
            "core": {
                "header": {
                    "name": event_name,
                    "meetingId": meeting_id,
                    "userId": user_id,
                },
                "body": {
                    "errorCode": error_code,
                    "errorMessage": error_message,
                },
            },
        }
        await self._publish(payload)
