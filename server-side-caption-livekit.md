

**Claude’s Plan**

# Server-Side Captions for LiveKit Audio Bridge

## Context

When BigBlueButton uses LiveKit as the audio bridge (`audioBridge=livekit`), server-side transcription does not work. The existing `bbb-transcription-controller` depends on FreeSWITCH audio streams, which are absent in the LiveKit path. Only client-side Web Speech API works, which has limitations (browser-dependent, doesn't work for phone-only users).

This plan creates a **Python LiveKit Agent** (`bbb-livekit-transcriber`) that joins LiveKit rooms, subscribes to participant audio tracks, runs STT via **faster-whisper** with auto language detection, and publishes transcriptions to BBB's existing caption pipeline via Redis. The entire existing caption infrastructure (akka-bbb-apps handlers, CaptionDAO, GraphQL subscriptions, client UI) is reused unchanged.

**Scope:** Core agent code + Redis integration only. No packaging/systemd/build scripts.

---

## Architecture

```
User speaks -> LiveKit Server (:7880)
                    |
         bbb-livekit-transcriber (Python agent)
         - Subscribes to audio tracks per participant
         - Runs faster-whisper STT (auto language detect)
                    |
         Redis PUBLISH "to-akka-apps-redis-channel"
         (UpdateTranscriptPubMsg)
                    |
         akka-bbb-apps (existing, unchanged)
         -> CaptionDAO -> PostgreSQL
         -> TranscriptUpdatedEvtMsg broadcast
                    |
         Client caption display (existing, unchanged)
```

**Key identity mapping (no code changes needed):**

- LiveKit room name = BBB `meetingId` ([UserJoinMeetingReqMsgHdlr.scala:160](vscode-webview://0jbob7rsj1qua02ko4uk2g319b20qvb3ak482vkosodrn8jclrnq/akka-bbb-apps/src/main/scala/org/bigbluebutton/core/apps/users/UserJoinMeetingReqMsgHdlr.scala#L160))
- LiveKit participant identity = BBB `userId` ([UserJoinMeetingReqMsgHdlr.scala:168](vscode-webview://0jbob7rsj1qua02ko4uk2g319b20qvb3ak482vkosodrn8jclrnq/akka-bbb-apps/src/main/scala/org/bigbluebutton/core/apps/users/UserJoinMeetingReqMsgHdlr.scala#L168))
- Participant metadata JSON contains `meetingId` and `voiceConf` ([LiveKitMsgs.scala:73-76](vscode-webview://0jbob7rsj1qua02ko4uk2g319b20qvb3ak482vkosodrn8jclrnq/bbb-common-message/src/main/scala/org/bigbluebutton/common2/msgs/LiveKitMsgs.scala#L73-L76))

---

## New Files

All new files go in a new directory: `bbb-livekit-transcriber/`

### 1. `bbb-livekit-transcriber/agent.py` — Main entry point

Uses the `livekit-agents` framework. When a LiveKit room is created, the framework dispatches a job to this agent. The agent:

1.  Connects to the room with `AutoSubscribe.AUDIO_ONLY`
2.  For each participant's audio track, creates a `faster-whisper` STT stream
3.  On transcript events (interim + final), computes a diff and publishes `UpdateTranscriptPubMsg` to Redis

Key logic:

- Listen for `track_subscribed` events to handle new participants
- Also iterate existing `remote_participants` on connect for late-joining agents
- Track per-participant transcript state (previous transcript text, current transcriptId)
- Use `livekit-plugins-silero` for VAD (Voice Activity Detection) to reduce STT load

### 2. `bbb-livekit-transcriber/bbb_redis.py` — Redis message publisher

Builds and publishes messages matching the exact JSON format that `ReceivedJsonMsgHandlerActor` expects.

**UpdateTranscriptPubMsg format** (from [AudioCaptionsMsgs.scala](vscode-webview://0jbob7rsj1qua02ko4uk2g319b20qvb3ak482vkosodrn8jclrnq/bbb-common-message/src/main/scala/org/bigbluebutton/common2/msgs/AudioCaptionsMsgs.scala)):

```json
{
  "envelope": {
    "name": "UpdateTranscriptPubMsg",
    "routing": {
      "msgType": "SYSTEM",
      "meetingId": "<meetingId>",
      "userId": "<userId>"
    }
  },
  "core": {
    "header": {
      "name": "UpdateTranscriptPubMsg",
      "meetingId": "<meetingId>",
      "userId": "<userId>"
    },
    "body": {
      "transcriptId": "<unique-id>",
      "start": 0,
      "end": 0,
      "text": "<new-text>",
      "transcript": "<full-transcript>",
      "locale": "<detected-locale>",
      "result": true
    }
  }
}
```

Publishes to Redis channel: `to-akka-apps-redis-channel`

Also supports `TranscriptionProviderErrorMsg` for error reporting.

### 3. `bbb-livekit-transcriber/transcript_state.py` — Per-participant transcript state tracker

Tracks per participant:

- Current `transcriptId` (format: `userId-timestamp`, matching [speech/service.ts:generateId()](vscode-webview://0jbob7rsj1qua02ko4uk2g319b20qvb3ak482vkosodrn8jclrnq/bigbluebutton-html5/imports/ui/components/audio/audio-graphql/audio-captions/speech/service.ts))
- Previous transcript text (for diff computation)
- Handles `isFinal` transitions: when a final result arrives, generate a new `transcriptId` for the next utterance

Diff computation: simple start/end/text calculation matching the `@mconf/bbb-diff` behavior used in [speech/component.tsx:136-145](vscode-webview://0jbob7rsj1qua02ko4uk2g319b20qvb3ak482vkosodrn8jclrnq/bigbluebutton-html5/imports/ui/components/audio/audio-graphql/audio-captions/speech/component.tsx#L136-L145).

### 4. `bbb-livekit-transcriber/config.py` — Configuration

Reads from environment variables or a YAML config file:

```yaml
livekit:
  url: ws://localhost:7880
  api_key: ""      # Read from /etc/bigbluebutton/livekit.yaml
  api_secret: ""   # Read from /etc/bigbluebutton/livekit.yaml

redis:
  host: 127.0.0.1
  port: 6379

stt:
  provider: faster-whisper
  whisper_model: base    # tiny, base, small, medium, large-v3
  device: cpu            # cpu or cuda
  compute_type: int8     # int8, float16, float32
```

### 5. `bbb-livekit-transcriber/requirements.txt`

```
livekit-agents>=1.0
livekit-plugins-silero>=1.0
faster-whisper>=1.1
redis>=5.0
pyyaml>=6.0
```

---

## Existing Files to Modify

### 1. `bigbluebutton-html5/private/config/settings.yml` (line 99)

Update the provider comment to include `livekit` as a valid option:

```yaml
# provider: [webspeech, vosk, gladia, livekit]
```

No other client-side changes needed. When `provider: livekit` is set, `isWebSpeechApi()` returns false, so the client skips browser speech recognition ([speech/component.tsx:101-104](vscode-webview://0jbob7rsj1qua02ko4uk2g319b20qvb3ak482vkosodrn8jclrnq/bigbluebutton-html5/imports/ui/components/audio/audio-graphql/audio-captions/speech/component.tsx#L101-L104)). The server-side agent handles everything. Caption display works unchanged via the existing GraphQL subscription.

---

## Message Flow (reuses existing infrastructure)

1.  Agent publishes `UpdateTranscriptPubMsg` to Redis `to-akka-apps-redis-channel`
2.  `ReceivedJsonMsgHandlerActor` routes it to the meeting actor (already handles this message type)
3.  `UpdateTranscriptPubMsgHdlr.handle()` processes it:
    - Validates `liveTranscription` is not disabled
    - Calls `AudioCaptions.editTranscript()` to update in-memory model
    - Calls `CaptionDAO.insertOrUpdateCaption()` to persist
    - Broadcasts `TranscriptUpdatedEvtMsg` to clients
4.  Client `getCaptions` GraphQL subscription receives update and renders caption

**No changes needed** to akka-bbb-apps, bbb-graphql-actions, bbb-graphql-server, or client caption display components.

---

## Implementation Steps

### Step 1: Create project structure

Create `bbb-livekit-transcriber/` with `agent.py`, `bbb_redis.py`, `transcript_state.py`, `config.py`, `requirements.txt`.

### Step 2: Implement `config.py`

Load LiveKit credentials (url, api_key, api_secret), Redis connection, and STT settings from YAML/env vars. Include helper to parse LiveKit keys from `/etc/bigbluebutton/livekit.yaml`.

### Step 3: Implement `bbb_redis.py`

Redis connection and message builder that constructs `UpdateTranscriptPubMsg` and `TranscriptionProviderErrorMsg` JSON matching the exact format in [AudioCaptionsMsgs.scala](vscode-webview://0jbob7rsj1qua02ko4uk2g319b20qvb3ak482vkosodrn8jclrnq/bbb-common-message/src/main/scala/org/bigbluebutton/common2/msgs/AudioCaptionsMsgs.scala). Publish to `to-akka-apps-redis-channel`.

### Step 4: Implement `transcript_state.py`

Per-participant state: transcriptId generation, previous transcript tracking, diff computation (start, end, text fields).

### Step 5: Implement `agent.py`

LiveKit Agent using `livekit-agents` framework:

- `WorkerOptions` with LiveKit URL and credentials
- `entrypoint` function that connects to room, subscribes to audio
- Per-participant STT stream using `faster-whisper` with auto language detection
- VAD via `livekit-plugins-silero`
- On transcript events, compute diff and publish via `bbb_redis.py`

### Step 6: Update `settings.yml`

Add `livekit` to the provider options comment.

---

## Verification

### Manual testing:

1.  Start the agent: `cd bbb-livekit-transcriber && python agent.py start`
2.  Ensure LiveKit is running and `audioBridge=livekit` is configured
3.  Create a BBB meeting and join with audio
4.  Speak into the microphone
5.  Verify captions appear in the BBB client UI
6.  Check Redis messages with `redis-cli SUBSCRIBE to-akka-apps-redis-channel`
7.  Check PostgreSQL `caption` table for persisted transcriptions

### Edge cases to test:

- Multiple simultaneous speakers
- User joins/leaves mid-transcription
- Agent reconnection after LiveKit disconnect
- Meeting end triggers agent cleanup