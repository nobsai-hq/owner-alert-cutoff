#!/usr/bin/env python3
"""Embed page.html into the Worker source."""
from pathlib import Path

here = Path(__file__).resolve().parent
html = here.joinpath("page.html").read_text()
# index.js stores HTML as a JS string. JSON stringify is a safe encoding.
js_str = __import__("json").dumps(html)
src = f'''export default {{
  async fetch(request) {{
    const url = new URL(request.url);
    if (url.pathname === "/" || url.pathname === "/index.html") {{
      return new Response(HTML, {{
        headers: {{
          "content-type": "text/html; charset=utf-8",
          "cache-control": "no-cache",
          "x-content-type-options": "nosniff",
        }},
      }});
    }}
    if (url.pathname === "/health") {{
      return new Response("ok", {{ headers: {{ "content-type": "text/plain" }} }});
    }}
    return new Response("Not found", {{ status: 404 }});
  }},
}};

const HTML = {js_str};
'''
here.joinpath("index.js").write_text(src)
print(f"Wrote {here / 'index.js'} ({len(src)} bytes)")
