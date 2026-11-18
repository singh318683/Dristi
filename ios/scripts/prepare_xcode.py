#!/usr/bin/env python3
"""Finish the generated Capacitor project: privacy keys, native source, build number.

Runs after `npx cap add ios`, in both the unsigned and signed jobs.
"""
import argparse
import plistlib
import shutil
from pathlib import Path

FILE_REF_ID = "D51571051FED79650016851F"
BUILD_ID = "D51571061FED79650016851F"
SOURCE_NAME = "DrishtiStore.swift"
BRIDGE_REF_ID = "D51571071FED79650016851F"
BRIDGE_BUILD_ID = "D51571081FED79650016851F"
BRIDGE_NAME = "DrishtiBridgeViewController.swift"
DEPLOYMENT = "15.0"


def additions(path):
    raw = Path(path).read_text(encoding="utf-8")
    start = raw.index("<dict>")
    end = raw.rindex("</dict>") + len("</dict>")
    wrapped = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        + raw[start:end]
        + "\n</plist>\n"
    )
    return plistlib.loads(wrapped.encode("utf-8"))


def merge_plist(plist_path, extra, build_number):
    with open(plist_path, "rb") as handle:
        info = plistlib.load(handle)
    for key, value in extra.items():
        if key == "UIBackgroundModes" and isinstance(info.get(key), list):
            for mode in value:
                if mode not in info[key]:
                    info[key].append(mode)
        else:
            info[key] = value
    if build_number:
        info["CFBundleVersion"] = str(build_number)
    with open(plist_path, "wb") as handle:
        plistlib.dump(info, handle)


def _insert_after(text, needle, addition):
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if needle in line:
            lines.insert(index + 1, addition if addition.endswith("\n") else addition + "\n")
            return "".join(lines)
    raise SystemExit("project.pbxproj has no line containing " + needle)


def add_named_source(project, source_name, file_ref_id, build_id):
    if source_name + " in Sources" in project:
        return project
    build_line = (
        "\t\t" + build_id + " /* " + source_name + " in Sources */ = {isa = PBXBuildFile; "
        "fileRef = " + file_ref_id + " /* " + source_name + " */; };\n"
    )
    ref_line = (
        "\t\t" + file_ref_id + " /* " + source_name + " */ = {isa = PBXFileReference; "
        "lastKnownFileType = sourcecode.swift; path = " + source_name + "; sourceTree = \"<group>\"; };\n"
    )
    project = _insert_after(project, "/* AppDelegate.swift in Sources */ = {isa = PBXBuildFile;", build_line)
    project = _insert_after(project, "/* AppDelegate.swift */ = {isa = PBXFileReference;", ref_line)
    lines = project.splitlines(keepends=True)
    inserted = False
    for index, line in enumerate(lines):
        if line.strip() != "path = App;":
            continue
        close = index - 1
        while close >= 0 and lines[close].strip() == "":
            close -= 1
        if close < 0 or ");" not in lines[close]:
            raise SystemExit("project.pbxproj App group did not close its children")
        lines.insert(close, "\t\t\t\t" + file_ref_id + " /* " + source_name + " */,\n")
        inserted = True
        break
    if not inserted:
        raise SystemExit("project.pbxproj has no App group")
    project = "".join(lines)
    start = project.find("/* Begin PBXSourcesBuildPhase section */")
    end = project.find("/* End PBXSourcesBuildPhase section */")
    if start < 0 or end < 0:
        raise SystemExit("project.pbxproj has no sources build phase")
    section = project[start:end]
    marker = "/* AppDelegate.swift in Sources */,"
    at = section.find(marker)
    if at < 0:
        raise SystemExit("project.pbxproj sources phase has no AppDelegate.swift")
    line_end = section.find("\n", at)
    source_line = "\n\t\t\t\t" + build_id + " /* " + source_name + " in Sources */,"
    section = section[:line_end] + source_line + section[line_end:]
    return project[:start] + section + project[end:]


def add_source(project):
    project = add_named_source(project, SOURCE_NAME, FILE_REF_ID, BUILD_ID)
    return add_named_source(project, BRIDGE_NAME, BRIDGE_REF_ID, BRIDGE_BUILD_ID)


def use_inline_playback(xcode_dir):
    storyboard = Path(xcode_dir) / "App" / "Base.lproj" / "Main.storyboard"
    if not storyboard.is_file():
        return
    text = storyboard.read_text(encoding="utf-8")
    if "DrishtiBridgeViewController" in text:
        return
    pair = 'customClass="CAPBridgeViewController" customModule="Capacitor"'
    if pair in text:
        text = text.replace(pair, 'customClass="DrishtiBridgeViewController" customModule="App"', 1)
    elif 'customClass="CAPBridgeViewController"' in text:
        text = text.replace(
            'customClass="CAPBridgeViewController"',
            'customClass="DrishtiBridgeViewController" customModule="App"',
            1,
        )
    else:
        raise SystemExit("Main.storyboard has no CAPBridgeViewController")
    storyboard.write_text(text, encoding="utf-8")


def prepare(xcode_dir, additions_path, swift_path, build_number):
    xcode_dir = Path(xcode_dir)
    app_dir = xcode_dir / "App"
    plist_path = app_dir / "Info.plist"
    project_path = xcode_dir / "App.xcodeproj" / "project.pbxproj"
    if not plist_path.is_file() or not project_path.is_file():
        raise SystemExit("not a Capacitor App project: " + str(xcode_dir))
    merge_plist(plist_path, additions(additions_path), build_number)
    shutil.copyfile(swift_path, app_dir / SOURCE_NAME)
    bridge_path = Path(swift_path).with_name(BRIDGE_NAME)
    if not bridge_path.is_file():
        raise SystemExit("missing " + str(bridge_path))
    shutil.copyfile(bridge_path, app_dir / BRIDGE_NAME)
    project = project_path.read_text(encoding="utf-8")
    project = add_source(project)
    use_inline_playback(xcode_dir)
    project = project.replace(
        "IPHONEOS_DEPLOYMENT_TARGET = 13.0;",
        "IPHONEOS_DEPLOYMENT_TARGET = " + DEPLOYMENT + ";",
    )
    project_path.write_text(project, encoding="utf-8")
    podfile = xcode_dir / "Podfile"
    if podfile.is_file():
        podfile.write_text(
            podfile.read_text(encoding="utf-8").replace("platform :ios, '13.0'", "platform :ios, '15.0'"),
            encoding="utf-8",
        )


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("xcode_dir")
    parser.add_argument("--additions", required=True)
    parser.add_argument("--swift", required=True)
    parser.add_argument("--build-number", default="")
    args = parser.parse_args(argv)
    prepare(args.xcode_dir, args.additions, args.swift, args.build_number.strip())
    print("prepared " + args.xcode_dir)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
