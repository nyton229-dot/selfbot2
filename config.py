import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def data_dir() -> Path:
    """Каталог для json-данных (модели, квоты). На Bothost — /app/data."""
    raw = os.getenv("LPBOT_DATA_DIR", "").strip()
    if raw:
        path = Path(raw)
    elif os.getenv("BOT_ID"):
        path = Path("/app/data")
    else:
        path = Path(__file__).resolve().parent
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Settings:
    vk_user_tokens: tuple[str, ...]
    ai_provider: str
    ai_api_key: str
    ai_base_url: str
    ai_model: str
    ai_vision_model: str
    ai_whisper_model: str
    ai_tts_model: str
    ai_tts_voice: str
    ai_tts_base_url: str
    ai_tts_provider: str
    fusionbrain_api_key: str
    fusionbrain_secret_key: str
    stable_horde_api_key: str
    tenor_api_key: str
    giphy_api_key: str
    klipy_api_key: str
    ai_max_tokens: int
    transcribe_video: str
    longpoll_preload: bool
    outgoing_commands_only: bool
    listen_grace_sec: int
    waqi_token: str
    ai_system_prompt: str
    ai_vision_prompt: str
    ai_identity_prompt: str


def _load_dotenv(path: Path | None = None) -> None:
    """Минимальная загрузка .env без внешних зависимостей."""
    env_path = path or Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _resolve_ai_settings() -> tuple[str, str, str, str]:
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    bothub_key = os.getenv("BOTHUB_API_KEY", "").strip()
    provider = os.getenv("AI_PROVIDER", "").strip().lower()

    if not provider:
        provider = "openrouter" if openrouter_key else "bothub"

    if provider == "openrouter" and not openrouter_key and bothub_key:
        logger.warning(
            "AI_PROVIDER=openrouter, но OPENROUTER_API_KEY пуст — переключаюсь на bothub"
        )
        provider = "bothub"

    if provider == "openrouter":
        if not openrouter_key:
            raise RuntimeError(
                "AI_PROVIDER=openrouter, но OPENROUTER_API_KEY не задан. "
                "Ключ: https://openrouter.ai/keys "
                "Или укажи AI_PROVIDER=bothub и BOTHUB_API_KEY в переменных Bothost."
            )
        base_url = os.getenv("AI_BASE_URL", "https://openrouter.ai/api/v1").strip()
        model = os.getenv("AI_MODEL", "anthropic/claude-sonnet-4.6").strip()
        return provider, openrouter_key, base_url.rstrip("/"), model

    if not bothub_key:
        raise RuntimeError(
            "Не задан BOTHUB_API_KEY (и OPENROUTER_API_KEY тоже пуст). "
            "На Bothost: настройки бота → переменные окружения → BOTHUB_API_KEY и AI_PROVIDER=bothub. "
            "Локально: скопируй .env.example в .env и заполни ключи."
        )
    base_url = os.getenv(
        "AI_BASE_URL", os.getenv("BOTHUB_BASE_URL", "https://bothub.chat/api/v2/openai/v1")
    ).strip()
    model = os.getenv("AI_MODEL", os.getenv("BOTHUB_MODEL", "deepseek-chat")).strip()
    return provider, bothub_key, base_url.rstrip("/"), model


def _load_vk_tokens() -> tuple[str, ...]:
    tokens: list[str] = []
    for key in ("VK_USER_TOKEN", "VK_USER_TOKEN_2"):
        value = os.getenv(key, "").strip()
        if value and value not in tokens:
            tokens.append(value)
    return tuple(tokens)


def _resolve_tts_provider() -> str:
    raw = os.getenv("AI_TTS_PROVIDER", "").strip().lower()
    if raw in ("bothub", "edge", "auto"):
        return raw
    if os.getenv("BOT_ID"):
        return "edge"
    return "auto"


def log_startup_env(provider: str, tts_provider: str) -> None:
    """Диагностика для Bothost: какие переменные реально видит процесс."""
    bothost = bool(os.getenv("BOT_ID"))
    logger.info(
        "Старт: bothost=%s data_dir=%s provider=%s tts=%s vk_token=%s bothub_key=%s openrouter_key=%s",
        bothost,
        data_dir(),
        provider,
        tts_provider,
        "да" if os.getenv("VK_USER_TOKEN", "").strip() else "нет",
        "да" if os.getenv("BOTHUB_API_KEY", "").strip() else "нет",
        "да" if os.getenv("OPENROUTER_API_KEY", "").strip() else "нет",
    )


