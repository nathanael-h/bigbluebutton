"""Generate WebVTT caption files from transcribed utterances.

Writes per-locale VTT files and a captions.json index to the BBB captions
directory, allowing caption export for every meeting (recorded or not).
"""

import json
import logging
import os
from collections import defaultdict

logger = logging.getLogger(__name__)

DEFAULT_CAPTIONS_DIR = "/var/bigbluebutton/captions"

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
    """
    if not utterances:
        logger.info("No utterances to export for meeting %s", meeting_id)
        return

    base_dir = captions_dir or os.environ.get("BBB_CAPTIONS_DIR", DEFAULT_CAPTIONS_DIR)
    out_dir = os.path.join(base_dir, meeting_id)
    os.makedirs(out_dir, exist_ok=True)

    # Group utterances by locale
    by_locale: dict[str, list[dict]] = defaultdict(list)
    for utt in utterances:
        by_locale[utt["locale"]].append(utt)

    written_locales = []
    for locale, cues in by_locale.items():
        vtt_path = os.path.join(out_dir, f"caption_{locale}.vtt")
        lines = ["WEBVTT", ""]
        for cue in cues:
            start = _seconds_to_vtt_timestamp(cue["start_time"])
            end = _seconds_to_vtt_timestamp(cue["end_time"])
            speaker = cue["speaker"]
            text = cue["text"]
            lines.append(f"{start} --> {end}")
            lines.append(f"[{speaker}] {text}")
            lines.append("")
        with open(vtt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        written_locales.append(locale)
        logger.info("Wrote VTT for meeting=%s locale=%s (%d cues)", meeting_id, locale, len(cues))

    # Write unified transcript.vtt — all utterances sorted chronologically
    transcript_path = os.path.join(out_dir, "transcript.vtt")
    lines = ["WEBVTT", ""]
    for cue in sorted(utterances, key=lambda u: u["start_time"]):
        start = _seconds_to_vtt_timestamp(cue["start_time"])
        end = _seconds_to_vtt_timestamp(cue["end_time"])
        lines.append(f"{start} --> {end}")
        lines.append(f"[{cue['speaker']}] {cue['text']}")
        lines.append("")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Wrote transcript.vtt for meeting=%s (%d cues total)", meeting_id, len(utterances))

    # Write captions.json index
    captions_json = [
        {"locale": loc, "localeName": _locale_display_name(loc)}
        for loc in sorted(written_locales)
    ]
    json_path = os.path.join(out_dir, "captions.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(captions_json, f, indent=2)
    logger.info("Wrote captions.json for meeting=%s locales=%s", meeting_id, written_locales)
