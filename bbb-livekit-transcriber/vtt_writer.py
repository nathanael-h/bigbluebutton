"""Generate WebVTT caption files from transcribed utterances.

Writes per-locale VTT files and a captions.json index to the BBB captions
directory, allowing caption export for every meeting (recorded or not).
"""

import json
import logging
import os
import re
import stat
from collections import defaultdict

logger = logging.getLogger(__name__)

DEFAULT_CAPTIONS_DIR = "/var/bigbluebutton/captions"

# Allowlist patterns for filesystem-derived values
_MEETING_ID_RE = re.compile(r'^[a-zA-Z0-9_\-]{1,256}$')
_LOCALE_RE = re.compile(r'^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{2,8})*$')

# Maps BCP-47 language subtag to a human-readable name for captions.json.
_LOCALE_NAMES = {
    "af": "Afrikaans", "ar": "Arabic", "bg": "Bulgarian", "bn": "Bengali",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "fa": "Persian", "fi": "Finnish", "fr": "French",
    "ga": "Irish", "gl": "Galician", "gu": "Gujarati", "he": "Hebrew",
    "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian", "hy": "Armenian",
    "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese",
    "ka": "Georgian", "km": "Khmer", "kn": "Kannada", "ko": "Korean",
    "lt": "Lithuanian", "lv": "Latvian", "mk": "Macedonian", "ml": "Malayalam",
    "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "my": "Burmese",
    "nb": "Norwegian", "ne": "Nepali", "nl": "Dutch", "pa": "Punjabi",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "sq": "Albanian",
    "sr": "Serbian", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil",
    "te": "Telugu", "th": "Thai", "tl": "Filipino", "tr": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese",
    "zh": "Chinese", "zu": "Zulu",
}


def _locale_display_name(locale: str) -> str:
    """Return a human-readable name for a BCP-47 locale tag."""
    lang = locale.split("-")[0].lower()
    region = locale.split("-")[1] if "-" in locale else None
    name = _LOCALE_NAMES.get(lang, lang.upper())
    if region:
        return f"{name} ({region})"
    return name


def _seconds_to_vtt_timestamp(seconds: float) -> str:
    """Convert seconds (float) to WebVTT timestamp HH:MM:SS.mmm."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def _escape_vtt(text: str) -> str:
    """Escape text for safe inclusion in a WebVTT cue payload.

    Prevents cue structure corruption and XSS when VTT files are served
    directly in a browser context.
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Replace '-->' to prevent corrupting VTT cue timing lines
    text = text.replace("-->", "- ->")
    # Collapse newlines — bare newlines terminate VTT cues
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text



def write_vtt_files(
    meeting_id: str,
    utterances: list[dict],
    captions_dir: str | None = None,
) -> None:
    """Write per-locale WebVTT files and captions.json for a completed meeting.

    Each utterance dict must have:
      start_time  float  seconds from session start
      end_time    float  seconds from session start
      speaker     str    participant display name
      text        str    raw transcript text (no [speaker] prefix)
      locale      str    BCP-47 locale e.g. "fr-FR"

    Errors are logged but not re-raised, since this is typically called from
    a finally block where propagating exceptions would suppress prior errors.
    """
    if not utterances:
        logger.info("No utterances to export for meeting %s", meeting_id)
        return

    try:
        _write_vtt_files_impl(meeting_id, utterances, captions_dir)
    except Exception:
        logger.exception("Failed to write VTT files for meeting %s", meeting_id)


