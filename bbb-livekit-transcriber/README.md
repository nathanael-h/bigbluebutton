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

- BigBlueButton instance with LiveKit integration enabled (`audioBridge=livekit`)
- Redis server (typically already running with BBB)
- **Systemd deployment:** Python 3.10+
- **Docker deployment:** Docker Engine and Docker Compose

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

## Production Deployment

### Option 1: systemd (recommended for bare-metal/VM BBB installs)

#### 1. Install the application

```bash
# Create the installation directory
sudo mkdir -p /opt/bbb-livekit-transcriber
sudo cp -r . /opt/bbb-livekit-transcriber/

# Create the virtualenv and install dependencies as root
sudo python3 -m venv /opt/bbb-livekit-transcriber/venv
sudo /opt/bbb-livekit-transcriber/venv/bin/pip install -r /opt/bbb-livekit-transcriber/requirements.txt

# Hand ownership of the directory to the bigbluebutton service account
sudo chown -R bigbluebutton:bigbluebutton /opt/bbb-livekit-transcriber
```

#### 2. Configure

Create `/etc/bigbluebutton/bbb-livekit-transcriber.yml` (see [Configuration](#configuration) above).

LiveKit API credentials are read automatically from `/etc/bigbluebutton/livekit.yaml` — no extra steps needed on a standard BBB install.

For environment-variable overrides (e.g. secrets not suitable for the YAML file), create `/etc/bigbluebutton/bbb-livekit-transcriber.env`:

```bash
# /etc/bigbluebutton/bbb-livekit-transcriber.env
# LIVEKIT_API_KEY=...
# LIVEKIT_API_SECRET=...
```

```bash
sudo chmod 640 /etc/bigbluebutton/bbb-livekit-transcriber.env
sudo chown root:bigbluebutton /etc/bigbluebutton/bbb-livekit-transcriber.env
```

#### 3. Install and enable the service

```bash
sudo cp /opt/bbb-livekit-transcriber/bbb-livekit-transcriber.service \
        /etc/systemd/system/bbb-livekit-transcriber.service
sudo systemctl daemon-reload
sudo systemctl enable --now bbb-livekit-transcriber
```

#### 4. Verify

```bash
sudo systemctl status bbb-livekit-transcriber
journalctl -u bbb-livekit-transcriber -f
```

Logs should show `registered worker` followed by `Agent joining room <meeting_id>` when a meeting starts.

---

### Option 2: Docker

The Docker image uses `network_mode: host` so the container reaches Redis and LiveKit on the host without any port mapping. The container runs as UID 999, which matches the `bigbluebutton` system user on a standard BBB host — so `/var/bigbluebutton/captions` permissions work without any `chown`.

Verify the UID before starting:

```bash
id bigbluebutton   # should show uid=999
```

#### 1. Configure

Create `/etc/bigbluebutton/bbb-livekit-transcriber.yml` as described in [Configuration](#configuration) above. The compose file mounts it read-only into the container.

#### 2. Build and start

```bash
cd bbb-livekit-transcriber
docker compose up -d --build
```

To include local faster-whisper (adds ~1.5 GB to the image), edit `docker-compose.yml` and uncomment the `INSTALL_FASTER_WHISPER: "true"` build arg before building.

#### 3. Verify

```bash
docker compose ps
docker compose logs -f
```

Logs should show `registered worker` followed by `Agent joining room <meeting_id>` when a meeting starts.

#### Updating

```bash
docker compose pull   # if using a pre-built image
# or
docker compose up -d --build   # to rebuild from source
```

---

### Development (with auto-reload)

```bash
cd bbb-livekit-transcriber
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python agent.py dev
```

## Testing

1. Start the agent (development: `python agent.py dev`, or check service is running)
2. Create a BBB meeting with LiveKit audio enabled
3. Open the captions panel in BBB and set your speech locale
4. Speak — transcripts should appear in the caption panel

Monitor agent logs:

```bash
# systemd
journalctl -u bbb-livekit-transcriber -f

# Docker
docker compose logs -f

# Development
# (output is printed to the terminal)
```

Monitor Redis messages while speaking:

```bash
redis-cli SUBSCRIBE to-akka-apps-redis-channel
```

Expected log output:

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
├── agent.py                          # Main agent: VAD, STT, transcript publishing
├── config.py                         # Configuration loader (YAML + env vars)
├── bbb_redis.py                      # Redis publisher for BBB caption messages
├── transcript_state.py               # Per-user transcription state and diff tracking
├── locale_tracker.py                 # Per-user speech locale from BBB Redis events
├── vtt_writer.py                     # WebVTT caption file export
├── requirements.txt                  # Full Python dependencies (includes faster-whisper)
├── requirements-docker.txt           # Docker dependencies (excludes faster-whisper)
├── Dockerfile                        # Container image (Python 3.11-slim, non-root)
├── docker-compose.yml                # Docker Compose for production deployment
├── bbb-livekit-transcriber.service   # systemd unit file (ready to install)
└── README.md                         # This file
```

## License

Part of the BigBlueButton open source project.
