from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Dict, List
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from openai import OpenAI

from ahca_client import fetch_page_content, search_ahca

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

_openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Server-side conversation store keyed by session ID
_conversations: Dict[str, List[dict]] = {}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a helpful documentation assistant for the Florida Agency for Health Care Administration (AHCA) — https://ahca.myflorida.com/.

AHCA oversees Florida Medicaid, health facility licensure, provider enrollment, background screening, and health quality assurance.

Your job:
1. Answer questions about AHCA programs, policies, and services.
2. Use `search_documentation` to find relevant resource pages whenever the user asks about a topic.
3. Use `fetch_page` to retrieve and summarize the actual content of a specific page when more detail is needed.
4. Always include direct clickable links to official AHCA pages.
5. Format responses with Markdown: use **bold** for key terms, bullet lists for multi-item results, and include a Sources section with links.

Be concise, accurate, and professional. If a topic is outside AHCA's scope, say so briefly."""

# ---------------------------------------------------------------------------
# OpenAI tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documentation",
            "description": (
                "Search the AHCA website for documentation, resources, or pages "
                "relevant to a topic, keyword, or question. Returns a ranked list "
                "of matching pages with titles, URLs, and categories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Topic, keyword, or question to search for (e.g. 'provider enrollment', 'background screening', 'Medicaid drug list').",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch and read the content of a specific AHCA web page. "
                "Use this to get detailed summaries, policies, or documentation "
                "from a URL returned by search_documentation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL of the AHCA page to fetch (must be an ahca.myflorida.com or related domain).",
                    }
                },
                "required": ["url"],
            },
        },
    },
]

# Domains permitted for fetch_page (defence-in-depth beyond ahca_client)
_ALLOWED_DOMAINS = {
    "ahca.myflorida.com",
    "apps.ahca.myflorida.com",
    "bi.ahca.myflorida.com",
    "quality.healthfinder.fl.gov",
    "b.apps.ahca.myflorida.com",
}


def _is_allowed_url(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in _ALLOWED_DOMAINS)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if "id" not in session:
        session["id"] = secrets.token_urlsafe(32)
    sid = session["id"]
    _conversations.setdefault(sid, [{"role": "system", "content": SYSTEM_PROMPT}])
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    sid = session.get("id")
    if not sid or sid not in _conversations:
        return jsonify({"error": "Session expired. Please refresh the page."}), 400

    history = _conversations[sid]
    history.append({"role": "user", "content": user_msg})

    try:
        # First LLM call — may request tool use
        resp = _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=history,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message

        if msg.tool_calls:
            # Record assistant turn with tool call metadata
            history.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            for tc in msg.tool_calls:
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                try:
                    if fn == "search_documentation":
                        result = search_ahca(args.get("query", ""))
                    elif fn == "fetch_page":
                        url = (args.get("url") or "").strip()
                        if not _is_allowed_url(url):
                            result = {"error": "Only AHCA-related URLs are permitted."}
                        else:
                            result = fetch_page_content(url)
                    else:
                        result = {"error": f"Unknown function: {fn}"}
                except Exception as exc:
                    logging.exception("Tool call error (%s)", fn)
                    result = {"error": str(exc)}

                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )

            # Second LLM call — generate final user-facing answer
            final = _openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=history,
            )
            answer = final.choices[0].message.content
        else:
            answer = msg.content

    except Exception as exc:
        logging.exception("OpenAI error")
        history.pop()  # Remove the failed user message so it can be retried
        return jsonify({"response": f"An error occurred: {exc}"}), 200

    history.append({"role": "assistant", "content": answer})
    return jsonify({"response": answer})


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5001)
