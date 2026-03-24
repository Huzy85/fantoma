"""Example: model escalation — start with a local model, fall back to cloud if stuck."""
from fantoma import Agent

agent = Agent(
    llm_url="http://localhost:8080/v1",       # Start with local model (free)
    escalation=[
        "http://localhost:8080/v1",            # Try local first
        "https://api.moonshot.ai/v1",          # Fall back to cloud API
    ],
    api_key="",                                # No key needed for local
)

# If the local model gets stuck after retries, Fantoma automatically
# switches to the cloud API for that one step, then continues.
result = agent.run("Go to booking.com and search for hotels in London")
print(f"Success: {result.success}")
print(f"Escalations used: {result.escalations}")
