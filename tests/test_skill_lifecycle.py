"""Local protocol fixtures only; tests never call a model or fetch NOAA data."""
import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("skill_lifecycle", ROOT / "scripts/skill_lifecycle.py")
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)
NAME = "ocean-sst-analysis"


class SkillLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.template = self.root / "skill-templates" / NAME
        (self.template / "scripts").mkdir(parents=True)
        (self.template / "references").mkdir()
        self.skill = b"---\nname: ocean-sst-analysis\ndescription: TEST FIXTURE ONLY\n---\nTest documentation.\n"
        self.script = b"# TEST FIXTURE: never executed by the lifecycle\n"
        (self.template / "SKILL.md").write_bytes(self.skill)
        (self.template / "scripts/analyze_sst.py").write_bytes(self.script)
        (self.template / "references/method.md").write_text("TEST ONLY\n", encoding="utf-8")
        (self.root / "data").mkdir()
        self.input = self.root / "data/point_A_monthly.csv"
        self.input.write_text("TEST PROTOCOL PAYLOAD; NOT SCIENTIFIC OBSERVATIONS\n", encoding="utf-8")
        self.metadata_file = self.root / "data/point_A.metadata.json"
        self.metadata = {"sha256": self.hash(self.input), "units": "degree_Celsius",
                         "lat": 18.125, "lon": 114.125,
                         "product": "PROTOCOL TEST FIXTURE, NOT NOAA OBSERVATIONS",
                         "source_url": "https://example.invalid/protocol-test"}
        self.metadata_file.write_text(json.dumps(self.metadata), encoding="utf-8")
        self.run = self.root / "outputs/success"
        self.run.mkdir(parents=True)
        for label in lifecycle.RUN_ARTIFACTS - {"report.json"}:
            (self.run / label).write_bytes(b"PROTOCOL TEST ARTIFACT ONLY\n")
        self.report = {
            "status": "ok", "run_id": "test-only-run", "method_version": "test-fixture",
            "input_file": str(self.input), "input_sha256": self.hash(self.input),
            "metadata_file": str(self.metadata_file), "metadata_sha256": self.hash(self.metadata_file),
            "script_sha256": hashlib.sha256(self.script).hexdigest(),
            "artifacts": sorted(lifecycle.RUN_ARTIFACTS), "baseline": [1991, 2020],
            "row_count": 528, "coverage": ["1982-01", "2025-12"], "style": "default",
            "point": {"lat": 18.125, "lon": 114.125},
            "checks": {"complete_monthly_coverage": True, "complete_daily_coverage": True,
                       "baseline_count_per_month": 30, "baseline_mean_anomaly_max_abs_c": 0.0},
        }
        self.save_report()

    def tearDown(self):
        self.temporary.cleanup()

    def hash(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save_report(self):
        (self.run / "report.json").write_text(json.dumps(self.report), encoding="utf-8")

    def create(self):
        return lifecycle.create_draft(self.root, "outputs/success", NAME)

    def archive(self, filename="skill.zip", extra=(), *, root=None, raw_names=False):
        folder = (self.root if root is None else root) / "dist"
        folder.mkdir(exist_ok=True)
        path = folder / filename
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("SKILL.md", self.skill)
            bundle.writestr("scripts/analyze_sst.py", self.script)
            for name, body in extra:
                if raw_names:
                    # ZipInfo.__init__ normalizes Windows separators and strips
                    # NUL suffixes. Set these fields after construction so both
                    # ZIP headers actually contain the unsafe filename bytes.
                    entry = zipfile.ZipInfo("fixture-placeholder")
                    entry.filename = name
                    entry.orig_filename = name
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = (stat.S_IFREG | 0o644) << 16
                    bundle.writestr(entry, body)
                else:
                    bundle.writestr(name, body)
        return path

    def test_create_records_verified_evidence_without_device_paths(self):
        result = self.create()
        self.assertEqual(result["status"], "ok")
        draft = self.root / "drafts" / NAME
        evidence_text = (draft / "references/example-run.json").read_text(encoding="utf-8")
        evidence = json.loads(evidence_text)
        self.assertNotIn(str(self.root), evidence_text)
        self.assertEqual(evidence["input_sha256"], self.hash(self.input))
        self.assertEqual(evidence["metadata_sha256"], self.hash(self.metadata_file))
        self.assertEqual(evidence["baseline"], [1991, 2020])
        self.assertEqual((draft / "scripts/analyze_sst.py").read_bytes(), self.script)

    def test_failed_and_old_reports_cannot_create(self):
        for changed in ({"status": "error"}, {"metadata_file": None}):
            with self.subTest(changed=changed):
                original = dict(self.report)
                self.report.update(changed)
                self.save_report()
                with self.assertRaises(lifecycle.LifecycleError):
                    self.create()
                self.assertFalse((self.root / "drafts" / NAME).exists())
                self.report = original

    def test_changed_input_metadata_or_template_is_rejected(self):
        for target in (self.input, self.metadata_file, self.template / "scripts/analyze_sst.py"):
            with self.subTest(target=target.name):
                original = target.read_bytes()
                target.write_bytes(original + b"\n")
                with self.assertRaisesRegex(lifecycle.LifecycleError, "SHA-256|script_sha256"):
                    self.create()
                target.write_bytes(original)
                self.assertFalse((self.root / "drafts" / NAME).exists())

    def test_missing_artifact_is_rejected(self):
        (self.run / "sst_analysis.svg").unlink()
        with self.assertRaises(lifecycle.LifecycleError):
            self.create()

    def test_create_does_not_overwrite_draft(self):
        self.create()
        marker = self.root / "drafts" / NAME / "references/keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(lifecycle.LifecycleError):
            self.create()
        self.assertEqual(marker.read_text(), "keep")

    def test_export_install_round_trip_excludes_environment_and_cache(self):
        self.create()
        draft = self.root / "drafts" / NAME
        (draft / ".env").write_text("TEST_ONLY_TOKEN=not-for-export\n", encoding="utf-8")
        (draft / "scripts/__pycache__").mkdir()
        (draft / "scripts/__pycache__/cache.pyc").write_bytes(b"test")
        exported = lifecycle.export_skill(self.root, "draft", NAME, "dist/export.zip")
        with zipfile.ZipFile(exported["path"]) as bundle:
            self.assertNotIn(".env", bundle.namelist())
            self.assertFalse(any("__pycache__" in path for path in bundle.namelist()))
        installed = lifecycle.install_skill(self.root, NAME, archive="dist/export.zip")
        self.assertTrue(installed["reload_required"])
        self.assertEqual((self.root / "skills" / NAME / "SKILL.md").read_bytes(), self.skill)
        second_export = lifecycle.export_skill(self.root, "installed", NAME, "dist/installed.zip")
        self.assertEqual(exported["sha256"], second_export["sha256"])

    def test_install_does_not_replace_without_explicit_option(self):
        self.create()
        lifecycle.install_skill(self.root, NAME, draft=NAME)
        marker = self.root / "skills" / NAME / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.install_skill(self.root, NAME, draft=NAME)
        self.assertEqual(marker.read_text(), "keep")

    def test_explicit_replace_preserves_old_installation_in_backup(self):
        self.create()
        lifecycle.install_skill(self.root, NAME, draft=NAME)
        installed = self.root / "skills" / NAME
        (installed / "old.txt").write_text("old installation", encoding="utf-8")
        result = lifecycle.install_skill(self.root, NAME, draft=NAME, replace=True)
        self.assertFalse((installed / "old.txt").exists())
        self.assertEqual((Path(result["backup"]) / "old.txt").read_text(), "old installation")

    def test_failed_replacement_rolls_back_original(self):
        self.create()
        lifecycle.install_skill(self.root, NAME, draft=NAME)
        installed = self.root / "skills" / NAME
        marker = installed / "old.txt"
        marker.write_text("restore me", encoding="utf-8")
        original_replace = lifecycle.os.replace

        def fail_new_install(source, target):
            if "-stage-" in Path(source).name and Path(target) == installed:
                raise OSError("test-only failed promotion")
            return original_replace(source, target)

        with mock.patch.object(lifecycle.os, "replace", side_effect=fail_new_install):
            with self.assertRaises(OSError):
                lifecycle.install_skill(self.root, NAME, draft=NAME, replace=True)
        self.assertEqual(marker.read_text(), "restore me")
        self.assertFalse(any("-stage-" in p.name for p in installed.parent.iterdir()))

    def test_zip_rejects_traversal_absolute_and_environment_entries(self):
        labels = ("../escape.txt", "/absolute.txt", "C:/absolute.txt",
                  "scripts\\escape.py", "references/safe.md\x00ignored",
                  ".env", "scripts/__pycache__/x.pyc")
        for label in labels:
            # A rejected assertion must not leave an installed Skill that can
            # contaminate the remaining cases on any operating system.
            with self.subTest(label=label), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                case_root = Path(temporary)
                archive = self.archive("bad.zip", [(label, b"bad")],
                                       root=case_root, raw_names=True)
                self.assertEqual(archive.read_bytes().count(label.encode("ascii")), 2,
                                 "unsafe filename must be present in both ZIP headers")
                with zipfile.ZipFile(archive) as bundle:
                    self.assertEqual(bundle.infolist()[-1].orig_filename, label,
                                     "fixture must preserve the raw archive filename")
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.install_skill(case_root, NAME, archive=str(archive))
                self.assertFalse((case_root / "skills" / NAME).exists())
                self.assertFalse((case_root / "escape.txt").exists())
        self.assertFalse((self.root / "escape.txt").exists())

    def test_zip_rejects_duplicate_and_case_colliding_members(self):
        for label in ("SKILL.md", "skill.md"):
            with self.subTest(label=label), warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive = self.archive("duplicate.zip", [(label, b"duplicate")])
                with self.assertRaisesRegex(lifecycle.LifecycleError, "重复|冲突"):
                    lifecycle.read_archive(archive, NAME)

    def test_zip_rejects_symbolic_links_and_oversized_payload(self):
        archive = self.archive()
        symlink = zipfile.ZipInfo("references/linked.md")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr(symlink, "../../outside")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "符号链接"):
            lifecycle.read_archive(archive, NAME)
        archive = self.archive("large.zip", [("references/large.txt", b"x" * 513)])
        with mock.patch.object(lifecycle, "MAX_FILE_BYTES", 512):
            with self.assertRaisesRegex(lifecycle.LifecycleError, "大小超限"):
                lifecycle.read_archive(archive, NAME)

    def test_zip_missing_required_script_is_rejected(self):
        folder = self.root / "dist"
        folder.mkdir()
        archive = folder / "missing.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("SKILL.md", self.skill)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "scripts/analyze_sst.py"):
            lifecycle.read_archive(archive, NAME)

    def test_sources_and_destinations_must_stay_inside_managed_areas(self):
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.create_draft(self.root, "outputs/../data", NAME)
        self.create()
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.export_skill(self.root, "draft", NAME, "../outside.zip")
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.skill_name("../other")

    def test_invalid_cli_returns_json_error_without_operations(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = lifecycle.main(["--project-root", str(self.root), "install", "--name", NAME])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")
        self.assertFalse((self.root / "skills").exists())


if __name__ == "__main__":
    unittest.main()
