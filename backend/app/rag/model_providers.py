"""Provider capability registry for admin-managed model configurations.

The database stores the provider with each model row.  Runtime consumers must
derive the fallback endpoint and API key from that provider, rather than from
the model type alone; otherwise a valid OpenAI-compatible endpoint can receive
another provider's credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

from app.core.config import Settings

ModelType = Literal["LLM", "EMBEDDING", "RERANK", "OCR", "VISION"]


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    api_key_attribute: str
    base_url_attribute: str
    supports_thinking: bool = False
    supports_json_mode: bool = False


_CAPABILITIES: dict[str, dict[str, ProviderCapability]] = {
    "LLM": {
        "zhipu": ProviderCapability(
            "zhipu_api_key", "zhipu_base_url", supports_thinking=True, supports_json_mode=True
        ),
        "siliconflow": ProviderCapability("siliconflow_api_key", "siliconflow_base_url"),
    },
    "EMBEDDING": {
        "zhipu": ProviderCapability("zhipu_api_key", "zhipu_base_url"),
        "siliconflow": ProviderCapability("siliconflow_api_key", "siliconflow_base_url"),
    },
    "RERANK": {
        "zhipu": ProviderCapability("zhipu_api_key", "zhipu_base_url"),
        "siliconflow": ProviderCapability("siliconflow_api_key", "siliconflow_base_url"),
    },
    "OCR": {
        "zhipu": ProviderCapability("zhipu_api_key", "zhipu_base_url", supports_thinking=True),
        "siliconflow": ProviderCapability("siliconflow_api_key", "siliconflow_base_url"),
    },
    "VISION": {
        "zhipu": ProviderCapability("zhipu_api_key", "zhipu_base_url", supports_thinking=True),
        "siliconflow": ProviderCapability("siliconflow_api_key", "siliconflow_base_url"),
    },
}


def normalize_provider(provider: str) -> str:
    return provider.strip().lower()


def supported_providers(model_type: str) -> tuple[str, ...]:
    return tuple(_CAPABILITIES.get(model_type.upper(), {}))


def get_provider_capability(model_type: str, provider: str) -> ProviderCapability | None:
    return _CAPABILITIES.get(model_type.upper(), {}).get(normalize_provider(provider))


def is_supported_provider(model_type: str, provider: str) -> bool:
    return get_provider_capability(model_type, provider) is not None


def environment_api_key(settings: Settings, model_type: str, provider: str) -> SecretStr | None:
    capability = get_provider_capability(model_type, provider)
    if capability is None:
        return None
    value = getattr(settings, capability.api_key_attribute)
    return value if isinstance(value, SecretStr) or value is None else None


def environment_base_url(settings: Settings, model_type: str, provider: str) -> str | None:
    capability = get_provider_capability(model_type, provider)
    if capability is None:
        return None
    value = getattr(settings, capability.base_url_attribute)
    return str(value) if value else None
