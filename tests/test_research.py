from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import research as research_module  # noqa: E402
from db import connect, init_db  # noqa: E402


class ResearchAdapterTests(unittest.TestCase):
    settings = {"request_timeout": 10, "user_agent": "test", "contact_email": ""}

    def source(self, adapter="arxiv", doc_type="paper", source_id="test"):
        return {
            "id": source_id, "name": "Test Source", "publisher": "Test",
            "adapter": adapter, "query": "test query", "homepage": "",
            "country": "US", "language": "en", "doc_type": doc_type,
            "days": 30, "refresh_minutes": 60, "max_items": 10,
            "enabled": True, "note": "",
        }

    def test_arxiv_normalization(self):
        xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>http://arxiv.org/abs/2411.00001v1</id>
            <title>Test Paper</title>
            <summary>An abstract.</summary>
            <published>2026-11-17T00:00:00Z</published>
            <author><name>Alice</name></author>
            <arxiv:doi>10.1234/test</arxiv:doi>
            <link href="http://arxiv.org/pdf/2411.00001" title="pdf" type="application/pdf"/>
            <category term="cs.AI"/>
          </entry>
        </feed>"""
        with mock.patch.object(research_module, "_xml", return_value=ET.fromstring(xml)):
            items = research_module.fetch_arxiv(self.source("arxiv"), self.settings)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Test Paper")
        self.assertEqual(items[0]["doi"], "10.1234/test")
        self.assertEqual(items[0]["authors"], ["Alice"])
        self.assertEqual(items[0]["topics"], ["cs.AI"])
        self.assertIn("/pdf/", items[0]["pdf_url"])

    def test_crossref_normalization(self):
        sample = {
            "message": {
                "items": [{
                    "title": ["Crossref Paper"],
                    "abstract": "<jats:p>Crossref abstract.</jats:p>",
                    "author": [{
                        "given": "Alice", "family": "Smith",
                        "affiliation": [{"name": "Test University"}],
                    }],
                    "published": {"date-parts": [[2026, 11, 17]]},
                    "DOI": "10.1234/crossref",
                    "URL": "https://doi.org/10.1234/crossref",
                    "container-title": ["Test Journal"],
                    "subject": ["AI"],
                    "link": [{"content-type": "application/pdf", "URL": "https://example.org/pdf"}],
                }]
            }
        }
        with mock.patch.object(research_module, "_json", return_value=sample):
            items = research_module.fetch_crossref(self.source("crossref"), self.settings)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Crossref Paper")
        self.assertEqual(items[0]["abstract"], "Crossref abstract.")
        self.assertEqual(items[0]["authors"], ["Alice Smith"])
        self.assertEqual(items[0]["institutions"], ["Test University"])
        self.assertEqual(items[0]["doi"], "10.1234/crossref")
        self.assertEqual(items[0]["publisher"], "Test Journal")

    def test_openalex_normalization(self):
        sample = {
            "results": [{
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1234/openalex",
                "title": "OpenAlex Paper",
                "publication_date": "2026-11-17",
                "authorships": [{
                    "author": {"display_name": "Alice"},
                    "institutions": [{"display_name": "Test University"}],
                }],
                "primary_location": {
                    "landing_page_url": "https://example.org/paper",
                    "pdf_url": "https://example.org/paper.pdf",
                    "source": {"display_name": "Test Journal"},
                },
                "abstract_inverted_index": {"Hello": [0], "world": [1]},
                "primary_topic": {"display_name": "Climate"},
            }]
        }
        with mock.patch.object(research_module, "_json", return_value=sample):
            items = research_module.fetch_openalex(self.source("openalex"), self.settings)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["abstract"], "Hello world")
        self.assertEqual(items[0]["doi"], "10.1234/openalex")
        self.assertEqual(items[0]["authors"], ["Alice"])
        self.assertEqual(items[0]["institutions"], ["Test University"])
        self.assertEqual(items[0]["topics"], ["Climate"])

    def test_pubmed_normalization(self):
        xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation>
          <PMID>123</PMID><Article><ArticleTitle>PubMed Paper</ArticleTitle>
          <Abstract><AbstractText Label="BACKGROUND">Abstract text.</AbstractText></Abstract>
          <Journal><Title>Test Journal</Title></Journal>
          <PubDate><Year>2026</Year><Month>Nov</Month><Day>17</Day></PubDate>
          <AuthorList><Author><LastName>Smith</LastName><ForeName>Alice</ForeName>
            <AffiliationInfo><Affiliation>Test University</Affiliation></AffiliationInfo>
          </Author></AuthorList></Article></MedlineCitation>
          <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1234/pubmed</ArticleId></ArticleIdList></PubmedData>
        </PubmedArticle></PubmedArticleSet>"""
        with mock.patch.object(research_module, "_json", return_value={"esearchresult": {"idlist": ["123"]}}), \
             mock.patch.object(research_module, "_xml", return_value=ET.fromstring(xml)):
            items = research_module.fetch_pubmed(self.source("pubmed"), self.settings)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "PubMed Paper")
        self.assertIn("Abstract text", items[0]["abstract"])
        self.assertEqual(items[0]["doi"], "10.1234/pubmed")
        self.assertEqual(items[0]["authors"], ["Alice Smith"])
        self.assertEqual(items[0]["institutions"], ["Test University"])
        self.assertEqual(items[0]["report_no"], "PMID 123")

    def test_federal_register_normalization(self):
        sample = {
            "results": [{
                "title": "Federal Report",
                "abstract": "Report abstract.",
                "publication_date": "2026-11-17",
                "html_url": "https://example.gov/report",
                "pdf_url": "https://example.gov/report.pdf",
                "document_number": "2026-12345",
                "agencies": [{"raw_name": "Test Agency"}],
                "type": "Notice",
            }]
        }
        with mock.patch.object(research_module, "_json", return_value=sample):
            items = research_module.fetch_federal_register(
                self.source("federal_register", "report"), self.settings
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["doc_type"], "report")
        self.assertEqual(items[0]["report_no"], "2026-12345")
        self.assertEqual(items[0]["institutions"], ["Test Agency"])
        self.assertIn("report.pdf", items[0]["pdf_url"])


class ResearchDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "research.db")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def source(self, source_id="src"):
        return {
            "id": source_id, "name": "Test Source", "publisher": "Test",
            "adapter": "arxiv", "query": "test", "homepage": "",
            "country": "US", "language": "en", "doc_type": "paper",
            "days": 30, "refresh_minutes": 60, "max_items": 10,
            "enabled": True, "note": "",
        }

    def test_insert_dedup_and_query(self):
        source = self.source("src-a")
        other = self.source("src-b")
        research_module.sync_sources(self.conn, [source, other])
        first = research_module._item(
            source, title="First Paper", abstract="First abstract.",
            published_at="2026-11-17", url="https://example.org/a",
            doi="10.1234/same", authors=["Alice"], institutions=["Uni"],
        )
        duplicate = research_module._item(
            other, title="Duplicate DOI", published_at="2026-11-17",
            url="https://example.org/b", doi="10.1234/same",
        )
        self.assertEqual(research_module.insert_items(self.conn, [first, duplicate]), 1)
        items, total, facets = research_module.query_items(
            self.conn, {"days": 3650, "limit": 10}
        )
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["title"], "First Paper")
        self.assertEqual(items[0]["authors"], ["Alice"])
        self.assertEqual(facets["doc_types"], {"paper": 1})
        self.assertEqual(research_module.stats(self.conn)["items"], 1)

    def test_query_filters(self):
        source = self.source("src-a")
        research_module.sync_sources(self.conn, [source])
        research_module.insert_items(self.conn, [
            research_module._item(
                source, title="Climate Paper", abstract="climate",
                published_at="2026-11-17", url="https://example.org/c",
                doi="10.1234/c",
            )
        ])
        items, total, _ = research_module.query_items(
            self.conn, {"q": "Climate", "doc_type": "paper", "days": 3650}
        )
        self.assertEqual(total, 1)
        self.assertEqual(len(items), 1)
        _, empty, _ = research_module.query_items(
            self.conn, {"doc_type": "report", "days": 3650}
        )
        self.assertEqual(empty, 0)
