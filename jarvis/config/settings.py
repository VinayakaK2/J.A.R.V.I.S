import os
from pydantic_settings import BaseSettings, SettingsConfigDict

# Loads environment variables and provides structured configuration for the entire system
class Settings(BaseSettings):
    # OpenAI LLM API key
    openai_api_key: str = ""
    # ElevenLabs TTS API key
    elevenlabs_api_key: str = ""
    # ElevenLabs voice ID (defaults to "Rachel" — calm English/Hindi voice)
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    # Database connection string — defaults to SQLite for local dev
    database_url: str = "sqlite:///./jarvis.db"

    # ── V6 Architecture Feature Flags ──
    use_semantic_perception: bool = True
    use_hierarchical_planner: bool = True   # ENABLED for Phase 2 validation
    use_simulation: bool = False
    use_critic_agent: bool = True           # ENABLED for Phase 2 validation

    # Application log level
    log_level: str = "INFO"
    # Sandboxed directory for all file tool operations
    workspace_dir: str = "./workspace"

    # Public base URL of this server — used to construct static audio URLs for Twilio
    # Example: https://abc123.ngrok-free.app  (no trailing slash)
    base_url: str = "http://localhost:8000"

    # Twilio credentials for WhatsApp + Voice integration
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = "whatsapp:+14155238886"
    # Twilio phone number used for outbound/inbound calls (E.164 format)
    twilio_voice_number: str = ""

    # Telegram Bot token for message delivery
    telegram_bot_token: str = ""

    # ── Owner Identity (loaded from env — never hardcode in source) ──
    # Used by auth/identity.py to determine owner vs. guest role
    owner_phone: str = "+91XXXXXXXXXX"
    owner_telegram_id: str = "YOUR_TELEGRAM_ID"
    owner_name: str = "Vinayaka"

    # Background agent scheduled task poll interval in seconds
    background_poll_interval: int = 10

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

# Global settings singleton
settings = Settings()

# Ensure the file operations sandbox directory exists at startup
if not os.path.exists(settings.workspace_dir):
    os.makedirs(settings.workspace_dir, exist_ok=True)
