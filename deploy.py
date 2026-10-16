"""Laptop-side deploy. Creates the parent sandbox once, then reattaches by ID.

    python deploy.py              upload, (re)install if requirements changed, restart db + web
    python deploy.py --netcheck   run netcheck.sh inside the parent
    python deploy.py --smoke      run smoke.py inside the parent
    python deploy.py --logs       tail web + db logs
    python deploy.py --shell CMD  run one command in the parent's app dir
"""
import hashlib
import hmac
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaNotFoundError, FileUpload, SessionExecuteRequest

ROOT = Path(__file__).parent
NAME = "incentive-parent"  # reattach by name, so a fresh container (cloud session) finds the same parent
SKIP_DIRS = {".git", "__pycache__", ".venv", "data"}
SKIP_FILES = {"deploy.py"}
SURREAL_VERSION = "3.3.2"


def env_value(key, default=""):
    # APP_<KEY> wins: lets a host that reserves a name (e.g. ANTHROPIC_API_KEY in Claude Code) pass it through.
    v = os.environ.get(f"APP_{key}") or os.environ.get(key) or default
    if key == "CPA_TOOL_SECRET" and not v and os.environ.get("DAYTONA_API_KEY"):
        # Stable across fresh containers without another secret to manage.
        v = hmac.new(os.environ["DAYTONA_API_KEY"].encode(), b"cpa-tool-secret", hashlib.sha256).hexdigest()[:32]
    return v


def ensure_env_file():
    """No .env (e.g. a fresh cloud session): build it from process env, using .env.example's names and defaults."""
    env = ROOT / ".env"
    if env.exists():
        return
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        key, sep, default = line.partition("=")
        if sep and not key.startswith("#"):
            lines.append(f"{key}={env_value(key, default)}")
    env.write_text("\n".join(lines) + "\n")
    print("wrote .env from environment variables")


ensure_env_file()
load_dotenv(ROOT / ".env")


def files():
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if p.is_file() and not SKIP_DIRS & set(rel.parts) and rel.name not in SKIP_FILES:
            yield rel


def sh(sb, cmd, timeout=60):
    r = sb.process.exec(cmd, cwd=APP, timeout=timeout)
    return r.exit_code, r.result


def restart(sb, name, cmd):
    try:
        sb.process.delete_session(name)
    except Exception:
        pass  # session doesn't exist after a fresh create or a sandbox restart
    sb.process.create_session(name)
    sb.process.execute_session_command(name, SessionExecuteRequest(command=f"cd {APP} && {cmd}", run_async=True))


d = Daytona()
try:
    sb = d.get(NAME)
    if str(sb.state).lower().rsplit(".", 1)[-1] in ("stopped", "archived"):
        sb.start()
except DaytonaNotFoundError:
    sb = d.create(CreateSandboxFromSnapshotParams(
        name=NAME,
        language="python",
        public=True,           # preview link reachable without x-daytona-preview-token (ElevenLabs webhook)
        auto_stop_interval=0,  # survive the demo
        labels={"role": "parent", "app": "incentive-agent"},
    ), timeout=180)

APP = f"{sb.get_work_dir().rstrip('/')}/app"

if "--logs" in sys.argv:
    print(sh(sb, "tail -n 80 web.log; echo ---- db; tail -n 20 db.log")[1])
    sys.exit()
if "--shell" in sys.argv:
    print(sh(sb, sys.argv[sys.argv.index("--shell") + 1], timeout=300)[1])
    sys.exit()

url = sb.get_preview_link(8000).url.rstrip("/")


def content(rel):
    data = (ROOT / rel).read_bytes()
    if str(rel) != ".env":
        return data
    lines = [l for l in data.decode().splitlines() if not l.startswith("PUBLIC_BASE_URL=")]
    return ("\n".join(lines + [f"PUBLIC_BASE_URL={url}"]) + "\n").encode()


rels = list(files())
sb.process.exec("mkdir -p " + " ".join(sorted({f"{APP}/{r.parent}" for r in rels})))
sb.fs.upload_files([FileUpload(source=content(r), destination=f"{APP}/{r}") for r in rels])
print(f"uploaded {len(rels)} files to {APP}")

missing = [k for k in ("DAYTONA_API_KEY", "ANTHROPIC_API_KEY", "ANCHOR_API_KEY") if not os.getenv(k)]
if missing:
    print(f"WARNING: unset in .env: {missing}")
if "--print-tool-secret" in sys.argv:
    print("CPA_TOOL_SECRET for the ElevenLabs X-Tool-Secret header:", os.getenv("CPA_TOOL_SECRET"))

if "--netcheck" in sys.argv:
    code, out = sh(sb, "bash netcheck.sh")
    print(out)
    sys.exit(code)

req_hash = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
_, remote_hash = sh(sb, "cat .req_hash 2>/dev/null")
if remote_hash.strip() != req_hash:
    code, out = sh(sb, f"pip install -q -r requirements.txt && echo {req_hash} > .req_hash", timeout=600)
    print(out[-2000:])
    if code:
        sys.exit(f"pip install failed ({code})")

code, _ = sh(sb, "test -x bin/surreal")
if code:
    code, out = sh(sb, f"curl -sSf https://install.surrealdb.com | sh -s -- --version {SURREAL_VERSION} {APP}/bin", timeout=300)
    print(out[-1000:])

if "--smoke" in sys.argv:
    code, out = sh(sb, "python smoke.py", timeout=600)
    print(out)
    sys.exit(code)

code, _ = sh(sb, "curl -sf http://127.0.0.1:8001/health")
if code:
    restart(sb, "db", "mkdir -p data && bin/surreal start --user root --pass root --bind 127.0.0.1:8001 surrealkv://data/db > db.log 2>&1")

restart(sb, "web", "python app.py > web.log 2>&1")

print(sb.id, url)
print(f"ElevenLabs tool URL: {url}/api/cpa_tool   post-call webhook: {url}/api/elevenlabs/postcall")
