"""Offline product migration and CLI tests. CLI: python3 -m unittest discover -s tests."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from painpoint_radar import pulse

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
FIXTURE_CATALOG = FIXTURES / 'catalogs' / 'acmemail.json'
_saved_home = None


def setUpModule() -> None:
    """--product acmemail resolves through $PAINPOINT_RADAR_HOME to the fictional test catalog."""
    global _saved_home
    _saved_home = os.environ.get('PAINPOINT_RADAR_HOME')
    os.environ['PAINPOINT_RADAR_HOME'] = str(FIXTURES)


def tearDownModule() -> None:
    if _saved_home is None:
        os.environ.pop('PAINPOINT_RADAR_HOME', None)
    else:
        os.environ['PAINPOINT_RADAR_HOME'] = _saved_home

# Copied from the pre-migration catalog, independently of the new composition.
OLD_SUBREDDIT_TERMS = {'learnpython': ['smtp',
                 'deliverability',
                 'bounce',
                 'spam folder',
                 'transactional email',
                 'dkim',
                 'spf',
                 'dmarc',
                 'acmemail',
                 'sendgrid',
                 'twilio sendgrid',
                 'mailgun',
                 'postmark',
                 'amazon ses',
                 'aws ses',
                 'brevo',
                 'sendinblue',
                 'mailchimp',
                 'mandrill',
                 'sparkpost',
                 'mailjet',
                 'smtp2go',
                 'elastic email',
                 'gmail smtp',
                 'google workspace smtp',
                 'zoho mail',
                 'zeptomail',
                 'receive email',
                 'receiving email',
                 'incoming email',
                 'inbound email',
                 'parse email',
                 'email webhook',
                 'imap idle',
                 'email to webhook',
                 'mail server',
                 'email',
                 'e-mail',
                 'resend',
                 'ses',
                 'imap',
                 'transactional'],
 'PHPhelp': ['smtp',
             'deliverability',
             'bounce',
             'spam folder',
             'transactional email',
             'dkim',
             'spf',
             'dmarc',
             'acmemail',
             'sendgrid',
             'twilio sendgrid',
             'mailgun',
             'postmark',
             'amazon ses',
             'aws ses',
             'brevo',
             'sendinblue',
             'mailchimp',
             'mandrill',
             'sparkpost',
             'mailjet',
             'smtp2go',
             'elastic email',
             'gmail smtp',
             'google workspace smtp',
             'zoho mail',
             'zeptomail',
             'receive email',
             'receiving email',
             'incoming email',
             'inbound email',
             'parse email',
             'email webhook',
             'imap idle',
             'email to webhook',
             'mail server',
             'email',
             'e-mail',
             'resend',
             'ses',
             'imap',
             'transactional'],
 'node': ['smtp',
          'deliverability',
          'bounce',
          'spam folder',
          'transactional email',
          'dkim',
          'spf',
          'dmarc',
          'acmemail',
          'sendgrid',
          'twilio sendgrid',
          'mailgun',
          'postmark',
          'amazon ses',
          'aws ses',
          'brevo',
          'sendinblue',
          'mailchimp',
          'mandrill',
          'sparkpost',
          'mailjet',
          'smtp2go',
          'elastic email',
          'gmail smtp',
          'google workspace smtp',
          'zoho mail',
          'zeptomail',
          'receive email',
          'receiving email',
          'incoming email',
          'inbound email',
          'parse email',
          'email webhook',
          'imap idle',
          'email to webhook',
          'mail server',
          'email',
          'e-mail',
          'resend',
          'ses',
          'imap',
          'transactional'],
 'rails': ['smtp',
           'deliverability',
           'bounce',
           'spam folder',
           'transactional email',
           'dkim',
           'spf',
           'dmarc',
           'acmemail',
           'sendgrid',
           'twilio sendgrid',
           'mailgun',
           'postmark',
           'amazon ses',
           'aws ses',
           'brevo',
           'sendinblue',
           'mailchimp',
           'mandrill',
           'sparkpost',
           'mailjet',
           'smtp2go',
           'elastic email',
           'gmail smtp',
           'google workspace smtp',
           'zoho mail',
           'zeptomail',
           'receive email',
           'receiving email',
           'incoming email',
           'inbound email',
           'parse email',
           'email webhook',
           'imap idle',
           'email to webhook',
           'mail server',
           'email',
           'e-mail',
           'resend',
           'ses',
           'imap',
           'transactional'],
 'django': ['smtp',
            'deliverability',
            'bounce',
            'spam folder',
            'transactional email',
            'dkim',
            'spf',
            'dmarc',
            'acmemail',
            'sendgrid',
            'twilio sendgrid',
            'mailgun',
            'postmark',
            'amazon ses',
            'aws ses',
            'brevo',
            'sendinblue',
            'mailchimp',
            'mandrill',
            'sparkpost',
            'mailjet',
            'smtp2go',
            'elastic email',
            'gmail smtp',
            'google workspace smtp',
            'zoho mail',
            'zeptomail',
            'receive email',
            'receiving email',
            'incoming email',
            'inbound email',
            'parse email',
            'email webhook',
            'imap idle',
            'email to webhook',
            'mail server',
            'email',
            'e-mail',
            'resend',
            'ses',
            'imap',
            'transactional'],
 'laravel': ['smtp',
             'deliverability',
             'bounce',
             'spam folder',
             'transactional email',
             'dkim',
             'spf',
             'dmarc',
             'acmemail',
             'sendgrid',
             'twilio sendgrid',
             'mailgun',
             'postmark',
             'amazon ses',
             'aws ses',
             'brevo',
             'sendinblue',
             'mailchimp',
             'mandrill',
             'sparkpost',
             'mailjet',
             'smtp2go',
             'elastic email',
             'gmail smtp',
             'google workspace smtp',
             'zoho mail',
             'zeptomail',
             'receive email',
             'receiving email',
             'incoming email',
             'inbound email',
             'parse email',
             'email webhook',
             'imap idle',
             'email to webhook',
             'mail server',
             'email',
             'e-mail',
             'resend',
             'ses',
             'imap',
             'transactional'],
 'webdev': ['smtp',
            'deliverability',
            'bounce',
            'spam folder',
            'transactional email',
            'dkim',
            'spf',
            'dmarc',
            'acmemail',
            'sendgrid',
            'twilio sendgrid',
            'mailgun',
            'postmark',
            'amazon ses',
            'aws ses',
            'brevo',
            'sendinblue',
            'mailchimp',
            'mandrill',
            'sparkpost',
            'mailjet',
            'smtp2go',
            'elastic email',
            'gmail smtp',
            'google workspace smtp',
            'zoho mail',
            'zeptomail',
            'receive email',
            'receiving email',
            'incoming email',
            'inbound email',
            'parse email',
            'email webhook',
            'imap idle',
            'email to webhook',
            'mail server'],
 'SaaS': ['smtp',
          'deliverability',
          'bounce',
          'spam folder',
          'transactional email',
          'dkim',
          'spf',
          'dmarc',
          'acmemail',
          'sendgrid',
          'twilio sendgrid',
          'mailgun',
          'postmark',
          'amazon ses',
          'aws ses',
          'brevo',
          'sendinblue',
          'mailchimp',
          'mandrill',
          'sparkpost',
          'mailjet',
          'smtp2go',
          'elastic email',
          'gmail smtp',
          'google workspace smtp',
          'zoho mail',
          'zeptomail',
          'receive email',
          'receiving email',
          'incoming email',
          'inbound email',
          'parse email',
          'email webhook',
          'imap idle',
          'email to webhook',
          'mail server'],
 'AI_Agents': ['smtp',
               'deliverability',
               'bounce',
               'spam folder',
               'transactional email',
               'dkim',
               'spf',
               'dmarc',
               'acmemail',
               'sendgrid',
               'twilio sendgrid',
               'mailgun',
               'postmark',
               'amazon ses',
               'aws ses',
               'brevo',
               'sendinblue',
               'mailchimp',
               'mandrill',
               'sparkpost',
               'mailjet',
               'smtp2go',
               'elastic email',
               'gmail smtp',
               'google workspace smtp',
               'zoho mail',
               'zeptomail',
               'receive email',
               'receiving email',
               'incoming email',
               'inbound email',
               'parse email',
               'email webhook',
               'imap idle',
               'email to webhook',
               'mail server'],
 'selfhosted': ['smtp',
                'deliverability',
                'bounce',
                'spam folder',
                'transactional email',
                'dkim',
                'spf',
                'dmarc',
                'acmemail',
                'sendgrid',
                'twilio sendgrid',
                'mailgun',
                'postmark',
                'amazon ses',
                'aws ses',
                'brevo',
                'sendinblue',
                'mailchimp',
                'mandrill',
                'sparkpost',
                'mailjet',
                'smtp2go',
                'elastic email',
                'gmail smtp',
                'google workspace smtp',
                'zoho mail',
                'zeptomail',
                'receive email',
                'receiving email',
                'incoming email',
                'inbound email',
                'parse email',
                'email webhook',
                'imap idle',
                'email to webhook',
                'mail server'],
 'sysadmin': ['smtp',
              'deliverability',
              'bounce',
              'spam folder',
              'transactional email',
              'dkim',
              'spf',
              'dmarc',
              'acmemail',
              'sendgrid',
              'twilio sendgrid',
              'mailgun',
              'postmark',
              'amazon ses',
              'aws ses',
              'brevo',
              'sendinblue',
              'mailchimp',
              'mandrill',
              'sparkpost',
              'mailjet',
              'smtp2go',
              'elastic email',
              'gmail smtp',
              'google workspace smtp',
              'zoho mail',
              'zeptomail',
              'receive email',
              'receiving email',
              'incoming email',
              'inbound email',
              'parse email',
              'email webhook',
              'imap idle',
              'email to webhook',
              'mail server'],
 'devops': ['smtp',
            'deliverability',
            'bounce',
            'spam folder',
            'transactional email',
            'dkim',
            'spf',
            'dmarc',
            'acmemail',
            'sendgrid',
            'twilio sendgrid',
            'mailgun',
            'postmark',
            'amazon ses',
            'aws ses',
            'brevo',
            'sendinblue',
            'mailchimp',
            'mandrill',
            'sparkpost',
            'mailjet',
            'smtp2go',
            'elastic email',
            'gmail smtp',
            'google workspace smtp',
            'zoho mail',
            'zeptomail',
            'receive email',
            'receiving email',
            'incoming email',
            'inbound email',
            'parse email',
            'email webhook',
            'imap idle',
            'email to webhook',
            'mail server'],
 'golang': ['smtp',
            'deliverability',
            'bounce',
            'spam folder',
            'transactional email',
            'dkim',
            'spf',
            'dmarc',
            'acmemail',
            'sendgrid',
            'twilio sendgrid',
            'mailgun',
            'postmark',
            'amazon ses',
            'aws ses',
            'brevo',
            'sendinblue',
            'mailchimp',
            'mandrill',
            'sparkpost',
            'mailjet',
            'smtp2go',
            'elastic email',
            'gmail smtp',
            'google workspace smtp',
            'zoho mail',
            'zeptomail',
            'receive email',
            'receiving email',
            'incoming email',
            'inbound email',
            'parse email',
            'email webhook',
            'imap idle',
            'email to webhook',
            'mail server',
            'email',
            'e-mail',
            'resend',
            'ses',
            'imap',
            'transactional'],
 'dotnet': ['smtp',
            'deliverability',
            'bounce',
            'spam folder',
            'transactional email',
            'dkim',
            'spf',
            'dmarc',
            'acmemail',
            'sendgrid',
            'twilio sendgrid',
            'mailgun',
            'postmark',
            'amazon ses',
            'aws ses',
            'brevo',
            'sendinblue',
            'mailchimp',
            'mandrill',
            'sparkpost',
            'mailjet',
            'smtp2go',
            'elastic email',
            'gmail smtp',
            'google workspace smtp',
            'zoho mail',
            'zeptomail',
            'receive email',
            'receiving email',
            'incoming email',
            'inbound email',
            'parse email',
            'email webhook',
            'imap idle',
            'email to webhook',
            'mail server',
            'email',
            'e-mail',
            'resend',
            'ses',
            'imap',
            'transactional']}


class ProductTests(unittest.TestCase):
    def load(self, catalog: dict) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps(catalog))
            return pulse.load_catalog(path)

    def test_required_fields(self) -> None:
        for product in ({}, {'aliases': [], 'keywords': {'core': []}},
                        {'name': 'Example', 'keywords': {'core': []}},
                        {'name': 'Example', 'aliases': []},
                        {'name': 'Example', 'aliases': [], 'keywords': {}},
                        {'name': '', 'aliases': [], 'keywords': {'core': []}},
                        {'name': 'Example', 'aliases': 'example', 'keywords': {'core': []}},
                        {'name': 'Example', 'aliases': [], 'keywords': {'core': 'term'}}):
            with self.subTest(product=product), self.assertRaisesRegex(ValueError, 'product'):
                self.load({'product': product})

    def test_defaults(self) -> None:
        catalog = self.load({'product': {'name': 'Example', 'aliases': ['example'],
                                        'keywords': {'core': ['specific']}},
                             'subreddits': {'node': {}}})
        self.assertEqual(catalog['product'], {
            'name': 'Example', 'aliases': ['example'], 'site': '', 'domain': '', 'category': '',
            'tool_label': 'tools', 'relevance': '', 'topics': [], 'contact': '',
            'keywords': {'core': ['specific'], 'generic': [], 'competitors': 'providers'}})
        self.assertEqual(catalog['subreddits']['node'], {
            'allow_generic': False, 'extra_keywords': [], 'comment_policy': 'unknown'})

    def test_legacy_catalog(self) -> None:
        catalog = self.load({'subreddits': {'node': {'keywords': ['old term']}}})
        self.assertEqual(pulse.subreddit_terms(catalog, 'r/NODE', 'extra'), ['old term', 'extra'])

    def test_migration_preserves_terms_and_matches(self) -> None:
        catalog = pulse.load_catalog(FIXTURE_CATALOG)
        self.assertEqual(set(catalog['subreddits']), set(OLD_SUBREDDIT_TERMS))
        samples = ['unrelated text', 'SMTP trouble', 'Amazon SES problem', 'resend',
                   'email', 'IMAP', 'transactional', 'custom phrase', 'emailing']
        for sub, old_terms in OLD_SUBREDDIT_TERMS.items():
            with self.subTest(sub=sub):
                self.assertEqual(set(pulse.subreddit_terms(catalog, sub, '')), set(old_terms))
                old = pulse.build_keyword_re(old_terms)
                new = pulse.subreddit_keyword_re(catalog, sub, '')
                for text in samples + old_terms + ['x' + term + 'x' for term in old_terms]:
                    self.assertEqual(bool(new.search(text)), bool(old.search(text)), text)

    def test_generic_and_provider_gating(self) -> None:
        catalog = {'product': {'keywords': {'core': ['specific'], 'generic': ['broad', 'rival']}},
                   'providers': {'Rival': ['rival', 'rival full']},
                   'subreddits': {'wide': {'extra_keywords': ['local']},
                                  'narrow': {'allow_generic': True}}}
        self.assertEqual(set(pulse.subreddit_terms(catalog, 'wide', 'cli')), {'specific', 'rival full', 'local', 'cli'})
        self.assertEqual(set(pulse.subreddit_terms(catalog, 'narrow', '')), {'specific', 'broad', 'rival', 'rival full'})

    def test_product_path_for_every_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'catalogs'
            folder.mkdir()
            (folder / 'x.json').write_text('{}')
            for command, positional in [('collect', []), ('omakase', []), ('thread', ['abc']), ('ping', [])]:
                with self.subTest(command=command), patch.object(pulse, 'CATALOGS_DIR', folder), \
                        patch.object(pulse, 'cmd_' + command, return_value=0) as run:
                    self.assertEqual(pulse.main([command, *positional, '--product', 'x']), 0)
                    self.assertEqual(Path(run.call_args.args[0].catalog), folder / 'x.json')

    def test_resolution_order(self) -> None:
        previous = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                self.assertEqual(pulse.resolve_catalog_path(None, None), pulse.CATALOGS_DIR / 'example-product.json')
                self.assertEqual(pulse.resolve_catalog_path('acmemail', None), FIXTURE_CATALOG)
                local = Path(directory) / 'catalogs'
                local.mkdir()
                (local / 'acmemail.json').write_text('{}')
                self.assertEqual(pulse.resolve_catalog_path('acmemail', None).resolve(), (local / 'acmemail.json').resolve())
        finally:
            os.chdir(previous)

    def test_explicit_catalog_wins(self) -> None:
        with patch.object(pulse, 'cmd_collect', return_value=0) as run:
            pulse.main(['collect', '--product', 'missing', '--catalog', '/nonexistent/custom.json'])
        self.assertEqual(run.call_args.args[0].catalog, '/nonexistent/custom.json')

    def test_missing_product_lists_available_products(self) -> None:
        with self.assertRaises(ValueError) as error:
            pulse.resolve_catalog_path('missing', None)
        self.assertIn(str(pulse.CATALOGS_DIR), str(error.exception))
        self.assertIn('acmemail', str(error.exception))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            pulse.main(['ping', '--product', 'missing'])
        self.assertEqual(error.exception.code, 2)

    def test_bare_and_explicit_site(self) -> None:
        for flags, site in [(['--site'], 'https://acmemail.example'),
                            (['--site', 'https://example.com'], 'https://example.com'), ([], None)]:
            with patch.object(pulse, 'cmd_omakase', return_value=0) as run:
                pulse.main(['omakase', '--product', 'acmemail', *flags])
            self.assertEqual(run.call_args.args[0].site, site)

    def test_bare_site_requires_product_site(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            pulse.main(['omakase', '--catalog', '/nonexistent/custom.json', '--site'])

    def test_user_agent_tracks_loaded_product(self) -> None:
        self.addCleanup(setattr, pulse, 'USER_AGENT', pulse.USER_AGENT)
        with patch.dict(os.environ, {'PAINPOINT_RADAR_CONTACT': ''}):
            self.load({'product': {'name': 'Example', 'aliases': [], 'keywords': {'core': []}, 'contact': 'someone@example.com'}})
            self.assertEqual(pulse.USER_AGENT, f'painpoint-radar/{pulse.VERSION} (Example content research; contact someone@example.com)')
            self.load({})
            self.assertEqual(pulse.USER_AGENT, f'painpoint-radar/{pulse.VERSION} (content research)')
        with patch.dict(os.environ, {'PAINPOINT_RADAR_CONTACT': 'env@example.com'}):
            self.load({'product': {'name': 'Example', 'aliases': [], 'keywords': {'core': []}, 'contact': 'someone@example.com'}})
            self.assertEqual(pulse.USER_AGENT, f'painpoint-radar/{pulse.VERSION} (Example content research; contact env@example.com)')

    def test_collection_records_product(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(pulse, 'Http') as http, \
                patch.object(pulse, 'make_backend') as backend:
            http.return_value.requests = http.return_value.cache_hits = 0
            backend.return_value.posts_window.return_value = []
            with contextlib.redirect_stdout(io.StringIO()):
                pulse.main(['collect', '--product', 'acmemail', '--subs', 'node', '--out', directory])
            summary = json.loads((Path(directory) / 'summary.json').read_text())
            self.assertEqual(summary['product'], 'AcmeMail')


if __name__ == '__main__':
    unittest.main()
