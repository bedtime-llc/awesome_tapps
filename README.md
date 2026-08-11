# awesome tapps

The community catalog of **tapps** — apps for [*tape!*](https://b.edti.me/projects/tape/).

Submit yours with **[one form](../../issues/new?template=submit-a-tapp.yml)**. It gets built from
source, checked against the device's loader, run in the emulator for a screenshot, and published to
**[b.edti.me/projects/tape/tapps](https://b.edti.me/projects/tape/tapps/)** where anyone can
download it.

Browse the catalog → **<https://b.edti.me/projects/tape/tapps/>**
Write a tapp → **[tape_sdk](https://github.com/bedtime-llc/tape_sdk)** ·
[API reference](https://sdk.b.edti.me) · [build one in your browser](https://tapp.b.edti.me)

---

## Submitting a tapp

Your tapp stays in **your** repo. This one just holds a few lines of metadata pointing at it.

1. **Check it builds** with the current SDK, ideally before you submit:
   ```sh
   git clone https://github.com/bedtime-llc/tape_sdk
   ./tape_sdk/tapp-build path/to/your/tapp
   ```
   `tapp-build` refuses to write the artifact if it would not load on the device.
2. **[Open a submission](../../issues/new?template=submit-a-tapp.yml)** and fill in the form.
   No fork, no clone, no file to get right.

That is the whole flow. A bot pins your repo to an exact commit, writes the catalog entry, and
opens the pull request; CI builds it, verifies it, and attaches the `.tapp` and a screenshot to the
run so both of us can see what we are shipping. Get something wrong and it says so on your issue
within about twenty seconds — **edit the issue** and it checks again. No need to start over.

Tagging a release is worth doing but is not required: leave **Version** blank and your last commit
is used. Either way the entry ends up naming one exact commit, so what gets reviewed is what gets
published.

Nothing else goes in this repo — no sources, no binaries, no images.

### What the form asks for

| field | | |
|---|---|---|
| Name | required | Display name, max 48 chars |
| Author | required | Your name or GitHub handle |
| Repository | required | Public GitHub URL |
| Version | optional | A release tag or a commit sha. Blank uses your last commit |
| Category | required | `instrument`, `effect`, `utility`, `game` or `toy` |
| Description | required | One line, max 200 chars |
| Tags | optional | Up to 6, comma separated, lowercase |
| License | optional | e.g. `MIT` |
| Notes | optional | Anything a reviewer should know. Goes on the PR, not the site |
| Id | optional | Your tapp's id in its URL. Defaults to your name, slugified |
| Build sources | optional | Source files, if pointing `tapp-build` at the repo is not enough |
| Screenshot frames | optional | Frames to run before the capture (default 90 ≈ 3 s) |

### Updating a tapp

Submit the form again with the same **Id**, or edit your original issue. The bot refreshes the same
pull request. The download URL never changes, so existing links keep working.

### Hand-writing an entry

You do not need this — it is how the bot's output is shaped, and how a maintainer fixes a typo.
An entry is one JSON file, `tapps/<id>.json`:

```json
{
  "name": "Drum Machine",
  "author": "someone",
  "repo": "https://github.com/someone/drum-machine",
  "ref": "v1.2.0",
  "category": "instrument",
  "description": "Eight pads, sixteen steps.",
  "tags": ["drums", "sequencer"],
  "license": "MIT"
}
```

Only `name`, `author`, `repo`, `category` and `description` are required. `ref` may be a tag or a
40-character sha; leave it out and the build takes the repo's default branch, recording whichever
commit that was. Check it before you push:

```sh
python3 tools/entry.py --check tapps/
```

---

## What CI does

On a **submission issue** (`.github/workflows/submit.yml`) — no secrets, no compiler:

0. `tools/issue_form.py` reads your answers out of the issue, `tools/submit_entry.py` resolves the
   version to a commit with `git ls-remote`, writes `tapps/<id>.json`, and reports back on the
   issue. Once a maintainer approves it, the same workflow opens the pull request.

On a **pull request** (`.github/workflows/validate.yml`) — no secrets, nothing published:

1. `tools/entry.py` validates the entry.
2. `tools/build_entry.py` shallow-clones your repo at the pinned ref and builds it with the SDK's
   `tapp-build` (clang 18 + `ld.lld`). A vendored `sdk/` submodule in your repo is deliberately
   ignored — the build uses the current SDK, so what is published matches the current firmware.
3. `verify-tapp.sh` runs the device loader's own checks: ELF shape, supported relocations, no
   `.ARM.exidx`, a valid manifest, a defined entry point, no 64-bit float instructions, and every
   imported symbol actually exported by the firmware.
4. `tools/screenshot.mjs` runs the artifact in the emulator — the real ARM binary, the same wasm
   build the browser uses — and saves the 400×240 screen. The emulator is fetched from
   <https://tapp.b.edti.me/emu/> on every run and cached outside this repo, so a local screenshot
   and CI's are produced by the same build by construction. If the tapp calls an API that emulator
   does not implement, the run fails and names the symbol rather than publishing a garbage image.

On **merge** (`.github/workflows/publish.yml`) the same build runs, then the `.tapp`, the
screenshot and a `data/tapps.json` entry are pushed to a `dev-tapps-<slug>` branch of the website
repo as a pull request. A human merges it after checking the preview.

## Rules

- The repo must be public, and the tapp must be yours to publish.
- Source must build unmodified from the pinned commit with the current SDK.
- No obfuscated or pre-compiled blobs standing in for source.
- Anything that damages a device, corrupts a user's tapes, or misrepresents what it does gets
  removed.

Being listed here is not an endorsement, and tapps run on your device at your own risk.