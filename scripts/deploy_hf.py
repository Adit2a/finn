"""Deploy FINN to a Hugging Face Space (Docker, free CPU).

Uploads exactly the files tracked by git (so .env, .venv and .cache can never leak), with a
Space-specific README header. Keys are stored as Space secrets, never in files.

    HF_TOKEN=... python scripts/deploy_hf.py --space <user>/finn            # code + data
    HF_TOKEN=... python scripts/deploy_hf.py --space <user>/finn --secrets  # also (re)set API keys from .env
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

HEADER = """---
title: FINN
emoji: 📈
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Portfolio brief and promise-checking research for NSE stocks
---

"""
SECRET_KEYS = ["GEMINI_API_KEY", "GROQ_API_KEY"]  # the Space needs only the model keys

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="e.g. yourname/finn")
    ap.add_argument("--secrets", action="store_true")
    a = ap.parse_args()
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        sys.exit("HF_TOKEN missing (add it to .env or the environment)")
    api = HfApi(token=token)
    api.create_repo(a.space, repo_type="space", space_sdk="docker", exist_ok=True)

    if a.secrets:
        for k in SECRET_KEYS:
            v = os.getenv(k, "").strip()
            if v:
                api.add_space_secret(a.space, k, v)
                print(f"secret set: {k}")

    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    with tempfile.TemporaryDirectory() as tmp:
        for f in filter(None, files):
            if f.startswith(".github/"):
                continue
            dst = Path(tmp) / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / f, dst)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        (Path(tmp) / "README.md").write_text(HEADER + readme, encoding="utf-8")
        api.upload_folder(folder_path=tmp, repo_id=a.space, repo_type="space",
                          commit_message="Deploy FINN", delete_patterns=["*"])
    print(f"deployed: https://huggingface.co/spaces/{a.space}")
