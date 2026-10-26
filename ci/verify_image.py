"""Linux Docker-runner verification of an immutable, self-contained radar image.

Only a fresh temporary /data is mounted. No WebSocket/game frames, saved Key,
collector trigger or external HTTP request is used. Persistence is tested by
changing prediction_mode in that disposable data directory only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid


class CheckFailed(Exception):
    pass


def need(value, code):
    if not value:
        raise CheckFailed(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def entries(rows):
    need(isinstance(rows, list), "MANIFEST_LIST")
    result = {}
    for item in rows:
        need(isinstance(item, dict) and set(item) == {"path", "size", "sha256"}, "MANIFEST_ENTRY")
        name = item["path"]
        need(isinstance(name, str) and "\\" not in name and not name.startswith("/") and
             all(part not in {"", ".", "..", "data", ".env"} for part in name.split("/")), "MANIFEST_PATH")
        need(name.startswith(("app/", "container/")) and name not in result, "MANIFEST_DUPLICATE_OR_ROOT")
        need(type(item["size"]) is int and item["size"] >= 0 and
             isinstance(item["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", item["sha256"]), "MANIFEST_HASH")
        result[name] = item
    return result


def verify_context(manifest_path, expected_release):
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    need(manifest.get("source_release_sha256") == expected_release, "SOURCE_RELEASE_MISMATCH")
    installed, build = entries(manifest["files"]), entries(manifest["build_files"])
    revision = manifest.get("runtime_revision")
    need(revision in {None, "cleanup1", "clock1", "profiles1", "settings1", "config1"}, "UNSUPPORTED_RUNTIME_REVISION")
    app_count = 996 if revision in {"cleanup1", "clock1", "profiles1", "settings1", "config1"} else 997
    need(len(installed) == app_count + 5 and len(build) == app_count + 31, "BASELINE_FILE_COUNTS")
    need(sum(n.startswith("app/") for n in installed) == app_count and
         sum(n.startswith("container/") for n in installed) == 5, "INSTALLED_ROOT_COUNTS")
    need(all(name in build and build[name] == item for name, item in installed.items()), "INSTALLED_NOT_IN_BUILD")
    base = manifest_path.parent.resolve()
    actual = set()
    for directory in ("app", "container"):
        for file in (base / directory).rglob("*"):
            need(not file.is_symlink(), "CONTEXT_SYMLINK")
            if file.is_file(): actual.add(file.relative_to(base).as_posix())
    need(actual == set(build), "CONTEXT_EXTRA_OR_MISSING_FILES")
    for name, item in build.items():
        file = base / name
        need(file.resolve().is_relative_to(base) and file.is_file() and not file.is_symlink(), "CONTEXT_FILE")
        need(file.stat().st_size == item["size"] and digest(file.read_bytes()) == item["sha256"], "CONTEXT_HASH")
    return manifest, digest(raw)


def docker(*args, timeout=90):
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise CheckFailed("DOCKER_UNAVAILABLE_OR_TIMEOUT") from None
    need(result.returncode == 0, "DOCKER_COMMAND_FAILED")
    return result.stdout.strip()


def inspect_container(identifier):
    return json.loads(docker("inspect", identifier))[0]


def wait_healthy(identifier, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = inspect_container(identifier)["State"]
        need(state.get("Running"), "CONTAINER_NOT_RUNNING")
        status = state.get("Health", {}).get("Status")
        if status == "healthy": return
        need(status != "unhealthy", "CONTAINER_UNHEALTHY")
        time.sleep(.5)
    raise CheckFailed("HEALTH_TIMEOUT")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs): return None


class Client:
    def __init__(self, port):
        self.origin = "http://127.0.0.1:" + str(port)
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                   urllib.request.HTTPCookieProcessor(self.jar))
        self.count = 0

    def request(self, path, body=None, method=None, expected=200, limit=5*1024*1024):
        need(path.startswith("/") and not path.startswith("//") and not urlsplit(path).netloc, "NONLOCAL_HTTP_PATH")
        method = method or ("POST" if body is not None else "GET")
        allowed = {("POST", "/api/zones/session"), ("POST", "/api/zones/predict"),
                   ("PUT", "/api/zones/settings")}
        need(method == "GET" or (method, path) in allowed, "HTTP_MUTATION_NOT_PERMITTED")
        self.count += 1
        need(self.count <= 40, "HTTP_BUDGET")
        raw = None if body is None else json.dumps(body, allow_nan=False).encode()
        headers = {"Accept-Encoding": "identity", "Origin": self.origin}
        if body is not None: headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.origin + path, data=raw, headers=headers, method=method)
        try:
            try: response = self.opener.open(request, timeout=15)
            except urllib.error.HTTPError as error: response = error
            with response:
                content = response.read(limit + 1)
                status = response.status
        except (OSError, urllib.error.URLError, TimeoutError):
            raise CheckFailed("LOOPBACK_HTTP_FAILED") from None
        need(status == expected and len(content) <= limit, "HTTP_STATUS_OR_SIZE")
        return content

    def json(self, path, **options):
        result = json.loads(self.request(path, **options))
        need(isinstance(result, dict), "HTTP_JSON_OBJECT")
        return result

    def login(self):
        need(self.json("/api/zones/session") == {"authenticated": False}, "FRESH_SESSION")
        self.json("/api/zones/settings", expected=401)
        need(self.json("/api/zones/session", body={"pin": "8888"}) == {"authenticated": True}, "SESSION_LOGIN")
        need(self.json("/api/zones/session") == {"authenticated": True}, "SESSION_COOKIE")

    def close(self):
        self.jar.clear()
        self.opener.close()


class Resources(HTMLParser):
    def __init__(self): super().__init__(); self.urls = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and "src" in attrs: self.urls.append(attrs["src"])
        if tag == "link" and "stylesheet" in attrs.get("rel", "").split() and "href" in attrs:
            self.urls.append(attrs["href"])


def verify_http(client, manifest, mode, assets=False):
    need(client.json("/api/zones/health") == {"status": "ok"}, "API_HEALTH")
    expected_settings = {"pubg_api_key_configured": False, "prediction_mode": mode}
    need(client.json("/api/zones/settings") == expected_settings, "FRESH_OR_PERSISTED_SETTINGS")
    status = client.json("/api/zones/collector/status")
    need(status.get("enabled") is True and status.get("api_key_configured") is False and
         status.get("interval_minutes") == 30 and status.get("batch_size") == 20 and
         status.get("total_matches") == 0 and status.get("total_circles") == 0 and status.get("models") == [] and
         status.get("last_run_at") is None and status.get("next_run_at") is None and
         status.get("last_error_code") is None, "FRESH_COLLECTOR_STATE")
    for map_id, radius in (("taego", 63379.70703125), ("erangel", 192059.71875)):
        value = client.json("/api/zones/predict", body={"map_id": map_id,
            "current_circle_center": {"x": 408000, "y": 408000}, "current_radius": radius})
        need(set(value) == {"map_id", "current_phase", "next_circle", "final_circle", "model_run_id", "warnings",
                            "engine", "match_type", "geometry_policy"}, "PREDICTION_SCHEMA")
        need(value["map_id"] == map_id and value["engine"] == "pubg_circle_analyzer_knn" and
             value["geometry_policy"] == "nested-circles-v2" and value["match_type"] == mode and
             all(value[k] is None for k in ("current_phase", "next_circle", "final_circle", "model_run_id")), "FRESH_PREDICTION")
        need(value["warnings"] == ["phase_or_match_type_unresolved" if mode == "auto" else "phase_unresolved_from_observed_radii"], "FRESH_PREDICTION_WARNING")
    records = []
    if assets:
        catalog = entries(manifest["files"])
        for page, name, prefix in (("/", "app/build/index.html", "app/build"),
                                    ("/zones/", "app/zones/web/index.html", "app/zones/web")):
            html = client.request(page)
            need(len(html) == catalog[name]["size"] and digest(html) == catalog[name]["sha256"], "HTML_HASH")
            records.append({"path": page, "sha256": digest(html), "size": len(html)})
            parser = Resources(); parser.feed(html.decode())
            need(len(parser.urls) == (4 if page == "/" else 2), "STATIC_RESOURCE_COUNT")
            for url in parser.urls:
                parsed = urlsplit(url)
                need(not parsed.scheme and not parsed.netloc and not parsed.fragment and parsed.path.startswith("/"), "REMOTE_ASSET")
                relative = parsed.path[len("/zones/"):] if page == "/zones/" else parsed.path.lstrip("/")
                entry = catalog.get(prefix + "/" + relative)
                need(entry is not None, "UNKNOWN_ASSET")
                raw = client.request(url, limit=entry["size"])
                need(len(raw) == entry["size"] and digest(raw) == entry["sha256"], "ASSET_HASH")
                records.append({"path": parsed.path, "sha256": digest(raw), "size": len(raw)})
    return {"settings": expected_settings, "matches": 0, "circles": 0, "models": 0,
            "purePredictionPosts": 2, "assets": records, "requests": client.count}


IMAGE_AUDIT = r'''
import os,sys,json,hashlib,stat
raw=open('/opt/gixradar-build.json','rb').read()
assert hashlib.sha256(raw).hexdigest()==sys.argv[1]
m=json.loads(raw);assert m['source_release_sha256']==sys.argv[2]
expected=set()
for e in m['files']:
 n=e['path'];p='/app/'+n[4:] if n.startswith('app/') else '/opt/gixradar-container/'+n[len('container/'):]
 assert not os.path.islink(p) and os.path.isfile(p)
 b=open(p,'rb').read();assert len(b)==e['size'] and hashlib.sha256(b).hexdigest()==e['sha256'];expected.add(p)
actual=set()
for base in ['/app','/opt/gixradar-container']:
 for d,dirs,files in os.walk(base,followlinks=False):
  assert not any(os.path.islink(os.path.join(d,n)) for n in dirs+files)
  actual.update(os.path.join(d,n) for n in files)
assert actual==expected
assert os.getuid()==1000 and os.getgid()==10 and sys.version_info[:2]==(3,12)
s=os.stat('/data');assert s.st_uid==1000 and s.st_gid==10 and stat.S_IMODE(s.st_mode)==0o700
assert os.access('/data',os.W_OK) and not os.environ.get('PUBG_API_KEY')
status=dict(line.strip().split(':',1) for line in open('/proc/self/status') if ':' in line)
assert int(status['CapEff'].strip(),16)==0 and status['NoNewPrivs'].strip()=='1'
print(json.dumps({'installedFiles':len(expected),'manifestSha256':hashlib.sha256(raw).hexdigest(),'source_release_sha256':m['source_release_sha256'],'uid':os.getuid(),'gid':os.getgid(),'python':list(sys.version_info[:3]),'dataMode':'0700','capEffective':0,'noNewPrivileges':True,'exactAppAndRuntimeTrees':True}))
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Local sha256:image-id or registry@sha256:digest; no mutable tags")
    parser.add_argument("--manifest", required=True, type=Path, help="Extracted build-context/source-manifest.json")
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18898)
    parser.add_argument("--health-timeout", type=int, default=180)
    parser.add_argument("--no-persistence", action="store_true")
    args = parser.parse_args(argv)
    report = {"checkedAtUtc": datetime.now(timezone.utc).isoformat(), "passed": False,
              "scope": {"temporaryDataOnly": True, "officialRequests": 0, "gameFramesOrWebSockets": 0,
                        "realKeyOrExistingUserDataRead": False, "localDockerExecution": True}, "cleanup": {}}
    identifier = network = temporary = data = client = image_id = None
    token = uuid.uuid4().hex
    label = "gixradar.verifier=" + token
    try:
        need(os.name == "posix", "LINUX_DOCKER_RUNNER_REQUIRED")
        need(re.fullmatch(r"sha256:[a-f0-9]{64}|[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}", args.image), "IMMUTABLE_IMAGE_REQUIRED")
        need(re.fullmatch(r"[a-f0-9]{64}", args.expected_release) and 1024 <= args.port <= 65535 and
             1 <= args.health_timeout <= 300, "INVALID_ARGUMENTS")
        manifest, manifest_sha = verify_context(args.manifest.resolve(), args.expected_release)
        image = json.loads(docker("image", "inspect", args.image))[0]
        image_id = image["Id"]
        need(re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) and image["Os"] == "linux" and image["Architecture"] == "amd64", "IMAGE_ID_OR_PLATFORM")
        need(image["Config"].get("User") == "1000:10", "IMAGE_DEFAULT_USER")
        temporary = Path(tempfile.mkdtemp(prefix="gixradar-image-verify-"))
        data = temporary / "data"; data.mkdir()
        mount = "type=bind,src=" + str(data.resolve()) + ",dst=/data"
        need("," not in str(data) and temporary.resolve().parent == Path(tempfile.gettempdir()).resolve(), "TEMP_DATA_PATH")
        docker("run", "--rm", "--pull=never", "--network=none", "--read-only", "--user=0:0",
               "--mount", mount, "--entrypoint=python3", image_id, "-c",
               "import os;os.chown('/data',1000,10);os.chmod('/data',0o700)")
        network = docker("network", "create", "--driver=bridge", "--label", label, "gixradar-verify-" + token)
        identifier = docker("run", "--detach", "--pull=never", "--name", "gixradar-verify-" + token,
            "--label", label, "--platform=linux/amd64", "--read-only", "--init",
            "--tmpfs=/tmp:rw,nosuid,noexec,size=16m", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--network", network, "--publish", f"127.0.0.1:{args.port}:8888/tcp", "--mount", mount,
            "--env=PUBG_API_KEY=", image_id)
        need(re.fullmatch(r"[a-f0-9]{64}", identifier), "CONTAINER_ID")
        wait_healthy(identifier, args.health_timeout)
        container = inspect_container(identifier)
        host = container["HostConfig"]
        need(container["Image"] == image_id and container["Config"]["User"] == "1000:10" and
             host["ReadonlyRootfs"] and host["Init"] and "ALL" in host["CapDrop"] and
             any(x in host["SecurityOpt"] for x in ("no-new-privileges:true", "no-new-privileges")) and
             host["PortBindings"] == {"8888/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(args.port)}]}, "CONTAINER_SECURITY")
        persistent_mounts = [m for m in container["Mounts"] if m["Type"] != "tmpfs"]
        need(len(persistent_mounts) == 1 and all(m["Type"] == "bind" and m["Destination"] == "/data" and
             m["RW"] and Path(m["Source"]).resolve() == data.resolve() for m in persistent_mounts), "DATA_ONLY_BIND")
        need(set(host.get("Tmpfs", {})) == {"/tmp"}, "TMPFS")
        need({"rw", "nosuid", "noexec", "size=16m"} <= set(host["Tmpfs"]["/tmp"].split(",")), "TMPFS_FLAGS")
        network_state = json.loads(docker("network", "inspect", network))[0]
        need(network_state.get("Driver") == "bridge" and network_state.get("Internal") is False, "DEDICATED_BRIDGE_NETWORK")
        need(docker("exec", identifier, "node", "--version") == "v18.5.0", "NODE_VERSION")
        report["installed"] = json.loads(docker("exec", identifier, "python3", "-c", IMAGE_AUDIT, manifest_sha, args.expected_release))
        report["image"] = {"requested": args.image, "id": image_id, "os": "linux", "architecture": "amd64",
                           "sourceReleaseSha256": args.expected_release, "runtimeRevision": manifest.get("runtime_revision"),
                           "defaultUser": image["Config"]["User"], "node": "v18.5.0"}
        report["context"] = {"buildFiles": len(manifest["build_files"]), "installedFiles": len(manifest["files"]),
                             "runtimeRevision": manifest.get("runtime_revision"), "manifestSha256": manifest_sha}
        report["productionSecurity"] = {"uid": 1000, "gid": 10, "readOnly": True, "init": True,
            "tmpfs": host["Tmpfs"], "capDrop": host["CapDrop"], "noNewPrivileges": True,
            "onlyPersistentBind": "/data", "publishedHost": "127.0.0.1", "publishedPort": args.port,
            "internalNetwork": False, "dedicatedBridge": True}
        client = Client(args.port); client.login()
        report["http"] = verify_http(client, manifest, "auto", assets=True)
        report["persistence"] = {"tested": not args.no_persistence}
        if not args.no_persistence:
            need(client.json("/api/zones/settings", body={"prediction_mode": "competitive"}, method="PUT") ==
                 {"pubg_api_key_configured": False, "prediction_mode": "competitive"}, "TEMP_MODE_SAVE")
            client.close(); client = None
            docker("restart", "--time=15", identifier)
            wait_healthy(identifier, args.health_timeout)
            client = Client(args.port); client.login()
            report["persistence"]["afterRestart"] = verify_http(client, manifest, "competitive")
            report["persistence"]["modePreserved"] = True
        report["passed"] = True
    except (CheckFailed, OSError, ValueError, KeyError, TypeError) as error:
        report["failure"] = {"code": str(error) if isinstance(error, CheckFailed) else "VERIFIER_INPUT_OR_RUNTIME_ERROR",
                             "type": type(error).__name__}
    finally:
        if client: client.close()
        if identifier:
            try: docker("rm", "--force", identifier); report["cleanup"]["containerRemoved"] = True
            except CheckFailed: report["cleanup"]["containerRemoved"] = False
        if network:
            try: docker("network", "rm", network); report["cleanup"]["networkRemoved"] = True
            except CheckFailed: report["cleanup"]["networkRemoved"] = False
        if temporary:
            try:
                if image_id:
                    docker("run", "--rm", "--pull=never", "--network=none", "--read-only", "--user=0:0",
                        "--mount", "type=bind,src=" + str(data.resolve()) + ",dst=/data", "--entrypoint=python3", image_id,
                        "-c", "import os,sys;u,g=map(int,sys.argv[1:]);\nfor d,ds,fs in os.walk('/data',followlinks=False):\n os.chown(d,u,g,follow_symlinks=False);os.chmod(d,0o700)\n for n in ds+fs: os.chown(os.path.join(d,n),u,g,follow_symlinks=False)", str(os.getuid()), str(os.getgid()))
                need(temporary.resolve().parent == Path(tempfile.gettempdir()).resolve() and temporary.name.startswith("gixradar-image-verify-"), "CLEANUP_PATH")
                shutil.rmtree(temporary)
                report["cleanup"]["temporaryDataRemoved"] = True
            except (CheckFailed, OSError): report["cleanup"]["temporaryDataRemoved"] = False
        if any(value is False for value in report["cleanup"].values()): report["passed"] = False
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(args.output), "failure": report.get("failure")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
