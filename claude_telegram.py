#!/usr/bin/env python3
"""
Telegram ↔ AI bridge — unified CLI for listening, sending, and replying.

Usage:
    python3 claude_telegram.py listen                           # long-polling daemon
    python3 claude_telegram.py send <chat_id> <message>         # send a message
    python3 claude_telegram.py reply [--model haiku] [--dry-run] # standalone LLM reply
"""

import argparse
import json
import os
import sys
import time
import requests
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional — env vars can be set manually

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
INBOX_FILE = BASE_DIR / "telegram_inbox.jsonl"
OFFSET_FILE = BASE_DIR / ".telegram_offset"

# ── Config (loaded after argparse so --help works without a token) ───────────

SYSTEM_PROMPT = (
    "You are a helpful assistant replying to Telegram messages. "
    "Be concise, friendly, and conversational. "
    "If asked to search for information, provide what you know. "
    "Keep responses brief and suitable for a mobile chat."
)

MODEL_ALIASES = {
    "openai-mini":  ("openai",    "gpt-5-mini"),
    "openai-5":     ("openai",    "gpt-5"),
    "haiku":        ("anthropic", "claude-haiku-4-5"),
    "sonnet":       ("anthropic", "claude-sonnet-4-6"),
    "opus":         ("anthropic", "claude-opus-4-6"),
}

DEFAULT_MODEL = "haiku"

# ── Telegram helpers ─────────────────────────────────────────────────────────

def _telegram_api():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("Error: TELEGRAM_BOT_TOKEN not set. Add it to .env or export it.")
    return f"https://api.telegram.org/bot{token}"


def telegram_send(chat_id: int, text: str):
    """Send a message, splitting into chunks if it exceeds Telegram's 4096 char limit."""
    api = _telegram_api()
    for i in range(0, len(text), 4096):
        r = requests.post(
            f"{api}/sendMessage",
            json={"chat_id": chat_id, "text": text[i:i+4096]},
            timeout=10,
        )
        r.raise_for_status()

# ── Listener ─────────────────────────────────────────────────────────────────

def load_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def save_offset(offset: int):
    OFFSET_FILE.write_text(str(offset))


def get_updates(api: str, offset: int) -> list:
    try:
        resp = requests.get(
            f"{api}/getUpdates",
            params={"timeout": 30, "offset": offset},
            timeout=35,
        )
        data = resp.json()
        return data.get("result", []) if data.get("ok") else []
    except Exception as e:
        print(f"Poll error: {e}")
        return []


def cmd_listen(_args):
    """Long-polling listener — writes incoming messages to telegram_inbox.jsonl."""
    api = _telegram_api()
    offset = load_offset()
    print(f"Listener started. Offset={offset}. Writing to {INBOX_FILE}")
    while True:
        try:
            updates = get_updates(api, offset)
            for update in updates:
                offset = update["update_id"] + 1
                save_offset(offset)
                msg = update.get("message") or update.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                entry = {
                    "update_id": update["update_id"],
                    "chat_id": msg["chat"]["id"],
                    "from": msg.get("from", {}).get("first_name", "unknown"),
                    "text": msg["text"],
                    "time": datetime.now().isoformat(),
                    "replied": False,
                }
                with open(INBOX_FILE, "a") as f:
                    f.write(json.dumps(entry) + "\n")
                print(f"[NEW] {entry['from']}: {entry['text']!r}")
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

# ── Send ─────────────────────────────────────────────────────────────────────

def cmd_send(args):
    """Send a message to a Telegram chat."""
    text = " ".join(args.message).replace("\\n", "\n")
    telegram_send(args.chat_id, text)
    print(f"Sent to {args.chat_id}.")

# ── Reply (standalone LLM) ──────────────────────────────────────────────────

def reply_anthropic(model_id: str, conversation: list[dict]) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit("Error: ANTHROPIC_API_KEY not set.")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model_id,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=conversation,
    )
    return next((b.text for b in resp.content if b.type == "text"), "")


def reply_openai(model_id: str, conversation: list[dict]) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise SystemExit("Error: OPENAI_API_KEY not set.")
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation
    resp = client.chat.completions.create(
        model=model_id,
        messages=messages,
        max_tokens=1024,
    )
    return resp.choices[0].message.content or ""


def generate_reply(provider: str, model_id: str, history: list[dict], user_text: str) -> str:
    conversation = history + [{"role": "user", "content": user_text}]
    if provider == "anthropic":
        return reply_anthropic(model_id, conversation)
    elif provider == "openai":
        return reply_openai(model_id, conversation)
    else:
        raise ValueError(f"Unknown provider: {provider}")


def load_inbox() -> list[dict]:
    if not INBOX_FILE.exists():
        return []
    messages = []
    for line in INBOX_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return messages


def save_inbox(messages: list[dict]):
    with open(INBOX_FILE, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


def build_history(messages: list[dict], chat_id: int) -> list[dict]:
    """Build conversation history for a chat from replied messages."""
    history = []
    for msg in messages:
        if msg["chat_id"] != chat_id or not msg.get("replied"):
            continue
        history.append({"role": "user", "content": msg["text"]})
        if "reply" in msg:
            history.append({"role": "assistant", "content": msg["reply"]})
    return history[-20:]  # keep last 10 exchanges


def cmd_reply(args):
    """Process unread messages and reply using an LLM."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Auto-fallback: if haiku requested but no Anthropic key, use gpt-5-mini
    chosen = args.model
    if chosen == "haiku" and not anthropic_key:
        print("No ANTHROPIC_API_KEY found — falling back to gpt-5-mini.")
        chosen = "openai-mini"

    provider, model_id = MODEL_ALIASES[chosen]

    messages = load_inbox()
    unread = [m for m in messages if not m.get("replied")]

    if not unread:
        print("No unread messages.")
        return

    changed = False
    for msg in messages:
        if not msg.get("replied"):
            chat_id = msg["chat_id"]
            user_text = msg["text"]
            history = build_history(messages, chat_id)

            print(f"[{msg['from']}] {user_text!r}")
            reply_text = generate_reply(provider, model_id, history, user_text)
            print(f"  → {reply_text[:80]}...")

            if not args.dry_run:
                telegram_send(chat_id, reply_text)
                msg["replied"] = True
                msg["reply"] = reply_text
                msg["reply_model"] = f"{provider}/{model_id}"
                changed = True

    if changed:
        save_inbox(messages)
        print(f"Done. {len(unread)} message(s) replied using {provider}/{model_id}.")

# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Telegram ↔ AI bridge — listen, send, and reply.",
        prog="claude_telegram.py",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # listen
    sub.add_parser("listen", help="Start long-polling listener")

    # send
    p_send = sub.add_parser("send", help="Send a message to a Telegram chat")
    p_send.add_argument("chat_id", type=int, help="Telegram chat ID")
    p_send.add_argument("message", nargs="+", help="Message text (words are joined)")

    # reply
    p_reply = sub.add_parser("reply", help="Reply to unread messages using an LLM")
    p_reply.add_argument(
        "--model", default=DEFAULT_MODEL,
        choices=list(MODEL_ALIASES.keys()),
        help=f"Model to use (default: {DEFAULT_MODEL})",
    )
    p_reply.add_argument("--dry-run", action="store_true", help="Print replies without sending")

    args = parser.parse_args()

    if args.command == "listen":
        cmd_listen(args)
    elif args.command == "send":
        cmd_send(args)
    elif args.command == "reply":
        cmd_reply(args)


if __name__ == "__main__":
    main()
