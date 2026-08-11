#!/usr/bin/env python3
"""Fixtures for the rendered issue-form format, and what the parser must make of them.

The bodies below are inline on purpose. GitHub's rendering is an undocumented contract — `###` per
field, the visible label as the heading, `_No response_` for a field left empty, an uppercase `X`
in a checked box, CRLF line endings — and the point of writing them out in full is that those
details are visible in the diff when one of them turns out to be wrong.

To re-capture from a live submission:

  gh issue view <n> --repo <owner>/<repo> --json body -q .body

  python3 -m unittest discover -s tools -p 'test_*.py' -v
"""

import tempfile
import unittest
from pathlib import Path

import issue_form

REPO_ROOT = Path(__file__).resolve().parent.parent
FORM = REPO_ROOT / '.github' / 'ISSUE_TEMPLATE' / 'submit-a-tapp.yml'

# GitHub delivers CRLF in the webhook payload, so the fixtures carry it.
FULL = '\r\n'.join([
    '### Name',
    '',
    'Drum Machine',
    '',
    '### Author',
    '',
    '@someone',
    '',
    '### Repository',
    '',
    'https://github.com/someone/drum-machine',
    '',
    '### Version (optional)',
    '',
    'v1.2.0',
    '',
    '### Category',
    '',
    'instrument',
    '',
    '### Description',
    '',
    'Eight pads, sixteen steps, swing you can feel.',
    '',
    '### Tags (optional)',
    '',
    'drums, Sequencer',
    '',
    '### License (optional)',
    '',
    'MIT',
    '',
    '### Notes (optional)',
    '',
    'Wants a /samples folder on the SD card.',
    '',
    '### Id (optional)',
    '',
    'drum-machine',
    '',
    '### Build sources (optional)',
    '',
    'src/app.c src/audio.c',
    '',
    '### Screenshot frames (optional)',
    '',
    '180',
    '',
    '### Confirmations',
    '',
    '- [X] The repository is public and the code is mine to publish',
    '- [X] It builds with the current SDK',
])

# Every optional left empty. A dropdown that was not required and not chosen renders the same way.
MINIMAL = '\n'.join([
    '### Name',
    '',
    'Tiny',
    '',
    '### Author',
    '',
    'me',
    '',
    '### Repository',
    '',
    'https://github.com/me/tiny',
    '',
    '### Version (optional)',
    '',
    '_No response_',
    '',
    '### Category',
    '',
    'toy',
    '',
    '### Description',
    '',
    'Does one thing.',
    '',
    '### Tags (optional)',
    '',
    '_No response_',
    '',
    '### License (optional)',
    '',
    '_No response_',
    '',
    '### Notes (optional)',
    '',
    '_No response_',
    '',
    '### Id (optional)',
    '',
    '_No response_',
    '',
    '### Build sources (optional)',
    '',
    '_No response_',
    '',
    '### Screenshot frames (optional)',
    '',
    '_No response_',
    '',
    '### Confirmations',
    '',
    '- [X] The repository is public and the code is mine to publish',
    '- [ ] It builds with the current SDK',
])


class Sections(unittest.TestCase):
    def test_full_body_maps_every_field(self):
        raw, extras, warnings = issue_form.parse(FULL)
        self.assertEqual(warnings, [])
        self.assertEqual(raw['name'], 'Drum Machine')
        self.assertEqual(raw['author'], '@someone')
        self.assertEqual(raw['repo'], 'https://github.com/someone/drum-machine')
        self.assertEqual(raw['ref'], 'v1.2.0')
        self.assertEqual(raw['category'], 'instrument')
        self.assertEqual(raw['description'], 'Eight pads, sixteen steps, swing you can feel.')
        self.assertEqual(raw['tags'], ['drums', 'Sequencer'])
        self.assertEqual(raw['license'], 'MIT')
        self.assertEqual(raw['build'], 'src/app.c src/audio.c')
        self.assertEqual(raw['screenshot_frames'], 180)
        self.assertEqual(extras['slug'], 'drum-machine')
        self.assertEqual(extras['notes'], 'Wants a /samples folder on the SD card.')
        self.assertEqual(extras['confirmations'],
                         [(True, 'The repository is public and the code is mine to publish'),
                          (True, 'It builds with the current SDK')])

    def test_blank_optionals_are_absent_not_empty(self):
        raw, extras, _ = issue_form.parse(MINIMAL)
        # dump() drops empties, so absence here is what keeps them out of the entry file.
        for key in ('ref', 'tags', 'license', 'build', 'screenshot_frames'):
            self.assertNotIn(key, raw, f"{key} should not survive `_No response_`")
        self.assertEqual(set(raw), {'name', 'author', 'repo', 'category', 'description'})
        self.assertEqual(extras['slug'], '')
        self.assertEqual(extras['notes'], '')
        self.assertEqual(extras['confirmations'][1], (False, 'It builds with the current SDK'))

    def test_no_response_is_case_and_emphasis_insensitive(self):
        for spelling in ('_No response_', '_no response_', '*No response*', '   '):
            self.assertEqual(issue_form.blank(spelling), '', spelling)

    def test_crlf_does_not_leak_into_values(self):
        raw, _, _ = issue_form.parse(FULL)
        for key, value in raw.items():
            if isinstance(value, str):
                self.assertNotIn('\r', value, key)


