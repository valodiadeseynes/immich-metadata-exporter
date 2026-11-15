import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from immich_export.metadata import Options, build_tags, capture_datetime, displayed_to_stored  # noqa: E402

ASSET = {
    "id": "a1",
    "type": "IMAGE",
    "originalFileName": "IMG_0001.jpg",
    "localDateTime": "2024-05-01T14:00:00.000Z",
    "fileCreatedAt": "2024-05-01T12:00:00.000Z",
    "exifInfo": {
        "dateTimeOriginal": "2024-05-01T12:00:00.000Z",
        "timeZone": "Europe/Paris",
        "description": "Pique-nique à Tours",
        "latitude": 47.39,
        "longitude": -0.68,
        "city": "Tours",
        "state": "Centre-Val de Loire",
        "country": "France",
        "rating": 4,
    },
    "tags": [{"value": "Voyage/Loire", "name": "Loire"}, {"value": "Famille", "name": "Famille"}],
    "people": [{"name": "Alice"}, {"name": ""}],
}
FACES = [
    {"imageWidth": 1000, "imageHeight": 500, "boundingBoxX1": 100, "boundingBoxX2": 200,
     "boundingBoxY1": 50, "boundingBoxY2": 150, "person": {"name": "Alice"}},
    {"imageWidth": 1000, "imageHeight": 500, "boundingBoxX1": 0, "boundingBoxX2": 10,
     "boundingBoxY1": 0, "boundingBoxY2": 10, "person": None},
]


class T(unittest.TestCase):
    def test_date_offset(self):
        local, off = capture_datetime(ASSET)
        self.assertEqual(local.isoformat(), "2024-05-01T14:00:00")
        self.assertEqual(off, "+02:00")

    def test_date_negative(self):
        a = dict(ASSET, localDateTime="2024-05-01T07:00:00.000Z")
        self.assertEqual(capture_datetime(a)[1], "-05:00")

    def test_tags(self):
        t = build_tags(ASSET, FACES, ["Été 2024"], Options(albums_as_keywords=True), (4000, 2000), 1)
        self.assertEqual(t["EXIF:DateTimeOriginal"], "2024:05:01 14:00:00")
        self.assertEqual(t["EXIF:OffsetTimeOriginal"], "+02:00")
        self.assertEqual(t["XMP-exif:DateTimeOriginal"], "2024:05:01 14:00:00+02:00")
        self.assertEqual(t["EXIF:GPSLongitudeRef#"], "W")
        self.assertEqual(t["EXIF:GPSLongitude"], 0.68)
        self.assertEqual(t["XMP-dc:Subject"], ["Loire", "Famille", "Alice", "Été 2024"])
        self.assertIn("Voyage|Loire", t["XMP-lr:HierarchicalSubject"])
        self.assertIn("Personnes|Alice", t["XMP-lr:HierarchicalSubject"])
        self.assertEqual(t["XMP-iptcExt:PersonInImage"], ["Alice"])
        self.assertEqual(t["XMP-xmp:Rating"], 4)
        reg = t["XMP-mwg-rs:RegionInfo"]
        self.assertEqual(reg["AppliedToDimensions"], {"W": 4000, "H": 2000, "Unit": "pixel"})
        self.assertEqual(len(reg["RegionList"]), 1)
        a = reg["RegionList"][0]["Area"]
        self.assertAlmostEqual(a["X"], 0.15)
        self.assertAlmostEqual(a["Y"], 0.2)
        self.assertEqual(t["IPTC:CodedCharacterSet"], "UTF8")

    def test_xmp_only(self):
        t = build_tags(ASSET, FACES, None, Options(), (4000, 2000), 1, xmp_only=True)
        self.assertTrue(all(k.startswith("XMP") for k in t), t.keys())

    def test_video(self):
        v = dict(ASSET, type="VIDEO")
        t = build_tags(v, FACES, None, Options(), None, 1)
        self.assertNotIn("EXIF:DateTimeOriginal", t)
        self.assertEqual(t["QuickTime:CreateDate"], "2024:05:01 12:00:00")
        self.assertNotIn("XMP-mwg-rs:RegionInfo", t)

    def test_orientation_roundtrip(self):
        # Le coin haut-gauche stocké (0.1, 0.1) apparaît en haut-droite pour l'orientation 6.
        x, y, w, h = displayed_to_stored(0.9, 0.1, 0.2, 0.1, 6)
        self.assertAlmostEqual(x, 0.1)
        self.assertAlmostEqual(y, 0.1)
        self.assertEqual((w, h), (0.1, 0.2))
        x, y, *_ = displayed_to_stored(0.1, 0.9, 0.1, 0.1, 8)  # 270° : haut-gauche → bas-gauche
        self.assertAlmostEqual(x, 0.1)
        self.assertAlmostEqual(y, 0.1)
        x, y, *_ = displayed_to_stored(0.9, 0.9, 0.1, 0.1, 3)
        self.assertAlmostEqual(x, 0.1)
        self.assertAlmostEqual(y, 0.1)

    def test_no_tz(self):
        a = dict(ASSET, exifInfo=dict(ASSET["exifInfo"], timeZone=None))
        t = build_tags(a, [], None, Options(), None, 1)
        self.assertNotIn("EXIF:OffsetTimeOriginal", t)
        self.assertEqual(t["XMP-exif:DateTimeOriginal"], "2024:05:01 14:00:00")


if __name__ == "__main__":
    unittest.main()
