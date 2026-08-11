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

Upon submission, bot pins your repo to an exact commit, writes the catalog entry, and
opens the pull request; CI builds it, verifies it, and attaches the `.tapp` and a screenshot. 
Get something wrong and it says so on your issue within about twenty seconds — **edit the issue** and it checks again. No need to start over.

Tagging a release is worth doing but is not required: leave **Version** blank and your last commit
is used. Either way the entry ends up naming one exact commit, so what gets reviewed is what gets
published.

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


Only `name`, `author`, `repo`, `category` and `description` are required. `ref` may be a tag or a
40-character sha; leave it out and the build takes the repo's default branch, recording whichever
commit that was. Check it before you push:

```sh
python3 tools/entry.py --check tapps/
```

## Rules

- The repo must be public, and the tapp must be yours to publish.
- Source must build unmodified from the pinned commit with the current SDK.
- No obfuscated or pre-compiled blobs standing in for source.
- Anything that damages a device, corrupts a user's tapes, or misrepresents what it does gets
  removed.

Being listed here is not an endorsement, and tapps run on your device at your own risk.
