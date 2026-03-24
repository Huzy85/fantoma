"""Example: using Fantoma with a local LLM (Ollama, llama.cpp, vLLM)."""
from fantoma import Agent

# With Ollama (default port 11434)
agent = Agent(
    llm_url="http://localhost:11434/v1",
    model="qwen2.5:7b",
    verbose=True,
)

# With llama.cpp server
# agent = Agent(llm_url="http://localhost:8080/v1", verbose=True)

# With vLLM
# agent = Agent(llm_url="http://localhost:8000/v1", verbose=True)

result = agent.run("Go to example.com and extract the main heading text")
print(f"Result: {result.data}")