def _write_vtt_files_impl(
    meeting_id: str,
    utterances: list[dict],
    captions_dir: str | None,
) -> None:
    # Validate meeting_id against an allowlist before using it as a path component
    if not _MEETING_ID_RE.match(meeting_id):
        raise ValueError(f"Invalid meeting_id: {meeting_id!r}")

    base_dir = captions_dir or os.environ.get("BBB_CAPTIONS_DIR", DEFAULT_CAPTIONS_DIR)
    base_real = os.path.realpath(base_dir)
    out_dir = os.path.realpath(os.path.join(base_dir, meeting_id))

    # Belt-and-suspenders containment check after regex validation
    if not out_dir.startswith(base_real + os.sep):
        raise ValueError(f"meeting_id {meeting_id!r} escapes captions directory")

    os.makedirs(out_dir, exist_ok=True)
    # Restrict directory to owner+group; transcripts are sensitive meeting content
    try:
        os.chmod(out_dir, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)  # 0o750
    except OSError as e:
        logger.warning("Could not set permissions on %s: %s", out_dir, e)

    # Group utterances by locale
    by_locale: dict[str, list[dict]] = defaultdict(list)
    for i, utt in enumerate(utterances):
        if not isinstance(utt, dict):
            logger.warning("Skipping malformed utterance at index %d", i)
            continue
        locale = utt.get("locale")
        if not locale or not isinstance(locale, str):
            logger.warning("Skipping utterance with missing locale at index %d", i)
            continue
        by_locale[locale].append(utt)

    written_locales = []
    for locale, cues in by_locale.items():
        # Validate locale before using it in a filename
        if not _LOCALE_RE.match(locale):
            logger.warning("Skipping invalid locale %r for meeting %s", locale, meeting_id)
            continue

        vtt_path = os.path.join(out_dir, f"caption_{locale}.vtt")
        lines = ["WEBVTT", ""]
        for cue in cues:
            try:
                start_time = float(cue.get("start_time", -1))
                end_time = float(cue.get("end_time", -1))
            except (TypeError, ValueError):
                logger.warning("Skipping cue with invalid timestamps: %r", cue)
                continue
            if end_time <= start_time:
                logger.warning(
                    "Skipping zero-duration cue for speaker %r (start=%.3f end=%.3f)",
                    cue.get("speaker", "?"), start_time, end_time,
                )
                continue
            start = _seconds_to_vtt_timestamp(start_time)
            end = _seconds_to_vtt_timestamp(end_time)
            speaker = _escape_vtt(str(cue.get("speaker", "")))
            text = _escape_vtt(str(cue.get("text", "")))
            lines.append(f"{start} --> {end}")
            lines.append(f"[{speaker}] {text}")
            lines.append("")
        try:
            with open(vtt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            logger.exception("Failed to write VTT file %s", vtt_path)
            continue
        written_locales.append(locale)
        logger.info("Wrote VTT for meeting=%s locale=%s (%d cues)", meeting_id, locale, len(cues))

    # Write unified transcript.vtt — all utterances sorted chronologically
    transcript_path = os.path.join(out_dir, "transcript.vtt")
    lines = ["WEBVTT", ""]
    for cue in sorted(utterances, key=lambda u: float(u.get("start_time", 0))):
        try:
            start_time = float(cue.get("start_time", -1))
            end_time = float(cue.get("end_time", -1))
        except (TypeError, ValueError):
            continue
        if end_time <= start_time:
            continue
        start = _seconds_to_vtt_timestamp(start_time)
        end = _seconds_to_vtt_timestamp(end_time)
        lines.append(f"{start} --> {end}")
        lines.append(f"[{_escape_vtt(str(cue.get('speaker', '')))}] {_escape_vtt(str(cue.get('text', '')))}")
        lines.append("")
    try:
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        logger.exception("Failed to write transcript.vtt %s", transcript_path)
    else:
        logger.info("Wrote transcript.vtt for meeting=%s (%d cues total)", meeting_id, len(utterances))

    # Write captions.json index
    captions_json = [
        {"locale": loc, "localeName": _locale_display_name(loc)}
        for loc in sorted(written_locales)
    ]
    json_path = os.path.join(out_dir, "captions.json")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(captions_json, f, indent=2)
    except Exception:
        logger.exception("Failed to write captions index %s", json_path)
    else:
        logger.info("Wrote captions.json for meeting=%s locales=%s", meeting_id, written_locales)
