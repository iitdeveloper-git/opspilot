import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("opspilot.ai.provider")


class AIProvider:
    """Pluggable LLM provider supporting OpenAI, Anthropic, Gemini, and Local Ollama."""

    def __init__(
        self,
        provider: str = "gemini",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.provider = (provider or "gemini").lower().strip()
        default_model = "gemini-1.5-flash" if self.provider == "gemini" else "gpt-4o-mini"
        self.model = model or default_model
        self.base_url = (base_url or "").strip()

        # Provider-scoped API key resolution - never leak Gemini key to OpenAI or vice-versa
        if api_key and api_key.strip():
            self.api_key = api_key.strip()
        else:
            if self.provider == "gemini":
                self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("AI_API_KEY") or ""
            elif self.provider == "openai":
                self.api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY") or ""
            elif self.provider == "anthropic":
                self.api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("AI_API_KEY") or ""
            elif self.provider == "ollama":
                self.api_key = "ollama-local"
            else:
                self.api_key = os.getenv("AI_API_KEY") or ""

        if not self.base_url and self.provider == "ollama":
            self.base_url = str(os.getenv("AI_BASE_URL", "http://localhost:11434"))

    @classmethod
    async def list_available_models(
        cls,
        provider: str,
        api_key: str = "",
        base_url: str = "",
    ) -> list[dict[str, Any]]:
        """Query the provider API dynamically to retrieve authorized/supported models."""
        provider = (provider or "gemini").lower().strip()
        base_url = (base_url or "").strip()
        api_key = (api_key or "").strip()

        if provider == "gemini":
            return await cls._fetch_gemini_models(api_key)
        elif provider == "openai":
            return await cls._fetch_openai_models(api_key, base_url)
        elif provider == "ollama":
            return await cls._fetch_ollama_models(base_url)
        elif provider == "anthropic":
            return await cls._fetch_anthropic_models(api_key)
        else:
            return [{"id": "default", "name": "Default Model", "recommended": True}]

    @classmethod
    async def _fetch_gemini_models(cls, api_key: str) -> list[dict[str, Any]]:
        """Query Google Gemini v1beta/models API with the user API key."""
        if not api_key:
            raise ValueError("Google Gemini API key is required to query models. Please enter your API key first.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("models", [])
                models: list[dict[str, Any]] = []
                for m in raw_models:
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" in methods:
                        raw_name = m.get("name", "")
                        clean_id = raw_name.replace("models/", "")
                        display_name = m.get("displayName") or clean_id
                        is_rec = clean_id in ("gemini-1.5-flash", "gemini-2.0-flash")
                        models.append({
                            "id": clean_id,
                            "name": f"{display_name} ({clean_id})" if display_name != clean_id else clean_id,
                            "recommended": is_rec,
                            "description": m.get("description", "")[:80],
                        })
                # Sort recommended first, then alphabetically
                models.sort(key=lambda x: (not x["recommended"], x["id"]))
                if not models:
                    raise ValueError("No models supporting generateContent found for this Gemini API key.")
                return models
            else:
                err_text = resp.text
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error", {}).get("message", err_text)
                except Exception:
                    err_msg = err_text
                raise ValueError(f"Gemini API Error ({resp.status_code}): {err_msg}")

    @classmethod
    async def _fetch_openai_models(cls, api_key: str, base_url: str = "") -> list[dict[str, Any]]:
        """Query OpenAI v1/models API with the Bearer token."""
        if not api_key and not base_url:
            raise ValueError("OpenAI API key is required to query models. Please enter your API key first.")

        url = f"{base_url.rstrip('/')}/models" if base_url else "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("data", [])
                models: list[dict[str, Any]] = []
                for m in raw_models:
                    m_id = m.get("id", "")
                    # Filter for chat-applicable models
                    if base_url or any(m_id.startswith(p) for p in ("gpt-", "o1", "o3", "chatgpt-")):
                        is_rec = m_id in ("gpt-4o-mini", "gpt-4o")
                        models.append({
                            "id": m_id,
                            "name": f"{m_id} {'(Recommended)' if is_rec else ''}".strip(),
                            "recommended": is_rec,
                        })
                # Sort: recommended first, then by name
                models.sort(key=lambda x: (not x["recommended"], x["id"]))
                if not models:
                    raise ValueError(f"No chat models found in OpenAI response (received {len(raw_models)} raw items).")
                return models
            else:
                err_text = resp.text
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error", {}).get("message", err_text)
                except Exception:
                    err_msg = err_text
                raise ValueError(f"OpenAI API Error ({resp.status_code}): {err_msg}")

    @classmethod
    async def _fetch_ollama_models(cls, base_url: str = "") -> list[dict[str, Any]]:
        """Query local Ollama instance for installed models directly from Ollama API."""
        target_base = (base_url or "http://localhost:11434").rstrip("/")
        url = f"{target_base}/api/tags" if not target_base.endswith("/v1") else f"{target_base[:-3]}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    raw = data.get("models", [])
                    models = []
                    for m in raw:
                        name = m.get("name", "")
                        models.append({"id": name, "name": name, "recommended": "llama" in name})
                    if models:
                        return models
                    raise ValueError(f"Ollama is reachable at {target_base}, but has no models installed. Run `ollama pull <model>` first.")
                raise ValueError(f"Ollama returned HTTP {resp.status_code} at {url}")
        except httpx.RequestError as e:
            raise ValueError(f"Could not connect to Ollama at {target_base}: {e!s}. Make sure `ollama serve` is running.")

    @classmethod
    async def _fetch_anthropic_models(cls, api_key: str) -> list[dict[str, Any]]:
        """Validate Anthropic API key and return supported Claude models."""
        if not api_key:
            raise ValueError("Anthropic API key is required to query models. Please enter your API key first.")

        # Test key validity with 1-token ping to Messages API
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 429):
                return [
                    {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet (Recommended - Coding & RCA)", "recommended": True},
                    {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku (Fast)", "recommended": False},
                    {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus", "recommended": False},
                    {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku", "recommended": False},
                ]
            elif resp.status_code == 401:
                raise ValueError("Anthropic API Error (401): Invalid API key provided.")
            else:
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error", {}).get("message", resp.text)
                except Exception:
                    err_msg = resp.text
                raise ValueError(f"Anthropic API Error ({resp.status_code}): {err_msg}")

    async def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider in ["openai", "ollama"]:
            return await self._call_openai_compatible(system_prompt, user_prompt)
        elif self.provider == "anthropic":
            return await self._call_anthropic(system_prompt, user_prompt)
        elif self.provider == "gemini":
            return await self._call_gemini(system_prompt, user_prompt)
        else:
            return f"Unsupported AI provider: {self.provider}"

    async def _call_openai_compatible(self, system_prompt: str, user_prompt: str) -> str:
        url = (
            f"{self.base_url.rstrip('/')}/chat/completions"
            if self.base_url
            else "https://api.openai.com/v1/chat/completions"
        )
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            return f"OpenAI Error ({resp.status_code}): {resp.text}"

    async def _call_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        url = "https://api.anthropic.com/v1/messages"
        headers: dict[str, str] = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model or "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["content"][0]["text"].strip()
            return f"Anthropic Error ({resp.status_code}): {resp.text}"

    async def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            return "Gemini API key is not configured. Please set GEMINI_API_KEY in your .env file or UI settings."
        model = self.model or "gemini-1.5-flash"
        # Strip models/ prefix if present
        if model.startswith("models/"):
            model = model.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (KeyError, IndexError) as err:
                    return f"Gemini response parsing error: {err}"
            return f"Gemini Error ({resp.status_code}): {resp.text}"
