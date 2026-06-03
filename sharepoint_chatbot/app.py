from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Dict, List

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from openai import OpenAI

from sharepoint_client import SharePointClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

_openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# SharePoint client instance — MSAL token cache is held here
_sp_client: SharePointClient | None = None

# Server-side conversation store: {session_id: [messages]}
# (avoids hitting the 4 KB browser-cookie limit)
_conversations: Dict[str, List[dict]] = {}

# ------------------------------------------------------------------
# Prompts & tool definitions
# ------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a helpful assistant for the FX Provider Services Knowledge Hub on SharePoint. "
    "You help users discover and find documents stored in the Knowledge Hub Documents folder. "
    "Use list_files to show everything in the folder, or search_files to find documents by topic. "
    "Format file results as a Markdown list; include clickable links when URLs are available. "
    "Be concise, friendly, and professional."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List all files and folders inside the SharePoint "
                "FX Provider Services Knowledge Hub Documents folder."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search for documents in the SharePoint FX Provider Services "
                "Knowledge Hub by keyword or phrase."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The keyword or phrase to search for.",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


# ------------------------------------------------------------------
# SharePoint client helper
# ------------------------------------------------------------------

def _get_sp_client() -> SharePointClient:
    global _sp_client
    if _sp_client is None:
        _sp_client = SharePointClient(
            tenant_id=os.environ["SP_TENANT_ID"],
            client_id=os.environ["SP_CLIENT_ID"],
            username=os.environ["SP_USERNAME"],
            password=os.environ["SP_PASSWORD"],
        )
    if not _sp_client.authenticate():
        raise RuntimeError("SharePoint authentication failed. Check SP_* environment variables.")
    return _sp_client


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

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
        sp = _get_sp_client()
    except Exception as exc:
        logging.exception("SharePoint connection error")
        history.pop()  # Remove the failed user message so it can be retried
        return jsonify({"response": f"SharePoint connection error: {exc}"}), 200

    # First LLM call — may request tool use
    resp = _openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=history,
        tools=TOOLS,
        tool_choice="auto",
    )
    msg = resp.choices[0].message

    if msg.tool_calls:
        # Append assistant turn (includes tool_calls metadata)
        history.append({
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
        })

        for tc in msg.tool_calls:
            fn = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            try:
                if fn == "list_files":
                    result = sp.list_files()
                elif fn == "search_files":
                    result = sp.search_files(args.get("query", ""))
                else:
                    result = {"error": f"Unknown function: {fn}"}
            except Exception as exc:
                logging.exception("Tool call error (%s)", fn)
                result = {"error": str(exc)}

            history.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

        # Second LLM call — generate final natural-language answer
        final = _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=history,
        )
        answer = final.choices[0].message.content
    else:
        answer = msg.content

    history.append({"role": "assistant", "content": answer})
    return jsonify({"response": answer})


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
