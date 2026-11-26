# Changelog

What's new on [Northeast Snow](https://tpkfounder.github.io/northeast-snow/), newest first.

## 2026-11-26

**Launch, plus a first round of additions.**

### Added
- **Storm timeline:** every mountain's popup charts snow vs rain in 6-hour blocks for the next 72 hours.
- **Snow on the ground:** modeled snow depth (NOAA SNODAS) at every summit, base and backcountry zone. Backcountry coverage scores now use it.
- **Powder alerts:** an RSS feed and an on-page banner for anything forecast 6"+ in the next 72 hours.
- **Stale-data warning:** the page says when data is over 8 hours old or a data source failed on the last update.
- **Indy Pass:** 39 alpine Indy resorts in NY and New England, with their own filter.
- **Four new backcountry zones:** Mount Mansfield, Adirondack High Peaks, Katahdin and Granite Backcountry Alliance glades (Maine), plus the Sherburne and Gulf of Slides ski trails. Each zone can be toggled on and off.
- **Season status:** opening dates and snow-report links for every resort. Resorts that aren't open yet score 0 for the weekend.
- **Weekend tools:** max-drive filter, best-bet summary, and ranks within the selected pass.

### Changed
- Smaller map markers when zoomed out, and labels that move to avoid overlapping.
- Loon's map point moved to the real ski area.
- Windham removed: it left the Ikon Pass in 2025.

### Security
- Content-Security-Policy, integrity-pinned Leaflet, escaped output, least-privilege workflow with SHA-pinned actions, Dependabot.
