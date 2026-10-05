from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@sentinel-db:5432/sentinel"
    mqtt_host: str = "sentinel-mosquitto"
    mqtt_port: int = 8883
    mqtt_tls: bool = True
    mqtt_ca_file: str = "/certs/ca.crt"
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_telemetry_topic: str = "sentinel/+/telemetry"
    mqtt_alerts_topic: str = "sentinel/+/alerts"
    mqtt_status_topic: str = "sentinel/+/status"
    mqtt_ai_topic: str = "sentinel/ai/+/risk"
    default_device_id: str = "sentinel-node-01"
    api_token: str = ""
    operator_user: str = "operateur"
    operator_password_hash: str = ""
    session_secret: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
