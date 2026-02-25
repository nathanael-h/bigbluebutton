"""Configuration loader for bbb-livekit-transcriber.

Reads settings from a YAML config file, with fallback to environment variables.
LiveKit API keys can be auto-read from /etc/bigbluebutton/livekit.yaml.
"""

import os
import yaml
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "/etc/bigbluebutton/bbb-livekit-transcriber.yml"
LIVEKIT_YAML_PATH = "/etc/bigbluebutton/livekit.yaml"

DEFAULTS = {
    "livekit": {
        "url": "ws://localhost:7880",
        "api_key": "",
        "api_secret": "",
    },
    "redis": {
        "host": "127.0.0.1",
        "port": 6379,
    },
    "stt": {
        # provider: "faster-whisper" (local) or "openai-compatible" (external HTTP API)
        "provider": "faster-whisper",
        # Fallback locale (BCP-47) used before the user sets their language in BBB.
        # Language is always taken from the user's BBB speech locale setting.
        "default_locale": "en-US",
        # Settings for local faster-whisper
        "whisper_model": "tiny",
        "device": "cpu",
        "compute_type": "int8",
        # Settings for openai-compatible external API (e.g. speaches, openai)
        "api": {
            "base_url": "http://localhost:8000",  # speaches default port
            "api_key": "cant-be-empty",           # speaches doesn't need a real key
            "model": "Systran/faster-whisper-base",  # speaches model name
            "language": None,                     # None = auto-detect
        },
    },
}


def _read_livekit_keys() -> tuple[str, str]:
    """Read LiveKit API key and secret from the standard BBB livekit.yaml."""
    try:
        with open(LIVEKIT_YAML_PATH, "r") as f:
            lk_config = yaml.safe_load(f)
        keys = lk_config.get("keys", {})
        if keys:
            api_key = next(iter(keys))
            api_secret = keys[api_key]
            return str(api_key), str(api_secret)
    except FileNotFoundError:
        logger.warning("LiveKit config not found at %s", LIVEKIT_YAML_PATH)
    except Exception as e:
        logger.warning("Failed to read LiveKit keys from %s: %s", LIVEKIT_YAML_PATH, e)
    return "", ""


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursively for nested dicts."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str | None = None) -> dict:
    """Load configuration from YAML file with defaults and env var overrides."""
    config = DEFAULTS.copy()

    # Load from YAML config file
    path = config_path or os.environ.get("BBB_LK_TRANSCRIBER_CONFIG", DEFAULT_CONFIG_PATH)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                file_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, file_config)
            logger.info("Loaded config from %s", path)
        except Exception as e:
            logger.warning("Failed to load config from %s: %s", path, e)

    # Auto-read LiveKit keys if not explicitly configured
    if not config["livekit"]["api_key"] or not config["livekit"]["api_secret"]:
        api_key, api_secret = _read_livekit_keys()
        if api_key and api_secret:
            config["livekit"]["api_key"] = api_key
            config["livekit"]["api_secret"] = api_secret
            logger.info("Read LiveKit keys from %s", LIVEKIT_YAML_PATH)

    # Environment variable overrides
    env_map = {
        "LIVEKIT_URL": ("livekit", "url"),
        "LIVEKIT_API_KEY": ("livekit", "api_key"),
        "LIVEKIT_API_SECRET": ("livekit", "api_secret"),
        "REDIS_HOST": ("redis", "host"),
        "REDIS_PORT": ("redis", "port"),
        "STT_PROVIDER": ("stt", "provider"),
        "STT_DEFAULT_LOCALE": ("stt", "default_locale"),
        "WHISPER_MODEL": ("stt", "whisper_model"),
        "WHISPER_DEVICE": ("stt", "device"),
        "WHISPER_COMPUTE_TYPE": ("stt", "compute_type"),
        "STT_API_BASE_URL": ("stt", "api", "base_url"),
        "STT_API_KEY": ("stt", "api", "api_key"),
        "STT_API_MODEL": ("stt", "api", "model"),
        "STT_API_LANGUAGE": ("stt", "api", "language"),
    }
    for env_var, path in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            # Navigate nested path like ("stt", "api", "base_url")
            target = config
            for key in path[:-1]:
                target = target[key]
            last_key = path[-1]
            target[last_key] = int(value) if last_key == "port" else value

    return config
