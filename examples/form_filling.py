"""Example: fill out a form on a test site."""
from fantoma import Agent

agent = Agent(
    llm_url="http://localhost:8080/v1",
    verbose=True,
)

result = agent.run(
    "Go to httpbin.org/forms/post and fill out the form with: "
    "customer name 'John Doe', telephone '555-1234', "
    "email 'john@example.com', size 'Large', topping 'Bacon', "
    "then submit the form"
)

print(f"Success: {result.success}")
print(f"Steps: {result.steps_taken}")
