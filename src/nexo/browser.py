"""`nexo browse`: a headless browser as a command, not as an engine tool (ADR 0014).

The agent calls it through bash like any other script, so the contract is ours and it
survives a change of engine.
"""

import json
import re
import sys

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

PRICE_RE = re.compile(r"\$\s?\d{1,3}(?:\.\d{3})+")
MAX_TEXT = 6000


def browse(url: str, wait_for: str | None = None, timeout_ms: int = 30000) -> dict:
    result = {"url": url, "title": "", "text": "", "prices": [], "http_status": None, "error": None}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(
                locale="es-CL",
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36",
            )
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            result["http_status"] = response.status if response else None
            if wait_for:
                page.wait_for_selector(wait_for, timeout=timeout_ms)
            else:
                page.wait_for_timeout(2500)  # let client-side prices render
            result["url"] = page.url
            result["title"] = page.title()
            text = page.inner_text("body")
            result["prices"] = _prices(text)
            result["text"] = text[:MAX_TEXT]
        except PWTimeout as exc:
            result["error"] = f"timeout: {str(exc)[:120]}"
        except Exception as exc:  # a dead page must not kill the caller
            result["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            browser.close()
    return result


def _prices(text: str) -> list[str]:
    seen, out = set(), []
    for match in PRICE_RE.findall(text):
        clean = match.replace(" ", "")
        if clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out[:40]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: nexo browse <url> [--wait <selector>] [--text]", file=sys.stderr)
        return 2
    url = args[0]
    wait_for = args[args.index("--wait") + 1] if "--wait" in args else None
    data = browse(url, wait_for)
    if "--text" in args:
        print(data["text"])
    else:
        print(json.dumps(data, ensure_ascii=False))
    return 0 if not data["error"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
