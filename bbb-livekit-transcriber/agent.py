"""BigBlueButton LiveKit Transcription Agent.

Joins LiveKit rooms as a server-side participant, subscribes to audio tracks,
performs speech-to-text using faster-whisper, and publishes transcription results
to BBB's existing caption pipeline via Redis.

Usage:
    python agent.py start
"""

import asyncio
import io
import logging
import os
import wave

import aiohttp
import numpy as np

from livekit import agents, rtc
from livekit.agents import AutoSubscribe, JobContext, AgentServer
from livekit.plugins import silero

from bbb_redis import BBBRedisPublisher
from config import load_config
from locale_tracker import LocaleTracker, bcp47_to_iso639
from transcript_state import TranscriptStateManager

logger = logging.getLogger("bbb-livekit-transcriber")
logging.basicConfig(level=logging.INFO)

# Global config loaded at startup
_config: dict = {}
_whisper_model = None


def get_whisper_model(stt_cfg: dict):
    """Lazy-load the faster-whisper model."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logger.info(
            "Loading Whisper model=%s device=%s compute_type=%s",
            stt_cfg["whisper_model"], stt_cfg["device"], stt_cfg["compute_type"],
        )
        _whisper_model = WhisperModel(
            stt_cfg["whisper_model"],
            device=stt_cfg["device"],
            compute_type=stt_cfg["compute_type"],
        )
    return _whisper_model


def audio_frames_to_ndarray(frames: list[rtc.AudioFrame], target_sample_rate: int = 16000) -> np.ndarray:
    """Convert a list of LiveKit AudioFrames to a float32 numpy array.

    Resamples to target_sample_rate if needed (faster-whisper expects 16kHz mono).
    """
    if not frames:
        return np.array([], dtype=np.float32)

    # Combine all frames into a single int16 array
    all_samples = []
    for frame in frames:
        samples = np.frombuffer(frame.data, dtype=np.int16)
        # If stereo, convert to mono by averaging channels
        if frame.num_channels > 1:
            samples = samples.reshape(-1, frame.num_channels).mean(axis=1).astype(np.int16)
        all_samples.append(samples)
    combined = np.concatenate(all_samples)

    # Resample if source sample rate differs
    source_rate = frames[0].sample_rate
    if source_rate != target_sample_rate:
        # Simple linear interpolation resampling
        duration = len(combined) / source_rate
        target_len = int(duration * target_sample_rate)
        indices = np.linspace(0, len(combined) - 1, target_len)
        combined = np.interp(indices, np.arange(len(combined)), combined.astype(np.float64)).astype(np.int16)

    # Convert to float32 in [-1, 1] range
    return combined.astype(np.float32) / 32768.0


def audio_frames_to_wav_bytes(frames: list[rtc.AudioFrame], sample_rate: int = 16000) -> bytes:
    """Convert LiveKit AudioFrames to a WAV file in memory.

    The OpenAI-compatible /v1/audio/transcriptions endpoint accepts WAV files.
    """
    pcm = audio_frames_to_ndarray(frames, target_sample_rate=sample_rate)
    pcm_int16 = (pcm * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16 = 2 bytes per sample
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int16.tobytes())
    return buf.getvalue()


async def transcribe_via_api(
    wav_bytes: bytes,
    lang_code: str,
    api_cfg: dict,
    session: aiohttp.ClientSession,
) -> str:
    """Transcribe audio using an OpenAI-compatible /v1/audio/transcriptions endpoint.

    Returns the transcript text. Language is provided by the caller from the
    user's BBB speech locale setting (via LocaleTracker).
    Compatible with speaches, openai, and any OpenAI-compatible STT API.
    """
    base_url = api_cfg["base_url"].rstrip("/")
    url = f"{base_url}/v1/audio/transcriptions"

    form = aiohttp.FormData()
    form.add_field("file", wav_bytes, filename="audio.wav", content_type="audio/wav")
    form.add_field("model", api_cfg["model"])
    form.add_field("response_format", "json")
    if lang_code:
        form.add_field("language", lang_code)

    headers = {"Authorization": f"Bearer {api_cfg['api_key']}"}

    async with session.post(url, data=form, headers=headers) as resp:
        resp.raise_for_status()
        result = await resp.json(content_type=None)

    return result.get("text", "").strip()


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    """Agent entrypoint - called when dispatched to a LiveKit room."""
    # Load config in subprocess (LiveKit agents run jobs in separate processes)
    config = load_config()
    redis_cfg = config["redis"]
    stt_cfg = config["stt"]
    use_api = stt_cfg["provider"] == "openai-compatible"
    meeting_id = ctx.room.name

    redis_pub = BBBRedisPublisher(host=redis_cfg["host"], port=redis_cfg["port"])
    state_mgr = TranscriptStateManager()
    vad = silero.VAD.load()

    # Track each user's speech locale from BBB's UserSpeechLocaleChangedEvtMsg events
    locale_tracker = LocaleTracker(
        meeting_id=meeting_id,
        default_locale=stt_cfg.get("default_locale", "en-US"),
    )
    await locale_tracker.start(redis_cfg["host"], redis_cfg["port"])

    # Shared aiohttp session for API provider (None when using local faster-whisper)
    http_session = aiohttp.ClientSession() if use_api else None

    logger.info("Agent joining room %s (meeting_id=%s)", ctx.room.name, meeting_id)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Track active transcription tasks per participant
    active_tasks: dict[str, asyncio.Task] = {}

    async def transcribe_track(
        track: rtc.Track,
        participant: rtc.RemoteParticipant,
    ):
        """Subscribe to an audio track and transcribe speech segments."""
        user_id = participant.identity
        logger.info("Starting transcription for participant %s (user_id=%s)", participant.name, user_id)

        audio_stream = rtc.AudioStream(track)
        vad_stream = vad.stream()

        # Process audio frames through VAD in background
        async def feed_audio():
            async for frame_event in audio_stream:
                vad_stream.push_frame(frame_event.frame)

        feed_task = asyncio.create_task(feed_audio())

        try:
            async for vad_event in vad_stream:
                if vad_event.type == agents.vad.VADEventType.END_OF_SPEECH:
                    # vad_event.frames contains the audio frames for the speech segment
                    speech_frames = getattr(vad_event, "frames", None) or []
                    if not speech_frames:
                        continue

                    locale = locale_tracker.get_locale(user_id)
                    lang_code = bcp47_to_iso639(locale)

                    if use_api:
                        wav = audio_frames_to_wav_bytes(speech_frames)
                        if len(wav) < 100:
                            continue
                        transcript = await transcribe_via_api(
                            wav, lang_code, stt_cfg["api"], http_session,
                        )
                    else:
                        # Local faster-whisper path
                        audio_data = audio_frames_to_ndarray(speech_frames)
                        if len(audio_data) < 1600:  # Less than 0.1s at 16kHz
                            continue

                        def _transcribe(audio):
                            seg_gen, _ = get_whisper_model(stt_cfg).transcribe(
                                audio,
                                language=lang_code or None,
                                beam_size=5,
                                vad_filter=False,  # We already did VAD
                            )
                            parts = [s.text.strip() for s in seg_gen if s.text.strip()]
                            return " ".join(parts)

                        loop = asyncio.get_event_loop()
                        transcript = await loop.run_in_executor(
                            None, _transcribe, audio_data,
                        )

                    if not transcript:
                        continue

                    # Get participant state and generate transcript update
                    state = state_mgr.get_or_create(user_id)
                    state.new_utterance()
                    start, end, text = state.finalize(transcript)

                    logger.info(
                        "Transcription [%s/%s] lang=%s: %s",
                        meeting_id, user_id, locale, transcript,
                    )

                    await redis_pub.publish_transcript_update(
                        meeting_id=meeting_id,
                        user_id=user_id,
                        transcript_id=state.transcript_id,
                        start=start,
                        end=end,
                        text=text,
                        transcript=transcript,
                        locale=locale,
                        is_final=True,
                    )
        except asyncio.CancelledError:
            logger.info("Transcription cancelled for %s", user_id)
        except Exception:
            logger.exception("Error transcribing for %s", user_id)
            await redis_pub.publish_transcription_error(
                meeting_id=meeting_id,
                user_id=user_id,
                error_code="STT_ERROR",
                error_message="Speech-to-text transcription failed",
            )
        finally:
            feed_task.cancel()
            await vad_stream.aclose()
            await audio_stream.aclose()
            state_mgr.remove(user_id)
            logger.info("Stopped transcription for %s", user_id)

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if participant.identity in active_tasks:
            return

        task = asyncio.create_task(transcribe_track(track, participant))
        active_tasks[participant.identity] = task

    @ctx.room.on("track_unsubscribed")
    def on_track_unsubscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        task = active_tasks.pop(participant.identity, None)
        if task and not task.done():
            task.cancel()

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        task = active_tasks.pop(participant.identity, None)
        if task and not task.done():
            task.cancel()

    # Handle participants that are already in the room when the agent connects
    for participant in ctx.room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.track and publication.kind == rtc.TrackKind.KIND_AUDIO:
                task = asyncio.create_task(
                    transcribe_track(publication.track, participant)
                )
                active_tasks[participant.identity] = task

    # Wait until the job is done, then clean up the shared HTTP session
    try:
        await ctx.wait_for_disconnection()
    finally:
        if http_session is not None:
            await http_session.close()



if __name__ == "__main__":
    _config = load_config()

    # Set environment variables for the LiveKit agents framework.
    # The framework reads LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
    # to connect to the LiveKit server. We use setdefault so explicit env vars
    # take precedence over config file values.
    os.environ.setdefault("LIVEKIT_URL", _config["livekit"]["url"])
    os.environ.setdefault("LIVEKIT_API_KEY", _config["livekit"]["api_key"])
    os.environ.setdefault("LIVEKIT_API_SECRET", _config["livekit"]["api_secret"])

    agents.cli.run_app(server)
