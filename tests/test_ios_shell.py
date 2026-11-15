"""The iOS shell URL and the generated Xcode project stay off the production site."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SET_URL = ROOT / "ios" / "scripts" / "set_server_url.py"
PREPARE = ROOT / "ios" / "scripts" / "prepare_xcode.py"
ADDITIONS = ROOT / "ios" / "Info.plist.additions.xml"
SWIFT = ROOT / "ios" / "plugins" / "DrishtiNative" / "DrishtiStore.swift"
WORKFLOW = ROOT / ".github" / "workflows" / "ios.yml"
README = ROOT / "ios" / "README.md"


def run_set_url(tmp_path, raw):
    config = tmp_path / "capacitor.config.json"
    config.write_text(json.dumps({"appId": "com.ikshana.drishti", "server": {"url": "https://example.invalid"}}), encoding="utf-8")
    env = os.environ.copy()
    if raw is None:
        env.pop("IOS_SERVER_URL", None)
    else:
        env["IOS_SERVER_URL"] = raw
    result = subprocess.run([sys.executable, str(SET_URL), str(config)], capture_output=True, text=True, env=env)
    return result, json.loads(config.read_text(encoding="utf-8"))


def test_server_url_defaults_away_from_production(tmp_path):
    result, config = run_set_url(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert config["server"]["url"] == "https://drishti-ios.vercel.app"
    refused, _ = run_set_url(tmp_path, "https://dristi-six.vercel.app/ask")
    assert refused.returncode != 0
    assert "dristi-six.vercel.app" in refused.stderr


def test_server_url_uses_the_repository_variable(tmp_path):
    result, config = run_set_url(tmp_path, "https://drishti-ios-preview.vercel.app")
    assert result.returncode == 0, result.stderr
    assert config["server"]["url"] == "https://drishti-ios-preview.vercel.app"


PBX = textwrap.dedent("""\
    /* Begin PBXBuildFile section */
    \t\t504EC3081FED79650016851F /* AppDelegate.swift in Sources */ = {isa = PBXBuildFile; fileRef = 504EC3071FED79650016851F /* AppDelegate.swift */; };
    /* End PBXBuildFile section */

    /* Begin PBXFileReference section */
    \t\t504EC3071FED79650016851F /* AppDelegate.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = AppDelegate.swift; sourceTree = "<group>"; };
    /* End PBXFileReference section */

    /* Begin PBXGroup section */
    \t\t504EC3061FED79650016851F /* App */ = {
    \t\t\tisa = PBXGroup;
    \t\t\tchildren = (
    \t\t\t\t504EC3071FED79650016851F /* AppDelegate.swift */,
    \t\t\t\t504EC3131FED79650016851F /* Info.plist */,
    \t\t\t);
    \t\t\tpath = App;
    \t\t\tsourceTree = "<group>";
    \t\t};
    /* End PBXGroup section */

    /* Begin PBXSourcesBuildPhase section */
    \t\t504EC3001FED79650016851F /* Sources */ = {
    \t\t\tisa = PBXSourcesBuildPhase;
    \t\t\tfiles = (
    \t\t\t\t504EC3081FED79650016851F /* AppDelegate.swift in Sources */,
    \t\t\t);
    \t\t\trunOnlyForDeploymentPostprocessing = 0;
    \t\t};
    /* End PBXSourcesBuildPhase section */

    \t\t\tIPHONEOS_DEPLOYMENT_TARGET = 13.0;
    """)


def _project(tmp_path):
    app = tmp_path / "App"
    (app / "App").mkdir(parents=True)
    (app / "App.xcodeproj").mkdir()
    info = {
        "CFBundleVersion": "$(CURRENT_PROJECT_VERSION)",
        "CFBundleDisplayName": "Drishti",
    }
    with (app / "App" / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    (app / "App.xcodeproj" / "project.pbxproj").write_text(PBX, encoding="utf-8")
    (app / "Podfile").write_text("platform :ios, '13.0'\n", encoding="utf-8")
    return app


def test_prepare_adds_the_plugin_privacy_keys_and_build_number(tmp_path):
    app = _project(tmp_path)
    result = subprocess.run(
        [
            sys.executable, str(PREPARE), str(app),
            "--additions", str(ADDITIONS),
            "--swift", str(SWIFT),
            "--build-number", "42",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    project = (app / "App.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    assert "DrishtiStore.swift in Sources" in project
    assert "IPHONEOS_DEPLOYMENT_TARGET = 15.0;" in project
    assert "IPHONEOS_DEPLOYMENT_TARGET = 13.0;" not in project
    assert (app / "App" / "DrishtiStore.swift").is_file()
    with (app / "App" / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    assert "microphone" in info["NSMicrophoneUsageDescription"]
    assert info["UIBackgroundModes"] == ["audio", "voip"]
    assert info["CFBundleVersion"] == "42"
    assert info["CFBundleDisplayName"] == "Drishti"
    assert "15.0" in (app / "Podfile").read_text(encoding="utf-8")
    again = subprocess.run(
        [sys.executable, str(PREPARE), str(app), "--additions", str(ADDITIONS), "--swift", str(SWIFT)],
        capture_output=True, text=True,
    )
    assert again.returncode == 0, again.stderr
    prepared = (app / "App.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    assert prepared.count("DrishtiStore.swift in Sources") == 2


def test_committed_shell_url_is_not_production():
    config = json.loads((ROOT / "ios" / "capacitor.config.json").read_text(encoding="utf-8"))
    assert config["server"]["url"] == "https://drishti-ios.vercel.app"


def test_prepare_matches_the_capacitor_template():
    template = Path("/tmp/ios-template/App")
    if not (template / "App.xcodeproj" / "project.pbxproj").is_file():
        import pytest
        pytest.skip("Capacitor iOS template is not extracted on this machine")
    dest = Path("/tmp/ios-prepare-check")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(template, dest)
    result = subprocess.run(
        [sys.executable, str(PREPARE), str(dest), "--additions", str(ADDITIONS), "--swift", str(SWIFT), "--build-number", "7"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    project = (dest / "App.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    assert project.count("DrishtiStore.swift in Sources") == 2


def test_signed_job_uses_api_key_signing_and_skips_without_secrets():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for phrase in (
        "workflow_dispatch:",
        "vars.IOS_SERVER_URL",
        "secrets.APPLE_TEAM_ID",
        "secrets.APPSTORE_API_KEY_ID",
        "secrets.APPSTORE_API_ISSUER_ID",
        "secrets.APPSTORE_API_KEY",
        "ready=false",
        "-allowProvisioningUpdates",
        "-authenticationKeyPath",
        "-authenticationKeyID",
        "-authenticationKeyIssuerID",
        "CODE_SIGN_STYLE=Automatic",
        "DEVELOPMENT_TEAM=",
        "app-store-connect",
        "signingStyle",
        "github.run_number",
        "xcrun altool --upload-app",
        "DrishtiStore.swift",
    ):
        assert phrase in workflow, phrase
    assert "vars.APPLE_TEAM_ID" not in workflow
    assert "APPLE_CERTIFICATE_P12" not in workflow
    assert "APPLE_CERTIFICATE_PASSWORD" not in workflow
    assert "APPLE_PROVISION_PROFILE" not in workflow
    assert "CODE_SIGN_STYLE=Manual" not in workflow
    for gone in ("APPLE_CERTIFICATE_P12", "APPLE_CERTIFICATE_PASSWORD", "APPLE_PROVISION_PROFILE"):
        assert gone not in readme
    assert "App Manager" in readme
    assert "com.ikshana.drishti" in readme
    assert "IOS_SERVER_URL" in readme
    assert "drishti-ios" in readme
