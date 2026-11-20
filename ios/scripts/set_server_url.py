#!/usr/bin/env python3
"""Write the site the iOS shell loads, before Capacitor copies the config.

The iPhone app is a separate product. It must not open the production site.
"""
import json
import os
import sys
from urllib.parse import urlparse

PRODUCTION_HOST = "dristi-six.vercel.app"
DEFAULT_URL = "https://drishti-ios.vercel.app"


def resolve_url(raw):
    url = (raw or "").strip() or DEFAULT_URL
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SystemExit("IOS_SERVER_URL must be an https URL, for example https://drishti-ios.vercel.app")
    if parsed.hostname == PRODUCTION_HOST:
        raise SystemExit(
            "IOS_SERVER_URL must not be the production site "
            + PRODUCTION_HOST
            + ". Point it at the separate Vercel project deployed from this branch."
        )
    return url


def apply(path, url):
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    server = config.get("server")
    if not isinstance(server, dict):
        server = {}
        config["server"] = server
    server["url"] = url
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    return url


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: set_server_url.py capacitor.config.json")
    url = apply(argv[1], resolve_url(os.environ.get("IOS_SERVER_URL", "")))
    print("iOS shell URL: " + url)


if __name__ == "__main__":
    main(sys.argv)
