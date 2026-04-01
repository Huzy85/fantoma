"""Example: extract structured data from a webpage."""
from fantoma import Agent

agent = Agent(
    llm_url="http://localhost:8080/v1",
)

# Step-by-step mode for more control
with agent.session("https://news.ycombinator.com") as session:
    data = session.extract("List the top 5 post titles with their point counts")
    print(data)
