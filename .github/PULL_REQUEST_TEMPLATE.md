Submitting a tapp? You almost certainly want the
**[submission form](../../issues/new?template=submit-a-tapp.yml)** instead — fill it in and a bot
pins your repo to a commit, writes the entry and opens the pull request for you.

This template is for hand-written entries: a maintainer fixing a typo, or anyone who would rather
edit the file directly. One JSON file, `tapps/<id>.json`, and nothing else — no sources, no
`.tapp`, no screenshot. CI builds and captures those.

```json
{
  "name": "Display name, max 48 characters",
  "author": "Your name or GitHub handle",
  "repo": "https://github.com/you/your-tapp",
  "ref": "v1.0.0",
  "category": "instrument",
  "description": "One line, max 200 characters. What it does.",
  "tags": ["drums", "sequencer"],
  "license": "MIT"
}
```

- The filename without `.json` is your tapp's id on the site: lowercase, dashes, permanent.
- **`ref`** is optional — a release tag, or a 40-character commit sha. Leave it out and the build
  takes the repo's default branch, recording whichever commit that turned out to be.
- **`category`** is one of `instrument`, `effect`, `utility`, `game`, `toy`.
- **`tags`** up to 6, lowercase. **`license`** optional but appreciated.
- Two more keys if you need them: **`build`** names your sources explicitly (`"src/app.c src/audio.c"`)
  for when pointing `tapp-build` at the repo is not enough, and **`screenshot_frames`** sets how many
  guest frames to run before the capture (default 90, about three seconds) — raise it if your tapp
  opens on a splash screen.

### Checklist

- [ ] `python3 tools/entry.py --check tapps/` passes
- [ ] The repo is public and the code is mine to publish
- [ ] It builds with the current SDK: `./tape_sdk/tapp-build path/to/my/tapp`

### Anything worth knowing?

<!-- Needs a specific SD card layout? Known limitations? Say so here. -->

---

CI will attach the built `.tapp` and a screenshot to this pull request. If the build fails, the log
names the exact gate that rejected it — the same checks the device's loader runs.
