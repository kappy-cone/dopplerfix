"""The committed QGIS template: group order, relative sources under outputs/latest, styles, basemaps.
(Full open-and-resolve verification needs QGIS: qgis/build_project.py --verify.)"""
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

QGZ = Path("qgis/doppler_template.qgz")
GROUPS = ["events", "candidates", "tracks", "stages", "grid", "score", "inputs", "basemap"]


def _qgs():
    with zipfile.ZipFile(QGZ) as z:
        return ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".qgs"))))


def test_group_order_and_layers():
    root = _qgs()
    tree = root.find("layer-tree-group")
    assert [g.get("name") for g in tree.findall("layer-tree-group")] == GROUPS
    stages = tree.find("layer-tree-group[@name='stages']")
    assert [l.get("name") for l in stages.findall("layer-tree-layer")] == ["stage_silence", "stage_offset", "stage_cpa"]
    base = [l.get("name") for l in tree.find("layer-tree-group[@name='basemap']").findall("layer-tree-layer")]
    assert base[-1] == "Esri World Imagery" and len(base) == 3
    text = ET.tostring(root, encoding="unicode")
    assert "cartocdn" not in text.lower()
    assert "World_Boundaries_and_Places" in text and "World_Transportation" in text


def test_sources_relative_to_latest():
    root = _qgs()
    srcs = [d.text for d in root.iter("datasource") if d.text and "type=xyz" not in d.text and d.text.strip()]
    assert srcs and all(s.lstrip("./").startswith(("outputs/latest/", "inputs/")) and s.startswith("..") for s in srcs), srcs
    assert root.find(".//Paths/Absolute").text == "false"
    assert root.find(".//Measure/Ellipsoid").text == "EPSG:7030"
    assert root.find(".//Measurement/DistanceUnits").text == "meters"
    assert root.find("projectCrs/spatialrefsys/authid").text == "EPSG:3857"


def test_styles_committed():
    for n in ("candidates", "tracks", "stage_cpa", "stage_offset", "stage_silence", "score", "grid",
              "event_cpa_points", "search_regions"):
        assert Path(f"qgis/styles/{n}.qml").stat().st_size > 0
    assert re.search(r'coalesce\(&quot;rank&quot;|coalesce\("rank"', Path("qgis/styles/candidates.qml").read_text())