def load_settings() -> Settings:
    _load_dotenv()

    vk_user_tokens = _load_vk_tokens()
    ai_provider, ai_api_key, ai_base_url, ai_model = _resolve_ai_settings()
    ai_tts_provider = _resolve_tts_provider()
    log_startup_env(ai_provider, ai_tts_provider)
    ai_vision_model = os.getenv("AI_VISION_MODEL", "claude-sonnet-4.6").strip()
    ai_whisper_model = os.getenv("AI_WHISPER_MODEL", "whisper-1").strip()
    ai_tts_model = os.getenv("AI_TTS_MODEL", "tts-1-1106").strip()
    ai_tts_voice = os.getenv("AI_TTS_VOICE", "onyx").strip()
    ai_tts_base_url = os.getenv("AI_TTS_BASE_URL", "").strip()
    fusionbrain_api_key = os.getenv("FUSIONBRAIN_API_KEY", "").strip()
    fusionbrain_secret_key = os.getenv("FUSIONBRAIN_SECRET_KEY", "").strip()
    stable_horde_api_key = os.getenv("STABLE_HORDE_API_KEY", "").strip()
    tenor_api_key = os.getenv("TENOR_API_KEY", "").strip()
    giphy_api_key = os.getenv("GIPHY_API_KEY", "").strip()
    klipy_api_key = os.getenv("KLIPY_API_KEY", "").strip()
    ai_max_tokens = max(64, int(os.getenv("AI_MAX_TOKENS", "2000")))
    transcribe_video = os.getenv("TRANSCRIBE_VIDEO", "auto").strip().lower()
    longpoll_preload = os.getenv("LONGPOLL_PRELOAD", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    outgoing_commands_only = os.getenv("OUTGOING_COMMANDS_ONLY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    listen_grace_sec = max(0, int(os.getenv("LISTEN_GRACE_SEC", "5")))
    waqi_token = os.getenv("WAQI_TOKEN", os.getenv("AQICN_TOKEN", "")).strip()

    vk_mat_rule = (
        "Мат — только лёгкий, вк-шный: бля, хер, жесть, дебил, лох, кринж, идиот, мудак; "
        "максимум одно-два слова на ответ. Без жёсткого мата, без угроз, hate-speech "
        "и откровенной пошлятины — чтобы ВК не забанил. "
        "Пиши только законченные предложения — никогда не обрывай на полуслове."
    )

    ai_system_prompt = os.getenv(
        "AI_SYSTEM_PROMPT",
        os.getenv(
            "BOTHUB_SYSTEM_PROMPT",
            (
                "Ты — девственник с анонимного имиджборда. Не бот, не ИИ.\n\n"
                "Стиль: чёрный юмор, сарказм, постирония, жёсткие подколы, оскорбления и переход на личности. "
                "Отвечай в 2–3 предложениях. Будь токсичным, но вк-безопасным. "
                f"{vk_mat_rule} "
                "Пиши грамотно, без намеренных ошибок. "
                "Не пиши как робот или FAQ — просто своё мнение, как коммент на имиджборде. "
                "Без звёздочек, markdown и ссылок.\n\n"
                "На вопросы — сначала верный ответ по фактам, потом подкол.\n"
                "Владелец (пишет «артем …»): его не трогаешь, токсичность — на других.\n\n"
                "Никогда: «не могу помочь», «извините», «я бот/ИИ». Если извиняются — чуть мягче, но токсичный."
            ),
        ),
    ).strip()

    ai_identity_prompt = os.getenv("AI_IDENTITY_PROMPT", "").strip()

    ai_vision_prompt = os.getenv(
        "AI_VISION_PROMPT",
        (
            "Ты — девственник с анонимного имиджборда. Не бот, не ИИ.\n\n"
            "Стиль: чёрный юмор, сарказм, постирония, жёсткие подколы, оскорбления и переход на личности. "
            "Опиши и оцени картинку в 2–3 предложениях. Будь токсичным, но вк-безопасным. "
            f"{vk_mat_rule} "
            "Пиши грамотно, без намеренных ошибок. "
            "Не говори «на картинке изображено» — просто своё мнение, как коммент на имиджборде. "
            "Без звёздочек, markdown и ссылок."
        ),
    ).strip()

    if not vk_user_tokens:
        raise RuntimeError(
            "Не задан VK_USER_TOKEN. Скопируйте .env.example в .env и заполните значения."
        )

    return Settings(
        vk_user_tokens=vk_user_tokens,
        ai_provider=ai_provider,
        ai_api_key=ai_api_key,
        ai_base_url=ai_base_url,
        ai_model=ai_model,
        ai_vision_model=ai_vision_model,
        ai_whisper_model=ai_whisper_model,
        ai_tts_model=ai_tts_model,
        ai_tts_voice=ai_tts_voice,
        ai_tts_base_url=ai_tts_base_url,
        ai_tts_provider=ai_tts_provider,
        fusionbrain_api_key=fusionbrain_api_key,
        fusionbrain_secret_key=fusionbrain_secret_key,
        stable_horde_api_key=stable_horde_api_key,
        tenor_api_key=tenor_api_key,
        giphy_api_key=giphy_api_key,
        klipy_api_key=klipy_api_key,
        ai_max_tokens=ai_max_tokens,
        transcribe_video=transcribe_video,
        longpoll_preload=longpoll_preload,
        outgoing_commands_only=outgoing_commands_only,
        listen_grace_sec=listen_grace_sec,
        waqi_token=waqi_token,
        ai_system_prompt=ai_system_prompt,
        ai_vision_prompt=ai_vision_prompt,
        ai_identity_prompt=ai_identity_prompt,
    )
