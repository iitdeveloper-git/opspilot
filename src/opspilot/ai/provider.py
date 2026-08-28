import logging
import os

import httpx

logger = logging.getLogger("opspilot.ai.provider")


class AIProvider:
    """Pluggable LLM provider supporting OpenAI, Anthropic, Gemini, and Local Ollama."""

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.provider = provider.lower()
        self.model = model
        self.api_key: str = api_key or str(os.getenv("AI_API_KEY", ""))
        self.base_url: str = base_url or str(os.getenv("AI_BASE_URL", "http://localhost:11434/v1"))

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
            if self.provider == "ollama"
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
            return f"AI Provider Error ({resp.status_code}): {resp.text}"

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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return f"Gemini Error ({resp.status_code}): {resp.text}"
