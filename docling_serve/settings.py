from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    do_ocr: bool = True
    do_table_structure: bool = True

    model_config = SettingsConfigDict(env_prefix="DOCLING_")
