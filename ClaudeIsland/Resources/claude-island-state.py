#!/usr/bin/env python3
"""
Claude Island Hook
- Sends session state to ClaudeIsland.app via Unix socket (local)
- Or via TCP socket for remote SSH sessions
- For PermissionRequest: waits for user decision from the app

Remote session usage:
  Set CLAUDE_ISLAND_HOST environment variable to your Mac's IP/hostname
  Optionally set CLAUDE_ISLAND_PORT (default: 52945)

  Example: export CLAUDE_ISLAND_HOST=192.168.1.100
"""
import json
import os
import socket
import sys
from pathlib import Path

SOCKET_PATH = "/tmp/claude-island.sock"
DEFAULT_TCP_PORT = 52945
TIMEOUT_SECONDS = 300  # 5 minutes for permission decisions


def get_connection_config():
    """Determine whether to use Unix socket or TCP based on environment"""
    remote_host = os.environ.get("CLAUDE_ISLAND_HOST")
    if remote_host:
        port = int(os.environ.get("CLAUDE_ISLAND_PORT", DEFAULT_TCP_PORT))
        return ("tcp", remote_host, port)
    return ("unix", SOCKET_PATH, None)


def is_remote_session():
    """Check if this is a remote session"""
    return os.environ.get("CLAUDE_ISLAND_HOST") is not None


def get_jsonl_path(session_id, cwd):
    """Get the path to the session's JSONL file"""
    home = Path.home()
    claude_dir = home / ".claude" / "projects"

    # The project directory is based on the cwd, encoded
    # Claude uses a hash or encoding of the path
    if cwd:
        # Try to find the project directory
        cwd_encoded = cwd.replace("/", "-").strip("-")
        # Also try just the last component
        cwd_last = Path(cwd).name if cwd else ""

        # Search for matching project directories
        if claude_dir.exists():
            for project_dir in claude_dir.iterdir():
                if project_dir.is_dir():
                    jsonl_file = project_dir / f"{session_id}.jsonl"
                    if jsonl_file.exists():
                        return jsonl_file

    return None


def parse_jsonl_messages(jsonl_path, limit=10):
    """Parse the last N messages from a JSONL file"""
    if not jsonl_path or not jsonl_path.exists():
        return []

    messages = []
    try:
        with open(jsonl_path, "r") as f:
            lines = f.readlines()

        for line in lines[-100:]:  # Check last 100 lines for messages
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                msg_type = entry.get("type")

                if msg_type == "user":
                    # User message
                    content = entry.get("message", {})
                    if isinstance(content, dict):
                        text = content.get("content", "")
                    else:
                        text = str(content)
                    if text:
                        messages.append({"role": "user", "content": text})

                elif msg_type == "assistant":
                    # Assistant message
                    content = entry.get("message", {})
                    if isinstance(content, dict):
                        # Extract text from content blocks
                        blocks = content.get("content", [])
                        if isinstance(blocks, list):
                            text_parts = []
                            for block in blocks:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text_parts.append(block.get("text", ""))
                            text = "\n".join(text_parts)
                        else:
                            text = str(blocks)
                    else:
                        text = str(content)
                    if text:
                        messages.append({"role": "assistant", "content": text})

            except json.JSONDecodeError:
                continue
    except Exception:
        pass

    # Return last N messages
    return messages[-limit:] if messages else []


def get_tty():
    """Get the TTY of the Claude process (parent)"""
    import subprocess

    # Get parent PID (Claude process)
    ppid = os.getppid()

    # Try to get TTY from ps command for the parent process
    try:
        result = subprocess.run(
            ["ps", "-p", str(ppid), "-o", "tty="],
            capture_output=True,
            text=True,
            timeout=2
        )
        tty = result.stdout.strip()
        if tty and tty != "??" and tty != "-":
            # ps returns just "ttys001", we need "/dev/ttys001"
            if not tty.startswith("/dev/"):
                tty = "/dev/" + tty
            return tty
    except Exception:
        pass

    # Fallback: try current process stdin/stdout
    try:
        return os.ttyname(sys.stdin.fileno())
    except (OSError, AttributeError):
        pass
    try:
        return os.ttyname(sys.stdout.fileno())
    except (OSError, AttributeError):
        pass
    return None


