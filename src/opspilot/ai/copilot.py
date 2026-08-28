from opspilot.ai.provider import AIProvider

SYSTEM_COPILOT_PROMPT = """You are OpsPilot Copilot, an AI assistant built into an infrastructure command center.
You help DevOps engineers monitor, diagnose, and maintain their servers.
You have access to real-time system metrics, container states, and logs.
Explain complex system events in clear, actionable terms.
Always prioritize stability and safety. If recommending a destructive action, clearly note the risks."""


class OpsCopilot:
    def __init__(self, provider: AIProvider):
        self.provider = provider

    async def ask(self, query: str, context: dict) -> str:
        user_prompt = f"""CURRENT INFRASTRUCTURE CONTEXT:
- System Metrics: {context.get('metrics')}
- Containers: {context.get('containers')}
- SSL Certificates: {context.get('ssl')}

USER QUESTION:
{query}
"""
        return await self.provider.generate_response(SYSTEM_COPILOT_PROMPT, user_prompt)
