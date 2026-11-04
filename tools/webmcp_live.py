"""Live WebMCP check. Needs Patchright 1.64+ and full Chrome (chromium channel).

    python tools/webmcp_live.py            # local page with two tools
    python tools/webmcp_live.py --demo URL # also list tools on a hosted demo

Serves a local page over http (set_content on about:blank does not expose
document.modelContext), lists its tools, calls both through the Fantoma API
and checks the results and a DOM change. Browsers are closed in finally.
"""
import http.server
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fantoma.browser_tool import Fantoma

PAGE = """<!doctype html><title>WebMCP test</title><body>
<h1 id="status">idle</h1>
<script>
document.modelContext.registerTool({
  name: "echo", description: "Echo a greeting back.",
  inputSchema: {type: "object", properties: {who: {type: "string"}}, required: ["who"]},
  execute: async (a) => ({content: [{type: "text", text: "hello " + a.who}]})
});
document.modelContext.registerTool({
  name: "set_status", description: "Change the status heading.",
  inputSchema: {type: "object", properties: {text: {type: "string"}}},
  execute: async (a) => { document.getElementById("status").textContent = a.text; return "ok"; }
});
</script></body>"""


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE.encode())

    def log_message(self, *a):
        pass


def main() -> int:
    demo = sys.argv[sys.argv.index("--demo") + 1] if "--demo" in sys.argv else ""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    f = Fantoma(browser="chromium", webmcp=True, headless=True)
    try:
        f.start(f"http://127.0.0.1:{srv.server_port}/")
        tools = f.webmcp_tools()
        print("tools:", [(t["name"], t["schema"]) for t in tools])
        assert [t["name"] for t in tools] == ["echo", "set_status"], tools
        r = f.webmcp_call("echo", {"who": "world"})
        print("echo ->", r["webmcp"])
        assert r["success"] and r["webmcp"]["result"] == "hello world"
        r = f.webmcp_call("set_status", {"text": "changed"})
        print("set_status ->", r["webmcp"], "dom changed:", r["changed"])
        assert r["success"] and r["changed"]
        assert f._engine.get_page().inner_text("#status") == "changed"
        r = f.webmcp_call("echo", {"who": "<secret:pw>"})
        print("secret ->", r["webmcp"]["error"])
        assert not r["success"]
        r = f.webmcp_call("nope", {})
        print("missing ->", r["webmcp"]["error"])
        assert not r["success"]
        print("prompt:", f.webmcp_prompt()[:200].replace("\n", " | "))
        print("LOCAL OK")
        if demo:
            f.navigate(demo)
            import time
            time.sleep(3)
            print("demo url:", f._engine.get_page().url)
            print("demo tools:", [(t["name"], t["description"][:60]) for t in f.webmcp_tools()])
        return 0
    finally:
        f.stop()
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
