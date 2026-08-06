import tempfile
import unittest
from pathlib import Path

from spiketrace.errors import ManifestError
from spiketrace.manifest import load_manifest, summarize_manifest


class ManifestTests(unittest.TestCase):
    def test_loads_relative_video_and_summarizes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "train.avi").touch()
            (root / "val.avi").touch()
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,team_side,player_number,split\n"
                "train.avi,0,1.5,serve,ours,8,train\n"
                "val.avi,2,3,attack,ours,8,val\n",
                encoding="utf-8",
            )
            records = load_manifest(manifest)
            summary = summarize_manifest(records)

            self.assertEqual(records[0].video_path, (root / "train.avi").resolve())
            self.assertEqual(records[0].player_number, "8")
            self.assertEqual(summary["records"], 2)
            self.assertEqual(summary["videos"], 2)
            self.assertEqual(summary["duration_seconds"], 2.5)

    def test_loads_optional_crop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,split,"
                "crop_x1,crop_y1,crop_x2,crop_y2\n"
                "match.avi,0,1,serve,train,0,0,1280,430\n",
                encoding="utf-8",
            )
            records = load_manifest(manifest, require_files=False)
            self.assertEqual(records[0].crop, (0, 0, 1280, 430))

    def test_loads_dig_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,split\n"
                "match.avi,0,1,dig,test\n",
                encoding="utf-8",
            )

            records = load_manifest(manifest, require_files=False)

            self.assertEqual(records[0].label, "dig")

    def test_rejects_partial_crop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,split,"
                "crop_x1,crop_y1,crop_x2,crop_y2\n"
                "match.avi,0,1,serve,train,0,,1280,430\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ManifestError, "all four crop"):
                load_manifest(manifest, require_files=False)

    def test_rejects_unknown_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,split\n"
                "missing.avi,0,1,celebrate,train\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ManifestError, "unknown label"):
                load_manifest(manifest, require_files=False)

    def test_rejects_invalid_time_range(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,split\n"
                "missing.avi,2,1,serve,train\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ManifestError, "must satisfy"):
                load_manifest(manifest, require_files=False)

    def test_rejects_non_finite_times(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = root / "annotations.csv"
                manifest.write_text(
                    "video_path,start_seconds,end_seconds,label,split\n"
                    f"missing.avi,{value},2,serve,train\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ManifestError, "finite"):
                    load_manifest(manifest, require_files=False)

    def test_rejects_same_video_in_multiple_splits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,split\n"
                "match.avi,0,1,serve,train\n"
                "match.avi,1,2,attack,val\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ManifestError, "Split leakage"):
                load_manifest(manifest, require_files=False)


if __name__ == "__main__":
    unittest.main()
