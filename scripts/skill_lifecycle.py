#!/usr/bin/env python3
"""Create, export, and install the ocean demo Skill without running its code.

All operations are local. Archives contain files directly at their root.
Use a successful analysis report to create a draft, then export or install it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile

TEMPLATE_NAME = "ocean-sst-analysis"
MAX_FILES = 256
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 12 * 1024 * 1024
REQUIRED_FILES = {"SKILL.md", "scripts/analyze_sst.py"}
RUN_ARTIFACTS = {"result.csv", "climatology.csv", "summary.md", "report.json",
                 "sst_analysis.png", "sst_analysis.svg", "sst_analysis.pdf"}
EXCLUDED_PARTS = {"__pycache__", "node_modules", "venv", "env", "cache", "caches"}
NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class LifecycleError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise LifecycleError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def skill_name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or not NAME_PATTERN.fullmatch(value):
        raise LifecycleError("Skill 名称只能使用小写英文字母、数字和单个连接用短横线，最多 64 字符")
    return value


def project_root(value=None) -> Path:
    root = Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[1]
    if not root.is_dir():
        raise LifecycleError("项目目录不存在")
    return root


def controlled_path(root: Path, value, area: str, *, allow_area=False) -> Path:
    supplied = Path(value)
    if ".." in supplied.parts:
        raise LifecycleError("路径不得包含 ..")
    candidate = supplied if supplied.is_absolute() else root / supplied
    base = root / area
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise LifecycleError(f"路径必须位于项目 {area}/ 中") from exc
    if not allow_area and not relative.parts:
        raise LifecycleError("请指定具体文件或子目录")
    cursor = root
    for part in candidate.relative_to(root).parts:
        cursor /= part
        if cursor.is_symlink():
            raise LifecycleError("受控路径不能经过符号链接")
    return candidate


def ensure_area(root: Path, area: str) -> Path:
    path = controlled_path(root, root / area, area, allow_area=True)
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise LifecycleError(f"{area} 必须是目录")
    return path


def excluded(path: PurePosixPath) -> bool:
    return any(part.startswith(".") or part.casefold() in EXCLUDED_PARTS for part in path.parts) or path.suffix.casefold() in {".pyc", ".pyo"}


def member_name(raw: str) -> str:
    if not raw or "\\" in raw or "\x00" in raw or raw.startswith("/"):
        raise LifecycleError("ZIP 含不安全路径")
    parts = raw.rstrip("/").split("/")
    if any(not p or p in {".", ".."} or ":" in p or p.endswith((" ", ".")) for p in parts):
        raise LifecycleError("ZIP 含绝对路径、路径穿越或不兼容文件名")
    if len(parts) > 16 or len(raw) > 240:
        raise LifecycleError("ZIP 路径过长")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if any(part.split(".")[0].casefold() in reserved for part in parts):
        raise LifecycleError("ZIP 含平台保留文件名")
    return PurePosixPath(*parts).as_posix()


def frontmatter_name(body: bytes) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeError as exc:
        raise LifecycleError("SKILL.md 必须是 UTF-8 文本") from exc
    block = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, flags=re.S)
    if not block:
        raise LifecycleError("SKILL.md 缺少 YAML frontmatter")
    # Accept both LF and CRLF template files.  The repository template is
    # commonly checked out with Windows line endings, so `$` alone would be
    # positioned after `\n` while the captured value still ends before `\r`.
    names = re.findall(r"^name:\s*([^\r\n]+)\r?$", block.group(1), flags=re.M)
    if len(names) != 1:
        raise LifecycleError("SKILL.md 必须有唯一 name 字段")
    return skill_name(names[0].strip().strip("\"'"))


def validate_payloads(files: dict[str, bytes], name: str):
    if not REQUIRED_FILES.issubset(files):
        raise LifecycleError("Skill 必须包含 SKILL.md 和 scripts/analyze_sst.py")
    if len(files) > MAX_FILES or sum(len(body) for body in files.values()) > MAX_TOTAL_BYTES:
        raise LifecycleError("Skill 文件数量或总大小超限")
    folded = set()
    for label, body in files.items():
        normalized = member_name(label)
        if normalized != label or normalized.casefold() in folded:
            raise LifecycleError("Skill 含重复或大小写冲突路径")
        folded.add(normalized.casefold())
        if excluded(PurePosixPath(label)):
            raise LifecycleError("Skill 包不能包含隐藏配置、.env 或缓存")
        if len(body) > MAX_FILE_BYTES:
            raise LifecycleError("Skill 单文件大小超限")
        if any(parent.as_posix().casefold() in {x.casefold() for x in files}
               for parent in PurePosixPath(label).parents if parent.as_posix() != "."):
            raise LifecycleError("Skill 文件与目录路径冲突")
    if not files["scripts/analyze_sst.py"]:
        raise LifecycleError("分析脚本为空")
    if frontmatter_name(files["SKILL.md"]) != name:
        raise LifecycleError("Skill frontmatter 名称与目标名称不一致")


def read_tree(folder: Path, name: str) -> dict[str, bytes]:
    if not folder.is_dir() or folder.is_symlink():
        raise LifecycleError("Skill 源目录不存在或是符号链接")
    files, total = {}, 0
    for current, directories, filenames in os.walk(folder, followlinks=False):
        base = Path(current)
        kept = []
        for child in sorted(directories):
            item = base / child
            rel = PurePosixPath(item.relative_to(folder).as_posix())
            if item.is_symlink():
                raise LifecycleError("Skill 源目录含符号链接")
            if not excluded(rel):
                kept.append(child)
        directories[:] = kept
        for child in sorted(filenames):
            item = base / child
            rel = item.relative_to(folder).as_posix()
            if item.is_symlink():
                raise LifecycleError("Skill 源文件不能是符号链接")
            if excluded(PurePosixPath(rel)):
                continue
            if not stat.S_ISREG(item.stat().st_mode):
                raise LifecycleError("Skill 只能包含普通文件")
            if item.stat().st_size > MAX_FILE_BYTES:
                raise LifecycleError("Skill 单文件大小超限")
            body = item.read_bytes()
            total += len(body)
            if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                raise LifecycleError("Skill 文件数量或总大小超限")
            files[rel] = body
    validate_payloads(files, name)
    return files


def read_archive(archive: Path, name: str) -> dict[str, bytes]:
    if not archive.is_file() or archive.is_symlink():
        raise LifecycleError("ZIP 文件不存在或是符号链接")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise LifecycleError("ZIP 文件大小超限")
    files, seen, declared_total = {}, set(), 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_FILES * 2:
                raise LifecycleError("ZIP 条目数量超限")
            for info in members:
                # ZipInfo can normalize Windows backslashes and truncate at a
                # NUL before assigning filename. Validate the original bytes'
                # decoded name first so those unsafe paths cannot be hidden.
                label = member_name(info.orig_filename)
                if info.orig_filename != info.filename or label != member_name(info.filename):
                    raise LifecycleError("ZIP 原始文件名被规范化，拒绝不一致路径")
                if label.casefold() in seen:
                    raise LifecycleError("ZIP 含重复或大小写冲突条目")
                seen.add(label.casefold())
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise LifecycleError("ZIP 含符号链接或特殊文件")
                if info.flag_bits & 1:
                    raise LifecycleError("不支持加密 ZIP")
                if excluded(PurePosixPath(label)):
                    raise LifecycleError("ZIP 不能包含 .env、隐藏配置或缓存")
                if info.is_dir():
                    continue
                if kind == stat.S_IFDIR:
                    raise LifecycleError("ZIP 文件类型不一致")
                declared_total += info.file_size
                if info.file_size > MAX_FILE_BYTES or declared_total > MAX_TOTAL_BYTES:
                    raise LifecycleError("ZIP 解压后大小超限")
                with bundle.open(info) as stream:
                    body = stream.read(MAX_FILE_BYTES + 1)
                if len(body) != info.file_size or len(body) > MAX_FILE_BYTES:
                    raise LifecycleError("ZIP 声明大小与实际内容不一致或文件过大")
                files[label] = body
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise LifecycleError(f"无法读取 ZIP：{exc}") from exc
    validate_payloads(files, name)
    return files


def stage_tree(parent: Path, name: str, files: dict[str, bytes]) -> Path:
    stage = Path(tempfile.mkdtemp(prefix=f".{name}-stage-", dir=parent))
    try:
        for label, body in sorted(files.items()):
            target = stage.joinpath(*PurePosixPath(label).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        validate_payloads(read_tree(stage, name), name)
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def checked_file(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise LifecycleError("运行证据文件不存在或是符号链接")
    if path.stat().st_size > MAX_TOTAL_BYTES:
        raise LifecycleError("运行证据文件过大")
    return path.read_bytes()


def verify_run(root: Path, value, template: dict[str, bytes]) -> dict:
    run = controlled_path(root, value, "outputs")
    if not run.is_dir():
        raise LifecycleError("运行输出目录不存在")
    report_bytes = checked_file(run / "report.json")
    report = json.loads(report_bytes.decode("utf-8"))
    if not isinstance(report, dict) or report.get("status") != "ok":
        raise LifecycleError("只能从成功的分析报告创建 Skill")
    if not report.get("metadata_file"):
        raise LifecycleError("旧报告缺少 metadata_file，不满足创建协议；请重新完成一次分析")
    for key in ("input_sha256", "metadata_sha256", "script_sha256"):
        if not isinstance(report.get(key), str) or not HASH_PATTERN.fullmatch(report[key]):
            raise LifecycleError(f"报告缺少有效的 {key}")
    source_input = controlled_path(root, report.get("input_file", ""), "data")
    source_meta = controlled_path(root, report["metadata_file"], "data")
    raw_input, raw_meta = checked_file(source_input), checked_file(source_meta)
    if digest(raw_input) != report["input_sha256"]:
        raise LifecycleError("原始输入文件的 SHA-256 与成功报告不匹配")
    if digest(raw_meta) != report["metadata_sha256"]:
        raise LifecycleError("原始 metadata 的 SHA-256 与成功报告不匹配")
    if digest(template["scripts/analyze_sst.py"]) != report["script_sha256"]:
        raise LifecycleError("模板分析脚本与成功报告的 script_sha256 不匹配；请使用同版本模板或重新分析")
    metadata = json.loads(raw_meta.decode("utf-8"))
    if not isinstance(metadata, dict) or metadata.get("sha256") != report["input_sha256"]:
        raise LifecycleError("原始 metadata 与输入 CSV 校验值不匹配")
    declared_artifacts = report.get("artifacts", [])
    if not isinstance(declared_artifacts, list) or not RUN_ARTIFACTS.issubset(declared_artifacts):
        raise LifecycleError("成功报告的科学产物清单不完整")
    for label in RUN_ARTIFACTS:
        if not checked_file(run / label):
            raise LifecycleError("成功报告对应的产物为空")
    checks = report.get("checks", {})
    daily_coverage = isinstance(checks, dict) and checks.get("complete_daily_coverage") is True
    source_coverage = isinstance(checks, dict) and checks.get("complete_source_coverage") is True
    if not isinstance(checks, dict) or checks.get("complete_monthly_coverage") is not True or not (daily_coverage or source_coverage) or checks.get("baseline_count_per_month") != 30:
        raise LifecycleError("报告未通过完整性与基准期检查")
    baseline = report.get("baseline")
    if not isinstance(baseline, list) or len(baseline) != 2 or not all(type(y) is int for y in baseline) or baseline[1] - baseline[0] != 29:
        raise LifecycleError("报告的 30 年基准期无效")
    residual = checks.get("baseline_mean_anomaly_max_abs_c")
    if not isinstance(residual, (int, float)) or not math.isfinite(residual) or residual < 0 or residual > 1e-9:
        raise LifecycleError("报告的基准期零均值验收无效")
    source_keys = ("product", "source_title", "source_url", "product_url", "dataset_doi",
                   "retrieved_at", "units", "aggregation", "historical_source_policy", "representation", "citation")
    source = {key: metadata[key] for key in source_keys if isinstance(metadata.get(key), str)}
    # Absolute paths and raw per-request payloads are deliberately not retained.
    return {"schema_version": 1, "evidence_type": "verified_local_successful_run",
            "run_id": report.get("run_id"), "report_sha256": digest(report_bytes),
            "input_file_name": source_input.name, "input_sha256": report["input_sha256"],
            "metadata_file_name": source_meta.name, "metadata_sha256": report["metadata_sha256"],
            "script_sha256": report["script_sha256"], "method_version": report.get("method_version"),
            "baseline": baseline, "point": report.get("point"), "coverage": report.get("coverage"),
            "row_count": report.get("row_count"), "style": report.get("style"),
            "checks": {key: checks[key] for key in ("complete_monthly_coverage",
                       "complete_daily_coverage", "complete_source_coverage", "coverage_basis",
                       "baseline_count_per_month",
                       "baseline_mean_anomaly_max_abs_c") if key in checks}, "source": source,
            "note": "This records verified local files from one successful run; it does not execute or independently repeat the scientific analysis."}


def create_draft(root: Path, from_run: str, name: str):
    name = skill_name(name)
    if name != TEMPLATE_NAME:
        raise LifecycleError(f"当前演示模板和专用工具使用固定名称 {TEMPLATE_NAME}")
    template_path = controlled_path(root, root / "skill-templates" / TEMPLATE_NAME, "skill-templates")
    files = read_tree(template_path, name)
    evidence = verify_run(root, from_run, files)
    files["references/example-run.json"] = (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    validate_payloads(files, name)
    parent = ensure_area(root, "drafts")
    target = controlled_path(root, parent / name, "drafts")
    if target.exists():
        raise LifecycleError("草稿已存在，不覆盖现有草稿")
    stage = stage_tree(parent, name, files)
    try:
        if target.exists():
            raise LifecycleError("草稿已存在，不覆盖现有草稿")
        os.rename(stage, target)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return {"status": "ok", "action": "create", "name": name, "path": str(target),
            "reference": str(target / "references/example-run.json"), "files": len(files)}


def export_skill(root: Path, source: str, name: str, output: str):
    name = skill_name(name)
    area = {"draft": "drafts", "installed": "skills"}.get(source)
    if area is None:
        raise LifecycleError("source 必须为 draft 或 installed")
    files = read_tree(controlled_path(root, root / area / name, area), name)
    destination = controlled_path(root, output, "dist")
    if destination.suffix.lower() != ".zip":
        raise LifecycleError("导出文件必须为 .zip")
    ensure_area(root, "dist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise LifecycleError("导出文件已存在，请使用新的文件名")
    descriptor, temp_name = tempfile.mkstemp(prefix=".skill-export-", suffix=".zip", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for label, body in sorted(files.items()):
                info = zipfile.ZipInfo(label)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, body)
        read_archive(temporary, name)
        if destination.exists():
            raise LifecycleError("导出文件已存在，不覆盖")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "ok", "action": "export", "name": name, "path": str(destination),
            "source": source, "sha256": digest(destination.read_bytes()), "files": len(files)}


def install_skill(root: Path, name: str, *, archive=None, draft=None, replace=False):
    name = skill_name(name)
    if (archive is None) == (draft is None):
        raise LifecycleError("必须且只能指定 archive 或 draft")
    if archive is not None:
        files = read_archive(controlled_path(root, archive, "dist"), name)
    else:
        source_name = skill_name(draft)
        files = read_tree(controlled_path(root, root / "drafts" / source_name, "drafts"), name)
    parent = ensure_area(root, "skills")
    target = controlled_path(root, parent / name, "skills")
    if target.exists() and not replace:
        raise LifecycleError("已安装同名 Skill；明确使用 --replace 才会备份并替换")
    if target.exists() and not target.is_dir():
        raise LifecycleError("同名目标不是目录")
    stage = stage_tree(parent, name, files)
    backup = None
    try:
        if target.exists():
            if not replace:
                raise LifecycleError("已安装同名 Skill，不覆盖")
            backup_parent = ensure_area(root, "backups")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = backup_parent / f"{name}-{stamp}-{uuid.uuid4().hex[:8]}"
            os.replace(target, backup)
        os.replace(stage, target)
    except BaseException as exc:
        if backup is not None and backup.exists() and not target.exists():
            try:
                os.replace(backup, target)
            except OSError as rollback_error:
                raise LifecycleError(f"安装失败且自动恢复未完成；原文件仍保存在 {backup}：{rollback_error}") from exc
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return {"status": "ok", "action": "install", "name": name, "path": str(target),
            "backup": str(backup) if backup else None, "files": len(files),
            "reload_required": True, "note": "重新加载同名 Skill 后使用；本工具没有执行包内脚本。"}


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--from-run", required=True)
    create.add_argument("--name", default=TEMPLATE_NAME)
    export = sub.add_parser("export")
    export.add_argument("--source", choices=["draft", "installed"], required=True)
    export.add_argument("--name", required=True)
    export.add_argument("--output", required=True)
    install = sub.add_parser("install")
    origins = install.add_mutually_exclusive_group(required=True)
    origins.add_argument("--archive")
    origins.add_argument("--draft")
    install.add_argument("--name", required=True)
    install.add_argument("--replace", action="store_true")
    args = None
    try:
        args = parser.parse_args(argv)
        root = project_root(args.project_root)
        if args.command == "create":
            result = create_draft(root, args.from_run, args.name)
        elif args.command == "export":
            result = export_skill(root, args.source, args.name, args.output)
        else:
            result = install_skill(root, args.name, archive=args.archive, draft=args.draft, replace=args.replace)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (LifecycleError, OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "error", "action": getattr(args, "command", None), "issues": [str(exc)]}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
