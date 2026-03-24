"""Example: using Fantoma with a proxy or VPN."""
from fantoma import Agent

# Single proxy (SOCKS5 or HTTP)
agent = Agent(
    llm_url="http://localhost:8080/v1",
    proxy="socks5://user:pass@proxy.example.com:1080",
)

# Proxy rotation (round-robin between multiple proxies)
# agent = Agent(
#     llm_url="http://localhost:8080/v1",
#     proxy=[
#         "socks5://user:pass@us.proxy.com:1080",
#         "socks5://user:pass@uk.proxy.com:1080",
#         "socks5://user:pass@de.proxy.com:1080",
#     ],
# )

result = agent.run("Go to https://httpbin.org/ip and tell me what IP address is shown")
print(f"IP: {result.data}")
