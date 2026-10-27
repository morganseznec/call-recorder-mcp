"""``call-recorder-setup``: builds CallRecorder.app and downloads the models.

Run it once after installing, and again after upgrading (it rebuilds the app;
models already downloaded are kept).
"""

import argparse
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from importlib import resources

from . import __version__, config

REPOSITORY = "https://github.com/morganseznec/call-recorder-mcp"
REQUIRED_TOOLS = {
    "whisper-cli": "brew install whisper-cpp",
    "whisper-vad-speech-segments": "brew install whisper-cpp",
    "ffmpeg": "brew install ffmpeg",
    "swiftc": "xcode-select --install",
    "codesign": "xcode-select --install",
}
MINIMUM_MACOS = (14, 4)  # Core Audio process taps


class SetupError(Exception):
    pass


def check_system() -> None:
    if platform.system() != "Darwin":
        raise SetupError("call-recorder-mcp only runs on macOS.")
    version = tuple(int(part) for part in platform.mac_ver()[0].split(".")[:2])
    if version < MINIMUM_MACOS:
        required = ".".join(map(str, MINIMUM_MACOS))
        raise SetupError(f"macOS {required} or later is required (found {platform.mac_ver()[0]}).")
    missing = {tool: fix for tool, fix in REQUIRED_TOOLS.items() if not shutil.which(tool)}
    if missing:
        fixes = "\n".join(f"  {fix}" for fix in dict.fromkeys(missing.values()))
        raise SetupError(f"Missing tools: {', '.join(missing)}. Install them with:\n{fixes}")


def signing_identity() -> str:
    """First "Apple Development" identity, else ad-hoc ("-").

    With a real identity macOS remembers the permissions across rebuilds; with
    ad-hoc signing it asks again after every rebuild.
    """
    output = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"], capture_output=True, text=True
    ).stdout
    for line in output.splitlines():
        if "Apple Development" in line:
            return line.split()[1]  # SHA-1: names can be ambiguous
    return "-"


def build_app(identity: str) -> None:
    sources = resources.files(__package__) / "macos"
    staging = config.APP.with_name("CallRecorder.app.build")
    shutil.rmtree(staging, ignore_errors=True)
    binary = staging / "Contents" / "MacOS" / "CallRecorder"
    binary.parent.mkdir(parents=True)
    with resources.as_file(sources) as source_dir:
        swift = source_dir / "main.swift"
        shutil.copy(source_dir / "Info.plist", staging / "Contents" / "Info.plist")
        # Translations of the permission prompts (English is in Info.plist).
        for strings in source_dir.glob("*.lproj"):
            shutil.copytree(strings, staging / "Contents" / "Resources" / strings.name)
        # Compiler output (warnings included) is only shown if the build fails.
        subprocess.run(
            ["swiftc", "-O", "-swift-version", "5", "-o", str(binary), str(swift)],
            check=True,
            capture_output=True,
            text=True,
        )
    subprocess.run(
        ["codesign", "--force", "--sign", identity, str(staging)], check=True, capture_output=True, text=True
    )
    shutil.rmtree(config.APP, ignore_errors=True)
    staging.rename(config.APP)


def _progress(name: str):
    shown = -1

    def hook(blocks: int, block_size: int, total: int) -> None:
        nonlocal shown
        if total > 0:
            percent = min(100, blocks * block_size * 100 // total) // 10 * 10
            if percent != shown:
                shown = percent
                print(f"\r  {name}: {percent}%", end="", flush=True)

    return hook


def download_models() -> None:
    config.MODELS.mkdir(parents=True, exist_ok=True)
    for target, url in config.DOWNLOADS.items():
        name = url.rsplit("/", 1)[-1]
        if target.exists():
            print(f"  {name}: already downloaded")
            continue
        partial = config.MODELS / f"{name}.part"
        urllib.request.urlretrieve(url, partial, _progress(name))
        print()
        if name.endswith(".tar.bz2"):
            with tarfile.open(partial) as archive:
                archive.extractall(config.MODELS, filter="data")
            partial.unlink()
        else:
            partial.rename(target)


def install_vocabulary() -> None:
    if config.VOCABULARY.exists():
        return
    config.VOCABULARY.parent.mkdir(parents=True, exist_ok=True)
    example = resources.files(__package__) / "vocabulary.example.txt"
    config.VOCABULARY.write_text(example.read_text())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="call-recorder-setup", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--skip-models", action="store_true", help="do not download the models (about 650 MB)")
    parser.add_argument(
        "--sign-identity",
        default=config.SIGN_IDENTITY,
        help='codesign identity for the app (default: first "Apple Development" identity, else ad-hoc)',
    )
    args = parser.parse_args(argv)
    config.extend_path()
    try:
        check_system()
        identity = args.sign_identity or signing_identity()
        print(f"Building {config.APP} (signed with {'ad-hoc signature' if identity == '-' else identity})")
        build_app(identity)
        if not args.skip_models:
            print(f"Downloading models to {config.MODELS}")
            download_models()
        install_vocabulary()
    except SetupError as error:
        sys.exit(f"error: {error}")
    except subprocess.CalledProcessError as error:
        sys.exit(f"error: {' '.join(map(str, error.cmd))} failed\n{error.stderr or ''}")

    print(
        f"\nReady. Recordings go to {config.RECORDINGS}, your vocabulary is {config.VOCABULARY}.\n"
        "Register the server in Claude Code:\n\n"
        f"  claude mcp add call-recorder -s user -- uvx --from git+{REPOSITORY} call-recorder-mcp\n\n"
        "macOS asks for the microphone and audio recording permissions on the first recording."
    )
