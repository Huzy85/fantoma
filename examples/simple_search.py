"""Simple example: search Hacker News for AI posts."""
from fantoma import Agent

agent = Agent(
    llm_url="http://localhost:8080/v1",  # Any OpenAI-compatible endpoint
)

result = agent.run("Go to news.ycombinator.com and find the title of the top post about AI")

if result.success:
    print(f"Found: {result.data}")
    print(f"Steps taken: {result.steps_taken}")
else:
    print(f"Failed: {result.error}")
