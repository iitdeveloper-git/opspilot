from opspilot.ai.provider import AIProvider

SYSTEM_RCA_PROMPT = """You are OpsPilot AI, an expert Senior Site Reliability Engineer (SRE).
Your task is to analyze incident context (logs, container states, system metrics) and provide:
1. Root Cause Analysis (RCA) - concise diagnosis of what went wrong
2. Evidence Timeline - key timestamped signals and error messages
3. Confidence score (0-100%)
4. Recommended Safe Remediation steps (prioritized from least destructive to most)

Be factual, concise, and technical. Do not output markdown codeblocks unless showing configs."""


class RootCauseAnalyzer:
    def __init__(self, provider: AIProvider):
        self.provider = provider

    async def analyze_incident(
        self,
        service_name: str,
        container_status: str,
        logs: str,
        system_metrics: dict | None = None,
    ) -> str:
        user_prompt = f"""SERVICE: {service_name}
CONTAINER STATUS: {container_status}
SYSTEM METRICS: {system_metrics or 'N/A'}

RECENT LOGS:
{logs[-3000:] if len(logs) > 3000 else logs}
"""
        return await self.provider.generate_response(SYSTEM_RCA_PROMPT, user_prompt)
