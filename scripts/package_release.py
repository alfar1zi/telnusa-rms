#!/usr/bin/env python3
"""Build hardened deployment package and exhaustive manifest for TELNUSA NMS Production Candidate."""
from __future__ import annotations
import datetime
import hashlib
import json
import os
import re
import zipfile


def compute_sha256(filepath: str) -> tuple[str, int]:
    with open(filepath, "rb") as f:
        data = f.read()
    return hashlib.sha256(data).hexdigest(), len(data)


def categorize_file(relpath: str) -> str:
    if relpath.startswith("dist/"):
        return "FRONTEND_DIST"
    if relpath.startswith("deployment/") or relpath.startswith("deploy/"):
        return "DEPLOYMENT"
    if relpath.startswith("backend/test_") or relpath.startswith("src/test_"):
        return "TEST"
    if relpath.startswith("backend/"):
        if relpath.endswith(".example") or relpath.endswith(".env"):
            return "CONFIG"
        return "BACKEND"
    if relpath.startswith("src/"):
        return "RUNTIME"
    if relpath.startswith("scripts/"):
        return "UTILITY"
    if relpath.endswith(".md") or relpath.startswith("docs/"):
        return "DOCUMENTATION"
    if relpath in (
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "tsconfig.app.json",
        "tsconfig.node.json",
        "vite.config.ts",
        "components.json",
        "index.html",
    ):
        return "CONFIG"
    if relpath.endswith(".json"):
        return "CONFIG"
    if relpath.endswith(".py"):
        return "UTILITY"
    return "RUNTIME"


SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "Private Key Header"),
    (re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key|auth[_-]?token|bearer[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{16,}['\"]"), "Hardcoded High-Entropy Secret"),
    (re.compile(r"(?i)zabbix[_-]?token\s*[:=]\s*['\"][0-9a-fA-F]{32,}['\"]"), "Zabbix API Token"),
]


def scan_for_secrets(data: bytes, filename: str) -> list[str]:
    findings = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return findings

    for pattern, desc in SECRET_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            filtered = [
                m for m in matches
                if "example" not in str(m).lower()
                and "placeholder" not in str(m).lower()
                and "test" not in filename.lower()
                and "mock" not in filename.lower()
            ]
            if filtered:
                findings.append(f"{filename}: matched pattern {desc}")
    return findings


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(repo_root)

    release_name = "TELNUSA_NMS_PRODUCTION_CANDIDATE_2026-09-30"
    release_timestamp = "2026-09-30T00:00:00Z"
    build_created_at_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    zip_filename = "telnusa-nms-production-candidate-2026-09-30.zip"
    tmp_zip = f"{zip_filename}.tmp"
    excluded_dirs = {
        ".git",
        ".omp_cache",
        ".slim",
        "node_modules",
        "deploy",
        ".venv",
        "__pycache__",
    }

    excluded_files = {
        "TELNUSA_NMS_CONTEXT_PACK_v1_2026-09-24.md",
        "REMOTE_WEBUI_SOURCE_AUDIT_20260926.md",
        "RELEASE_METADATA.json",
        "DEPLOYMENT_MANIFEST.json",
        ".env",
        "id_rsa",
        "id_ed25519",
        "known_hosts",
        "nms.db",
        "RUT906_HTTP_OUTPUT_FORENSIC_20260928.md",
        "SENSOR_AGNOSTIC_TELEMETRY_EGRESS_AUDIT_20260928.md",
        "TELNUSA_NMS_MASTER_FIX_CHECKPOINT_20260930.md",
    }

    excluded_extensions = (".pyc", ".db", ".sqlite", ".sqlite3", ".log", ".tmp", ".zip", ".sha256", ".key", ".pem", ".bak", ".bak2", ".1")

    # 1. Discover all candidate files
    candidate_files: list[str] = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in excluded_dirs and not d.startswith(".git")]
        for file in sorted(files):
            if file.endswith(excluded_extensions) or file in excluded_files:
                continue
            filepath = os.path.join(root, file)
            arcname = os.path.relpath(filepath, ".")
            if (
                or arcname.startswith(".git")
                or arcname.startswith("deploy/")
                or arcname in excluded_files
            ):
                continue
            if arcname == "DEPLOYMENT_MANIFEST.json":
                continue
            candidate_files.append(arcname)

    candidate_files.sort()

    # 2. Build exhaustive source manifest
    manifest_files = {}
    for relpath in candidate_files:
        sha, size = compute_sha256(relpath)
        manifest_files[relpath] = {
            "sha256": sha,
            "size_bytes": size,
            "category": categorize_file(relpath),
        }

    manifest = {
        "release_name": release_name,
        "release_timestamp_utc": release_timestamp,
        "environment_target": "Ubuntu Server 26.04.1 LTS (telnusa-nms)",
        "classifications": {
            "FRONTEND_VISUAL_QA": "LOCALLY_VERIFIED",
            "SOURCE_PATCH": "LOCALLY_VERIFIED",
            "REAL_RUT906": "NOT_RETESTED_IN_THIS_RELEASE",
            "REAL_VERIFIED": False,
            "DEPLOYMENT": "NOT_PERFORMED",
        },
        "verification_status": "PASS - LOCALLY VERIFIED (VISUAL SIGN-OFF PASS)",
        "total_source_files": len(manifest_files),
        "source_files": manifest_files,
    }

    manifest_path = "DEPLOYMENT_MANIFEST.json"
    manifest_content = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(manifest_content)

    # 3. Create zip deterministically in tmp file
    if os.path.exists(tmp_zip):
        os.remove(tmp_zip)

    # Zip entries to include: all candidate files + DEPLOYMENT_MANIFEST.json
    all_zip_files = sorted(candidate_files + [manifest_path])
    zip_dt = (2026, 9, 30, 0, 0, 0)

    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        for arcname in all_zip_files:
            with open(arcname, "rb") as f:
                content = f.read()
            zinfo = zipfile.ZipInfo(arcname, date_time=zip_dt)
            zinfo.external_attr = 0o644 << 16  # standard non-executable permissions
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zipf.writestr(zinfo, content)

    # 4. Verify ZIP integrity & scan secrets
    with zipfile.ZipFile(tmp_zip, "r") as zipf:
        bad_file = zipf.testzip()
        if bad_file:
            os.remove(tmp_zip)
            raise RuntimeError(f"Corrupted entry found in zip archive: {bad_file}")

        secret_findings = []
        for member in zipf.namelist():
            content = zipf.read(member)
            findings = scan_for_secrets(content, member)
            if findings:
                secret_findings.extend(findings)

    if secret_findings:
        os.remove(tmp_zip)
        raise RuntimeError(f"Secret scan failed! Findings:\n" + "\n".join(secret_findings))

    # 5. Atomically promote tmp zip to canonical release file via os.replace
    os.replace(tmp_zip, zip_filename)

    archive_sha256, archive_size = compute_sha256(zip_filename)

    # 6. Generate external metadata AFTER zip creation
    metadata = {
        "release_archive": {
            "filename": zip_filename,
            "size_bytes": archive_size,
            "sha256": archive_sha256,
            "entry_count": len(all_zip_files),
            "release_timestamp_utc": release_timestamp,
            "build_created_at_utc": build_created_at_utc,
            "secret_scan": "CLEAN (0 secrets found)",
            "integrity_check": "PASS (CRC32 validated)",
            "reproducibility": "REPRODUCIBLE (Normalized zip timestamp and deterministic entry sort)",
        },
        "classifications": {
            "FRONTEND_VISUAL_QA": "LOCALLY_VERIFIED",
            "SOURCE_PATCH": "LOCALLY_VERIFIED",
            "REAL_RUT906": "NOT_RETESTED_IN_THIS_RELEASE",
            "REAL_VERIFIED": False,
            "DEPLOYMENT": "NOT_PERFORMED",
        },
        "internal_manifest": manifest_path,
        "environment_target": "Ubuntu Server 26.04.1 LTS (telnusa-nms)",
        "real_verified": False,
        "verification_summary": "PASS - LOCALLY VERIFIED (VISUAL SIGN-OFF PASS)",
    }

    with open("RELEASE_METADATA.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    with open(f"{zip_filename}.sha256", "w", encoding="utf-8") as f:
        f.write(f"{archive_sha256}  {zip_filename}\n")

    print(f"Generated canonical release archive: {zip_filename}")
    print(f"Archive SHA256: {archive_sha256}")
    print(f"Archive Size: {archive_size} bytes")
    print(f"Total entries: {len(all_zip_files)}")


if __name__ == "__main__":
    main()