class Injection(unittest.TestCase):
    """A body is attacker-controlled text. It must not be able to move a section boundary."""

    def test_unknown_heading_inside_a_textarea_is_content(self):
        body = ('### Name\n\nX\n\n### Notes (optional)\n\n'
                '### Controls\n\nENC spins the piece.\n')
        _, extras, _ = issue_form.parse(body)
        self.assertEqual(extras['notes'], '### Controls\n\nENC spins the piece.')

    def test_a_second_known_heading_is_content_not_a_boundary(self):
        # Without the first-occurrence-wins rule this truncates the notes AND could overwrite
        # the real category with 'game'.
        body = ('### Category\n\ninstrument\n\n### Notes (optional)\n\n'
                'see below\n\n### Category\n\ngame\n')
        raw, extras, warnings = issue_form.parse(body)
        self.assertEqual(raw['category'], 'instrument')
        self.assertIn('### Category', extras['notes'])
        self.assertIn('game', extras['notes'])
        self.assertEqual(len(warnings), 1)

    def test_line_separator_cannot_forge_a_heading(self):
        # str.splitlines() breaks on U+2028; str.split('\n') does not. If this parser used the
        # former, the category below would be read as a section of its own.
        body = '### Name\n\nX ### Category  game\n'
        raw, _, _ = issue_form.parse(body)
        self.assertNotIn('category', raw)
        self.assertIn('Category', raw['name'])

    def test_shell_metacharacters_survive_as_literal_text(self):
        body = '### Name\n\n$(id) `whoami` && rm -rf /\n'
        raw, _, _ = issue_form.parse(body)
        self.assertEqual(raw['name'], '$(id) `whoami` && rm -rf /')


class Slugify(unittest.TestCase):
    def test_common_names(self):
        self.assertEqual(issue_form.slugify('Drum Machine'), 'drum-machine')
        self.assertEqual(issue_form.slugify('  Tapetris!  '), 'tapetris')
        self.assertEqual(issue_form.slugify('Café Noir'), 'cafe-noir')
        self.assertEqual(issue_form.slugify('8-bit  DRUMS'), '8-bit-drums')

    def test_returns_empty_when_there_is_nothing_to_slug(self):
        # The caller must ask for an Id rather than write a file called '.json'.
        self.assertEqual(issue_form.slugify('日本語'), '')
        self.assertEqual(issue_form.slugify('---'), '')
        self.assertEqual(issue_form.slugify('_No response_'), '')

    def test_never_exceeds_the_slug_limit_or_ends_in_a_dash(self):
        s = issue_form.slugify('a very long name that goes on and on and on past the limit')
        self.assertLessEqual(len(s), issue_form.MAX_SLUG)
        self.assertFalse(s.endswith('-'))


class Normalize(unittest.TestCase):
    def test_labels_fold_to_their_keys(self):
        self.assertEqual(issue_form.normalize('Version (optional)'), 'version')
        self.assertEqual(issue_form.normalize('Screenshot frames (optional)'), 'screenshot frames')
        self.assertEqual(issue_form.normalize('  REPOSITORY  '), 'repository')

    def test_every_label_in_the_table_is_already_normalised(self):
        for label in issue_form.LABELS:
            self.assertEqual(issue_form.normalize(label), label)


class CheckForm(unittest.TestCase):
    def test_the_shipped_form_matches_the_parser(self):
        self.assertEqual(issue_form.check_form(FORM), [],
                         'run: python3 tools/issue_form.py --check-form ' + str(FORM))

    def test_a_reworded_label_is_caught(self):
        yml = ('body:\n'
               '  - type: input\n'
               '    id: name\n'
               '    attributes:\n'
               '      label: Display name\n')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'form.yml'
            p.write_text(yml)
            errors = issue_form.check_form(p)
        joined = '\n'.join(errors)
        self.assertIn('display name', joined)  # the form has a field the parser ignores
        self.assertIn("'name'", joined)        # and lacks the one it reads

    def test_checkbox_option_labels_are_not_mistaken_for_fields(self):
        yml = ('body:\n'
               '  - type: checkboxes\n'
               '    id: confirmations\n'
               '    attributes:\n'
               '      label: Confirmations\n'
               '      options:\n'
               '        - label: The repository is public\n'
               '        - label: It builds with the current SDK\n')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'form.yml'
            p.write_text(yml)
            errors = issue_form.check_form(p)
        self.assertFalse([e for e in errors if 'the repository is public' in e], errors)


if __name__ == '__main__':
    unittest.main()
