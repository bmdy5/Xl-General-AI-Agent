#!/usr/bin/env python3
"""Test Stitch MCP generation — with HTML download."""
import sys, os, asyncio, json, subprocess, urllib.request, ssl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["STITCH_TIMEOUT"] = "300"

from agent.tools.stitch_tool import StitchTool

async def download_html(code_url: str) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    full_url = code_url if code_url.startswith("http") else f"https:{code_url}"
    req = urllib.request.Request(full_url)
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

async def main():
    stitch = StitchTool()
    async for result in stitch.call({
        "prompt": "A warm-toned roundtable discussion chat interface. Include agent selection cards, topic input, message stream with avatars and names, typing indicator, new discussion button. Warm cream and amber colors, serif for headings, clean sans-serif for body.",
        "style": "modern"
    }):
        if result.type == "result":
            data = result.data
            path = os.path.join(os.path.dirname(__file__), "..", "agent", "duoagent", "ui", "index.html")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            
            # If result has preview URL, download the actual HTML
            if data.startswith("[Stitch") or data.startswith("{"):
                # Parse JSON response to find code URL, then download
                for line in data.split("\n"):
                    try:
                        parsed = json.loads(line)
                        # Look for inner content
                        content = parsed.get("result", {}).get("content", [])
                        for item in content:
                            if item.get("type") == "text":
                                inner_raw = item.get("text", "")
                                try:
                                    inner = json.loads(inner_raw)
                                    for comp in inner.get("outputComponents", []):
                                        for screen in comp.get("design", {}).get("screens", []):
                                            code_url = screen.get("htmlCode", {}).get("downloadUrl", "")
                                            if code_url:
                                                print(f"Downloading HTML from: {code_url[:60]}...")
                                                html = await download_html(code_url)
                                                with open(path, "w") as f:
                                                    f.write(html)
                                                print(f"OK: {len(html)} chars -> {path}")
                                                return
                                except json.JSONDecodeError:
                                    pass
                    except json.JSONDecodeError:
                        pass
                # Fallback: save raw data
                with open(path, "w") as f:
                    f.write(data)
                print(f"Raw save: {len(data)} chars")
            else:
                with open(path, "w") as f:
                    f.write(data)
                print(f"OK: {len(data)} chars -> {path}")
        elif result.type == "progress":
            print(f"  {result.data[:60]}")

if __name__ == "__main__":
    asyncio.run(main())
