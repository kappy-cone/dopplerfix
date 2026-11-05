# OpenStreetMap view in viewer.html (prototype)

The user found QGIS too slow to be usable for looking at results. This prototype adds a **Map** tab to each
run's `viewer.html`, drawn on an OpenStreetMap basemap. The view is read-only and meant for a quick look; it
feeds nothing back into a run. QGIS remains milestone 6's plan (CLAUDE.md "QGIS MCP", `qgis/`) until the user
decides otherwise.

## What it shows
- **Every scored cell** at its refined location, coloured by log10 score (lower is better), up to
  `viewer.map_max_cells` (20 000, best first).
- **The top candidates** (`viewer.max_candidates`). Clicking one selects it, here and in the Compare tab.
- **The tracks assigned to the top candidates**, sampled every 2 s. The selected candidate's tracks are red,
  with a marker at each event's CPA point and a line from it to the candidate.

## How it is built
- **No new dependency.** It uses the pinned plotly.js 2.35.2 (`outputs/plotly.min.js`). Its MapLibre trace
  `scattermap` has a built-in `open-street-map` style that needs no API key. The style is configurable
  (`viewer.map_style`; plotly's styles include `carto-positron` and `carto-voyager`).
- **Small:** about 40 bytes per cell, plus the downsampled tracks. In a 600 s synthetic run (279 cells,
  6 tracks) the map data is 42 kB of a 527 kB viewer.
- **Code:** `plot.map_layers` builds the layer data from the ranked candidates and the TrackSet; `write_viewer`
  embeds it; `cli.write_outputs` passes it.

## Caveats
- **Online tiles.** The basemap tiles come from tile.openstreetmap.org. Offline, the layers still draw on a
  blank background, so the viewer otherwise still works offline as CLAUDE.md requires.
- **OSM tile usage policy:** attribution is required (shown under the map), and heavy or bulk use is not
  allowed. A person browsing a run is fine; prefetching tiles is not. The policy asks web pages to send a
  valid Referer. Served over `http://localhost` the tiles load (checked in a browser). **Opened as a local
  file, `file://`, the browser sends no Referer, and that case is unverified.** If tiles are refused there,
  serve the folder (`python -m http.server -d outputs`) or switch `viewer.map_style` to a Carto style (check
  its terms).
- **No satellite imagery.** The CLAUDE.md QGIS plan uses a satellite basemap. A satellite tile source is a
  licence question for the user.
- **No score raster.** The cells are drawn as points, not `score.tif`. A raster overlay (a PNG image layer)
  is a possible next step.

## Open decision (the user's)
Whether this view replaces the QGIS template for milestone 6, or complements it. If it replaces it, CLAUDE.md's
QGIS sections and `qgis/` change; nothing here touches them.
