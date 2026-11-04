"""Rebuild qgis/doppler_template.qgz (and qgis/styles/*.qml) from scratch, headlessly.

Run with QGIS's bundled Python (not the project venv), isolated from user site-packages:

    QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python -s qgis/build_project.py
    QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python -s qgis/build_project.py --verify

The project only *reads* pipeline outputs (CLAUDE.md: no pipeline logic in QGIS).
Layer sources point at outputs/latest/ (a symlink to the newest run) and
inputs/search_regions.gpkg, stored as paths relative to the project file.
Build and verify need a real run in outputs/latest (``python cli.py locate ...``).
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

from qgis.core import (Qgis, QgsApplication, QgsCategorizedSymbolRenderer, QgsColorRampShader, QgsFillSymbol,
                       QgsGradientColorRamp, QgsLineSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsProject,
                       QgsRasterLayer, QgsRasterShader, QgsReferencedRectangle, QgsRendererCategory,
                       QgsRuleBasedRenderer, QgsSingleBandPseudoColorRenderer, QgsTextFormat, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling, QgsCoordinateReferenceSystem, QgsCoordinateTransform)
from qgis.PyQt.QtGui import QColor

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LATEST = REPO / "outputs" / "latest"          # keep the symlink in the path (never resolve it)
REGIONS = REPO / "inputs" / "search_regions.gpkg"
STYLES = HERE / "styles"
OUT = HERE / "doppler_template.qgz"

XYZ = {
    "Esri World Boundaries and Places":
        "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    "Esri World Transportation":
        "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
    "Esri World Imagery":
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}
GROUPS = ["events", "candidates", "tracks", "stages", "grid", "score", "inputs", "basemap"]
# (group, layer name, source kind, source)
LAYERS = [
    ("events", "event_cpa_points", "gpkg", "event_cpa_points"),
    ("candidates", "candidates", "gpkg", "candidates"),
    ("tracks", "tracks", "gpkg", "tracks"),
    ("stages", "stage_silence", "gpkg", "stage_silence"),
    ("stages", "stage_offset", "gpkg", "stage_offset"),
    ("stages", "stage_cpa", "gpkg", "stage_cpa"),
    ("grid", "grid", "gpkg", "grid"),
    ("score", "score", "tif", "score.tif"),
    ("inputs", "search_regions", "regions", "search_regions"),
]


def _xyz_uri(url: str) -> str:
    from urllib.parse import quote

    return f"type=xyz&url={quote(url, safe=':/')}&zmax=19&zmin=0"


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def style_candidates(lyr: QgsVectorLayer) -> None:
    """By rank (coalesce(rank, interim_rank): interim cascade rank until milestone 5)."""
    root = QgsRuleBasedRenderer.Rule(None)
    rank = 'coalesce("rank", "interim_rank")'
    for label, expr, color, size, shape in [
        ("rank 1", f"{rank} = 1", "#e41a1c", 6.0, "star"),
        ("rank 2–3", f"{rank} IN (2, 3)", "#ff7f00", 4.5, "circle"),
        ("rank 4–10", f"{rank} BETWEEN 4 AND 10", "#ffd92f", 3.5, "circle"),
        ("rank > 10", "ELSE", "#bdbdbd", 2.2, "circle"),
    ]:
        sym = QgsMarkerSymbol.createSimple({"name": shape, "color": color, "size": str(size),
                                            "outline_color": "#000000", "outline_width": "0.3"})
        rule = QgsRuleBasedRenderer.Rule(sym, 0, 0, "" if expr == "ELSE" else expr, label)
        if expr == "ELSE":
            rule.setIsElse(True)
        root.appendChild(rule)
    lyr.setRenderer(QgsRuleBasedRenderer(root))
    pal = QgsPalLayerSettings()
    pal.fieldName = rank
    pal.isExpression = True
    fmt = QgsTextFormat()
    fmt.setSize(9)
    fmt.setColor(QColor("white"))
    buf = fmt.buffer()
    buf.setEnabled(True)
    buf.setColor(QColor("black"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    pal.dist = 1.5
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)


def style_tracks(lyr: QgsVectorLayer) -> None:
    cats = []
    for val, color in (("prop", "#4daf4a"), ("jet", "#377eb8"), ("unknown", "#999999")):
        sym = QgsLineSymbol.createSimple({"color": color, "width": "0.8"})
        cats.append(QgsRendererCategory(val, sym, val))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("class", cats))


def style_fill(lyr: QgsVectorLayer, rgb: str, alpha: int) -> None:
    lyr.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": f"{rgb},{alpha}", "outline_color": f"{rgb},200", "outline_width": "0.15"}))


def style_grid(lyr: QgsVectorLayer) -> None:
    lyr.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "255,255,255,70", "outline_width": "0.05"}))


def style_regions(lyr: QgsVectorLayer) -> None:
    lyr.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "#00e5ff", "outline_width": "0.6", "outline_style": "dash"}))


def style_events(lyr: QgsVectorLayer) -> None:
    cats = []
    for k, color in enumerate(("#00e5ff", "#ff00ff", "#ffff33", "#ffffff")):
        sym = QgsMarkerSymbol.createSimple({"name": "triangle", "color": color, "size": "2.6",
                                            "outline_color": "#000000", "outline_width": "0.2"})
        cats.append(QgsRendererCategory(k, sym, f"event {k}"))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("event", cats))


def style_score(lyr: QgsRasterLayer, vmin: float = 0.0, vmax: float = 2.0) -> None:
    """Colour ramp, low (good) = bright. Interim cascade-consistency range 0–2 until milestone 5."""
    ramp = QgsGradientColorRamp(QColor("#fde725"), QColor("#440154"))
    ramp.setStops([])
    shader_fn = QgsColorRampShader(vmin, vmax, ramp, QgsColorRampShader.Interpolated)
    shader_fn.classifyColorRamp(6, -1)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)
    r = QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), 1, shader)
    r.setClassificationMin(vmin)
    r.setClassificationMax(vmax)
    lyr.setRenderer(r)
    lyr.renderer().setOpacity(0.75)


STYLE_FN = {
    "candidates": style_candidates, "tracks": style_tracks, "event_cpa_points": style_events,
    "stage_cpa": lambda l: style_fill(l, "31,120,180", 70),
    "stage_offset": lambda l: style_fill(l, "255,127,0", 90),
    "stage_silence": lambda l: style_fill(l, "51,160,44", 120),
    "grid": style_grid, "search_regions": style_regions, "score": style_score,
}


# ---------------------------------------------------------------------------
# Build / verify
# ---------------------------------------------------------------------------
def make_layer(kind: str, src: str, name: str):
    if kind == "gpkg":
        return QgsVectorLayer(f"{LATEST / 'results.gpkg'}|layername={src}", name, "ogr")
    if kind == "tif":
        return QgsRasterLayer(str(LATEST / src), name, "gdal")
    if kind == "regions":
        return QgsVectorLayer(f"{REGIONS}|layername={src}", name, "ogr")
    raise ValueError(kind)


def build() -> int:
    if not (LATEST / "results.gpkg").exists():
        print(f"no run at {LATEST}: run `python cli.py locate --scenario ...` first", file=sys.stderr)
        return 2
    STYLES.mkdir(exist_ok=True)
    prj = QgsProject.instance()
    prj.clear()
    prj.setTitle("Acoustic Doppler geolocation")
    prj.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    prj.setEllipsoid("EPSG:7030")                       # WGS84
    prj.setDistanceUnits(Qgis.DistanceUnit.Meters)
    prj.setAreaUnits(Qgis.AreaUnit.SquareMeters)
    prj.setFilePathStorage(Qgis.FilePathType.Relative)
    root = prj.layerTreeRoot()
    groups = {g: root.addGroup(g) for g in GROUPS}

    problems = []
    for group, name, kind, src in LAYERS:
        lyr = make_layer(kind, src, name)
        if not lyr.isValid():
            problems.append(f"{name}: invalid source {lyr.source()}")
        STYLE_FN[name](lyr)
        qml = STYLES / f"{name}.qml"
        lyr.saveNamedStyle(str(qml))
        lyr.loadNamedStyle(str(qml))                    # the committed .qml is what the project carries
        prj.addMapLayer(lyr, False)
        groups[group].addLayer(lyr)
    for name, url in XYZ.items():                       # overlays above imagery; streamed, no API key
        lyr = QgsRasterLayer(_xyz_uri(url), name, "wms")
        if not lyr.isValid():
            problems.append(f"{name}: invalid XYZ layer")
        prj.addMapLayer(lyr, False)
        groups["basemap"].addLayer(lyr)

    regions = prj.mapLayersByName("search_regions")[0]
    ext = QgsCoordinateTransform(regions.crs(), prj.crs(), prj).transformBoundingBox(regions.extent())
    ext.scale(1.15)
    prj.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(ext, prj.crs()))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if not prj.write(str(OUT)):
        print(f"could not write {OUT}", file=sys.stderr)
        return 1
    print(f"wrote {OUT} and {len(list(STYLES.glob('*.qml')))} styles in {STYLES}")
    return 0


def verify(render: Path | None = None) -> int:
    """Open the template in a fresh project; every layer must resolve against outputs/latest."""
    prj = QgsProject.instance()
    prj.clear()
    if not prj.read(str(OUT)):
        print(f"cannot read {OUT}", file=sys.stderr)
        return 1
    ok = True
    order = [c.name() for c in prj.layerTreeRoot().children()]
    print("groups:", order)
    ok &= order == GROUPS
    for node in prj.layerTreeRoot().findLayers():
        lyr = node.layer()
        group = node.parent().name()
        n = lyr.featureCount() if isinstance(lyr, QgsVectorLayer) else None
        valid = lyr.isValid()
        print(f"  [{group:10s}] {lyr.name():34s} valid={valid} features={n} source={lyr.source()[:90]}")
        ok &= valid
    with zipfile.ZipFile(OUT) as z:
        qgs = z.read(next(n for n in z.namelist() if n.endswith(".qgs"))).decode()
    rel = "./../outputs/latest/results.gpkg" in qgs or "../outputs/latest/results.gpkg" in qgs
    print("relative sources:", rel, "| absolute repo path present:", str(REPO) in qgs)
    ok &= rel and str(REPO) not in qgs
    print("crs", prj.crs().authid(), "ellipsoid", prj.ellipsoid(), "distance units", prj.distanceUnits(),
          "relative paths", prj.filePathStorage() == Qgis.FilePathType.Relative)
    ok &= prj.crs().authid() == "EPSG:3857" and prj.ellipsoid() == "EPSG:7030"
    if render:
        from qgis.core import QgsMapRendererParallelJob, QgsMapSettings
        from qgis.PyQt.QtCore import QSize

        data = [n.layer() for n in prj.layerTreeRoot().findLayers()
                if n.parent().name() not in ("basemap", "grid")]
        cands = prj.mapLayersByName("candidates")[0]
        ext = QgsCoordinateTransform(cands.crs(), prj.crs(), prj).transformBoundingBox(cands.extent())
        ext.grow(max(ext.width(), ext.height(), 1.0) * 0.6)
        ms = QgsMapSettings()
        ms.setLayers(data)
        ms.setDestinationCrs(prj.crs())
        ms.setExtent(ext)
        ms.setOutputSize(QSize(900, 900))
        ms.setBackgroundColor(QColor("#202020"))
        job = QgsMapRendererParallelJob(ms)
        job.start()
        job.waitForFinished()
        job.renderedImage().save(str(render))
        print("rendered", render)
    print("VERIFY", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="open the saved template and check every layer")
    ap.add_argument("--render", type=Path, help="with --verify: render data layers (no basemap) to a PNG")
    a = ap.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    try:
        return verify(a.render) if a.verify else build()
    finally:
        app.exitQgis()


if __name__ == "__main__":
    sys.exit(main())
