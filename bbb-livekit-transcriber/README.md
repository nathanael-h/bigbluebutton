# BigBlueButton LiveKit Transcription Agent

Server-side automatic transcription service for BigBlueButton using LiveKit agents.

## Overview

This agent provides real-time speech-to-text transcription for BigBlueButton meetings by:

1. Joining LiveKit rooms as a server-side participant
2. Subscribing to audio tracks from meeting participants
3. Using Voice Activity Detection (VAD) to detect speech segments
4. Transcribing speech via **local faster-whisper** or an **external OpenAI-compatible STT API**
5. Using each participant's **BBB speech locale** (set in the BBB UI) as the transcription language
6. Publishing transcripts to BBB's caption system via Redis

Unlike client-side transcription, this runs entirely on the server and works with any BBB client, including phone dial-in users.

## STT Providers

### Option A: Local faster-whisper

Runs the Whisper model directly on the same machine. Good for CPU or single-GPU setups.

### Option B: External OpenAI-compatible API (recommended for GPU servers)

Connects to any OpenAI-compatible `/v1/audio/transcriptions` endpoint. Works with:

- **[speaches](https://github.com/speaches-ai/speaches)** — self-hosted faster-whisper server with a GPU
- **OpenAI Whisper API** — managed cloud service
- Any other compatible STT service

## Prerequisites

- Python 3.10+
- BigBlueButton instance with LiveKit integration enabled (`audioBridge=livekit`)
- Redis server (typically already running with BBB)

## Installation

```bash
cd bbb-livekit-transcriber
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

For local faster-whisper only, `faster-whisper` is already in `requirements.txt`. For the external API provider, `aiohttp` handles the HTTP calls and is also included.

## Configuration

### YAML config file (recommended)

Create `/etc/bigbluebutton/bbb-livekit-transcriber.yml`:

```yaml
redis:
  host: 127.0.0.1
  port: 6379

livekit:
  url: ws://localhost:7880
  # api_key and api_secret are auto-loaded from /etc/bigbluebutton/livekit.yaml
  # Uncomment to override:
  # api_key: your_api_key
  # api_secret: your_api_secret

stt:
  # "faster-whisper" (local) or "openai-compatible" (external HTTP API)
  provider: faster-whisper

  # Fallback locale used before a user sets their speech language in BBB.
  # Language is always taken from the user's BBB speech locale setting.
  default_locale: en-US

  # --- Local faster-whisper settings ---
  whisper_model: base   # tiny, base, small, medium, large-v3
  device: cpu           # cpu or cuda
  compute_type: int8    # int8, float16, float32

  # --- External API settings (provider: openai-compatible) ---
  api:
    base_url: http://localhost:8000   # speaches default port
    api_key: cant-be-empty            # speaches doesn't require a real key
    model: Systran/faster-whisper-base
```

### Environment variables

All settings can be provided via environment variables instead:

| Variable | Description |
| -------- | ----------- |
| `LIVEKIT_URL` | LiveKit server WebSocket URL |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |
| `REDIS_HOST` | Redis host |
| `REDIS_PORT` | Redis port |
| `STT_PROVIDER` | `faster-whisper` or `openai-compatible` |
| `STT_DEFAULT_LOCALE` | Default BCP-47 locale (e.g. `en-US`) |
| `WHISPER_MODEL` | Model size for local faster-whisper |
| `WHISPER_DEVICE` | `cpu` or `cuda` |
| `WHISPER_COMPUTE_TYPE` | `int8`, `float16`, or `float32` |
| `STT_API_BASE_URL` | Base URL for external STT API |
| `STT_API_KEY` | API key for external STT service |
| `STT_API_MODEL` | Model name for external STT service |

LiveKit credentials are auto-read from `/etc/bigbluebutton/livekit.yaml` if not explicitly set.

### Model selection (local faster-whisper)

| Model | Speed | Accuracy | RAM | Best for |
| ----- | ----- | -------- | --- | -------- |
| `tiny` | Fastest | Low | ~1 GB | Testing, low-resource |
| `base` | Fast | Good | ~1 GB | Balanced CPU use |
| `small` | Medium | Better | ~2 GB | Production on CPU |
| `medium` | Slow | Very good | ~5 GB | Production with GPU |
| `large-v3` | Slowest | Best | ~10 GB | High accuracy, GPU required |

## Deploying speaches (external GPU STT server)

[speaches](https://github.com/speaches-ai/speaches) runs faster-whisper behind an OpenAI-compatible HTTP API. Recommended when you have a separate GPU server.

### Quick start with Docker

```bash
docker run -d \
  --name speaches \
  --gpus all \
  -p 8000:8000 \
  ghcr.io/speaches-ai/speaches:latest-cuda
```

For CPU-only:

```bash
docker run -d \
  --name speaches \
  -p 8000:8000 \
  ghcr.io/speaches-ai/speaches:latest
```

### Configure the agent to use speaches

```yaml
stt:
  provider: openai-compatible
  default_locale: en-US
  api:
    base_url: http://<speaches-server-ip>:8000
    api_key: cant-be-empty
    model: Systran/faster-whisper-base   # or large-v3 for better accuracy
```

speaches downloads the model on first use. Available models are listed at the speaches documentation.

## Language Handling

The agent uses each participant's **BBB speech locale** setting as the transcription language. This is the same locale users set in the BBB captions UI when enabling captions.

- The agent subscribes to `UserSpeechLocaleChangedEvtMsg` events on Redis and tracks each user's locale in real time.
- Language is passed to the STT provider as an ISO 639-1 code (e.g. `en`, `fr`, `pt`).
- Before a user sets their locale, the `default_locale` from config is used.

> **Note**: Users must set their speech locale in the BBB captions UI for accurate transcription. If no locale is set, the agent falls back to `default_locale`.

## Running the Agent

### Development (with auto-reload)

```bash
source venv/bin/activate
python agent.py dev
```

### Production

```bash
python agent.py start
```

### As a systemd service

Create `/etc/systemd/system/bbb-livekit-transcriber.service`:

```ini
[Unit]
Description=BigBlueButton LiveKit Transcription Agent
After=network.target redis.service

[Service]
Type=simple
User=bigbluebutton
WorkingDirectory=/opt/bbb-livekit-transcriber
Environment=PATH=/opt/bbb-livekit-transcriber/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/bbb-livekit-transcriber/venv/bin/python agent.py start
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bbb-livekit-transcriber
sudo systemctl status bbb-livekit-transcriber
```

## Testing

1. Start the agent: `python agent.py dev`
2. Create a BBB meeting with LiveKit audio enabled
3. Open the captions panel in BBB and set your speech locale
4. Speak — transcripts should appear in the caption panel

Monitor Redis messages while speaking:

```bash
redis-cli SUBSCRIBE to-akka-apps-redis-channel
```

Check agent logs for transcription output:

```text
INFO:bbb-livekit-transcriber:Agent joining room <meeting_id>
INFO:bbb-livekit-transcriber:Starting transcription for participant <user_id>
INFO:bbb-livekit-transcriber:Transcription [<meeting_id>/<user_id>] lang=en-US: Hello world
```

## Troubleshooting

### `LiveKit config not found`

```bash
cat /etc/bigbluebutton/livekit.yaml
bbb-conf --check
```

### `ModuleNotFoundError`

```bash
source venv/bin/activate
pip install -r requirements.txt
```

### No transcriptions appearing

- Check that the agent registered: logs should show `registered worker`
- Check that the agent received a job: logs should show `Agent joining room ...`
- Check Redis: `redis-cli ping` should return `PONG`
- Check that the user's speech locale is set in the BBB UI

### Poor transcription quality

- Upgrade to a larger model (base → small → medium → large-v3)
- Ensure users have set their speech locale in the BBB UI
- For the external API, try a larger model on speaches

### High CPU usage (local faster-whisper)

- Use a smaller model
- Switch to `int8` compute type
- Consider using speaches on a separate GPU server instead

## Architecture

```text
BBB Client (Browser)
     │ WebRTC audio
     ▼
LiveKit Server (:7880)
     │
     │ room dispatch
     ▼
bbb-livekit-transcriber (this agent)
     │
     ├── VAD (Silero) → detects speech segments
     ├── STT:
     │     • faster-whisper (local)  OR
     │     • POST /v1/audio/transcriptions (speaches/OpenAI)
     │
     ├── LocaleTracker ←── Redis SUB from-akka-apps-redis-channel
     │                     (UserSpeechLocaleChangedEvtMsg)
     │
     └── PUBLISH to-akka-apps-redis-channel
           UpdateTranscriptPubMsg
                │
                ▼
         akka-bbb-apps (existing, unchanged)
           → CaptionDAO → PostgreSQL
           → TranscriptUpdatedEvtMsg
                │
                ▼
         BBB Client captions UI
```

## Project Structure

```text
bbb-livekit-transcriber/
├── agent.py              # Main agent: VAD, STT, transcript publishing
├── config.py             # Configuration loader (YAML + env vars)
├── bbb_redis.py          # Redis publisher for BBB caption messages
├── transcript_state.py   # Per-user transcription state and diff tracking
├── locale_tracker.py     # Per-user speech locale from BBB Redis events
├── requirements.txt      # Python dependencies
└── README.md             # This file
```

## License

Part of the BigBlueButton open source project.
