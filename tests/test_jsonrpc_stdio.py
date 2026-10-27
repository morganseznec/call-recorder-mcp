"""The server speaks MCP over stdio and advertises the expected tools.

Runs the real entry point in a subprocess (initialize, then tools/list), so it
covers the transport and the tool declarations end to end. No audio involved.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
EXPECTED_TOOLS = {
    "start_recording",
    "recording_status",
    "stop_recording",
    "get_transcript",
    "transcribe_recording",
    "transcribe_file",
    "rename_speakers",
    "list_recordings",
    "list_audio_sources",
}


def test_initialize_and_list_tools(tmp_path):
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    env = {
        **os.environ,
        "CALL_RECORDER_DIR": str(tmp_path / "recordings"),
        "CALL_RECORDER_HOME": str(tmp_path / "home"),
        "PYTHONPATH": SRC + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", "from call_recorder_mcp.server import main; main()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        process.stdin.write("".join(json.dumps(message) + "\n" for message in messages))
        process.stdin.flush()
        responses = {}
        while 2 not in responses:
            line = process.stdout.readline()
            assert line, process.stderr.read()
            message = json.loads(line)
            responses[message.get("id")] = message
    finally:
        process.kill()
        process.wait()

    server = responses[1]["result"]["serverInfo"]
    assert server["name"] == "call-recorder"
    assert server["version"]
    tools = {tool["name"]: tool for tool in responses[2]["result"]["tools"]}
    assert set(tools) == EXPECTED_TOOLS
    assert "speakers" in tools["start_recording"]["inputSchema"]["properties"]
