"""Phone widths must not scroll sideways or clip a control off the screen."""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_screens_do_not_overflow_on_phone_widths():
    node = shutil.which("node")
    chrome = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
    if not node or not chrome:
        pytest.skip("node and Chrome are required for the layout check")
    env = os.environ.copy()
    puppeteer = "/tmp/puppeteer-core/node_modules"
    if os.path.isdir(puppeteer):
        env["NODE_PATH"] = puppeteer + (os.pathsep + env["NODE_PATH"] if env.get("NODE_PATH") else "")
    env["CHROME"] = chrome
    script = os.path.join(ROOT, "tests", "check_layout.js")
    subprocess.check_call([node, script], cwd=ROOT, env=env)
