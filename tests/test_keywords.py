"""Offline keyword CLI tests. CLI: python3 -m unittest discover -s tests."""
import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from painpoint_radar import pulse

FIXTURES = Path(__file__).parent / 'fixtures'


class KeywordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'catalog.json'
        self.path.write_bytes((FIXTURES / 'catalogs' / 'acmemail.json').read_bytes())
        self.original = self.path.read_bytes()
        self.addCleanup(setattr, pulse, 'USER_AGENT', pulse.USER_AGENT)

    def call(self, *args: str) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(pulse.main(['keywords', *args, '--catalog', str(self.path)]), 0)
        return output.getvalue()

    def error(self, *args: str) -> str:
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as error:
            pulse.main(['keywords', *args, '--catalog', str(self.path)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.path.read_bytes(), self.original)
        return output.getvalue()

    def catalog(self) -> dict:
        return json.loads(self.path.read_text())

    def source(self, text: str, encoding: str = 'utf-8') -> str:
        path = Path(self.directory.name) / 'input.csv'
        path.write_text(text, encoding=encoding)
        return str(path)

    def test_utf16_import(self) -> None:
        output = self.call('import', str(FIXTURES / 'ahrefs-utf16.tsv'), '--into', 'core')
        self.assertEqual(output, '+ hosted inbox (200)\n+ rare routing (20)\n+ message observability (300)\n'
                         '3 added, 2 already present, 1 dropped (short or below --min-volume)\n')
        self.assertEqual(self.catalog()['product']['keywords']['core'][-3:],
                         ['hosted inbox', 'rare routing', 'message observability'])
        original = json.loads(self.original)
        original['product']['keywords']['core'] += ['hosted inbox', 'rare routing', 'message observability']
        self.assertEqual(self.path.read_text(), json.dumps(original, indent=2, ensure_ascii=False) + '\n')

    def test_volume_filter(self) -> None:
        output = self.call('import', str(FIXTURES / 'ahrefs-utf16.tsv'), '--into', 'core', '--min-volume', '100')
        self.assertNotIn('rare routing', output)
        self.assertIn('2 added, 2 already present, 2 dropped', output)

    def test_dry_run_output_matches_write(self) -> None:
        args = ('import', str(FIXTURES / 'ahrefs-utf16.tsv'), '--into', 'generic')
        dry = self.call(*args, '--dry-run')
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(dry, self.call(*args))
        self.assertIn('hosted inbox', self.catalog()['product']['keywords']['generic'])

    def test_serp_existing_group(self) -> None:
        source = self.source('keyword\nmailgun alternative\nsmtp service\nfresh alternative\nsmtp\n')
        output = self.call('import', source, '--into', 'serp:alternatives')
        self.assertIn('2 added, 2 already present, 0 dropped', output)
        self.assertEqual(self.catalog()['serp_keywords']['alternatives'][-2:], ['fresh alternative', 'smtp'])
        self.assertEqual(self.catalog()['serp_keywords']['_note'], json.loads(self.original)['serp_keywords']['_note'])

    def test_serp_new_group(self) -> None:
        self.call('import', str(FIXTURES / 'plain.csv'), '--into', 'serp:newgroup')
        self.assertEqual(self.catalog()['serp_keywords']['newgroup'], ['queue monitoring', 'smtp', 'message routing'])

    def test_bad_destination(self) -> None:
        for value in ('other', 'serp:', 'serp:_note'):
            with self.subTest(value=value):
                message = self.error('import', str(FIXTURES / 'plain.csv'), '--into', value)
                for form in ('core', 'generic', 'serp:<group>'):
                    self.assertIn(form, message)

    def test_volume_required(self) -> None:
        self.assertIn('volume column', self.error('import', str(FIXTURES / 'plain.csv'), '--into', 'core',
                                                '--min-volume', '100'))

    def test_list_all_groups(self) -> None:
        catalog = self.catalog()
        catalog['subreddits']['node']['extra_keywords'] = ['local term']
        self.path.write_text(json.dumps(catalog))
        before = self.path.read_bytes()
        lines = self.call('list').splitlines()
        self.assertTrue(lines[0].startswith('core (17): smtp, '))
        self.assertTrue(lines[1].startswith('generic (6): '))
        aliases = [alias for group in catalog['providers'].values() for alias in group]
        self.assertTrue(lines[2].startswith(f'competitors ({len(aliases)}): '))
        self.assertIn('ses*', lines[2])
        self.assertIn('resend*', lines[2])
        self.assertIn('extra_keywords:node (1): local term', lines)
        for name, terms in catalog['serp_keywords'].items():
            if name != '_note':
                self.assertIn(f"serp_keywords:{name} ({len(terms)}): {', '.join(terms)}", lines)
        self.assertEqual(len(lines), 4 + len(catalog['serp_keywords']) - 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_seed_shared_additions_and_dry_run(self) -> None:
        with patch('painpoint_radar.seed.site_keywords', return_value=['SMTP', 'New  Seed', 'new seed', 'go']) as seeded:
            dry = self.call('seed', '--dry-run', '--max-terms', '9', '--min-count', '4')
            seeded.assert_called_once_with('https://acmemail.example', max_terms=9, min_count=4)
            self.assertEqual(self.path.read_bytes(), self.original)
            self.assertEqual(dry, self.call('seed'))
        self.assertEqual(dry, '+ new seed\n1 added, 2 already present, 1 dropped (short or below --min-volume)\n')
        self.assertEqual(self.catalog()['product']['keywords']['core'][-1], 'new seed')

    def test_seed_explicit_site_and_generic(self) -> None:
        with patch('painpoint_radar.seed.site_keywords', return_value=['new seed']) as seeded:
            self.call('seed', '--site', 'https://example.com', '--into', 'generic')
            seeded.assert_called_once_with('https://example.com', max_terms=25, min_count=3)
        self.assertEqual(self.catalog()['product']['keywords']['generic'][-1], 'new seed')

    def test_seed_missing_site(self) -> None:
        catalog = self.catalog()
        catalog['product'].pop('site')
        self.path.write_text(json.dumps(catalog))
        self.original = self.path.read_bytes()
        with patch('painpoint_radar.seed.site_keywords') as seeded:
            self.assertIn('product.site', self.error('seed'))
            seeded.assert_not_called()

    def test_plain_csv(self) -> None:
        output = self.call('import', str(FIXTURES / 'plain.csv'), '--into', 'core')
        self.assertEqual(output, '+ queue monitoring\n+ message routing\n'
                         '2 added, 1 already present, 1 dropped (short or below --min-volume)\n')

    def test_headerless(self) -> None:
        output = self.call('import', self.source('routing\nmonitoring\n'), '--into', 'core')
        self.assertTrue(output.startswith('+ routing\n+ monitoring\n'))

    def test_header_heuristic(self) -> None:
        for text in ('Search Terms\nfresh phrase\n', 'Term\nfresh phrase\n', 'term\nfresh phrase\n',
                     'rank\n100\n', 'Keyword Ideas\nfresh phrase\n'):
            with self.subTest(text=text):
                output = self.call('import', self.source(text), '--into', 'core', '--dry-run')
                self.assertNotIn('+ ' + text.splitlines()[0].casefold() + '\n', output)

    def test_column_overrides_and_utf8_bom(self) -> None:
        source = self.source('Rank;Search Phrase;Monthly\n1;"New   Phrase";250\n2;low phrase;10\n', 'utf-8-sig')
        output = self.call('import', source, '--into', 'core', '--col', 'search phrase',
                           '--volume-col', 'MONTHLY', '--min-volume', '100')
        self.assertEqual(output, '+ new phrase (250)\n1 added, 0 already present, 1 dropped (short or below --min-volume)\n')

    def test_utf16_big_endian_and_keyword_prefix(self) -> None:
        source = self.source('\ufeffCountry\tKeyword phrase\tVolume\nUS\tBrand New\t200\n', 'utf-16-be')
        self.assertTrue(self.call('import', source, '--into', 'core').startswith('+ brand new (200)\n'))

    def test_fallback_delimiters(self) -> None:
        for source in ('keyword\nnew phrase\n', 'Keyword\tVolume\nnew phrase\t200\n'):
            with self.subTest(source=source), patch('painpoint_radar.keywords.csv.Sniffer.sniff', side_effect=csv.Error):
                self.assertIn('+ new phrase', self.call('import', self.source(source), '--into', 'core', '--dry-run'))

    def test_dedupe_union_and_quotes(self) -> None:
        catalog = self.catalog()
        catalog['subreddits']['node']['extra_keywords'] = ['Local Term']
        self.path.write_text(json.dumps(catalog))
        output = self.call('import', self.source("keyword\nSMTP\nEMAIL\nSendGrid\nlocal  term\n'Fresh  Term'\nfresh term\n"),
                           '--into', 'generic')
        self.assertEqual(output, '+ fresh term\n1 added, 5 already present, 0 dropped (short or below --min-volume)\n')

    def test_unknown_column(self) -> None:
        self.assertIn('not found', self.error('import', str(FIXTURES / 'plain.csv'), '--into', 'core', '--col', 'missing'))

    def test_product_selection_and_catalog_override(self) -> None:
        with patch.object(pulse, 'CATALOGS_DIR', self.path.parent):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(pulse.main(['keywords', 'list', '--product', 'catalog']), 0)
            self.assertIn('core (17)', output.getvalue())
            self.assertIn('core (17)', self.call('list', '--product', 'missing'))

    def test_fetch_uses_shared_user_agent(self) -> None:
        from painpoint_radar import seed
        with patch.object(pulse, 'USER_AGENT', 'test shared agent'), patch('painpoint_radar.seed.urllib.request.urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'<xml/>'
            self.assertEqual(seed.fetch('https://example.com/sitemap.xml'), '<xml/>')
            self.assertEqual(urlopen.call_args.args[0].get_header('User-agent'), 'test shared agent')


if __name__ == '__main__':
    unittest.main()
