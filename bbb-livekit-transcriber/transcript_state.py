"""Per-participant transcript state tracking and diff computation.

Tracks the current transcriptId and previous transcript text per participant,
computes diffs for UpdateTranscriptPubMsg start/end/text fields.
"""

import re
import uuid

_SAFE_ID_CHARS = re.compile(r'[^\w\-]')


def generate_transcript_id(user_id: str) -> str:
    """Generate a unique transcript ID matching BBB's format: `{userId}-{hex}`.

    user_id is sanitized to contain only word characters and hyphens so that
    the resulting transcript_id is safe to log and store.
    """
    safe_id = _SAFE_ID_CHARS.sub('_', user_id)
    return f"{safe_id}-{uuid.uuid4().hex[:12]}"


def compute_diff(previous: str, current: str) -> tuple[int, int, str]:
    """Compute the diff between previous and current transcript text.

    Returns (start, end, text) where:
    - start: position in previous where the change begins
    - end: position in previous where the change ends
    - text: the replacement text

    This replicates the behavior of @mconf/bbb-diff used client-side.
    """
    if not previous:
        return (0, 0, current)

    # Find common prefix
    prefix_len = 0
    min_len = min(len(previous), len(current))
    while prefix_len < min_len and previous[prefix_len] == current[prefix_len]:
        prefix_len += 1

    # Find common suffix (not overlapping with prefix)
    suffix_len = 0
    while (suffix_len < min_len - prefix_len
           and previous[len(previous) - 1 - suffix_len] == current[len(current) - 1 - suffix_len]):
        suffix_len += 1

    start = prefix_len
    end = len(previous) - suffix_len
    text = current[prefix_len:len(current) - suffix_len] if suffix_len > 0 else current[prefix_len:]

    return (start, end, text)


class ParticipantTranscriptState:
    """Tracks transcript state for a single participant."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.transcript_id = generate_transcript_id(user_id)
        self._previous_transcript = ""

    def new_utterance(self):
        """Start tracking a new utterance (new transcriptId)."""
        self.transcript_id = generate_transcript_id(self.user_id)
        self._previous_transcript = ""

    def update(self, transcript: str) -> tuple[int, int, str]:
        """Compute diff for an interim transcript update.

        Returns (start, end, text) for the UpdateTranscriptPubMsg body.
        """
        start, end, text = compute_diff(self._previous_transcript, transcript)
        self._previous_transcript = transcript
        return start, end, text

    def finalize(self, transcript: str) -> tuple[int, int, str]:
        """Compute diff for a final transcript and prepare for next utterance.

        Returns (start, end, text) for the UpdateTranscriptPubMsg body.
        After calling this, the state is ready for a new utterance.
        """
        start, end, text = compute_diff(self._previous_transcript, transcript)
        self._previous_transcript = ""
        return start, end, text


class TranscriptStateManager:
    """Manages transcript state for all participants in a meeting."""

    def __init__(self):
        self._states: dict[str, ParticipantTranscriptState] = {}

    def get_or_create(self, user_id: str) -> ParticipantTranscriptState:
        if user_id not in self._states:
            self._states[user_id] = ParticipantTranscriptState(user_id)
        return self._states[user_id]

    def remove(self, user_id: str):
        self._states.pop(user_id, None)
