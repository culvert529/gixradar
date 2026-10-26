"""Authenticate and safely extract the exact accepted container build context."""
import argparse
import hashlib
import json
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate(archive, release, destination):
    raw = archive.read_bytes()
    if len(raw) != release["asset_size"] or digest(raw) != release["asset_sha256"]:
        raise ValueError("Build context archive size/hash mismatch")
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive path")
        manifest_bytes = source.read("source-manifest.json")
        if digest(manifest_bytes) != release["manifest_sha256"]:
            raise ValueError("Source manifest hash mismatch")
        manifest = json.loads(manifest_bytes)
        if manifest["source_release_sha256"] != release["source_release_sha256"]:
            raise ValueError("Source release mismatch")
        expected = {entry["path"]: entry for entry in manifest["build_files"]}
        if len(expected) != release["build_files"] or set(names) != set(expected) | {"source-manifest.json"}:
            raise ValueError("Archive inventory differs from signed-off source")
        if sum(path.startswith("app/") for path in expected) != release["app_files"]:
            raise ValueError("Application inventory mismatch")
        if len(manifest["files"]) != release["runtime_files"]:
            raise ValueError("Runtime inventory mismatch")
        for item in source.infolist():
            path = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if path.is_absolute() or "\\" in item.filename or any(p in {"", ".", ".."} for p in item.filename.split("/")) or stat.S_ISLNK(mode) or item.is_dir():
                raise ValueError("Unsafe archive member")
            if item.filename != "source-manifest.json":
                entry = expected[item.filename]
                data = source.read(item)
                if len(data) != entry["size"] or digest(data) != entry["sha256"]:
                    raise ValueError("Source payload mismatch: " + item.filename)
        destination.mkdir(parents=True, exist_ok=False)
        for name in names:
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read(name))
    return {"source_release_sha256": release["source_release_sha256"], "archive_sha256": digest(raw), "app_files": release["app_files"], "build_files": len(expected), "verified": True}


def clean_runtime(destination, release, root):
    """Apply the reviewed runtime patch and authenticate the complete resulting tree."""
    destination, root = destination.resolve(), root.resolve()
    patch = root / "patches/runtime.patch"
    if digest(patch.read_bytes()) != release["cleanup_sha256"]:
        raise ValueError("Cleanup patch hash mismatch")
    prefix = destination.relative_to(root).as_posix()
    command = ["git", "-c", "core.autocrlf=false", "apply"]
    paths = ["--directory=" + prefix, str(patch)]
    subprocess.run(command + ["--check"] + paths, cwd=root, check=True)
    subprocess.run(command + paths, cwd=root, check=True)
    manifest_path = destination / "source-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    before = {entry["path"]: entry for entry in manifest["build_files"]}
    after = {}
    for folder in ("app", "container"):
        for file in (destination / folder).rglob("*"):
            if file.is_symlink():
                raise ValueError("Cleanup introduced a symlink")
            if file.is_file():
                name = file.relative_to(destination).as_posix()
                data = file.read_bytes()
                after[name] = {"path": name, "size": len(data), "sha256": digest(data)}
    removed = set(before) - set(after)
    added = set(after) - set(before)
    changed = {name for name in before.keys() & after.keys() if after[name] != before[name]}
    revision = release.get("runtime_revision", "cleanup1")
    if revision not in {"cleanup1", "clock1", "profiles1"}:
        raise ValueError("Unsupported runtime revision")
    reviewed_changes = {"app/zones/backend/app/core/config.py"}
    if revision in {"clock1", "profiles1"}:
        reviewed_changes |= {"app/build/static/js/gix-zones.js", "app/build/index.html"}
    if revision == "profiles1":
        reviewed_changes |= {"app/zones/backend/app/services/analyzer.py", "app/zones/backend/app/services/training.py"}
    if (removed != {"app/zones/backend/app/db/repository.py"} or added or
            changed != reviewed_changes):
        raise ValueError("Runtime patch changed files outside the approved scope")
    for key in ("files", "build_files"):
        manifest[key] = [after[entry["path"]] for entry in manifest[key] if entry["path"] in after]
    manifest.update(runtime_revision=revision, cleanup_sha256=release["cleanup_sha256"])
    data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    if digest(data) != release["runtime_manifest_sha256"]:
        raise ValueError("Cleaned runtime manifest mismatch")
    manifest_path.write_bytes(data)
    return {"runtime_revision": revision, "runtime_manifest_sha256": digest(data),
            "app_files": sum(name.startswith("app/") for name in after),
            "runtime_files": len(manifest["files"]), "build_files": len(after)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--release", type=Path, default=Path("release.json"))
    args = parser.parse_args()
    release = json.loads(args.release.read_text(encoding="utf-8"))
    result = validate(args.archive, release, args.destination)
    if "cleanup_sha256" in release:
        result["cleanup"] = clean_runtime(args.destination, release, args.release.parent)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