def send_event(state):
    """Send event to app, return response if any"""
    try:
        conn_type, host_or_path, port = get_connection_config()

        if conn_type == "tcp":
            # TCP socket for remote sessions
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TIMEOUT_SECONDS)
            sock.connect((host_or_path, port))
        else:
            # Unix socket for local sessions
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(TIMEOUT_SECONDS)
            sock.connect(host_or_path)

        sock.sendall(json.dumps(state).encode())

        # For permission requests, wait for response
        if state.get("status") == "waiting_for_approval":
            response = sock.recv(4096)
            sock.close()
            if response:
                return json.loads(response.decode())
        else:
            sock.close()

        return None
    except (socket.error, OSError, json.JSONDecodeError):
        return None


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(1)

    session_id = data.get("session_id", "unknown")
    event = data.get("hook_event_name", "")
    cwd = data.get("cwd", "")
    tool_input = data.get("tool_input", {})

    # Get process info
    claude_pid = os.getppid()
    tty = get_tty()

    # Build state object
    state = {
        "session_id": session_id,
        "cwd": cwd,
        "event": event,
        "pid": claude_pid,
        "tty": tty,
    }

    # Map events to status
    if event == "UserPromptSubmit":
        # User just sent a message - Claude is now processing
        state["status"] = "processing"
        # Include user message for remote sessions
        user_message = data.get("message") or data.get("prompt") or data.get("user_prompt")
        if user_message:
            state["message"] = user_message

    elif event == "PreToolUse":
        state["status"] = "running_tool"
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input
        # Send tool_use_id to Swift for caching
        tool_use_id_from_event = data.get("tool_use_id")
        if tool_use_id_from_event:
            state["tool_use_id"] = tool_use_id_from_event

    elif event == "PostToolUse":
        state["status"] = "processing"
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input
        # Send tool_use_id so Swift can cancel the specific pending permission
        tool_use_id_from_event = data.get("tool_use_id")
        if tool_use_id_from_event:
            state["tool_use_id"] = tool_use_id_from_event

    elif event == "PermissionRequest":
        # This is where we can control the permission
        state["status"] = "waiting_for_approval"
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input
        # tool_use_id lookup handled by Swift-side cache from PreToolUse

        # Send to app and wait for decision
        response = send_event(state)

        if response:
            decision = response.get("decision", "ask")
            reason = response.get("reason", "")

            if decision == "allow":
                # Output JSON to approve
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {"behavior": "allow"},
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

            elif decision == "deny":
                # Output JSON to deny
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {
                            "behavior": "deny",
                            "message": reason or "Denied by user via ClaudeIsland",
                        },
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

        # No response or "ask" - let Claude Code show its normal UI
        sys.exit(0)

    elif event == "Notification":
        notification_type = data.get("notification_type")
        # Skip permission_prompt - PermissionRequest hook handles this with better info
        if notification_type == "permission_prompt":
            sys.exit(0)
        elif notification_type == "idle_prompt":
            state["status"] = "waiting_for_input"
        else:
            state["status"] = "notification"
        state["notification_type"] = notification_type
        state["message"] = data.get("message")

    elif event == "Stop":
        state["status"] = "waiting_for_input"
        # Include stop reason/message if available
        stop_reason = data.get("stop_reason") or data.get("message")
        if stop_reason:
            state["message"] = stop_reason

    elif event == "SubagentStop":
        # SubagentStop fires when a subagent completes - usually means back to waiting
        state["status"] = "waiting_for_input"

    elif event == "SessionStart":
        # New session starts waiting for user input
        state["status"] = "waiting_for_input"

    elif event == "SessionEnd":
        state["status"] = "ended"

    elif event == "PreCompact":
        # Context is being compacted (manual or auto)
        state["status"] = "compacting"

    else:
        state["status"] = "unknown"

    # For remote sessions, include conversation content on key events
    if is_remote_session() and event in ("Stop", "UserPromptSubmit", "SessionStart", "Notification"):
        jsonl_path = get_jsonl_path(session_id, cwd)
        if jsonl_path:
            messages = parse_jsonl_messages(jsonl_path, limit=20)
            if messages:
                state["conversation"] = messages

    # Send to socket (fire and forget for non-permission events)
    send_event(state)


if __name__ == "__main__":
    main()
