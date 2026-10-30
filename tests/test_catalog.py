"""Offline catalog and collection tests. CLI: python3 -m unittest discover -s tests."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from painpoint_radar import pulse

CATALOG = Path(__file__).resolve().parent / 'fixtures' / 'catalogs' / 'acmemail.json'


class CatalogTests(unittest.TestCase):
    def test_catalog_loads(self) -> None:
        catalog = pulse.load_catalog(CATALOG)
        self.assertEqual(len(catalog['subreddits']), 14)
        self.assertEqual(len(catalog['providers']), 15)
        language = {'learnpython', 'PHPhelp', 'node', 'rails', 'django', 'laravel', 'golang', 'dotnet'}
        for name, entry in catalog['subreddits'].items():
            self.assertEqual(entry['comment_policy'], 'unknown')
            self.assertEqual(entry['policy_note'], 'fill by hand from the rules page; the tool must not fetch reddit.com')
            self.assertEqual(entry['policy_url'], f'https://www.reddit.com/r/{name}/about/rules')
            if name not in language:
                self.assertFalse({'email', 'e-mail', 'resend', 'ses'} & set(pulse.subreddit_terms(catalog, name, '')))

    def parsed(self, flags: list[str]) -> argparse.Namespace:
        with patch.object(pulse, 'cmd_collect', return_value=0) as collect:
            pulse.main(['collect'] + flags)
        args = collect.call_args.args[0]
        pulse.resolve_collect_args(args, pulse.load_catalog(args.catalog))
        return args

    def test_preset_fills_args(self) -> None:
        args = self.parsed(['--catalog', str(CATALOG), '--preset', 'inbound-email'])
        self.assertEqual(args.subs, 'learnpython,PHPhelp,node,webdev,AI_Agents,selfhosted')
        self.assertEqual((args.created_within, args.active_within, args.min_hits), ('180d', '60d', 2))

    def test_explicit_flags_win(self) -> None:
        args = self.parsed(['--catalog', str(CATALOG), '--preset', 'inbound-email', '--subs', 'node',
                            '--created-within', '90d', '--active-within', '30d', '--min-hits', '1'])
        self.assertEqual((args.subs, args.created_within, args.active_within, args.min_hits),
                         ('node', '90d', '30d', 1))

    def test_per_sub_regex_and_extra_keywords(self) -> None:
        catalog = pulse.load_catalog(CATALOG)
        broad = pulse.subreddit_keyword_re(catalog, 'webdev', 'custom phrase')
        language = pulse.subreddit_keyword_re(catalog, 'r/LEARNPYTHON', '')
        for term in ['email', 'e-mail', 'resend', 'ses']:
            self.assertIsNone(broad.search(term))
            self.assertIsNotNone(language.search(term))
        self.assertIsNotNone(broad.search('CUSTOM phrase'))
        self.assertIsNotNone(broad.search('Amazon SES'))

    def test_gate(self) -> None:
        args = argparse.Namespace(exclude_flair=set(), min_comments=0, min_score=0, min_hits=2)
        for title, body, expected in [('smtp', '', False), ('smtp smtp', '', True),
                                       ('smtp', 'smtp', True), ('unrelated', 'smtp smtp', False)]:
            rec = pulse.thread_record({'id': 'a', 'title': title, 'created_utc': 100},
                                      [{'body': body, 'created_utc': 100}], 100, 0,
                                      pulse.build_keyword_re(['smtp']))
            self.assertEqual(pulse.passes(rec, args, 0), (expected, 'ok' if expected else 'few-hits'))

    def test_missing_catalog_collection_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / 'missing.json')
            self.assertEqual(pulse.load_catalog(missing), {})
            with patch.object(pulse, 'Http') as http, patch.object(pulse, 'make_backend') as backend:
                http.return_value.requests = 0
                http.return_value.cache_hits = 0
                backend.return_value.posts_window.return_value = [
                    {'id': 'a', 'title': 'smtp smtp', 'created_utc': 100, 'subreddit': 'example'},
                    {'id': 'b', 'title': 'smtp', 'created_utc': 100, 'subreddit': 'example'}]
                backend.return_value.comments_for.return_value = []
                with patch.object(pulse.time, 'time', return_value=100), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(pulse.main(['collect', '--catalog', missing, '--subs', 'example',
                                                 '--keywords', 'smtp', '--min-hits', '2', '--min-comments', '0',
                                                 '--out', directory]), 0)
            summary = json.loads((Path(directory) / 'summary.json').read_text())
            self.assertEqual(summary['kept_by_sub'], {'example': 1})
            self.assertEqual(summary['min_hits'], 2)
            self.assertIsNone(summary['preset'])
            self.assertEqual(summary['product'], '')
            self.assertEqual(summary['threads_dropped'], {'few-hits': 1})

    def test_defaults_without_preset(self) -> None:
        args = self.parsed(['--subs', 'node'])
        self.assertEqual((args.created_within, args.active_within, args.min_hits), ('90d', '30d', 1))


if __name__ == '__main__':
    unittest.main()
